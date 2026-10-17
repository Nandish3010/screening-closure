# Screening Closure

Every actionable screening result has a named owner, a clock, an audit trail, and a clinician-signed closure. Nothing is diagnosed.

## What this is / is not

This is a deterministic case-tracking engine for the pathway from a positive FIT (faecal immunochemical test) result to a confirmatory colonoscopy and a documented closure. It tracks, nudges, escalates to humans, and closes with evidence. It is not a diagnostic tool: screening results are inputs, never conclusions, and clinical decisions stay with clinicians.

## Quickstart

```
cp .env.example .env
make up
curl localhost:8000/health
```

Runs fully offline on a laptop. Run the closure-invariant property test and the other unit tests with `make test` (no database needed). Run the Postgres integration tests with `make test-db` (needs the stack up: `make up`; postgres is published on host port 5433).

## Persistence

Cases are stored in Postgres. The `events` table is append-only (triggers reject UPDATE, DELETE and TRUNCATE) and is the source of truth. Appending an event is one transaction: the case row is locked, the stored log is replayed and checked against `cases.current_state`, the new event is validated by the state machine, and only then are the event row and the new state written, so an invalid transition leaves nothing behind and concurrent writers cannot both win. `cases.current_state` is a derived read model, never trusted without replay. Every event carries an `event_id`; retrying a request with the same `event_id` inserts once. Schema changes are applied at API startup by an in-app migration runner (`api/migrations/*.sql`, recorded in `schema_migrations`). Without `DATABASE_URL` the API falls back to a non-durable in-memory store. HAPI FHIR stores its data in the same Postgres (its own `hfj_*` tables), so `make reset` wipes cases and FHIR resources together.

## Repo layout

```
config/pathways/   pathway definitions (states, transitions, SLAs, escalation ladder)
api/               FastAPI service, event log, state machine, tests, SQL migrations
scheduler/         SLA sweep loop (stub) + heartbeat
watchdog/          silent-failure watchdog (stub)
simulator/         synthetic cohort pipeline (load, FIT results, cases) + simulator parameters
tools/synthea/     containerised Synthea runner (make synthea)
web/               Vite + React PWA (not scaffolded yet)
docs/              data provenance, design choices, assumptions and limitations
```

Services (docker compose): postgres 16, HAPI FHIR R4, api, scheduler, watchdog. Optional `synthea` (profile `tools`) generates the synthetic cohort; `make demo-seed` seeds HAPI and the API with it (see [simulator/README.md](simulator/README.md)).

## Status

Week 1 skeleton: structure, configuration, wiring, stubs, the closure-invariant property test, and Postgres persistence. FHIR mapping, SLA evaluation, the simulator, and the web app are TODO.

## Docs

- [Data provenance](docs/data-provenance.md)
- [Design choices](docs/design-choices.md)
- [Assumptions and limitations](docs/assumptions-limitations.md)
