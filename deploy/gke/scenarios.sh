#!/usr/bin/env bash
# Runs INSIDE the load-generator pod (image deploy/gke/loadgen.Dockerfile; started by deploy/gke/loadtest.sh via the sc-loadtest chart).
# The scenarios, in this order, each with the repository's existing tool (the same tools and the same traffic mix as the kind runs in
# docs/results/scale.md, so the numbers are comparable):
#   seed     tools/load/scale_sweep.py seed-closure       the 1M-case population in the `closure` database (parallel COPY seeder)
#   sweep    tools/load/scale_sweep.py sweep              the 1M-case daily burst: unbounded sweep (1 and 4 workers), bounded drain loops,
#                                                         incremental watchdog scans; with the scheduler and the watchdog scaled to 0
#   ladder   tools/k8s/api_load.py                        open-loop arrival-rate stages up to and past the shedding limit (503 + Retry-After)
#   soak     tools/k8s/soak.py                            steady load with the scheduler and watchdog on; per-pod memory every 30 s
# Results go to /results (JSON); the script writes /results/DONE at the end and then waits for /results/COLLECTED (the orchestrator copies
# the folder out) for up to 30 minutes. A failing scenario is recorded and the next one still runs, except that nothing runs without a seed.
set -uo pipefail
R="${RESULTS:-/results}"
mkdir -p "$R"
cd /repo
export PYTHONPATH=/repo/api

SEED_SIZE="${SEED_SIZE:-1000000}"
SWEEP_SIZES="${SWEEP_SIZES:-1000000}"
SWEEP_WORKERS="${SWEEP_WORKERS:-1,4}"
LADDER_RATES="${LADDER_RATES:-50,100,175,250,300,400,500}"
LADDER_SECONDS="${LADDER_SECONDS:-180}"
SOAK_RATE="${SOAK_RATE:-50}"
SOAK_SECONDS="${SOAK_SECONDS:-4500}"
SC_SEED_PROCS="${SC_SEED_PROCS:-4}"
export SC_SEED_PROCS
REL="${SC_RELEASE:-closure}"
SKIP="${SKIP_PHASES:-}"      # space-separated phase names to skip (a rehearsal skips nothing; a partial rerun may)

log() { echo "[$(date -u +%FT%TZ)] $*"; }
phase() { printf '{"phase":"%s","at":"%s","epoch":%s}\n' "$1" "$(date -u +%FT%TZ)" "$(date +%s)" >> "$R/phases.jsonl"; log "== phase: $1"; }
skipped() { case " $SKIP " in *" $1 "*) log "skipping phase $1"; return 0;; esac; return 1; }
fail=0
run() {  # run NAME CMD... : record the exit code in phases.jsonl
  local name="$1"; shift
  "$@"; local rc=$?
  printf '{"phase":"%s-exit","rc":%s,"at":"%s"}\n' "$name" "$rc" "$(date -u +%FT%TZ)" >> "$R/phases.jsonl"
  [ "$rc" -eq 0 ] || { log "phase $name exited $rc"; fail=1; }
  return "$rc"
}
finish() {
  phase done
  printf '{"failed_phases":%s}\n' "$fail" > "$R/result-status.json"
  touch "$R/DONE"
  log "done; waiting up to 30 min for the orchestrator to copy $R (it creates $R/COLLECTED)"
  for _ in $(seq 1 180); do [ -e "$R/COLLECTED" ] && break; sleep 10; done
  exit "$fail"
}

phase start
log "waiting for the API"
for i in $(seq 1 60); do
  python - <<'PY' && break
import os, sys, httpx
try:
    sys.exit(0 if httpx.get(os.environ["SC_API_BASE"] + "/health", timeout=5).status_code == 200 else 1)
except Exception:
    sys.exit(1)
PY
  sleep 5
done

# The scheduler and the watchdog are off until the sweep scenario is over: they would sweep a population that is still being seeded.
kubectl scale deployment "$REL-scheduler" "$REL-watchdog" --replicas=0 >/dev/null 2>&1 || true

if ! skipped seed; then
  phase seed
  run seed python tools/load/scale_sweep.py seed-closure --size "$SEED_SIZE" --out "$R/seed.json" || { log "no seed, nothing to measure"; fail=1; finish; }
fi

if ! skipped sweep; then
  phase sweep
  run sweep python tools/load/scale_sweep.py sweep --sizes "$SWEEP_SIZES" --workers "$SWEEP_WORKERS" --label gke \
      --drain --watchdog --no-full-scan --no-wd-reference --out "$R/sweep.json"
fi

phase workers-up
SCHED_REPLICAS="${SCHEDULER_REPLICAS:-2}"
kubectl scale deployment "$REL-scheduler" --replicas="$SCHED_REPLICAS" >/dev/null 2>&1 || log "could not scale the scheduler"
kubectl scale deployment "$REL-watchdog" --replicas=1 >/dev/null 2>&1 || log "could not scale the watchdog"
kubectl rollout status "deployment/$REL-scheduler" --timeout=300s || true
kubectl rollout status "deployment/$REL-watchdog" --timeout=300s || true
sleep 90   # let the scheduler elect a leader and the first sweeps and the watchdog's catch-up scans run before the measured stages

if ! skipped ladder; then
  phase ladder
  run ladder python tools/k8s/api_load.py --rates "$LADDER_RATES" --duration "$LADDER_SECONDS" --label gke --probe-worklist --out "$R/latency.json"
fi

if ! skipped soak; then
  phase soak
  run soak python tools/k8s/soak.py --rate "$SOAK_RATE" --duration "$SOAK_SECONDS" --sample-period 30 --label gke --out "$R/soak.json"
fi

finish
