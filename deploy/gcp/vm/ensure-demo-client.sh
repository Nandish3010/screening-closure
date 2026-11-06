#!/bin/bash
# ADR-060: make sure the Keycloak realm has the one-click demo client. `start --import-realm` only imports a realm that does not exist
# yet, so on a VM whose realm predates ADR-060 the client has to be added to the live realm; on a fresh VM the import already has it and
# this does nothing. Idempotent. Run by vm/sc-up.sh (production mode) once Keycloak answers. Prints no secret: the admin password
# stays inside the container (its own KC_BOOTSTRAP_ADMIN_PASSWORD), the client JSON holds no secret.
# Usage: ensure-demo-client.sh REALM.json   (the rendered realm file, which contains the client)
set -euo pipefail
REALM_FILE=${1:?usage: ensure-demo-client.sh REALM.json}
SVC=${SC_KEYCLOAK_SERVICE:-keycloak-prod}
CID=screening-closure-demo
client_json=$(python3 -I - "$REALM_FILE" "$CID" <<'PYEOF'
import json, sys
realm = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps(next(c for c in realm["clients"] if c["clientId"] == sys.argv[2])))
PYEOF
)
kc() { docker compose exec -T "$SVC" /opt/keycloak/bin/kcadm.sh "$@"; }
for i in $(seq 1 90); do
  docker compose exec -T "$SVC" sh -c 'exec /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080/auth --realm master --user admin --password "$KC_BOOTSTRAP_ADMIN_PASSWORD"' >/dev/null 2>&1 && break
  [ "$i" = 90 ] && { echo "ensure-demo-client: Keycloak did not accept the admin login in time" >&2; exit 1; }
  sleep 5
done
if [ -n "$(kc get clients -r screening-closure -q "clientId=$CID" --fields clientId 2>/dev/null | grep -o "$CID" || true)" ]; then
  echo "ensure-demo-client: $CID already present"
else
  printf '%s' "$client_json" | kc create clients -r screening-closure -f - >/dev/null
  echo "ensure-demo-client: $CID created"
fi
