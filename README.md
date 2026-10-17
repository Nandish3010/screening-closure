# Screening Closure

Every actionable screening result has a named owner, a clock, an audit trail, and a clinician-signed closure. Nothing is diagnosed.

**All data in this repository is synthetic: a Synthea v3.3.0 cohort plus a seeded simulator, with no real patient, clinician or facility data.**

## What this is / is not

This is a deterministic closure engine for one pathway: a positive FIT (faecal immunochemical test) result, then a confirmatory colonoscopy, then a clinician-signed closure. It tracks each case, nudges, escalates to humans, and closes only with evidence. It is not a diagnostic tool, and the only use of AI is to draft patient messages that a human approves before anything is sent.

## Quickstart (about 60 seconds)

```
cp .env.example .env
make up
curl localhost:8000/health
make test
```

The stack runs offline once the images are pulled. Postgres is on host port 5433, HAPI FHIR on 8080, the API on 8000.

The web app is not a compose service. Run `make web-install` once, then `make web-dev` and open http://localhost:5173. It uses the mock adapter by default. For the real API: `cd web && VITE_API_ADAPTER=http VITE_API_URL=http://localhost:8000 npm run dev`.

`make evaluate` regenerates every number in `docs/results`; `make evaluate-check` proves the result is byte-for-byte reproducible. `make demo-seed` loads the Synthea cohort into HAPI and creates the cases in the API.

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
| `postgres` | Events, read model, notifications, drafts, heartbeats, alerts; also HAPI's tables | 5433 (host) |
| `fhir` | HAPI FHIR R4 server, receives the projection | 8080 |
| `api` | FastAPI engine | 8000 |
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
| Test | `test-chaos` | Chaos-marked tests: stop and start the scheduler, watch the watchdog alert |
| Test | `test-tools` | Cross-cutting tests in `tests/` |
| Test | `test-security` | Secrets and repo hygiene scan |
| Test | `test-load`, `load-check` | Sweep timing at 1k, 10k, 100k cases and rewrite `docs/results/load.md`; the 1k size only |
| Evaluation | `evaluate`, `simulate`, `evaluate-check` | Regenerate `docs/results`; one arm, one run; prove reproducibility |
| Docs and checks | `check` | Run every stage below in order |
| Docs and checks | `docs-check`, `tracker-lint` | Links, tables and markers in docs; tracker and test-case consistency |
| Web | `web-install`, `web-dev`, `web-build`, `web-test`, `web-e2e` (`test-e2e`) | Install, dev server, build, unit tests, Playwright |
| Review packs | `review-packs`, `review-packs-check`, `test-review-packs` | Build the clinician and Arabic packs; fail on drift; test them |
| Planned (not on this branch yet) | `demo-reset`, `demo-scenario` | One-click reset and demo scenario (ticket E8) |
| Planned (not on this branch yet) | `demo-kill-scheduler` | Kill-the-scheduler rehearsal (F11) |
| Planned (not on this branch yet) | `test-offline` | Demo with outbound network blocked (F10) |
| Planned (not on this branch yet) | `risk-report` | Risk score calibration report (D10) |

## Tests and checks

`make check` runs 13 stages in this order: test, test-db, web-test, web-build, evaluate-check, docs-check, tracker-lint, test-security, test-tools, load-check, review-packs-check, test-review-packs, web-e2e. It stops at the first failure; `CHECK_KEEP_GOING=1` runs all of them.

At the M2/M3 integration, project/LOG.md records: "make check: green, 13 stages (1133 api and simulator tests, 94 integration, 147 web unit, 33 e2e, 52 tool tests, 17 pack tests)".

The closure rule has its own property test: `api/tests/test_state_machine.py::test_never_closed_without_evidence_and_human_signoff`.

## Submission documents

- [Submission index](docs/submission/README.md), [technical documentation](docs/submission/technical-documentation.md), [demo script](docs/submission/demo-script.md)
- Results: [results](docs/results/results.md), [metrics](docs/results/metrics.md), [load test](docs/results/load.md)
- Basis: [evidence](docs/evidence.md), [assumptions and limitations](docs/assumptions-limitations.md), [design choices](docs/design-choices.md), [data provenance](docs/data-provenance.md), [pilot plan](docs/pilot-plan.md)
- Governance: [decision matrix](docs/governance/decision-matrix.md), [hazard log](docs/governance/hazard-log.md), [AI boundary](docs/governance/ai-boundary.md), [regulatory mapping](docs/governance/regulatory-mapping.md), [Malaffi adapter plan](docs/governance/malaffi-adapter-plan.md)
- Review packs: [clinician pack](docs/review/clinician-pack.md), [Arabic pack](docs/review/arabic-pack.md)
- Project: [plan](project/PLAN.md), [tracker](project/TRACKER.md), [decisions](project/DECISIONS.md)

## Status

As of the M2/M3 integration on 18 Oct 2026, built: the core loop, the simulator and evaluation, the interfaces (on the mock adapter), the AI drafting edge, the reviewer packs and the governance documents.

Open (project/TRACKER.md):

- Demo scenario and one-click reset (E8), chaos suite (F3, F4), offline run (F10), kill-the-scheduler rehearsal script (F11) and fresh-clone script (F12) are not started.
- The loss-to-follow-up risk score (D8 to D10) is not started; the worklist falls back to SLA ordering.
- The 100,000-case sweep meets its 5 s target only when sharded on a quiet machine. The committed run in docs/results/load.md shows 18.80 s single process and 5.99 s with 10 workers, and the scheduler route still uses the single-process sweep (F6, in review).
- In review, not done: the "cases nobody is looking at" panel needs a test against the real stack with the scheduler stopped (C9); the messy-case scenarios have no dedicated test (B5); the parameter-override metadata test and the docs-numbers checker are missing (B10).
- Clinician review (H1) and native Arabic review (H2) are pending. The Arabic text is an unreviewed draft.

## Licence

MIT. See [LICENSE](LICENSE).

The Synthea jar (Apache 2.0) is downloaded at run time by `tools/synthea/run.sh` and is not vendored. The HAPI FHIR image keeps its own licence.
