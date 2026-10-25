# Operations: clock modes, leader election and what fails

How to run the stack outside a single-laptop demo. It covers the two clock modes (A30), running several API replicas and several scheduler instances without doing the periodic work twice (A31), and what each failure does. The reasoning is in [ADR-041](../../project/DECISIONS.md); the open limitation this closes is L31 in [assumptions-limitations.md](../assumptions-limitations.md); the pilot checklist rows are in [pilot-hardening.md](pilot-hardening.md). Everything here was exercised on synthetic data in Docker on one machine. Nothing is a deployment claim.

## Clock modes (`CLOCK_MODE`)

Every timer, SLA, escalation, idle check and event timestamp reads one object, `app.clock.clock`. `CLOCK_MODE` chooses what it is, once, at process start.

| | `sim` (default) | `wall` |
|---|---|---|
| `clock.now()` | the persisted simulation clock (`sim_clock` row `main`, from `SIM_CLOCK_START`), moved only by a supervisor | real UTC time |
| `/clock/advance`, `/clock/reset` | registered when `APP_ENV=dev`; absent (404) otherwise | never registered, whatever `APP_ENV` says |
| `GET /health` | `clock_mode: "sim"`, `sim_time` is the simulated date | `clock_mode: "wall"`, `sim_time` is the real time (the key name is kept so older clients work) |
| Allowed when `APP_ENV` is not `dev` | no: the API refuses to start (`secure_config_problems`) | yes, and required |
| Use | demo, development, the simulator-driven tests | a deployment that handles real time |

An unknown value (`CLOCK_MODE=fast`) refuses to start. The sweep, the watchdog and the automation take `now` as an argument, so the same log at the same instant gives the same events in either mode (test: `test_sweep_due_computations_are_identical_given_the_same_instant`). Replay never reads a clock, so it is unaffected.

### Switching modes

The `events` table and the `sim_clock` row are never rewritten by a switch. At start-up the API checks the log against the mode:

- **sim to wall.** The sim row is left exactly as it was (not read, not written). If the log holds an event dated after real now, which means a simulation ran ahead of real time, the API refuses to start with the date of that event: every timer computed from such a log would be wrong. Restore a snapshot or stay in `sim`.
- **wall to sim.** The sim clock resumes from its row; if the log holds events later than that (written in wall mode), it moves forward to the latest event (logged as a warning) and persists that. It never moves back, so events are never in the clock's future.

Both are tested on Postgres (`test_clock_modes_pg.py`): a digest of every event row and the clock row is unchanged by starting in the other mode.

### Several replicas in `sim` mode

The sim clock is shared through its row: a replica re-reads it at most every second, and `advance` first adopts a later time another replica saved, so two replicas' advances add up. The read-then-write is not atomic across processes; the clock routes are a development tool, so this is accepted. `wall` mode has nothing to share.

## Leader election (`leader_lease`)

Periodic work must run once, not once per instance: the sweep, the outbox drain, the automation and evidence scans, the agency run that follows a sweep, and the FHIR projection drain. Several scheduler instances and several API replicas may be up at once; a Postgres lease picks one leader for each kind of work.

**The lease.** Table `leader_lease(name, holder, expires_at, token)` (migration 015). `acquire` is one statement: insert the row, or take an expired one, or renew one the caller already holds (`UPDATE … WHERE expires_at < now() OR holder = me`). The fencing `token` goes up only when the holder changes. All times are the database's `now()`, so clock skew between instances cannot give two of them the lease. Two leases exist: `scheduler` (the scheduler loop's work) and `api-background` (the API's own background loops).

**Scheduler instances.** Each asks `POST /scheduler/lease {holder}` at the top of every loop and from a keeper thread every TTL/3, through the sleep and through a long sweep. The winner sweeps, drains and scans, and sends `X-Leader-Holder` and `X-Leader-Token` on every call. The others skip all of it, post a `follower` heartbeat and ask again every `SCHEDULER_FOLLOWER_POLL_SECONDS` (2). On `SIGTERM` the leader releases the lease so a follower takes over at its next poll instead of after the TTL.

**Fencing.** The four periodic routes (`/scheduler/sweep`, `/outbox/drain`, `/automation-scan`, `/evidence-scan`) check the headers against the lease before doing anything: a token that is not the current one for that holder gets 409 `stale leader` and the call writes nothing. A scheduler that was paused or partitioned and wakes up after another took over is refused. With `REQUIRE_LEADER_FENCE=true` (the `scale` profile) a call with no headers is refused too; without it, a call with no headers is allowed (a manual run, a single-scheduler stack). A deposed scheduler keeps its old token on purpose, so its late calls are refused instead of passing as unfenced.

**API replicas.** Each runs a `LeaderLoop` on `api-background`. Only the holder drains the FHIR projection queue; the others idle. The sweep itself runs on whichever replica the scheduler's request reaches, which is safe because only the scheduler leader sends it.

**What the lease is not.** It prevents wasted work and racing drains. It is not what keeps the log correct: event ids are deterministic and every append is transactional, so a stale leader that got through would append nothing twice. That is why a lapse of the lease is a performance event, never a data event.

### Reporting

`GET /heartbeats` (coordinator, supervisor, watchdog):

- `scheduler`: the leader's heartbeat, with `role: "leader"`, `holder` and `fencing_token`.
- `followers`: one entry per other live scheduler instance, `role: "follower"`, listed while its heartbeat is under 120 s old.
- `leader`: the current lease `{holder, token, expires_in}`, or null.

The watchdog still judges the scheduler by the `scheduler` heartbeat, which only the leader writes (a follower that claims to lead is refused with 409). A leader that dies leaves that heartbeat to age; a follower takes over within the TTL and beats again, well inside the watchdog's 60 s limit with the default TTL of 30 s. If no follower exists, `scheduler_down` is raised as before.

### Running it

```
make up-scale        # two API replicas behind Caddy (host port PROXY_PORT, default 8100) and two schedulers
make down
```

`up-scale` starts the `scale` profile (`api-2`, `scheduler-2`, `proxy`), points the schedulers at the proxy, and turns `REQUIRE_LEADER_FENCE` on. The proxy image is pinned by tag and digest. Settings: `CLOCK_MODE`, `LEADER_LEASE_TTL_SECONDS` (30), `REQUIRE_LEADER_FENCE`, `SCHEDULER_HOLDER` (a name for the instance; the compose services use `scheduler-1` and `scheduler-2`), `SCHEDULER_FOLLOWER_POLL_SECONDS` (2).

### Measured handover

| What | Result | Where |
|---|---|---|
| Lease only, Postgres, TTL 2 s, poll 0.2 s | taken over 2.1 s after the leader's last renewal | `test_leader_crash_takeover_within_the_ttl` |
| Compose `scale` stack, leader container killed (`docker kill`), TTL 10 s, poll 2 s | the other scheduler led after 10.1 s, token incremented by one, no event lost or duplicated across 12 cases | `test_chaos_leader.py` (`make test-chaos`) |

The handover is the TTL plus up to one poll, never less than the TTL: a follower cannot take the lease before it has expired. With the default TTL of 30 s a dead leader leaves the work undone for about 32 s.

## Failure modes

| Failure | What happens | What to look at |
|---|---|---|
| Leader scheduler container dies | its lease expires after the TTL; a follower becomes leader (token + 1) and sweeps. Cases are swept late by about the TTL, none are missed: due times come from the log | `/heartbeats` `leader`, scheduler logs |
| Leader paused (stop-the-world, partition), then resumes | its calls carry the old token and get 409; it learns at its next lease call that it is a follower | scheduler logs: `no longer the leader` |
| Postgres unreachable | no lease can be acquired or renewed: every scheduler idles (not knowing is not leading), the API's own `LeaderLoop` keeps its last good lease only until it runs out. The API is down anyway | `/health`, Postgres |
| API unreachable from the schedulers | no lease, no work, no heartbeat; the watchdog raises `scheduler_down` after 60 s | `/alerts` |
| Both schedulers dead | `scheduler_down` after 60 s; nothing sweeps | `/alerts` |
| Two schedulers believe they lead | cannot happen through the lease: one row, one holder. If a bug let both call, the second is refused (stale token) or, with no headers and fencing off, the deterministic ids make the repeat a no-op | |
| One API replica dies | the proxy's health check removes it within a few seconds; the other keeps serving. If it held `api-background`, the FHIR projection drain pauses for up to the TTL | proxy, `/health` |
| Lease TTL too short for the sweep | the keeper renews every TTL/3 in its own thread, so a long sweep does not lose the lease; the TTL only bounds how fast a dead leader is replaced | |
| `CLOCK_MODE=wall` started on a database written under a runaway sim clock | refuses to start with the date of the offending event | API logs |
| `CLOCK_MODE=sim` after a stretch in wall mode | starts, moves the sim clock forward to the latest event, logs a warning | API logs |

## Observability (F14)

The API collects OpenTelemetry metrics in process and serves them at `GET /metrics`; with `OTEL_EXPORTER_OTLP_ENDPOINT` set it also exports metrics and traces over OTLP/HTTP. `LOG_FORMAT=json` makes every log line a JSON object with `trace_id` and `span_id`. Labels and attributes are counts, state names, lease names and route templates; no patient reference, name, message text or query value is exported (the PHI scan, `make test-security`, still covers the logs).

- **`GET /metrics`** is answered by middleware ahead of the router (so it is not in the OpenAPI document or the route policy table). It needs `Authorization: Bearer`: a **supervisor's token**, or `METRICS_SCRAPE_TOKEN` for a scraper. No token or an invalid one is 401, any other role (and every service token) is 403. The scrape token's development default works only while `APP_ENV=dev`; outside dev only a value someone set (32 bytes or more) is accepted. Tests: `api/tests/test_observability.py`.
- **Instruments:** `closure_cases_open{state}`, `closure_cases_overdue`, `closure_outbox_pending`, `closure_projection_queue_depth`, `closure_projection_dead_letters`, `closure_leader{lease}`, `closure_service_heartbeat_age_seconds{service}`, `closure_escalations_emitted_total`, `closure_overdue_marked_total`, `closure_sweeps_total{result}`, `closure_sweep_duration_seconds`, `closure_llm_tokens_total{provider,kind}`, and the FastAPI request histogram `http_server_request_duration_seconds{http_route,...}`. The database-backed gauges are computed on demand and cached (`METRICS_CACHE_SECONDS`, 15 by default); the overdue count replays every open case and is cached for `METRICS_OVERDUE_CACHE_SECONDS` (60). Every replica reports the same gauge values, so queries take `max by`.
- **Traces:** one span per request (route template as the name) and per Postgres statement (parameterised text, never the values). Off unless an OTLP endpoint is set.
- **Local stack:** `make up-obs` starts the `obs` compose profile: OpenTelemetry Collector (traces to its debug log, metrics re-exposed for Prometheus), Prometheus on `127.0.0.1:9090` with the rules in `config/prometheus/alerts.yml`, Grafana on `127.0.0.1:3000` with the dashboard `config/grafana/dashboards/closure.json` provisioned. Images are pinned by tag and digest.

The SLOs these serve, the alert rules and the deployment shape are in [deployment.md](deployment.md).

## What is not covered

- Postgres is a single point of failure here (L20); the lease is only as available as the database.
- The agency's run lock is still per process (agency.md); with the lease the scheduler hook runs only after a leader's sweep, but a supervisor can still start a run through any replica.
- The sim clock's cross-replica read-modify-write is not atomic (above).
- No leader lease for the watchdog service: two watchdogs would each scan, which is harmless (the flag ids are deterministic) but wasteful.
- HAPI, the HL7 listener and Keycloak are not replicated.
