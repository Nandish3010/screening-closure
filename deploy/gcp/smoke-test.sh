#!/bin/bash
# Checks the hosted URL after deploy. No credentials needed; sends no data. Usage: smoke-test.sh [https://host]   (default: the VM's host)
# Exit 0 = every check passed. Uses only curl.
set -u
here=$(cd "$(dirname "$0")" && pwd)
if [ -n "${1:-}" ]; then BASE=${1%/}; else
  # shellcheck disable=SC1091
  . "$here/lib.sh"; need_project; BASE="https://$(public_host)"; fi
HOST=${BASE#https://}
fail=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fail=1; }
code() { curl -s -o /dev/null -m 15 -w '%{http_code}' "$@" || echo 000; }
check() { # name expected-code url [curl args...]
  local n=$1 want=$2; shift 2; local got; got=$(code "$@"); [ "$got" = "$want" ] && ok "$n ($got)" || bad "$n: wanted $want, got $got"; }

[ "$(code "http://$HOST/")" = 301 ] || [ "$(code "http://$HOST/")" = 308 ] && ok "http redirects to https" || bad "http does not redirect to https"
check "web app served" 200 "$BASE/"
body=$(curl -s -m 15 "$BASE/api/health"); echo "$body" | grep -q '"status"' && ok "api /health answers" || bad "api /health: $body"
echo "$body" | grep -qi 'demo placeholder' && bad "health reports the DEMO PLACEHOLDER approval (must be off in production)" || ok "no demo-placeholder approval"
check "OIDC discovery document" 200 "$BASE/auth/realms/screening-closure/.well-known/openid-configuration"
iss=$(curl -s -m 15 "$BASE/auth/realms/screening-closure/.well-known/openid-configuration" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("issuer",""))' 2>/dev/null || true)
[ "$iss" = "$BASE/auth/realms/screening-closure" ] && ok "issuer is $iss" || bad "issuer is '$iss', expected $BASE/auth/realms/screening-closure"
check "cases need a token" 401 "$BASE/api/cases"
check "dev login is closed" 404 -X POST -H 'Content-Type: application/json' -d '{"role":"supervisor","name":"x"}' "$BASE/api/auth/dev-login"
check "clock reset does not exist" 404 -X POST "$BASE/api/clock/reset"
check "clock advance needs a supervisor token" 401 -X POST -H 'Content-Type: application/json' -d '{"days":1}' "$BASE/api/clock/advance"
echo "$body" | grep -q '"simulated_clock": *true' && ok "health reports the demo clock (the UI banner source)" || bad "health does not report demo.simulated_clock"
echo "$body" | grep -q '"clock_mode": *"sim"' && ok "clock is the simulated one" || bad "clock_mode is not sim"
check "scheduler path blocked at the edge" 404 -X POST "$BASE/api/scheduler/sweep"
check "metrics endpoint blocked at the edge" 404 "$BASE/api/metrics"
check "Keycloak admin console not public" 404 "$BASE/auth/admin/master/console/"
check "master realm not public" 404 "$BASE/auth/realms/master/.well-known/openid-configuration"
check "database port closed" 000 -m 4 "http://$HOST:5432/"
# rate limit: a burst of 80 unauthenticated calls must produce at least one 429
n429=$(seq 1 80 | xargs -P 16 -I{} curl -s -o /dev/null -m 15 -w '%{http_code}\n' "$BASE/api/pathways" | grep -c '^429$' || true)
[ "$n429" -gt 0 ] && ok "rate limit active ($n429 of 80 got 429)" || bad "no 429 in a burst of 80 requests (rate limit not active?)"
hsts=$(curl -sI -m 15 "$BASE/" | tr -d '\r' | grep -i '^strict-transport-security' || true)
[ -n "$hsts" ] && ok "HSTS header present" || bad "no Strict-Transport-Security header"
echo; [ $fail = 0 ] && echo "SMOKE TEST PASSED for $BASE" || echo "SMOKE TEST FAILED for $BASE"
exit $fail
