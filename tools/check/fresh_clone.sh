#!/usr/bin/env bash
# Fresh-clone verification (F12, T-F13).
#
# Clones this repository into a temporary directory and runs, there, what a person on a second machine would run:
#
#   venv + python dependencies        (a fresh clone has no .venv; `make` falls back to python3, which has no packages)
#   cp .env.example .env
#   make up
#   make demo-seed
#   make evaluate
#   make check
#
# against its OWN compose project (`closure_fresh`) and shifted host ports (Postgres 35433, API 38000, HAPI 38080), so it
# does not collide with a stack that is already running. Each step is timed; the first failure stops the run (use
# --keep-going to continue). At the end it prints a Markdown report: the timings, the T-F13 verdict (every command
# succeeds and `make evaluate` takes under two minutes) and the checklist for the second-machine run (H5), then
# removes the stack, its volumes and the temp dir (--keep leaves them).
#
#   sh tools/check/fresh_clone.sh [--source PATH_OR_URL] [--ref COMMIT] [--keep] [--keep-going] [--skip-check]
#                                 [--report FILE]
#
# --source defaults to this repository's own git directory (committed state only: uncommitted changes are NOT
# tested, which is the point); --ref defaults to HEAD. On the second machine, run the same commands by hand from
# the checklist below and record the numbers in docs/results/fresh-clone.md.
set -u

SOURCE=""
REF=""
KEEP=0
KEEP_GOING=0
SKIP_CHECK=0
REPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --keep-going) KEEP_GOING=1; shift ;;
    --skip-check) SKIP_CHECK=1; shift ;;
    --report) REPORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
[ -n "$SOURCE" ] || SOURCE="$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir)"
[ -n "$REF" ] || REF="$(git -C "$HERE" rev-parse HEAD)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/closure-fresh.XXXXXX")"
CLONE="$WORK/screening-closure"
[ -n "$REPORT" ] || REPORT="$WORK/fresh-clone-report.md"

# Isolation: its own compose project and ports, and nothing inherited from the caller's environment.
unset PYTHONPATH DATABASE_URL TEST_DATABASE_URL LOAD_PG_ADMIN_URL
export COMPOSE_PROJECT_NAME="${FRESH_PROJECT:-closure_fresh}"
export POSTGRES_PORT="${FRESH_POSTGRES_PORT:-35433}" API_PORT="${FRESH_API_PORT:-38000}" FHIR_PORT="${FRESH_FHIR_PORT:-38080}"
export API_URL="http://localhost:$API_PORT" FHIR_BASE_URL="http://localhost:$FHIR_PORT/fhir"
export TEST_API_URL="$API_URL" TEST_FHIR_URL="$FHIR_BASE_URL"
export TEST_DATABASE_URL="postgresql://closure:closure_dev_password@localhost:$POSTGRES_PORT/closure_test"
export LOAD_PG_ADMIN_URL="postgresql://closure:closure_dev_password@localhost:$POSTGRES_PORT/postgres"
export CHECK_PG_PORT="$POSTGRES_PORT"

NAMES=()
CODES=()
SECS=()
NOTES=()
FAILED=0

now() { date +%s; }

run_step() { # name, directory, command...
  local name="$1" dir="$2"; shift 2
  if [ "$FAILED" -ne 0 ] && [ "$KEEP_GOING" -eq 0 ]; then
    NAMES+=("$name"); CODES+=("-"); SECS+=("0"); NOTES+=("not run: an earlier step failed"); return
  fi
  echo; echo "===== [fresh-clone] $name: $*" ; echo
  local t0 t1 rc
  t0=$(now)
  ( cd "$dir" && "$@" ) 2>&1 | tee -a "$WORK/steps.log"
  rc=${PIPESTATUS[0]}
  t1=$(now)
  NAMES+=("$name"); CODES+=("$rc"); SECS+=("$((t1 - t0))"); NOTES+=("")
  [ "$rc" -eq 0 ] || FAILED=1
}

cleanup() {
  if [ "$KEEP" -eq 0 ]; then
    [ -d "$CLONE" ] && (cd "$CLONE" && docker compose down -v --remove-orphans >/dev/null 2>&1)
    rm -rf "$WORK"
  else
    echo "[fresh-clone] kept: $CLONE (compose project $COMPOSE_PROJECT_NAME; remove with: cd $CLONE && docker compose down -v)"
  fi
}

echo "[fresh-clone] source=$SOURCE ref=$REF"
echo "[fresh-clone] work dir $WORK; compose project $COMPOSE_PROJECT_NAME; ports postgres=$POSTGRES_PORT api=$API_PORT fhir=$FHIR_PORT"
if (docker ps --format '{{.Ports}}' | grep -E ":($POSTGRES_PORT|$API_PORT|$FHIR_PORT)->" >/dev/null 2>&1); then
  echo "[fresh-clone] a container already publishes one of the ports $POSTGRES_PORT/$API_PORT/$FHIR_PORT: set FRESH_POSTGRES_PORT, FRESH_API_PORT and FRESH_FHIR_PORT" >&2
  rm -rf "$WORK"; exit 2
fi

T_START=$(now)
run_step "git clone + checkout" "$WORK" sh -c "git clone --quiet --no-checkout '$SOURCE' screening-closure && git -C screening-closure checkout --quiet '$REF'"
# A fresh clone has no .venv. Make's PY falls back to python3, so give the clone a venv with what the Makefile needs.
run_step "python venv + dependencies" "$CLONE" sh -c "python3 -m venv .venv && .venv/bin/pip install --quiet -e 'api[dev]'"
run_step "cp .env.example .env" "$CLONE" cp .env.example .env
run_step "make up" "$CLONE" make up
run_step "wait until the API and HAPI answer" "$CLONE" sh -c \
  "for i in \$(seq 1 90); do curl -fsS $API_URL/health >/dev/null 2>&1 && curl -fsS $FHIR_BASE_URL/metadata >/dev/null 2>&1 && exit 0; sleep 3; done; echo 'stack did not become healthy in 270 s'; exit 1"
run_step "make demo-seed" "$CLONE" make demo-seed
run_step "make evaluate" "$CLONE" make evaluate
if [ "$SKIP_CHECK" -eq 0 ]; then
  run_step "make check" "$CLONE" make check
fi
T_TOTAL=$(( $(now) - T_START ))

# ---- report
{
  echo "# Fresh-clone run"
  echo
  echo "- Commit: \`$REF\` cloned from \`$SOURCE\`"
  echo "- Host: $(uname -srm); docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'); $(python3 --version 2>&1); node $(node --version 2>/dev/null || echo '-'); $(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null) CPUs"
  echo "- Compose project \`$COMPOSE_PROJECT_NAME\`, host ports postgres $POSTGRES_PORT, api $API_PORT, hapi $FHIR_PORT"
  echo "- Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "| Step | Result | Seconds |"
  echo "|---|---|---|"
  EVAL_SECS=""
  ALL_OK=1
  for i in "${!NAMES[@]}"; do
    case "${CODES[$i]}" in
      0) r="ok" ;;
      -) r="not run"; ALL_OK=0 ;;
      *) r="FAILED (exit ${CODES[$i]})"; ALL_OK=0 ;;
    esac
    echo "| ${NAMES[$i]} | $r | ${SECS[$i]} |"
    [ "${NAMES[$i]}" = "make evaluate" ] && EVAL_SECS="${SECS[$i]}"
  done
  [ "$SKIP_CHECK" -eq 0 ] || { echo "| make check | skipped (--skip-check) | - |"; ALL_OK=0; }
  echo "| total | | $T_TOTAL |"
  echo
  if [ -n "$EVAL_SECS" ] && [ "$EVAL_SECS" -lt 120 ]; then EVAL_VERDICT="met ($EVAL_SECS s)"; else EVAL_VERDICT="NOT met (${EVAL_SECS:-no run} s)"; fi
  echo "T-F13: every command succeeded: $([ "$ALL_OK" -eq 1 ] && echo yes || echo NO). \`make evaluate\` under two minutes: $EVAL_VERDICT."
  echo
  cat <<'CHECKLIST'
## Checklist for the second machine (H5)

Run on a machine that has never seen this repository. Record the answers in `docs/results/fresh-clone.md`.

Prerequisites (these are not installed by the repository): Docker Desktop or Engine with Compose v2 and at least 8 GB
for Docker (HAPI takes a 3 GB heap and Synthea generation is memory hungry), Python 3.12 or newer, `make`, `git`, Node 20
or newer with `npm`, and network access for the first run (container images, the pinned Synthea jar, PyPI, npm,
and `npx playwright install chromium` for the browser tests). Ports 5433, 8000 and 8080 free.

- [ ] `git clone <repo> && cd screening-closure` at the commit under test (write down `git rev-parse HEAD`).
- [ ] `python3 -m venv .venv && .venv/bin/pip install -e 'api[dev]'` (the Makefile uses `.venv` if present).
- [ ] `cp .env.example .env`
- [ ] `make up`: all five services start; `curl localhost:8000/health` returns `status: ok`. Time: ____
- [ ] `make demo-seed`: Synthea runs in its container, 300 patients load into HAPI, cases are created
      (`SUMMARY results=... failed=0`). Time: ____
- [ ] `make evaluate`: exits 0 and rewrites `docs/results/results.md` with no diff against the committed file
      (`git status --short docs/results`). **Under two minutes.** Time: ____
- [ ] `make check`: every stage PASS (a SKIPPED stage is not a pass). Time: ____
- [ ] Optional: `make test-offline` (needs Docker only), `make test-phi-logs` against this stack.
- [ ] Machine: CPU ____ RAM ____ OS ____ Docker ____ Date ____ Person ____
- [ ] Anything that needed a step not listed above (a missing package, a port, a permission): ____
CHECKLIST
} | tee "$REPORT"

echo
echo "[fresh-clone] report written to $REPORT"
if [ "$KEEP" -eq 0 ]; then
  # keep the report: copy it out before the temp dir goes
  cp "$REPORT" "${TMPDIR:-/tmp}/closure-fresh-report.md" 2>/dev/null && echo "[fresh-clone] copy at ${TMPDIR:-/tmp}/closure-fresh-report.md"
fi
cleanup
exit "$FAILED"
