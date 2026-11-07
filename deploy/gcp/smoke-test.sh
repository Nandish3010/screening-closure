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
code() { local c; c=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$@" 2>/dev/null); echo "${c:-000}"; }  # curl already prints 000 on no connection
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
# ADR-060: one-click demo sign-in. The coordinator's tokens come back without any password, and the access token works against the API.
# S1: right after a VM restart Keycloak is still warming and the API answers 503 (signin_warming_up) or the edge 502/504. Those attempts are
# not counted against the limit, so wait for them (up to ~2 minutes, one request per 10 s) instead of failing; one counted sign-in per run.
for _try in 1 2 3 4 5 6 7 8 9 10 11 12; do
  signin=$(curl -s -m 30 -X POST -H 'Content-Type: application/json' -d '{"role":"coordinator"}' "$BASE/api/demo/sign-in" -w '\n%{http_code}' || true)
  signin_code=${signin##*$'\n'}; signin_body=${signin%$'\n'*}
  case "$signin_code" in 502|503|504|000) echo "WAIT  sign-in not ready ($signin_code), retrying in 10 s ($_try/12)"; sleep 10 ;; *) break ;; esac
done
[ "$signin_code" = 200 ] && ok "one-click demo sign-in as coordinator (200)" || bad "one-click demo sign-in: wanted 200, got $signin_code"
echo "$signin_body" | grep -qi 'password' && bad "the sign-in answer contains the substring 'password'" || ok "sign-in answer carries no password"
tok=$(echo "$signin_body" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || true)
[ -n "$tok" ] && ok "sign-in answer has an access_token" || bad "sign-in answer has no access_token"
check "that token reads /api/cases" 200 -H "Authorization: Bearer $tok" "$BASE/api/cases"
check "sign-in refuses a role outside the four" 422 -X POST -H 'Content-Type: application/json' -d '{"role":"lab"}' "$BASE/api/demo/sign-in"
unset tok signin signin_body
check "clock reset does not exist" 404 -X POST "$BASE/api/clock/reset"
check "clock advance needs a supervisor token" 401 -X POST -H 'Content-Type: application/json' -d '{"days":1}' "$BASE/api/clock/advance"
echo "$body" | grep -q '"simulated_clock": *true' && ok "health reports the demo clock (the UI banner source)" || bad "health does not report demo.simulated_clock"
echo "$body" | grep -q '"clock_mode": *"sim"' && ok "clock is the simulated one" || bad "clock_mode is not sim"
check "interactive API docs not public" 404 "$BASE/api/docs"
check "API schema not public" 404 "$BASE/api/openapi.json"
check "redoc not public" 404 "$BASE/api/redoc"
# the demo block must show that the seed's snapshot (or a later reset) was recorded: last_reset_at and next_auto_reset_at set
lr=$(echo "$body" | python3 -c 'import sys,json;print((json.load(sys.stdin).get("demo") or {}).get("last_reset_at") or "")' 2>/dev/null || true)
[ -n "$lr" ] && ok "health demo.last_reset_at is recorded ($lr)" || bad "health demo.last_reset_at is null (the seed did not record its snapshot)"
check "scheduler path blocked at the edge" 404 -X POST "$BASE/api/scheduler/sweep"
check "metrics endpoint blocked at the edge" 404 "$BASE/api/metrics"
check "Keycloak admin console not public" 404 "$BASE/auth/admin/master/console/"
check "master realm not public" 404 "$BASE/auth/realms/master/.well-known/openid-configuration"
check "database port closed" 000 -m 4 "http://$HOST:5432/"
# rate limit: a burst of 120 unauthenticated calls, 40 at a time (enough to exceed 10/s + burst 40 even over a slow link),
# must produce at least one 429
n429=$(seq 1 120 | xargs -P 40 -I{} curl -s -o /dev/null -m 15 -w '%{http_code}\n' "$BASE/api/pathways" | grep -c '^429$' || true)
[ "$n429" -gt 0 ] && ok "rate limit active ($n429 of 120 got 429)" || bad "no 429 in a burst of 120 requests (rate limit not active?)"
hsts=$(curl -sI -m 15 "$BASE/" | tr -d '\r' | grep -i '^strict-transport-security' || true)
[ -n "$hsts" ] && ok "HSTS header present" || bad "no Strict-Transport-Security header"
csp=$(curl -sI -m 15 "$BASE/" | tr -d '\r' | grep -i '^content-security-policy' || true)
{ echo "$csp" | grep -q "frame-ancestors 'none'" && echo "$csp" | grep -q "script-src 'self'" && ! echo "$csp" | grep -q "unsafe-eval"; } && ok "Content-Security-Policy present (script-src 'self', frame-ancestors 'none')" || bad "no (or a weak) Content-Security-Policy header on the web app"
echo; [ $fail = 0 ] && echo "SMOKE TEST PASSED for $BASE" || echo "SMOKE TEST FAILED for $BASE"
exit $fail
