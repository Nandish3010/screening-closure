#!/bin/bash
# The host side of the web "Reset the demo" button and of the scheduled reset (ADR-058). On the VM, as root.
#
#   demo-reset-watch.sh             one pass: if the API left a reset request, act on it (restore the snapshot), record who and when
#   demo-reset-watch.sh --scheduled the scheduled reset (the timer calls this once a day at 23:00 UTC = 03:00 Asia/Dubai)
#   demo-reset-watch.sh --record-seed  reset nothing; record "the seed's snapshot" as the first reset point (seed-on-vm.sh, after
#                                   `snapshot.sh save`), so /health shows last_reset_at and next_auto_reset_at from the start
#
# Why a script and not an API call: a reset stops the API and replaces both databases (tools/demo/snapshot.sh), which the API
# cannot do to itself and must not be able to do to the host (ADR-055). The API (supervisor click, rate limited) only writes
# $CTL/reset-request.json; this script reads it. $CTL is bind-mounted into the api container (deploy/gcp/compose.gcp.yml).
#
# Files in $CTL (default /var/lib/sc-demo):
#   reset-request.json  written by the API: {requested_at, requested_by}; removed by this script when it takes the job
#   status.json         written by this script: {last_reset_at, last_reset_by, last_reset_reason, last_request_at, duration_s,
#                       last_scheduled_at (the timer's last pass, even a skipped one; the API shows the next 23:00 UTC after it)}
#   api-restart.json    written by sc-api-restart.sh: {at, reason, mode}; the API shows it in the demo block of /health
#   clock-budget.json   written by the API (days the shared clock has been advanced in the rolling window); removed here too
#                       On a FAILED reset it also holds last_reset_error (what failed, when) and last_reset_error_at; the API shows them
#                       in /health demo.last_reset_error. The next successful reset clears them.
#   baseline.json       written by the API (the event count of every case just after a reset); removed here so it is re-taken
#
# Run it from a systemd timer every 15 s for the request check and once a day with --scheduled (units: see the PR notes of ADR-058;
# deploy/gcp/vm/sc-demo-reset-watch.{service,timer} are examples). Safe to run twice at once: a lock directory serialises passes.
set -euo pipefail
export SC_REPO=${SC_REPO:-/srv/sc/repo}
CTL=${DEMO_CONTROL_DIR:-/var/lib/sc-demo}
MIN_SCHEDULED_GAP=${DEMO_MIN_SCHEDULED_GAP_SECONDS:-1800}   # a scheduled reset right after a manual one is skipped
mode=request
case "${1:-}" in --scheduled) mode=scheduled ;; --record-seed) mode=record-seed ;; esac

mkdir -p "$CTL"
# The api container runs as uid 10001 and must write the request; this script (root) writes status.json that it only reads.
chown 10001 "$CTL" 2>/dev/null || true
chmod 0755 "$CTL"
LOCK="$CTL/.lock"
mkdir "$LOCK" 2>/dev/null || { echo "demo-reset-watch: another pass is running"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# Merge key=value pairs into status.json (atomic; keeps last_request_at, which the API's rate limit reads). Values are data.
update_status() { python3 -I - "$CTL/status.json" "$@" <<'PY'
import json, os, sys, tempfile
path, pairs = sys.argv[1], sys.argv[2:]
try:
    st = json.load(open(path))
except Exception:
    st = {}
for kv in pairs:
    k, _, v = kv.partition("=")
    st[k] = int(v) if k == "duration_s" else v
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".status.")
with os.fdopen(fd, "w") as fh:
    json.dump(st, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}

json_get() { python3 -I - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")
except Exception:
    pass
PY
}

now_iso() { python3 -I -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())'; }
age_s()   { python3 -I - "$1" <<'PY'
import sys
from datetime import datetime, timezone
try:
    print(int((datetime.now(timezone.utc) - datetime.fromisoformat(sys.argv[1])).total_seconds()))
except Exception:
    print(10**9)
PY
}

if [ "$mode" = record-seed ]; then
  rm -f "$CTL/baseline.json" "$CTL/clock-budget.json"
  update_status "last_reset_at=$(now_iso)" "last_reset_by=the seed" "last_reset_reason=seed"
  echo "demo-reset-watch: recorded the seed snapshot as the first reset point"; exit 0
fi

REQ="$CTL/reset-request.json"
API_UID=${DEMO_API_UID:-10001}   # the api container's user: the only one whose request file is honoured
owner_uid() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1"; }
by=""; reason=""
if [ "$mode" = request ]; then
  [ -e "$REQ" ] || [ -L "$REQ" ] || exit 0
  # The request is DATA, never code: it must be a plain file written by the API user (not a link, not someone else's file), and
  # only the requester's name is read from it, through python's json parser into a variable that is quoted wherever it is used and
  # never interpolated into a command. What runs after it is the one fixed script below, whatever the file says.
  if [ -L "$REQ" ] || [ ! -f "$REQ" ] || { [ "$(owner_uid "$REQ")" != "$API_UID" ] && [ "$(owner_uid "$REQ")" != 0 ]; }; then
    echo "demo-reset-watch: ignoring a reset request that is not a plain file owned by the API user" >&2
    rm -f -- "$REQ"; exit 0
  fi
  by=$(json_get "$REQ" requested_by | head -c 120 | tr -cd '[:print:]'); reason=manual
  [ -n "$by" ] || by="a supervisor"
else
  update_status "last_scheduled_at=$(now_iso)"   # the timer fired now, whatever this pass decides: the next slot is one interval on
  [ -f "$REQ" ] && { echo "demo-reset-watch: a manual request is pending; it will reset the demo"; exit 0; }
  last=$(json_get "$CTL/status.json" last_reset_at)
  if [ -n "$last" ] && [ "$(age_s "$last")" -lt "$MIN_SCHEDULED_GAP" ]; then
    echo "demo-reset-watch: reset $(age_s "$last") s ago; scheduled reset skipped"; exit 0
  fi
  by="the schedule"; reason=scheduled
fi

rm -f "$REQ" "$CTL/baseline.json" "$CTL/clock-budget.json"
started=$(date +%s)

# A reset that fails half way leaves the API, scheduler, watchdog and HAPI stopped (the reset stops them first). Without this the site
# stayed down until the 15-minute API timer or an operator noticed, and /health still showed the PREVIOUS reset as the last one.
# Armed only around the reset itself, so an error in the bookkeeping above cannot trigger a recovery.
on_reset_failure() {
  local rc=$? line=$1
  trap - ERR
  echo "demo-reset-watch: RESET FAILED (exit $rc, line $line); bringing the stack back up" >&2
  update_status "last_reset_error=${reason} reset by ${by} failed (exit ${rc}); the stack was restarted, the data may be as before the reset" \
                "last_reset_error_at=$(now_iso)" || true
  "$SC_REPO/deploy/gcp/vm/demo-recover.sh" || echo "demo-reset-watch: recovery also failed; run deploy/gcp/vm/sc-up.sh on the VM" >&2
  exit "$rc"
}
trap 'on_reset_failure $LINENO' ERR
echo "demo-reset-watch: resetting ($reason, by $by)"
"$SC_REPO/deploy/gcp/vm/demo-reset-on-vm.sh"
trap - ERR
finished=$(date +%s)

update_status "last_reset_at=$(now_iso)" "last_reset_by=$by" "last_reset_reason=$reason" "duration_s=$((finished - started))" "last_reset_error=" "last_reset_error_at="
echo "demo-reset-watch: done in $((finished - started)) s"
