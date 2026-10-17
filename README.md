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

Runs fully offline on a laptop. Run the closure-invariant property test with `make test`.

## Repo layout

```
config/pathways/   pathway definitions (states, transitions, SLAs, escalation ladder)
api/               FastAPI service, event log, state machine, tests, SQL migrations
scheduler/         SLA sweep loop (stub) + heartbeat
watchdog/          silent-failure watchdog (stub)
simulator/         seeded behaviour simulator (parameters only so far)
web/               Vite + React PWA (not scaffolded yet)
docs/              data provenance, design choices, assumptions and limitations
```

Services (docker compose): postgres 16, HAPI FHIR R4, api, scheduler, watchdog.

## Status

Week 1 skeleton: structure, configuration, wiring, stubs, and the closure-invariant property test. Postgres persistence, FHIR mapping, SLA evaluation, the simulator, and the web app are TODO.

## Docs

- [Data provenance](docs/data-provenance.md)
- [Design choices](docs/design-choices.md)
- [Assumptions and limitations](docs/assumptions-limitations.md)
