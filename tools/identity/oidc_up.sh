#!/bin/sh
# A29 (ADR-040): start (or stop) the local Keycloak and put the API in AUTH_MODE=oidc.
# Demo passwords are never committed: each KC_PW_* comes from the environment or .env if set there, else it is generated
# for this run (openssl) and written to .oidc-demo-credentials (mode 600, git-ignored) so the demo user can read it.
set -eu
cd "$(dirname "$0")/../.."
cmd="${1:-up}"

# value of KEY from the environment, else from .env, else empty
from_env() {
  eval "v=\${$1:-}"
  if [ -z "$v" ] && [ -f .env ]; then v=$(sed -n "s/^$1=//p" .env | tail -n 1); fi
  printf '%s' "$v"
}

if [ "$cmd" = "down" ]; then
  docker compose --profile oidc stop keycloak
  AUTH_MODE=dev docker compose up -d api
  echo "oidc-down: keycloak stopped; the API is back in AUTH_MODE=dev"
  exit 0
fi

umask 077
creds=.oidc-demo-credentials
: > "$creds.new"
for key in KEYCLOAK_ADMIN_PASSWORD KC_PW_LAB KC_PW_COORDINATOR KC_PW_OWNER KC_PW_SPECIALIST KC_PW_CLINICIAN KC_PW_SUPERVISOR KC_PW_PATIENT; do
  val=$(from_env "$key")
  if [ -z "$val" ]; then val=$(openssl rand -hex 12); fi
  export "$key=$val"
  echo "$key=$val" >> "$creds.new"
done
mv "$creds.new" "$creds"

export AUTH_MODE=oidc
docker compose --profile oidc up -d --build keycloak api
echo "oidc-up: Keycloak on http://localhost:${KEYCLOAK_PORT:-8180} (realm screening-closure), API in AUTH_MODE=oidc."
echo "Demo users and the generated passwords are in $creds (not committed). Web: VITE_API_ADAPTER=http VITE_AUTH_MODE=oidc npm run dev"
