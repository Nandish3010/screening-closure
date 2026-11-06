#!/bin/bash
# ADR-060: make sure the Keycloak realm has the one-click demo client. `start --import-realm` only imports a realm that does not exist
# yet, so on a VM whose realm predates ADR-060 the client has to be added to the live realm; on a fresh VM the import already has it and
# this changes nothing that is already right. Idempotent. Run by vm/sc-up.sh (production mode) once Keycloak answers. Prints no secret:
# the admin password stays inside the container (its own KC_BOOTSTRAP_ADMIN_PASSWORD) and so does the client secret (the container's
# own KC_DEMO_CLIENT_SECRET, which compose.gcp.yml sets from the same Secret Manager value the API holds); the client JSON holds neither.
# ADR-060 addendum: the client is made CONFIDENTIAL and an EXISTING client is updated in place (public -> confidential, new secret),
# and the realm's brute-force settings are made short and temporary, because an existing realm is never touched by the import.
# Usage: ensure-demo-client.sh REALM.json   (the rendered realm file, which contains the client)
set -euo pipefail
REALM_FILE=${1:?usage: ensure-demo-client.sh REALM.json}
SVC=${SC_KEYCLOAK_SERVICE:-keycloak-prod}
CID=screening-closure-demo
client_json=$(python3 -I - "$REALM_FILE" "$CID" <<'PYEOF'
import json, sys
realm = json.load(open(sys.argv[1], encoding="utf-8"))
c = next(c for c in realm["clients"] if c["clientId"] == sys.argv[2])
c.pop("secret", None)  # the import placeholder; the real secret is set below from the container's environment
print(json.dumps(c))
brute = {k: v for k, v in realm.items() if k in ("bruteForceProtected", "failureFactor", "permanentLockout", "waitIncrementSeconds",
                                                 "maxFailureWaitSeconds", "minimumQuickLoginWaitSeconds", "quickLoginCheckMilliSeconds",
                                                 "maxDeltaTimeSeconds")}
print(json.dumps(brute))
PYEOF
)
brute_json=$(printf '%s\n' "$client_json" | sed -n 2p)
client_json=$(printf '%s\n' "$client_json" | sed -n 1p)
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
# In place, every run: confidential + the secret (so a client created public by an earlier deploy stops accepting the public), and
# the short temporary brute-force lockouts on the live realm. The secret is read from the container's environment, never an argument.
uuid=$(kc get clients -r screening-closure -q "clientId=$CID" --fields id --format csv --noquotes 2>/dev/null | tr -d '\r' | head -n1)
[ -n "$uuid" ] || { echo "ensure-demo-client: cannot find the $CID client id" >&2; exit 1; }
patch=$(python3 -I -c 'import json,sys; print(json.dumps({"publicClient": False, "clientAuthenticatorType": "client-secret", "standardFlowEnabled": False, "implicitFlowEnabled": False, "directAccessGrantsEnabled": True, "serviceAccountsEnabled": False}))')
printf '%s' "$patch" | kc update "clients/$uuid" -r screening-closure -m -f - >/dev/null
docker compose exec -T "$SVC" sh -c 'test -n "$KC_DEMO_CLIENT_SECRET" && printf "{\"secret\":\"%s\"}" "$KC_DEMO_CLIENT_SECRET" | /opt/keycloak/bin/kcadm.sh update "clients/'"$uuid"'" -r screening-closure -m -f - >/dev/null' \
  || { echo "ensure-demo-client: the Keycloak container has no KC_DEMO_CLIENT_SECRET (re-run sc-up.sh so compose recreates it)" >&2; exit 1; }
echo "ensure-demo-client: $CID is confidential with its secret set"
printf '%s' "$brute_json" | kc update realms/screening-closure -m -f - >/dev/null
echo "ensure-demo-client: realm brute-force lockouts are short and temporary"
