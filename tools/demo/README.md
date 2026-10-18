# Demo tooling (E8)

Everything the 7-minute GITEX demo needs to start from a known state, run the same story every time, and go back to the start in one command. All data is synthetic.

| Command | What it does |
|---|---|
| `make demo-stage` | Stage the demo's second case through the API: walk it to `confirmatory_completed`, owned by Dr. Karim Nasser (the specialist login). Safe to rerun. See [The second case](#the-second-case). |
| `make demo-snapshot` | Stage the second case (`demo-stage`), then save the current database as the demo snapshot. Do it once on a clean, seeded stack: `make reset && make demo-seed && make demo-snapshot`. |
| `make demo-reset` | One click: restore the snapshot, reset the simulation clock, and check that the state hash equals the snapshot's. |
| `make demo-restore` | The restore alone, without the clock reset and the hash check. |
| `make demo-scenario [UNTIL=<step>]` | Run the scripted story for the named patient through the API, optionally up to and including one step. |
| `make demo-kill-scheduler [RUNS=3]` | The F11 rehearsal: stop the scheduler, wait for the alert, restart it, check no event was lost. |
| `make test-demo` | T-E9 and T-E10 against the running stack (about 4 minutes; leaves the demo reset). |
| `python3 tools/demo/create_colonoscopy_report.py --case-id ... --patient-ref Patient/...` | Create the colonoscopy `Procedure` and `DiagnosticReport` for a case in HAPI without going through the API. |

## The named patient

`scenario.py` picks the demo patient deterministically: the first positive FIT in seed order, which is the first row of `data/results/manifest.json` (`simulator/generate_results.py`), and its case id is the same uuid5 `simulator/create_cases.py` gave it. The display name is the patient's Synthea name read from HAPI, with Synthea's numeric suffixes removed. With the committed seed (`20261017`, 300 patients, ages 40-75) that is **Anisha Kyoko Roberts** (`Patient/10242`), case `158a089f-9ee9-5ddb-ab0b-7b02bda8d7dc`. A different seed or population gives a different patient and the script follows; `make demo-scenario` prints who it picked.

## The scenario

`python3 tools/demo/scenario.py --list` prints the steps with what each does. Every step is an ordinary API call by one of the demo logins (the same names as the web's role switcher); a supervisor moves the clock, and the scheduler sweep is called directly after each move so the result does not depend on when the scheduler container wakes up. Timings are from a full run on the development stack (28 events in the case, 13 s in total; the clock moves are the slow steps because every open case is swept).

| # | Step (`--until` value) | What happens | Sim time after | Wall time |
|---|---|---|---|---|
| 1 | `result_received` | The positive FIT of the named patient is in; the case has no owner. | 5 Jan 08:00 | 0.0 s |
| 2 | `acknowledged` | The lab acknowledges the result. | 5 Jan 08:00 | 0.9 s |
| 3 | `owner_accepted` | Dr. Layla Haddad (primary care) accepts ownership. | 5 Jan 08:00 | 0.3 s |
| 4 | `patient_unreachable` | She phones the patient; nobody answers, so the patient never acknowledges. | 5 Jan 08:00 | 0.3 s |
| 5 | `overdue` | The clock passes the 7-working-day SLA: the scheduler marks the case overdue and sends rung 1 (reminder to the owner). | 14 Jan 09:00 | 7.7 s |
| 6 | `escalation_supervisor` | Three working days later, rung 2: the supervisor is notified. | 19 Jan 09:00 | 1.7 s |
| 7 | `escalation_coordinator` | Seven working days after the breach, rung 3: the programme coordinator is notified. | 23 Jan 09:00 | 0.1 s |
| 8 | `escalation_accepted` | The coordinator accepts rung 3. | 23 Jan 09:00 | 0.1 s |
| 9 | `draft_sent` | An Arabic reminder is drafted for the patient; the owner approves it and it is sent. | 23 Jan 09:00 | 0.3 s |
| 10 | `patient_acknowledged` | The patient taps "I understand" on the PWA. | 23 Jan 09:00 | 0.1 s |
| 11 | `handoff_rejected` | The owner asks Al Noor Endoscopy Centre; they decline for capacity. Ownership stays with the referrer. | 23 Jan 09:00 | 0.2 s |
| 12 | `handoff_bounced` | The owner asks Marina Digestive Health, who never answer; three working days later the scheduler bounces the request. | 28 Jan 10:00 | 0.3 s |
| 13 | `reassigned` | The coordinator re-routes the case back to the owner. | 28 Jan 10:00 | 0.1 s |
| 14 | `handoff_accepted` | The owner asks Corniche Gastro Unit; Dr. Karim Nasser accepts and becomes the owner. | 28 Jan 10:00 | 0.2 s |
| 15 | `patient_reacknowledged` | The new owner phones the patient, who acknowledges again (a re-routed referral goes back through contact). | 28 Jan 10:00 | 0.2 s |
| 16 | `booked` | The colonoscopy is booked, 14 days out. | 28 Jan 10:00 | 0.1 s |
| 17 | `colonoscopy_done` | The clock advances to the appointment day. | 11 Feb 09:00 | 0.0 s |
| 18 | `result_recorded` | The specialist records the colonoscopy: a `Procedure` and `DiagnosticReport` are created in HAPI and the case is told the result exists. | 11 Feb 09:00 | 0.2 s |
| 19 | `clinician_review` | The clinician reviews and signs as themselves (`Practitioner/<own id>`). | 11 Feb 09:00 | 0.1 s |
| 20 | `closed` | The clinician closes the case with the `Procedure` as evidence. | 11 Feb 09:00 | 0.1 s |

Two things differ from the one-line version of the story in the demo script, because the engine says so:

- **Acceptance stops the ladder** (ADR-014). A supervisor can accept rung 2 only before rung 3 exists, so showing all three rungs means the last one is accepted by its own target, the coordinator. The supervisor's role in the demo is moving the clock.
- **A re-routed referral goes back to `owner_accepted`**, so the new owner contacts the patient and the patient acknowledges again before the booking (steps 15 and 16).

To rehearse the live part of the demo, stop the scripted part where the live part begins and carry on in the browser: `make demo-reset && make demo-scenario UNTIL=patient_acknowledged`, then switch roles in the web app (`make web-dev` with `VITE_API_ADAPTER=http`) from there. The web's supervisor role has an "Advance clock (days)" control in the header for the clock moves.

### Determinism (T-E9)

Event ids are uuid5 of the case and the step, every clock move lands on an exact whole hour computed with the same working-day calendar as the sweep, and the scheduler sweep is called directly. At the end the script prints a hash of the case's event log (type, time, actor, payload; the random draft and notification ids replaced by a placeholder). After `make demo-reset` two runs print the same hash:

```
EVENT LOG HASH cada77e1cf81a6fd96c9e2699f6900bb4c3b2c8e60d088e07bde071a2f493e2e
```

The script refuses to run unless the clock is at its start and the case is at `result_received`; run `make demo-reset` first.

## The agency (A22)

The agency is the layer of agents that prepares work for people (one supervisor, five department heads, fourteen executioners; [docs/governance/agency.md](../../docs/governance/agency.md)). Agents are labour, not authority: the agency is preparation only. They write no case event, they draft messages only through the drafting route (the owner still approves), and the demo story above is unchanged by them.

- **Run it.** Log in as the supervisor and call `POST /agency/run` (or use the agency service token, `AGENCY_SERVICE_TOKEN`), then open the `/agency` page in the web app: the org chart, the last run and the inbox of work that needs a person. A second call at the same simulated time returns the same run.
- **Between sweeps.** The scheduler also starts a run on every `AGENCY_EVERY_SWEEPS`-th sweep (default 6).
- **The T-E9 hash caveat.** A run drafts messages for cases in the contact phase, and a draft is a `message_drafted` event, so if the scheduler hook runs while the demo case is in the contact phase the event-log hash printed by `scenario.py` can differ from `cada77e1...` (the scenario calls the sweep route directly after each clock move, and every sixth sweep starts a run). For a hash-exact rehearsal (`make demo-scenario`, `make test-demo`) start the stack with `AGENCY_EVERY_SWEEPS=0`; to rehearse the agency itself, leave it at the default or call `POST /agency/run` by hand and accept the extra `message_drafted` events (they carry no text and nobody approves them unless you do).
- **Reset.** The task ledger (`agency_tasks`) is in the application database, so `make demo-reset` restores it with the rest: it holds whatever the snapshot held (nothing, if the snapshot was taken before any run). The state fingerprint does not include it.

## The second case

The live demo ends on a case that is ready to be reviewed and closed, so it has to exist before going on stage. `stage_second_case.py` (E9) prepares it through the real API; `make demo-stage` runs it, and `make demo-snapshot` runs it first, so the snapshot contains the staged case and every `make demo-reset` brings it back.

- **Which case.** The second positive FIT in seed order: row 1 of `data/results/manifest.json`, with the uuid5 case id `simulator/create_cases.py` gave it. `scenario.py` drives row 0 (the named patient), so the two never meet, and the scenario's check that its case is at `result_received` is unaffected. `python3 tools/demo/stage_second_case.py --show` prints the patient and the case id without touching the API; in the web worklist it is the case whose state reads "Confirmatory completed" and whose owner is Dr. Karim Nasser.
- **What it does**, as the demo logins (the same names as `scenario.py`): the coordinator acknowledges the result; Dr. Karim Nasser (`specialist_owner`, Corniche Gastro Unit) accepts ownership and records the phone contact; the patient acknowledges; Dr. Nasser books the colonoscopy (appointment `APPT-2026-0002`, 14 days after the simulation clock) and records the result: `POST /cases/{id}/confirmatory-result` writes the `Procedure` and `DiagnosticReport` to HAPI, then `complete_confirmatory` carries the `Procedure` as `result_ref`. Six calls in about a second. The simulation clock is not moved, which `scenario.py` requires of the snapshot.
- **Safe to rerun.** Event ids are uuid5 of the case and the step and the `Procedure` id is deterministic. A case that is part-way (an earlier run stopped) continues from its state; one already at `confirmatory_completed`, or past it (reviewed or closed), is left alone and the script says so. A case in an unexpected state, or at the target with another owner, is an error with the instruction to `make demo-reset`.
- **Exit codes.** 0 staged or already there; 1 the staging failed or the case cannot be staged (the message says why); 2 the API or HAPI is not reachable or ready. It prints a note on stderr if the clock has moved, since a snapshot taken then would not start at the clock's start.

## Snapshot and reset (T-E10)

HAPI stores its data in its own database (`hapi`) next to the application's (`closure`) on the same Postgres server, and `snapshot.sh` copies both together, so the pair of copies is the whole demo state: cases, events, drafts, notifications, the simulation clock and the FHIR resources. `snapshot.sh` makes the copy as a Postgres template copy (`CREATE DATABASE closure_snapshot TEMPLATE closure STRATEGY FILE_COPY`, and the same for `hapi_snapshot`), a file-level copy inside the server: a 3 GB database in 4 to 8 seconds, where `pg_dump` and `pg_restore` take many minutes. The event log is append-only (triggers forbid UPDATE and DELETE), so "undo the demo" can only mean replacing the database.

The API, scheduler, watchdog and HAPI are stopped around the copy (Postgres will not copy a database someone is connected to, the API keeps the clock in memory and rereads it when it starts, and HAPI must not keep caches or preallocated resource ids from before the restore; it is killed rather than stopped since its database is about to be replaced).

`make demo-reset` runs `snapshot.sh reset`: restore, `POST /clock/reset`, then compare the **state fingerprint** with the one stored at snapshot time (`data/demo-snapshot.fingerprint`). The fingerprint has the counts and md5 of cases, events, drafts and notifications, the sim clock, and the number of live FHIR resources per type; heartbeats, alerts and the users directory are left out because they change by themselves. Measured on the development machine:

| | Seconds |
|---|---|
| stop the services | 1.5 |
| copy the database | 4 to 8 |
| the API answers again | about 13 |
| HAPI answers again (a JVM; depends on how busy the machine is) | 29 to 42 |

The demo is usable once the API answers (about 13 s); HAPI is needed by the colonoscopy step and the closure evidence check, long after. `snapshot.sh` waits for HAPI too before it returns, so the next command is safe.

The snapshot lives in the compose volume: it survives `make down` and `make up`, not `make reset`. After `make reset && make demo-seed`, take a new one with `make demo-snapshot` (it stages the second case first; to stage without saving, `make demo-stage`). `snapshot.sh status` says whether one exists, `snapshot.sh hash` prints the fingerprint of the running database, `snapshot.sh refresh` rewrites the stored fingerprint from the snapshot database.

## Kill the scheduler (F11)

`make demo-kill-scheduler RUNS=3` runs the on-stage moment, three times if asked: record the event log (count and a hash of every event id in order), stop the scheduler container, wait for the watchdog's `scheduler_down` alert (the dashboard banner reads the same alert; the limit is 90 s), check that the API still answers, start the scheduler, wait for the alert to clear, and require that the log is unchanged (same count, same ids in the same order, nothing lost or duplicated). It writes no events itself.

## The colonoscopy result

`POST /cases/{id}/confirmatory-result` (the case owner only, no body) creates a synthetic colonoscopy `Procedure` (SNOMED 73761001) and `DiagnosticReport` in HAPI for the case's patient, and returns their references. The ids are deterministic (`Procedure/colonoscopy-<case id>`), so recording twice is idempotent. `evidence_ref` in the answer is the Procedure: it is what `complete_confirmatory.result_ref` and `close.evidence_ref` take, and `close` checks it against HAPI. The web has the "Record colonoscopy result" button on it; `create_colonoscopy_report.py` does the same from the command line.
