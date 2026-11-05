#!/bin/bash
# Rehearse the hosted (production-mode) stack on THIS machine, without GCP: the same docker-compose.yml + deploy/gcp/compose.gcp.yml
# overlay, the env files rendered by vm/render_env.py from GENERATED throw-away secrets, the committed realm for host "localhost",
# Caddy with its own internal CA, then deploy/gcp/smoke-test.sh against https://localhost and the gated API restart against the live
# container. Never touches Secret Manager, gcloud or a real credential. Uses its own compose project (sc-rehearsal), so it does not
# touch a `make up` stack's volumes; but both publish 127.0.0.1 ports (8000, 5432 ...) and 80/443, so stop other stacks first.
#   deploy/gcp/rehearse-local.sh up      build, start, wait, smoke test, gated-restart check
#   deploy/gcp/rehearse-local.sh down    remove the rehearsal stack and its volumes and the temp directory
# Cannot show: real certificates (Let's Encrypt), the VM's systemd, 2 vCPU timing, cgroup memory on Linux, IAP SSH, Secret Manager.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$here/../.." && pwd)
T=${SC_REHEARSAL_DIR:-${TMPDIR:-/tmp}/sc-rehearsal}
export COMPOSE_PROJECT_NAME=sc-rehearsal
export SC_PUBLIC_HOST=localhost
export COMPOSE_FILE="$REPO/docker-compose.yml:$T/compose.gcp.local.yml"
export COMPOSE_ENV_FILES="$T/sc/compose.env"
export COMPOSE_PROFILES=public
export SNAPSHOT_SKIP_CLOCK_RESET=1
cd "$REPO"

if [ "${1:-}" = down ]; then
  [ -f "$T/compose.gcp.local.yml" ] && docker compose down -v --remove-orphans || true
  rm -rf "$T"; echo "rehearsal stack removed"; exit 0
fi
[ "${1:-}" = up ] || { echo "usage: $0 up|down" >&2; exit 2; }

rm -rf "$T"; mkdir -p "$T/sc/keycloak-import"; chmod 700 "$T"
# generated test secrets (not printed, not committed), rendered exactly as the VM does
python3 - "$T/sc" <<'PYEOF'
import json, secrets, subprocess, sys
sys.path.insert(0, "deploy/gcp/vm")
from render_env import REQUIRED
s = {n: secrets.token_hex(24) for n in REQUIRED}
subprocess.run([sys.executable, "deploy/gcp/vm/render_env.py", "--host", "localhost", "--mode", "production", "--out-dir", sys.argv[1]],
               input=json.dumps(s), text=True, check=True)
PYEOF
python3 deploy/gcp/vm/render_realm.py localhost config/keycloak/screening-closure-realm.json "$T/sc/keycloak-import/screening-closure-realm.json"
# the overlay with /etc/sc swapped for the temp directory (paths only; nothing else differs)
sed "s#/etc/sc/#$T/sc/#g" deploy/gcp/compose.gcp.yml > "$T/compose.gcp.local.yml"
grep -c "$T/sc/" "$T/compose.gcp.local.yml" | sed 's/^/overlay paths rewritten: /'

echo "== docker compose config (overlay merges)"; docker compose config -q && echo ok
docker compose up -d postgres db-init
until [ "$(docker compose ps postgres --format '{{.Health}}')" = healthy ]; do sleep 2; done
kcpw=$(sed -n 's/^KC_DB_PASSWORD=//p' "$T/sc/compose.env")
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U closure -d postgres >/dev/null <<SQL
DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak') THEN CREATE ROLE keycloak LOGIN; END IF; END \$\$;
ALTER ROLE keycloak PASSWORD '${kcpw}';
SELECT 'CREATE DATABASE keycloak OWNER keycloak' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
REVOKE CONNECT ON DATABASE keycloak FROM PUBLIC;
SQL
unset kcpw
docker compose up -d --build
echo "== waiting for https://localhost/api/health (Keycloak production start takes minutes)"
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$T/caddy-root.crt" 2>/dev/null || true
for i in $(seq 1 120); do
  [ -f "$T/caddy-root.crt" ] || docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$T/caddy-root.crt" 2>/dev/null || true
  [ -f "$T/caddy-root.crt" ] && CURL_CA_BUNDLE="$T/caddy-root.crt" curl -fs -m 5 https://localhost/api/health >/dev/null 2>&1 && break
  sleep 5
done
echo "== smoke test (deploy/gcp/smoke-test.sh https://localhost, trusting Caddy's local CA)"
rc=0; CURL_CA_BUNDLE="$T/caddy-root.crt" "$here/smoke-test.sh" https://localhost || rc=$?
echo "smoke-test exit code: $rc"

echo "== gated API restart against the live container (vm/sc-api-restart.sh)"
export SC_ENV_SH=/nonexistent SC_RESTART_LOG="$T/api-restart.log" SC_RESTART_STATE="$T/api-restart.last" SC_REPO="$REPO"
"$here/vm/sc-api-restart.sh" check
echo "-- gate at 70%: expect no restart"; "$here/vm/sc-api-restart.sh" gate
before=$(docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q api)")
echo "-- gate forced to 1%: expect a restart of api only"; SC_RESTART_GATE_PCT=1 "$here/vm/sc-api-restart.sh" gate
after=$(docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q api)")
[ "$before" != "$after" ] && echo "api StartedAt changed: restarted" || echo "api StartedAt UNCHANGED"
echo "-- again at once: expect the loop guard"; SC_RESTART_GATE_PCT=1 "$here/vm/sc-api-restart.sh" gate
echo "-- restart log:"; cat "$T/api-restart.log"
for i in $(seq 1 60); do curl -fs -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 2; done
CURL_CA_BUNDLE="$T/caddy-root.crt" curl -fs -m 10 https://localhost/api/health >/dev/null && echo "api healthy again behind the edge"
exit "$rc"
