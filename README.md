# Screening Closure

[![check](https://github.com/Nandish3010/screening-closure/actions/workflows/check.yml/badge.svg)](https://github.com/Nandish3010/screening-closure/actions/workflows/check.yml)

Every actionable screening result has a named owner, a clock, an audit trail, and a clinician-signed closure. Nothing is diagnosed.

**Outcome, in a simulation on synthetic data (literature-anchored scenario):** colonoscopy within 180 days rises from 51% to 65%; cases lost without anyone told fall from 45.7% to 0% for cases the programme reaches, and to 20.8% programme-level under a 0.55 reach assumption. Details and intervals: [docs/results/results.md](docs/results/results.md).

**Under finite colonoscopy capacity (same scenario, synthetic):** at base capacity the 180-day gain is +14.7 points (95% CI +14.3 to +15.1), the 90-day gain is near zero (+0.2, -0.1 to +0.5), and the gain largely disappears when slots are short: at 70% of base supply the 180-day gain is +2.7 points. Base capacity is a calibrated scale device, not a measurement. See "Under finite capacity" in [docs/results/results.md](docs/results/results.md) and [docs/evidence.md](docs/evidence.md) section 12.

**Capacity, in the same simulation (literature-anchored scenario):** 33.4% fewer staff minutes per case the system handles (literature-anchored, in reach, 95% CI 32.5 to 34.4), 18.3% programme-level under the 0.55 reach assumption (95% CI 17.8 to 18.8), 37.3% in the upper range (95% CI 36.4 to 38.1); synthetic; minutes from published time studies and assumptions.\* The 40% target is not met: the anchored figures fall short of it, and even the upper range (37.3%, which is not a forecast) does not reach it. Coordinator time falls 35 to 68 percent; specialist time rises (+19.5% to +36.6%) because more patients reach colonoscopy, which is the outcome; owner-clinician time doubles in the current routing (+109.2% anchored, +189.6% in the upper range) and the planned fix is routing escalations to nurse navigators after the clinician review.

\* From `make evaluate` on the merged tree, 18 October 2026 ([docs/results/results.md](docs/results/results.md), Human effort): 20 replications x 2,000 synthetic cases; minutes from published time studies (sourced-adjacent) and assumptions.

**All data in this repository is synthetic: a Synthea v3.3.0 cohort plus a seeded simulator, with no real patient, clinician or facility data.**

## What this is / is not

This is a deterministic closure engine for one pathway: a positive FIT (faecal immunochemical test) result, then a confirmatory colonoscopy, then a clinician-signed closure. It tracks each case, nudges, escalates to humans, and closes only with evidence. It is not a diagnostic tool. Models are used in four places, drafting patient messages for human approval, ordering the worklist by a calibrated risk score, planning the agency's work queue, and classifying inbound patient replies, plus automatic sending of clinician-pre-approved deterministic templates; no model and no agent can move a case between states, approve a message, change consent or close a case.

**Automatic patient messages are OFF until a clinician signs.** The pre-approved message templates are sent by the automation only under `config/outreach_policy.yaml`, and the shipped policy is **unsigned** (`approved_by: null`): an unsigned policy sends nothing (the automation counts and logs what it held back, `outreach_held`, reason `policy_unsigned`). No clinician has reviewed any template or rule (H1 is open; the AI-simulated reviews in `docs/governance/` are not that sign-off). The compose demo sets `OUTREACH_DEMO_APPROVAL=1`, which treats the policy as signed by **"DEMO PLACEHOLDER (clinical sign-off pending, H1)"**; the web app then shows a persistent amber banner with that text on the case page and the dashboard, `GET /health` reports `outreach.demo_approval`, and the API refuses to start with the switch on outside `APP_ENV=dev`. A real signature in the file wins over the switch.

People act on decisions and conversations; the system does the rest. The phone call after an escalation is the evidence-based intervention, not waste; automation prepares it, a person makes it.

## Judge path in 5 minutes

Prerequisites: Docker, the `.venv` from the Quickstart below, and `make web-install` once. The first time only, the demo needs a saved state (the first seed takes about 12 minutes; later runs are fast):

```
make demo-seed && make demo-stage && make demo-snapshot
```

Then:

1. `make up` starts the stack.
2. `make demo-reset` brings the database, the FHIR store and the simulation clock back to the snapshot (about 25 s).
3. Start the web app against the real API, not the in-browser mock that `make web-dev` serves by default: `cd web && VITE_API_ADAPTER=http VITE_API_URL=http://localhost:8000 npm run dev`. Open the three surfaces at http://localhost:5173: the coordinator app at `/coordinator`, the closure dashboard at `/dashboard`, and the patient PWA (switch the role to Patient from a case page, or open `/patient/<caseId>`).
4. `make evaluate` regenerates every evaluation number in `docs/results` (about 25 to 30 s on 10 cores, measured 22 October 2026, now including the capacity layer; simulator/README.md).
5. `make check` runs all 18 stages (listed under Tests and checks; about 18 minutes).

The scripted walkthrough is [docs/submission/demo-script.md](docs/submission/demo-script.md).

## Quickstart (about 60 seconds, plus the one-time venv)

A fresh clone has no `.venv`. The Makefile uses `.venv` when it exists and otherwise falls back to a bare `python3` with no packages, which is enough for `make up` and `make test` (the latter then runs inside the api container) but not for `make demo-seed`, `make evaluate` or `make demo-scenario`. Create it first, with two lines:

```
python3 -m venv .venv
.venv/bin/pip install -e 'api[dev]'
```

Or run `sh tools/check/fresh_clone.sh` (`make fresh-clone`), which clones the committed state into a temporary directory, creates the venv there and runs the whole sequence below against its own compose project and ports.

```
cp .env.example .env
make up
curl localhost:8000/health
make test
```

The stack runs offline once the images are pulled. Postgres is on host port 5433, HAPI FHIR on 8080, the API on 8000, each bound to 127.0.0.1 only. `make up` first runs two one-shot containers (`db-init`, `migrate`) that create the databases and apply the schema as the database owner; the API then runs as the unprivileged role `closure_app` (ADR-029).

The web app is not a compose service. Run `make web-install` once, then `make web-dev` and open http://localhost:5173. It uses the mock adapter by default. For the real API: `cd web && VITE_API_ADAPTER=http VITE_API_URL=http://localhost:8000 npm run dev`.

`make evaluate` regenerates every number in `docs/results`; `make evaluate-check` proves the result is byte-for-byte reproducible. `make demo-seed` loads the Synthea cohort into HAPI and creates the cases in the API. **The first `make demo-seed` takes about 12 minutes**: Synthea runs in a container to generate the cohort, and then HAPI loads 364 patients. A rerun is fast: it reuses the Synthea output in `data/synthea/out` and skips the load when HAPI already has patients. After it, `make demo-stage && make demo-snapshot` stages the second demo case and saves the demo state, and `make demo-reset` brings it back in about 25 s.

## Architecture

```mermaid
flowchart LR
  web["web: coordinator app, patient PWA, dashboard"] --> api
  sched["scheduler: SLA sweep, heartbeat"] --> api
  dog["watchdog: idle scan, heartbeat alert"] --> api
  api["api (FastAPI): state machine, policy and RBAC, consent ledger, drafts and approval gate, notification mock, metrics, FHIR projection"]
  api --> pg[("postgres: append-only events, read model, notifications, drafts, heartbeats, alerts")]
  api --> fhir["HAPI FHIR R4: Task, ServiceRequest, Appointment, Communication, Consent, Provenance"]
  sim["simulator (offline, same state machine)"] --> res["docs/results"]
```

- Event sourcing: the `events` table is append-only (triggers reject UPDATE, DELETE and TRUNCATE). State comes from replay. An append validates the event, then writes it in one transaction with the case row locked.
- Closure invariant: `close` needs an `evidence_ref` that resolves in HAPI and a `signed_by` from a human clinician. It is property-tested (T-A1).
- A working-day SLA engine drives an escalation ladder to named humans.
- The watchdog finds open cases nobody is touching, and heartbeats make a stopped scheduler raise an alert.
- Every event is projected to HAPI FHIR R4 resources.
- Models sit at the edges and cannot move a case, approve a message, change consent or close a case ([AI boundary](docs/governance/ai-boundary.md)).

Detail: [docs/submission/technical-documentation.md](docs/submission/technical-documentation.md).

## Why a programme would buy it

Capacity first: grow screening without growing coordinators. The same engine that lifts colonoscopy completion from 51% to 65% takes over the routine steps of each case, so coordinator hours per handled case fall by more than a third in the literature-anchored scenario (synthetic, assumed minutes), and people keep the decisions and the conversations. Outcomes come with the capacity: more patients reach colonoscopy and fewer are lost without anyone told.

The saving is in coordinator hours, not in cost per case. At wage ratios taken from published UAE salary pages (a clinician 4 times and a specialist 10 times a coordinator; graded sourced-adjacent, not an official pay scale; [docs/evidence-wages.md](docs/evidence-wages.md)), weighted staff time per case goes up, not down: 125.5 coordinator-equivalent minutes at baseline against 140.1 under the literature-anchored scenario at programme level (+11.6%), 125.8 against 152.4 in reach (+21.2%) and 149.1 in the upper range (+18.8%). Across the ranges in that note (clinician 3 to 6, specialist 8 to 15) the programme-level change stays positive, +6.8% to +18.3% (`python3 tools/pilot/cost_model.py`). The specialist ratio is what moved it: at the earlier assumed ratios of 3 and 4 the same minutes gave roughly flat weighted time (+1.8%). The commercial claim is therefore capacity plus outcomes, and the pilot measures the minutes (time-and-motion study, KPI K9 in [docs/pilot-plan.md](docs/pilot-plan.md)). The business case ([docs/commercial/business-case.md](docs/commercial/business-case.md)) says the same thing more bluntly: avoided downstream cost alone does not pay the fee (AED 68 to 155 per positive case avoided in the literature-anchored simulation against an illustrative AED 90), so the case rests on freed coordinator capacity and on whether the Department of Health sets a follow-up standard, which is to verify. No buyer exists and no price has been tested.

## What is in the box

| Service | What it does | Port |
|---|---|---|
| `postgres` | Events, read model, notifications, drafts, heartbeats, alerts (database `closure`); HAPI's tables (database `hapi`) | 5433 (host, 127.0.0.1) |
| `db-init`, `migrate` | One-shot: create the extra databases; apply the schema and set the API role's password | none |
| `fhir` | HAPI FHIR R4 server, receives the projection | 8080 (127.0.0.1) |
| `api` | FastAPI engine (non-root, unprivileged database role) | 8000 (127.0.0.1) |
| `scheduler` | Periodic SLA sweep, writes a heartbeat | none |
| `watchdog` | Idle-case scan, alerts on a stale heartbeat | none |
| `web` | Vite and React app, started with `make web-dev` | 5173 |
| `synthea` | Cohort generator, compose profile `tools`, run with `make synthea` | none |

```
config/      pathway definitions (states, transitions, SLAs, escalation ladder), calendar
api/         FastAPI app, state machine, migrations, tests
scheduler/   SLA sweep service
watchdog/    idle scan and heartbeat alert service
simulator/   behaviour simulator, evaluation harness, cohort loading scripts
web/         coordinator app, patient PWA, dashboard
tools/       check, load, pilot, review and synthea scripts
docs/        results, governance, review packs, submission documents
project/     plan, tracker, decisions, log, test cases
```

## Make targets

| Group | Target | Meaning |
|---|---|---|
| Run | `up`, `down`, `logs` | Build and start, stop, and follow logs for the compose stack |
| Run | `reset` | Wipe volumes (cases and FHIR data) and rebuild |
| Run | `synthea`, `demo-seed`, `demo-seed-clean` | Generate the cohort in a container; load it and create cases; delete generated files |
| Test | `test` | Unit and property tests, no database |
| Test | `verify` | Exhaustive model check of the state machine (`tools/verify/`, ADR-038): 9,057 abstract states, 7 invariants, about a minute, no database |
| Test | `test-db` (`test-db-create`, `test-db-reset`) | Integration tests against `closure_test`; create or recreate that database |
| Test | `test-chaos` | Chaos-marked tests: duplicate delivery, clock skew and store failures through the fault harness (F3), a sweep killed part-way (F4), bad model output and consent withdrawn mid-send (F5), and the scheduler container stopped and restarted with the watchdog alert |
| Test | `test-tools` | Cross-cutting tests in `tests/` |
| Test | `test-security` | Secrets and repo hygiene scan; RBAC matrix (F7); no PHI in route parameters or in the logs of an in-process demo walk (F8) |
| Test | `rbac-matrix` | Rewrite `docs/governance/rbac-matrix.md` from the app; `test-security` fails if the file differs |
| Test | `test-phi-logs` | The F8 log scan against the running stack's service logs; writes a few synthetic marker cases, run it on a throwaway stack |
| Test | `test-demo` | T-E9 and T-E10 against the running stack: the scenario repeats identically after a reset, and the reset restores the snapshot's state (about 4 minutes) |
| Test | `test-e2e-live` | Playwright against the running stack: the full flow, the declined-handoff re-route and T-C16 (stops the scheduler container); skips if the API is down |
| Test | `test-load`, `load-check` | Sweep timing at 1k, 10k, 100k cases and rewrite `docs/results/load.md`; the 1k size only |
| Evaluation | `evaluate`, `simulate`, `evaluate-check` | Regenerate `docs/results`; one arm, one run; prove reproducibility |
| Evaluation | `sensitivity`, `sensitivity-check` | One-at-a-time parameter sensitivity sweep, written to `docs/results/sensitivity.*`; prove it reproduces |
| Docs and checks | `check` | Run every stage below in order |
| Docs and checks | `docs-check`, `tracker-lint` | Links, tables and markers in docs; tracker and test-case consistency |
| Web | `web-install`, `web-dev`, `web-build`, `web-test`, `web-e2e` (`test-e2e`) | Install, dev server, build, unit tests, Playwright |
| Review packs | `review-packs`, `review-packs-check`, `test-review-packs` | Build the clinician and Arabic packs; fail on drift; test them |
| Demo | `demo-scenario` (`UNTIL=<step>`) | Run the scripted story for the named synthetic patient through the API, optionally up to one step (E8, `tools/demo/README.md`) |
| Demo | `demo-stage` | Walk the second seeded case to `confirmatory_completed`, owned by the specialist login, through the API; safe to rerun (E9, `tools/demo/README.md`) |
| Demo | `demo-snapshot`, `demo-restore`, `demo-reset` | Save the demo state (stages the second case first); restore it; one-click reset (restore, reset the clock, check the state fingerprint) |
| Demo | `demo-kill-scheduler` (`RUNS=3`) | Kill-the-scheduler rehearsal: stop the scheduler, wait for the alert, restart, check no event was lost (F11) |
| Demo | `agency-run` | One agency run on demand against the running stack (`POST /agency/run` as the supervisor), printing the summary; the compose stack sets `AGENCY_EVERY_SWEEPS=0`, so the scheduler never starts one itself and the demo snapshot stays deterministic (A22, `tools/demo/README.md`) |
| Risk score | `risk-report`, `risk-check` | Retrain the loss-to-follow-up risk score and rewrite its report and score card (D10); fail if the committed files differ from a fresh run |
| Reproducibility | `fresh-clone` | Clone the repository into a temporary directory and run the fresh-clone steps there, with the checklist for the second machine (F12) |
| Test | `test-offline` | The demo scenario with api, HAPI and Postgres on a network with no route out, in its own project (Docker only; a few minutes) |

## Tests and checks

`make check` runs 18 stages in this order: test, verify, test-db, web-test, web-build, evaluate-check, docs-check, tracker-lint, test-security, test-tools, load-check, risk-check, review-packs-check, test-review-packs, effort-check, web-e2e, sensitivity-check, calibrate-check. It stops at the first failure; `CHECK_KEEP_GOING=1` runs all of them. The whole run took 13.5 minutes at the M5 final merge and 17.8 minutes (1,069 s, all 18 stages passing, none skipped) at the M6 week 1 merge, both inside the 25-minute budget, so the slow regeneration checks (sensitivity-check about 6 to 7 minutes, calibrate-check, risk-check) stay in `make check` rather than in a separate target. test-db and load-check are SKIPPED loudly (not passed) when Postgres on :5433 is down, and calibrate-check when the gitignored downloads are absent (`make calibrate-fetch` once). Set `E2E_PORT` if port 4173 is taken by another preview.

At the M5 final merge (project/LOG.md, line "M5-final", 2026-10-18), `make check` was green over 17 of 17 stages: 2154 api and simulator tests, 230 integration, 287 web unit, 51 e2e, 188 tool tests, 72 security-gate tests, 19 pack tests, 20 effort tests, 21 calibration tests. Outside it: `make test-chaos` 45 passed, `make test-demo` 2 passed, `make test-e2e-live` 3 passed, `make test-offline` 17 of 17.

The state machine is also model-checked exhaustively by `make verify` (`tools/verify/model_check.py`, [docs/governance/verification.md](docs/governance/verification.md)): 9,057 abstract states, 7 invariants, and 5 seeded mutations, all caught. It found one real bug, now fixed: an approved draft could be sent while an opt-out or wrong-number hold was open (ADR-038).

The closure rule has its own property test: `api/tests/test_state_machine.py::test_never_closed_without_evidence_and_human_signoff`.

## Continuous integration

GitHub Actions runs `.github/workflows/check.yml` on every push and pull request to `main`: `unit` (Python 3.12: `make test`, `evaluate-check`, `docs-check`, `tracker-lint`, `test-security`, `test-tools`, `review-packs-check`, `effort-check`), `db` (Postgres 16 and HAPI FHIR as service containers: `make test-db`; the HAPI tests skip if HAPI does not start) and `web` (Node 22: unit tests, build, Playwright e2e on the mock adapter). `heavy` (`sensitivity-check`, `risk-check`) runs nightly and on demand. Stages that need the whole compose stack (`load-check`, `test-offline`, `test-demo`, the live e2e) are not in `check.yml`.

`.github/workflows/fresh-clone.yml` (manual and weekly) runs `tools/check/fresh_clone.sh` on a clean runner with a reduced Synthea population (60) and uploads the timing report as an artifact. It is the automated stand-in for the second-machine check; a run on a real second machine (H5) is still a human task.

## Submission documents

- [Submission index](docs/submission/README.md), [technical documentation](docs/submission/technical-documentation.md), [demo script](docs/submission/demo-script.md)
- Results: [results](docs/results/results.md), [metrics](docs/results/metrics.md), [load test](docs/results/load.md)
- Basis: [evidence](docs/evidence.md), [assumptions and limitations](docs/assumptions-limitations.md), [design choices](docs/design-choices.md), [data provenance](docs/data-provenance.md), [pilot plan](docs/pilot-plan.md)
- Governance: [decision matrix](docs/governance/decision-matrix.md), [hazard log](docs/governance/hazard-log.md), [AI boundary](docs/governance/ai-boundary.md), [regulatory mapping](docs/governance/regulatory-mapping.md), [Malaffi adapter plan](docs/governance/malaffi-adapter-plan.md), [pilot hardening checklist](docs/governance/pilot-hardening.md) (what must change before real patients)
- Review packs: [clinician pack](docs/review/clinician-pack.md), [Arabic pack](docs/review/arabic-pack.md)
- Project: [plan](project/PLAN.md), [tracker](project/TRACKER.md), [decisions](project/DECISIONS.md)

## Status

As of the M5 fix wave on 18 Oct 2026, built: the core loop, the simulator and evaluation, the interfaces (on the mock adapter and against the real stack), the AI drafting edge, the risk score, the reviewer packs, the governance documents, the chaos, RBAC, PHI and offline checks, and the demo scenario with its one-click reset.

Open (project/TRACKER.md):

- The fresh-clone script is built (F12, `make fresh-clone`); its run on a second machine is still a human task (H5).
- The 100,000-case sweep meets its 5 s target at the default worker count (A20). The committed run in docs/results/load.md shows 8.72 s single process, 3.93 s through `POST /scheduler/sweep` with 4 workers (the default, A17) and 3.17 s with 10 workers, on a shared laptop. The first sweep after migration 012 reads every case once (20.4 s with 4 workers).
- In review, not done: the messy-case scenarios have no dedicated test (B5); the parameter-override metadata test and the docs-numbers checker are missing (B10).
- Clinician review (H1) and native Arabic review (H2) are pending. The Arabic text is an unreviewed draft.
- Not ready for real patients: login is dev-only (OIDC or UAE PASS is not built), tokens do not expire, the clock is a simulation clock on a single API replica, and there is no Malaffi adapter or DoH cloud authorisation. The checklist is [docs/governance/pilot-hardening.md](docs/governance/pilot-hardening.md).

## Licence

MIT. See [LICENSE](LICENSE).

The Synthea jar (Apache 2.0) is downloaded at run time by `tools/synthea/run.sh` and is not vendored. The HAPI FHIR image keeps its own licence.
