# Screening Closure

Every actionable screening result has a named owner, a clock, an audit trail, and a clinician-signed closure. Nothing is diagnosed.

**All data in this repository is synthetic: a Synthea v3.3.0 cohort plus a seeded simulator, with no real patient, clinician or facility data.**

## What this is / is not

This is a deterministic closure engine for one pathway: a positive FIT (faecal immunochemical test) result, then a confirmatory colonoscopy, then a clinician-signed closure. It tracks each case, nudges, escalates to humans, and closes only with evidence. It is not a diagnostic tool, and the only use of AI is to draft patient messages that a human approves before anything is sent.

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

`make evaluate` regenerates every number in `docs/results`; `make evaluate-check` proves the result is byte-for-byte reproducible. `make demo-seed` loads the Synthea cohort into HAPI and creates the cases in the API. **The first `make demo-seed` takes about 12 minutes**: Synthea runs in a container to generate the cohort, and then HAPI loads 364 patients. A rerun is fast: it reuses the Synthea output in `data/synthea/out` and skips the load when HAPI already has patients. After it, `make demo-snapshot` saves the demo state and `make demo-reset` brings it back in about 25 s.

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
- The model is confined to drafting patient messages. It cannot send, decide or close.

Detail: [docs/submission/technical-documentation.md](docs/submission/technical-documentation.md).

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
| Docs and checks | `check` | Run every stage below in order |
| Docs and checks | `docs-check`, `tracker-lint` | Links, tables and markers in docs; tracker and test-case consistency |
| Web | `web-install`, `web-dev`, `web-build`, `web-test`, `web-e2e` (`test-e2e`) | Install, dev server, build, unit tests, Playwright |
| Review packs | `review-packs`, `review-packs-check`, `test-review-packs` | Build the clinician and Arabic packs; fail on drift; test them |
| Demo | `demo-scenario` (`UNTIL=<step>`) | Run the scripted story for the named synthetic patient through the API, optionally up to one step (E8, `tools/demo/README.md`) |
| Demo | `demo-snapshot`, `demo-restore`, `demo-reset` | Save the demo state; restore it; one-click reset (restore, reset the clock, check the state fingerprint) |
| Demo | `demo-kill-scheduler` (`RUNS=3`) | Kill-the-scheduler rehearsal: stop the scheduler, wait for the alert, restart, check no event was lost (F11) |
| Risk score | `risk-report`, `risk-check` | Retrain the loss-to-follow-up risk score and rewrite its report and score card (D10); fail if the committed files differ from a fresh run |
| Reproducibility | `fresh-clone` | Clone the repository into a temporary directory and run the fresh-clone steps there, with the checklist for the second machine (F12) |
| Test | `test-offline` | The demo scenario with api, HAPI and Postgres on a network with no route out, in its own project (Docker only; a few minutes) |

## Tests and checks

`make check` runs 14 stages in this order: test, test-db, web-test, web-build, evaluate-check, docs-check, tracker-lint, test-security, test-tools, load-check, risk-check, review-packs-check, test-review-packs, web-e2e. It stops at the first failure; `CHECK_KEEP_GOING=1` runs all of them.

At the M4/M5 integration, project/LOG.md records: "make check: green, 14 stages (1263 api and simulator tests, 103 integration, 164 web unit, 33 e2e, 106 tool tests, 47 security-gate tests, 17 pack tests)".

The closure rule has its own property test: `api/tests/test_state_machine.py::test_never_closed_without_evidence_and_human_signoff`.

## Submission documents

- [Submission index](docs/submission/README.md), [technical documentation](docs/submission/technical-documentation.md), [demo script](docs/submission/demo-script.md)
- Results: [results](docs/results/results.md), [metrics](docs/results/metrics.md), [load test](docs/results/load.md)
- Basis: [evidence](docs/evidence.md), [assumptions and limitations](docs/assumptions-limitations.md), [design choices](docs/design-choices.md), [data provenance](docs/data-provenance.md), [pilot plan](docs/pilot-plan.md)
- Governance: [decision matrix](docs/governance/decision-matrix.md), [hazard log](docs/governance/hazard-log.md), [AI boundary](docs/governance/ai-boundary.md), [regulatory mapping](docs/governance/regulatory-mapping.md), [Malaffi adapter plan](docs/governance/malaffi-adapter-plan.md)
- Review packs: [clinician pack](docs/review/clinician-pack.md), [Arabic pack](docs/review/arabic-pack.md)
- Project: [plan](project/PLAN.md), [tracker](project/TRACKER.md), [decisions](project/DECISIONS.md)

## Status

As of the M4/M5 integration on 18 Oct 2026, built: the core loop, the simulator and evaluation, the interfaces (on the mock adapter and against the real stack), the AI drafting edge, the risk score, the reviewer packs, the governance documents, the chaos, RBAC, PHI and offline checks, and the demo scenario with its one-click reset.

Open (project/TRACKER.md):

- The fresh-clone script is built (F12, `make fresh-clone`); its run on a second machine is still a human task (H5).
- The 100,000-case sweep misses its 5 s target. The committed run in docs/results/load.md shows 20.38 s single process, 6.76 s through `POST /scheduler/sweep` with 4 workers (the default, A17) and 5.21 s with 10 workers.
- In review, not done: the messy-case scenarios have no dedicated test (B5); the parameter-override metadata test and the docs-numbers checker are missing (B10).
- Clinician review (H1) and native Arabic review (H2) are pending. The Arabic text is an unreviewed draft.

## Licence

MIT. See [LICENSE](LICENSE).

The Synthea jar (Apache 2.0) is downloaded at run time by `tools/synthea/run.sh` and is not vendored. The HAPI FHIR image keeps its own licence.
