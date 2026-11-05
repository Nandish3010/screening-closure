#!/bin/bash
# The host side of the web "Reset the demo" button and of the scheduled reset (ADR-058). On the VM, as root.
#
#   demo-reset-watch.sh             one pass: if the API left a reset request, act on it (restore the snapshot), record who and when
#   demo-reset-watch.sh --scheduled the scheduled reset (the timer calls this every DEMO_AUTO_RESET_HOURS, default 6)
#
# Why a script and not an API call: a reset stops the API and replaces both databases (tools/demo/snapshot.sh), which the API
# cannot do to itself and must not be able to do to the host (ADR-055). The API (supervisor click, rate limited) only writes
# $CTL/reset-request.json; this script reads it. $CTL is bind-mounted into the api container (deploy/gcp/compose.gcp.yml).
#
# Files in $CTL (default /var/lib/sc-demo):
#   reset-request.json  written by the API: {requested_at, requested_by}; removed by this script when it takes the job
#   status.json         written by this script: {last_reset_at, last_reset_by, last_reset_reason, last_request_at, duration_s}
#   baseline.json       written by the API (the event count of every case just after a reset); removed here so it is re-taken
#
# Run it from a systemd timer every 15 s for the request check and every 6 h with --scheduled (units: see the PR notes of ADR-058;
# deploy/gcp/vm/sc-demo-reset-watch.{service,timer} are examples). Safe to run twice at once: a lock directory serialises passes.
set -euo pipefail
export SC_REPO=${SC_REPO:-/srv/sc/repo}
CTL=${DEMO_CONTROL_DIR:-/var/lib/sc-demo}
MIN_SCHEDULED_GAP=${DEMO_MIN_SCHEDULED_GAP_SECONDS:-1800}   # a scheduled reset right after a manual one is skipped
mode=request; [ "${1:-}" = "--scheduled" ] && mode=scheduled

mkdir -p "$CTL"
# The api container runs as uid 10001 and must write the request; this script (root) writes status.json that it only reads.
chown 10001 "$CTL" 2>/dev/null || true
chmod 0755 "$CTL"
LOCK="$CTL/.lock"
mkdir "$LOCK" 2>/dev/null || { echo "demo-reset-watch: another pass is running"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

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
  [ -f "$REQ" ] && { echo "demo-reset-watch: a manual request is pending; it will reset the demo"; exit 0; }
  last=$(json_get "$CTL/status.json" last_reset_at)
  if [ -n "$last" ] && [ "$(age_s "$last")" -lt "$MIN_SCHEDULED_GAP" ]; then
    echo "demo-reset-watch: reset $(age_s "$last") s ago; scheduled reset skipped"; exit 0
  fi
  by="the schedule"; reason=scheduled
fi

rm -f "$REQ" "$CTL/baseline.json"
started=$(date +%s)
echo "demo-reset-watch: resetting ($reason, by $by)"
"$SC_REPO/deploy/gcp/vm/demo-reset-on-vm.sh"
finished=$(date +%s)

# Merge into status.json (keeps last_request_at, which the API's rate limit reads).
python3 -I - "$CTL/status.json" "$(now_iso)" "$by" "$reason" "$((finished - started))" <<'PY'
import json, os, sys, tempfile
path, at, by, reason, secs = sys.argv[1:6]
try:
    st = json.load(open(path))
except Exception:
    st = {}
st.update(last_reset_at=at, last_reset_by=by, last_reset_reason=reason, duration_s=int(secs))
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".status.")
with os.fdopen(fd, "w") as fh:
    json.dump(st, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
echo "demo-reset-watch: done in $((finished - started)) s"
