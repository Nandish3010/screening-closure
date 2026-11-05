#!/bin/bash
# BUG-77 / P1-16 workaround, gated: restart ONLY the API container, and only when its memory says it is needed.
# The API's anonymous memory was seen to grow (docs/submission/assumptions-and-limitations.md 6.1); the cause is not found, so this
# bounds the growth instead of fixing it. State is in Postgres, so a restart loses nothing; requests in flight at that moment fail
# and the browser retries (a few seconds). A blind clock-time restart could land in the middle of a judging session; this one
# restarts only when memory is high, and never more often than SC_RESTART_MIN_GAP_S.
#
#   sc-api-restart.sh gate    every 15 min (sc-api-restart.timer): restart if usage >= SC_RESTART_GATE_PCT (default 70) of the limit
#   sc-api-restart.sh daily   once a day, at a quiet hour (sc-api-restart-daily.timer): restart if usage >= SC_RESTART_DAILY_PCT (default 40)
#   sc-api-restart.sh check   print the reading and the decision, restart nothing
#
# The limit is the container's own memory limit (compose.gcp.yml sets mem_limit on api); without one, the host's MemTotal.
# Usage is the container's anonymous memory (cgroup v2 memory.stat "anon"), else `docker stats`. Every restart is logged with its
# reason to the journal and to $SC_RESTART_LOG. Run by systemd as root. Prints no secret.
set -euo pipefail
MODE=${1:-gate}
case "$MODE" in gate | daily | check) ;; *) echo "usage: $0 gate|daily|check" >&2; exit 2 ;; esac
export SC_REPO=${SC_REPO:-/srv/sc/repo}
GATE_PCT=${SC_RESTART_GATE_PCT:-70}
DAILY_PCT=${SC_RESTART_DAILY_PCT:-40}
MIN_GAP=${SC_RESTART_MIN_GAP_S:-3600}
LOG=${SC_RESTART_LOG:-/var/lib/sc/api-restart.log}
STATE=${SC_RESTART_STATE:-/var/lib/sc/api-restart.last}
CGROOT=${SC_CGROUP_ROOT:-/sys/fs/cgroup}
MEMINFO=${SC_MEMINFO:-/proc/meminfo}

log() { local m; m="sc-api-restart: $(date -u +%FT%TZ) $*"; echo "$m"; mkdir -p "$(dirname "$LOG")" 2>/dev/null && echo "$m" >> "$LOG" || true; }

cd "$SC_REPO"
# shellcheck disable=SC1091
[ -f "${SC_ENV_SH:-$SC_REPO/deploy/gcp/vm/env.sh}" ] && . "${SC_ENV_SH:-$SC_REPO/deploy/gcp/vm/env.sh}"

cid=$(docker compose ps -q api 2>/dev/null | head -n1 || true)
if [ -z "$cid" ]; then log "skip: api container is not running (nothing to restart; sc-up.sh / docker restart policy own that)"; exit 0; fi

# --- limit, bytes
limit=$(docker inspect -f '{{.HostConfig.Memory}}' "$cid" 2>/dev/null || echo 0)
limit_src="container limit"
if ! [ "${limit:-0}" -gt 0 ] 2>/dev/null; then
  limit=$(awk '/^MemTotal:/ {print $2 * 1024}' "$MEMINFO")
  limit_src="host MemTotal (no container limit set)"
fi

# --- usage, bytes
to_bytes() { # "123.4MiB" -> bytes
  awk -v s="$1" 'BEGIN { match(s, /[A-Za-z]+$/); u = substr(s, RSTART); v = substr(s, 1, RSTART - 1) + 0;
    m = 1; if (u == "kB" || u == "KB") m = 1000; else if (u == "KiB") m = 1024; else if (u == "MB") m = 1e6; else if (u == "MiB") m = 1048576;
    else if (u == "GB") m = 1e9; else if (u == "GiB") m = 1073741824; printf "%.0f", v * m }'
}
usage="" ; usage_src=""
for d in "$CGROOT/system.slice/docker-$cid.scope" "$CGROOT/docker/$cid"; do
  if [ -r "$d/memory.stat" ]; then
    usage=$(awk '$1 == "anon" {print $2}' "$d/memory.stat"); usage_src="cgroup anon"; break
  fi
done
if [ -z "$usage" ]; then
  raw=$(docker stats --no-stream --format '{{.MemUsage}}' "$cid" 2>/dev/null | awk -F' / ' '{print $1}' | head -n1)
  [ -n "$raw" ] || { log "skip: could not read the api container's memory"; exit 0; }
  usage=$(to_bytes "$raw"); usage_src="docker stats"
fi
pct=$((usage * 100 / limit))
reading="api memory ${usage} B = ${pct}% of ${limit} B (${limit_src}; usage from ${usage_src})"

if [ "$MODE" = check ]; then echo "sc-api-restart: $reading; gate ${GATE_PCT}%, daily ${DAILY_PCT}%"; exit 0; fi

reason=""
if [ "$MODE" = gate ] && [ "$pct" -ge "$GATE_PCT" ]; then reason="memory ${pct}% >= gate ${GATE_PCT}%"; fi
if [ "$MODE" = daily ] && [ "$pct" -ge "$DAILY_PCT" ]; then reason="daily fallback, memory ${pct}% >= ${DAILY_PCT}%"; fi
if [ -z "$reason" ]; then echo "sc-api-restart: $MODE: no restart; $reading"; exit 0; fi

# Loop guard: a restart that does not bring memory down must not become a restart every 15 minutes.
now=$(date +%s); last=$(cat "$STATE" 2>/dev/null || echo 0)
if [ $((now - last)) -lt "$MIN_GAP" ]; then
  log "$MODE: WANTED restart ($reason; $reading) but the last restart was $((now - last)) s ago (< ${MIN_GAP} s): not restarting"
  exit 0
fi
docker compose restart api
mkdir -p "$(dirname "$STATE")" 2>/dev/null && echo "$now" > "$STATE" || true
log "$MODE: api RESTARTED, reason: $reason; $reading"
