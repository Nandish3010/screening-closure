#!/bin/bash
# On the VM, as root, on every boot and after every deploy: read the secrets from Secret Manager (with the VM's own service
# account), render /etc/sc/*.env (0600), make sure the Keycloak database exists, and start the stack in the mode in /var/lib/sc/mode
# (seed = no public listener; production = fail-closed config behind Caddy). Prints no secret.
set -euo pipefail
export SC_REPO=${SC_REPO:-/srv/sc/repo}
md() { curl -fs -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/$1"; }
PROJECT=$(md project/project-id)
HOST=$(md instance/attributes/sc-public-host)
MODE=$(cat /var/lib/sc/mode 2>/dev/null || echo seed)
umask 077
mkdir -p /etc/sc /var/lib/sc
install -d -m 0755 /srv/sc/media   # the submission video, served read-only at /media/ (deploy/gcp/put-media.sh)

PY=$SC_REPO/deploy/gcp/vm

# BUG-77 workaround: the gated API restart (every 15 min) and its daily fallback. Installed here, not in startup.sh, because this runs
# on every boot AND after every deploy, once the bundle exists. Idempotent: install overwrites, enable --now is a no-op if enabled.
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  install -m 0644 "$PY"/sc-api-restart.service "$PY"/sc-api-restart.timer "$PY"/sc-api-restart-daily.service "$PY"/sc-api-restart-daily.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now sc-api-restart.timer sc-api-restart-daily.timer
  systemctl try-restart sc-api-restart.timer sc-api-restart-daily.timer
  # the earlier fixed 22:30 UTC timer was the same unit name as the gate timer, so it is replaced above; nothing else to remove
fi
# ADR-058: the judge demo's reset. /var/lib/sc-demo is bind-mounted into the api container (compose.gcp.yml), which runs as uid 10001
# and writes the reset request there; the host-side watcher (root) answers. Created before the stack starts so docker does not create
# it root-owned. Then the watcher (every 15 s) and the scheduled reset (every 6 h) units, same pattern as above; nothing else is added.
mkdir -p /var/lib/sc-demo
chown 10001 /var/lib/sc-demo
chmod 0755 /var/lib/sc-demo
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  install -m 0644 "$PY"/sc-demo-reset-watch.service "$PY"/sc-demo-reset-watch.timer "$PY"/sc-demo-reset-scheduled.service "$PY"/sc-demo-reset-scheduled.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now sc-demo-reset-watch.timer sc-demo-reset-scheduled.timer
  systemctl try-restart sc-demo-reset-watch.timer sc-demo-reset-scheduled.timer
fi
secrets_json=$(python3 - "$PROJECT" <<'PYEOF'
import json, subprocess, sys
sys.path.insert(0, "/srv/sc/repo/deploy/gcp/vm")
from render_env import REQUIRED
out = {}
for n in REQUIRED:
    out[n] = subprocess.run(["gcloud", "secrets", "versions", "access", "latest", f"--secret=sc-demo-{n}", f"--project={sys.argv[1]}"],
                            check=True, capture_output=True, text=True).stdout.strip()
print(json.dumps(out))
PYEOF
)
ref=""
[ -f /var/lib/sc/demo_patient_ref ] && ref=$(cat /var/lib/sc/demo_patient_ref)
printf '%s' "$secrets_json" | python3 "$PY/render_env.py" --host "$HOST" --mode "$MODE" --out-dir /etc/sc --demo-patient-ref "$ref"
unset secrets_json

mkdir -p /etc/sc/keycloak-import
python3 "$PY/render_realm.py" "$HOST" "$SC_REPO/config/keycloak/screening-closure-realm.json" /etc/sc/keycloak-import/screening-closure-realm.json
# The umask above makes these root-only, but Keycloak runs as uid 1000 in its container and must READ the import directory (otherwise:
# "Failed to start server ... directory not found"). The file holds only ${KC_PW_*} placeholders, no secret, so it is world-readable.
chmod 0755 /etc/sc/keycloak-import
chmod 0644 /etc/sc/keycloak-import/screening-closure-realm.json

# shellcheck disable=SC1091
. "$PY/env.sh"
docker compose up -d postgres db-init
until [ "$(docker compose ps postgres --format '{{.Health}}')" = healthy ]; do sleep 2; done
# Keycloak's own role and database (idempotent). The password goes in on stdin, never on a command line.
kcpw=$(sed -n 's/^KC_DB_PASSWORD=//p' /etc/sc/compose.env)
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U closure -d postgres >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak') THEN CREATE ROLE keycloak LOGIN; END IF;
END \$\$;
ALTER ROLE keycloak PASSWORD '${kcpw}';
SELECT 'CREATE DATABASE keycloak OWNER keycloak' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
REVOKE CONNECT ON DATABASE keycloak FROM PUBLIC;
SQL
unset kcpw
docker compose up -d --build
echo "sc-up: mode=$MODE host=$HOST"
