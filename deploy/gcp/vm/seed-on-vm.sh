#!/bin/bash
# On the VM, as root (run through deploy/gcp/seed.sh over IAP SSH). Loads the SYNTHETIC cohort and takes the demo snapshot, then
# switches the stack to the fail-closed production configuration and opens the public edge.
#
#   1. mode=seed: APP_ENV=dev, dev login, simulation clock, NO public listener (only 127.0.0.1) - the seeding scripts
#      (simulator/*.py) authenticate with the dev login, which production refuses by design.
#   2. Synthea (deterministic: seed 20261017, 300 patients, in a container) -> HAPI -> FIT results -> cases, as `make demo-seed`,
#      then the four long-term-conditions demo cases (blood pressure, HbA1c, BMI; `make demo-seed-chronic`) so Today shows them.
#      Staging (simulator/stage_demo_state.py, real API calls as the demo cast, hosted-only): ONE case is created first and taken past
#      its follow-up date so the real scheduler marks it overdue and escalates it to the supervisor (the other cases are created after the
#      clock moved, so none is overdue); after the chronic cases: a case with a pending AI draft (recorded Claude output, nothing sent), one
#      at the clinician's sign-off and one closed with documented evidence. So the clock does NOT stay at 2026-01-05 on the hosted demo.
#   3. tools/demo/snapshot.sh save: the reset point ("reset demo" restores it).
#   4. mode=production: APP_ENV=production, OIDC, no dev login, FHIR evidence check on, demo-approval off, simulated clock under the demo
#      gate; Caddy opens 80/443.
#
# The simulation clock starts at the repository default (2026-01-05T08:00:00Z) and the staging moves it a few working days on (to the
# overdue case's supervisor rung); same cohort and cases as the local demo. The recorded Claude drafts (api/app/ai/cache) are keyed by
# prompt content, not by date, so they replay. Production keeps the clock where the snapshot left it (demo gate).
# Re-running starts from an empty database only if you pass --fresh (it deletes the volumes).
set -Eeuo pipefail
# A failed step must fail the whole seed (non-zero exit, so ssh, systemd and the caller see it) and must leave the stack in seed mode
# rather than switch it to production on a half-staged database.
trap 'rc=$?; echo "seed: FAILED (exit $rc) at line $LINENO: $BASH_COMMAND; the stack stays in seed mode, nothing was snapshotted" >&2' ERR
export SC_REPO=${SC_REPO:-/srv/sc/repo}
SYNTH_POP=${SYNTHEA_POPULATION:-300}
if [ "${1:-}" = "--fresh" ]; then
  . "$SC_REPO/deploy/gcp/vm/env.sh"; docker compose --profile public down -v || true
fi
echo seed > /var/lib/sc/mode
rm -f /var/lib/sc/demo_patient_ref
"$SC_REPO/deploy/gcp/vm/sc-up.sh"
# shellcheck disable=SC1091
. "$SC_REPO/deploy/gcp/vm/env.sh"

i=0; until curl -fs http://127.0.0.1:8000/health >/dev/null; do i=$((i+1)); [ $i -gt 90 ] && { echo "API did not come up" >&2; exit 1; }; sleep 2; done
i=0; until curl -fs http://127.0.0.1:8080/fhir/metadata >/dev/null; do i=$((i+1)); [ $i -gt 120 ] && { echo "HAPI did not come up" >&2; exit 1; }; sleep 3; done

mkdir -p data
if [ -z "$(ls data/synthea/out/fhir 2>/dev/null)" ]; then
  echo "seed: generating the synthetic cohort ($SYNTH_POP patients; downloads the pinned Synthea release inside a container; ~10-20 min)"
  SYNTHEA_POPULATION=$SYNTH_POP docker compose --profile tools run --rm synthea
else
  echo "seed: using the cohort already in data/synthea/out/fhir"
fi

# The stager may ask the API for the evidence scan right after it records the colonoscopy (POST /scheduler/evidence-scan, open to the
# automation token): hand it that token if the rendered api.env has one. Read here, passed by NAME (never on a command line or in a log).
AUTOMATION_SERVICE_TOKEN=$(sed -n 's/^AUTOMATION_SERVICE_TOKEN=//p' /etc/sc/api.env 2>/dev/null | head -1 || true)
export AUTOMATION_SERVICE_TOKEN

# One command per line under `sh -e`: the first failing step (a stager that exits 1 or 2 included) ends the container non-zero.
if ! docker run --rm --network host -e AUTOMATION_SERVICE_TOKEN -v "$SC_REPO":/repo -w /repo python:3.12-slim sh -ec '
  pip install -q --no-cache-dir httpx pyyaml
  python simulator/load_cohort.py --skip-if-loaded
  python simulator/generate_results.py
  python simulator/stage_demo_state.py --phase overdue
  python simulator/create_cases.py
  python simulator/create_chronic_cases.py
  python simulator/stage_demo_state.py --phase rest
'; then
  echo "seed: the seeding/staging container failed (see its log above); not snapshotting, not switching to production" >&2
  exit 1
fi

ref=$(docker compose exec -T postgres psql -U closure -d closure -At -c "SELECT payload->>'patient_ref' FROM events WHERE payload ? 'patient_ref' ORDER BY id LIMIT 1")
echo "seed: demo patient reference for the patient login: ${ref:-<none>}"
[ -n "$ref" ] && echo "$ref" > /var/lib/sc/demo_patient_ref

tools/demo/snapshot.sh save
# The snapshot is the first reset point: record it so /health's demo block has last_reset_at (and next_auto_reset_at) from the start.
"$SC_REPO/deploy/gcp/vm/demo-reset-watch.sh" --record-seed

echo production > /var/lib/sc/mode
"$SC_REPO/deploy/gcp/vm/sc-up.sh"
echo "seed: done. The public edge is starting; run deploy/gcp/smoke-test.sh from your machine."
