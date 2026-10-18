#!/bin/sh
# Snapshot and restore the whole demo state: the case/event/draft/notification/user/clock tables (database `closure`)
# AND HAPI's FHIR data (database `hapi`, since SEC-1: HAPI no longer shares the application's database). Both databases
# are saved and restored together, so cases and FHIR resources always match.
#
#   tools/demo/snapshot.sh save      stop the services, copy `closure` and `hapi` to `closure_snapshot` and `hapi_snapshot`, start again
#   tools/demo/snapshot.sh restore   stop the services, replace both with copies of their snapshots, start again
#   tools/demo/snapshot.sh reset     restore, reset the sim clock, and check the fingerprint equals the snapshot's
#   tools/demo/snapshot.sh hash      print the state fingerprint (counts and md5s) of the running database
#   tools/demo/snapshot.sh status    is there a snapshot, and when and with what fingerprint was it taken
#
# How: a Postgres template copy (CREATE DATABASE ... TEMPLATE ... STRATEGY FILE_COPY), a file-level copy
# inside the server, so a 3 GB database copies in seconds where pg_dump / pg_restore takes many minutes.
# The role `closure_app` is cluster-wide and its grants live inside each database, so a copy carries them along.
# The copy lives in the compose `pgdata` volume: it survives `make down` and `make up`, not `make reset`
# (which deletes the volume); take a new snapshot after `make reset && make demo-seed`.
#
# The services that hold connections (api, scheduler, watchdog, fhir) are stopped around the copy because
# Postgres refuses to use a database as a template while anything is connected to it, and because the API
# keeps the simulation clock in memory: starting it again rereads `sim_clock` from the restored database.
# The event log is append-only (triggers forbid UPDATE and DELETE), so "undo the demo" can only mean
# replacing the database, never deleting rows.
#
# The fingerprint covers what a demo can change and nothing that changes by itself: cases, events (every column
# that is data), drafts, notifications, the clock, and the number of live FHIR resources per type. Heartbeats,
# alerts and the `users` directory (every dev login writes it) are left out on purpose.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

DB=${POSTGRES_DB:-closure}
HAPI_DB=hapi   # fixed in docker-compose.yml (SPRING_DATASOURCE_URL) and docker/postgres-init
USER_=${POSTGRES_USER:-closure}
SNAP="${DB}_snapshot"
HAPI_SNAP="${HAPI_DB}_snapshot"
SERVICES="api scheduler watchdog fhir"
API_URL=${API_URL:-http://localhost:8000}
FHIR_URL=${FHIR_BASE_URL_HOST:-http://localhost:8080/fhir}

psql_admin() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$USER_" -d postgres -At "$@"; }
psql_db() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$USER_" -d "$1" -At "${@:2}"; }

now() { python3 -c 'import time; print(time.time())'; }
elapsed() { python3 -c "import time; print(f'{time.time()-$1:.1f}')"; }

fingerprint() {
  # $1 = the application database, $2 = the HAPI database. Prints sorted key=value lines (the format has not changed
  # since both lived in one database), then a final hash=<md5 of the lines above>.
  { psql_db "$1" <<'SQL'; psql_db "$2" <<'SQL2'; } | sort > /tmp/.demo-fp.$$
SELECT 'cases=' || count(*) FROM cases;
SELECT 'cases_md5=' || coalesce(md5(string_agg(id::text || current_state || created_at::text, ',' ORDER BY id)), '-') FROM cases;
SELECT 'events=' || count(*) FROM events;
SELECT 'events_md5=' || coalesce(md5(string_agg(event_id::text || case_id::text || seq::text || type || at::text || actor || payload::text, ',' ORDER BY id)), '-') FROM events;
SELECT 'drafts=' || count(*) FROM drafts;
SELECT 'drafts_md5=' || coalesce(md5(string_agg(draft_id::text || text || coalesce(approved_by, '-') || coalesce(rejected_by, '-'), ',' ORDER BY seq)), '-') FROM drafts;
SELECT 'notifications=' || count(*) FROM notifications;
SELECT 'sim_clock=' || now_at::text FROM sim_clock WHERE clock_id = 'main';
SQL
SELECT 'fhir_' || res_type || '=' || count(*) FROM hfj_resource WHERE res_deleted_at IS NULL GROUP BY res_type;
SQL2
  cat /tmp/.demo-fp.$$
  printf 'hash=%s\n' "$(md5 -q /tmp/.demo-fp.$$ 2>/dev/null || md5sum < /tmp/.demo-fp.$$ | cut -d' ' -f1)"
  rm -f /tmp/.demo-fp.$$
}

wait_http() {  # url, label, seconds
  i=0
  until curl -fsS -o /dev/null "$1" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -gt "$3" ] && { echo "snapshot: $2 did not come back within $3 s" >&2; exit 1; }
    sleep 1
  done
}

# HAPI is killed, not stopped: its database is about to be replaced, so a graceful shutdown buys nothing and
# costs seconds. The others stop with a one second grace.
stop_services() {
  docker compose kill fhir >/dev/null 2>&1 &
  docker compose stop -t 1 api scheduler watchdog >/dev/null 2>&1
  wait
}
# Starts everything and waits for the API, then for HAPI (the JVM boots in 20 to 35 s, the slowest part of a reset).
# The elapsed seconds at which each answered (since $1) are left in API_READY_S and HAPI_READY_S.
start_services() {
  docker compose start $SERVICES >/dev/null 2>&1
  wait_http "$API_URL/health" "the API" 120
  API_READY_S=$(elapsed "$1")
  wait_http "$FHIR_URL/metadata" "HAPI" 180
  HAPI_READY_S=$(elapsed "$1")
}

# POST /clock/reset as the demo supervisor: puts the sim clock back at its configured start (a no-op after a
# restore from a snapshot taken at the start, which is the usual case, but it makes "reset" mean that).
clock_reset() {
  python3 - "$API_URL" <<'PY'
import json, sys, urllib.error, urllib.request
api = sys.argv[1]
def call(method, path, body=None, token=None):
    req = urllib.request.Request(api + path, data=json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None), method=method,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)
token = call("POST", "/auth/dev-login", {"role": "supervisor", "name": "Dr. Noura Al Hammadi"})["token"]
try:
    print("snapshot: sim clock reset to", call("POST", "/clock/reset", None, token)["sim_time"])
except urllib.error.HTTPError as exc:
    if exc.code != 409:
        raise
    # SEC-1: the API refuses to rewind past events dated after the clock's start. The restore already put the clock
    # where the snapshot had it, which is all "reset" needs when the snapshot was taken after time had moved.
    print("snapshot: sim clock left as restored (events exist after its start, so /clock/reset answered 409)")
PY
}

has_db() { [ "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname='$1'")" = "1" ]; }
has_snapshot() { has_db "$SNAP" && has_db "$HAPI_SNAP"; }

cmd=${1:-}
case "$cmd" in
  save)
    t0=$(now)
    stop_services
    for pair in "$SNAP:$DB" "$HAPI_SNAP:$HAPI_DB"; do
      psql_admin -c "DROP DATABASE IF EXISTS ${pair%%:*} WITH (FORCE)" >/dev/null
      psql_admin -c "CREATE DATABASE ${pair%%:*} TEMPLATE ${pair##*:} STRATEGY FILE_COPY OWNER $USER_" >/dev/null
    done
    mkdir -p "$ROOT/data"
    fingerprint "$SNAP" "$HAPI_SNAP" > "$ROOT/data/demo-snapshot.fingerprint"
    start_services "$t0"
    echo "snapshot: saved $SNAP and $HAPI_SNAP in $(elapsed "$t0") s (API answering after $API_READY_S s, HAPI after $HAPI_READY_S s)"; grep -E '^(cases|events|sim_clock|hash)=' "$ROOT/data/demo-snapshot.fingerprint"
    ;;
  restore|reset)
    has_snapshot || { echo "snapshot: no snapshot databases $SNAP and $HAPI_SNAP; run 'make demo-snapshot' on a good state first" >&2; exit 1; }
    t0=$(now)
    stop_services
    ts=$(now)
    for pair in "$DB:$SNAP" "$HAPI_DB:$HAPI_SNAP"; do
      psql_admin -c "DROP DATABASE IF EXISTS ${pair%%:*} WITH (FORCE)" >/dev/null
      psql_admin -c "CREATE DATABASE ${pair%%:*} TEMPLATE ${pair##*:} STRATEGY FILE_COPY OWNER $USER_" >/dev/null
    done
    # a new database starts with the default CONNECT-for-everyone; HAPI's is for its owner only (SEC-1)
    psql_admin -c "REVOKE CONNECT ON DATABASE $HAPI_DB FROM PUBLIC" >/dev/null
    t1=$(now)
    start_services "$t0"
    echo "snapshot: restored $DB and $HAPI_DB from $SNAP and $HAPI_SNAP (stop $(python3 -c "print(f'{$ts-$t0:.1f}')") s, database copy $(python3 -c "print(f'{$t1-$ts:.1f}')") s); API answering after $API_READY_S s, HAPI after $HAPI_READY_S s"
    if [ "$cmd" = reset ]; then
      clock_reset
      fingerprint "$DB" "$HAPI_DB" > /tmp/.demo-fp-now.$$
      if cmp -s /tmp/.demo-fp-now.$$ "$ROOT/data/demo-snapshot.fingerprint"; then
        echo "snapshot: state hash $(tail -1 /tmp/.demo-fp-now.$$) equals the snapshot's; reset complete in $(elapsed "$t0") s (the demo is usable once the API answers, HAPI is ready $HAPI_READY_S s in)"
        rm -f /tmp/.demo-fp-now.$$
      else
        echo "snapshot: STATE HASH DIFFERS from the snapshot's:" >&2
        diff "$ROOT/data/demo-snapshot.fingerprint" /tmp/.demo-fp-now.$$ >&2 || true
        rm -f /tmp/.demo-fp-now.$$
        exit 1
      fi
    fi
    ;;
  hash)
    fingerprint "$DB" "$HAPI_DB"
    ;;
  refresh)  # rewrite the stored fingerprint from the snapshot database (after the fingerprint's definition changed)
    has_snapshot || { echo "snapshot: none" >&2; exit 1; }
    fingerprint "$SNAP" "$HAPI_SNAP" > "$ROOT/data/demo-snapshot.fingerprint"
    echo "snapshot: fingerprint refreshed"; grep -E '^(cases|events|sim_clock|hash)=' "$ROOT/data/demo-snapshot.fingerprint"
    ;;
  status)
    if has_snapshot; then
      echo "snapshot: $SNAP and $HAPI_SNAP exist"; cat "$ROOT/data/demo-snapshot.fingerprint" 2>/dev/null | grep -E '^(cases|events|sim_clock|hash)=' || true
    else
      echo "snapshot: none"
    fi
    ;;
  *)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
