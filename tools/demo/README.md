# Demo tooling (E8)

Everything the 7-minute GITEX demo needs to start from a known state, run the same story every time, and go back to the start in one command. All data is synthetic.

| Command | What it does |
|---|---|
| `make demo-stage` | Stage the demo's second case through the API: walk it to `confirmatory_completed`, owned by Dr. Karim Nasser (the specialist login). Safe to rerun. See [The second case](#the-second-case). |
| `make demo-snapshot` | Stage the second case (`demo-stage`), then save the current database as the demo snapshot. Do it once on a clean, seeded stack: `make reset && make demo-seed && make demo-snapshot`. |
| `make demo-reset` | One click: restore the snapshot, reset the simulation clock, and check that the state hash equals the snapshot's. |
| `make demo-restore` | The restore alone, without the clock reset and the hash check. |
| `make demo-scenario [UNTIL=<step>]` | Run the scripted story for the named patient through the API, optionally up to and including one step. |
| `make agency-run` | Trigger one agency run on demand against the running stack (`POST /agency/run` as the supervisor, or with `AGENCY_SERVICE_TOKEN` set) and print the run summary. The stack's scheduler never starts one by itself (`AGENCY_EVERY_SWEEPS=0`). See [The agency](#the-agency-a22). |
| `make demo-kill-scheduler [RUNS=3]` | The F11 rehearsal: stop the scheduler, wait for the alert, restart it, check no event was lost. |
| `make test-demo` | T-E9 and T-E10 against the running stack (about 4 minutes; leaves the demo reset). |
| `python3 tools/demo/create_colonoscopy_report.py --case-id ... --patient-ref Patient/...` | Create the colonoscopy `Procedure` and `DiagnosticReport` for a case in HAPI without going through the API. |

## The named patient

`scenario.py` picks the demo patient deterministically: the first positive FIT in seed order, which is the first row of `data/results/manifest.json` (`simulator/generate_results.py`), and its case id is the same uuid5 `simulator/create_cases.py` gave it. The display name is the patient's Synthea name read from HAPI, with Synthea's numeric suffixes removed. With the committed seed (`20261017`, 300 patients, ages 40-75) that is **Anisha Kyoko Roberts** (`Patient/10242`), case `158a089f-9ee9-5ddb-ab0b-7b02bda8d7dc`. A different seed or population gives a different patient and the script follows; `make demo-scenario` prints who it picked.

The patient is registered with **Marina Digestive Health** (`simulator/create_cases.py` derives the registration from the patient id, `config/providers.yaml` lists the owner of each provider), so the automation names **Dr. Huda Rahman** as the owner. The script reads the owner from the case and logs in as them, so a patient registered with Riverside Primary Care (Dr. Layla Haddad) runs the same story.

## The scenario

`python3 tools/demo/scenario.py --list` prints the steps with what each does. Every step is an ordinary API call by one of the demo logins (the same names as the web's role switcher) or what the automation does about it; a supervisor moves the clock, and the scheduler sweep, the automation scan and the evidence scan are called directly after each move or event that can make something due, so the result does not depend on when the scheduler container wakes up. Timings are from a full run on the development stack (36 events in the case, 2.4 s in total with the 22 demo cases).

**The automation is on** (`AUTOMATION_ENABLED: true` in `docker-compose.yml`, A21, ADR-032), so the story follows `system:automation`:

- The case is **acknowledged and assigned when it is created**: the lab does nothing (steps 1 and 2). The owner is named, not made to accept: the named owner still accepts (step 3), because the engine needs `accept_ownership` for the clock and the ladder.
- **Pre-approved messages go out on their own** (`config/outreach_policy.yaml`: no draft, nobody to approve): the result notice when the owner accepts (3), the reminder two working days after the call (5), the reminder on rung 1 (6), the notice again when the case is re-routed (15) and the appointment confirmation on booking (18). Each is a `message_sent` with `auto: true`, `template_id` and `template_version`, from `system:automation`. They go out only because compose sets `OUTREACH_DEMO_APPROVAL=1`: the shipped policy is unsigned (`approved_by: null`) and an unsigned policy sends nothing, so the web app shows an amber "DEMO PLACEHOLDER (clinical sign-off pending, H1)" banner on the case page and the dashboard while the demo runs (A26, ADR-037). The weekly cap (two per patient) and the patient's consent are the policy's and the state machine's, not the script's.
- **A rejected or bounced referral is re-requested from the next provider** in `reroute_order` (`config/providers.yaml`), skipping the ones already asked and the owner's own. Al Noor declines for capacity (11) and the automation asks Corniche Gastro Unit (12); Corniche does not answer, the scheduler bounces it (13) and, the list being used up, the automation flags the case `reroute_exhausted` and stops. A coordinator re-routes it (14), the owner asks Corniche again and Dr. Karim Nasser accepts (15, 16).
- **The closure is proposed, never made.** The evidence scan asks HAPI for the colonoscopy once the appointment has begun (so not before the clock reaches the booked date) and remembers a "nothing yet" for six simulated hours. The script moves the clock to the appointment day, lets the scan look (nothing there), records the result, and six hours later the scan finds the Procedure and writes `closure_proposed`; the clinician still reviews and closes by hand (22, 23).

| # | Step (`--until` value) | What happens | Sim time after | Wall time |
|---|---|---|---|---|
| 1 | `result_received` | The positive FIT of the named patient is in and the automation has taken the intake: the case is `acknowledged` and its owner is named. Nobody has accepted. | 5 Jan 08:00 | 0.0 s |
| 2 | `auto_intake` | The log shows `acknowledge` and `assign_ownership` from `system:automation`, both flagged `auto`, naming the owner and the registered provider. | 5 Jan 08:00 | 0.0 s |
| 3 | `owner_accepted` | The named owner (Dr. Huda Rahman, Marina Digestive Health) accepts ownership; the pre-approved result notice is sent by the automation. | 5 Jan 08:00 | 0.3 s |
| 4 | `patient_unreachable` | She phones the patient; nobody answers, so the patient never acknowledges. | 5 Jan 08:00 | 0.0 s |
| 5 | `auto_reminder` | Two working days after the call the automation sends the pre-approved reminder. | 7 Jan 09:00 | 0.1 s |
| 6 | `overdue` | The clock passes the 5-working-day patient-acknowledgement SLA: the scheduler marks the case overdue and sends rung 1 (reminder to the owner). | 12 Jan 09:00 | 0.1 s |
| 7 | `escalation_supervisor` | Three working days later, rung 2: the supervisor is notified. | 15 Jan 09:00 | 0.1 s |
| 8 | `escalation_coordinator` | Seven working days after the breach, rung 3: the programme coordinator is notified. | 21 Jan 09:00 | 0.1 s |
| 9 | `escalation_accepted` | The coordinator accepts rung 3. | 21 Jan 09:00 | 0.1 s |
| 10 | `draft_sent` | An Arabic reminder is drafted for the patient; the owner approves it and it is sent. | 21 Jan 09:00 | 0.2 s |
| 11 | `patient_acknowledged` | The patient taps "I understand" on the PWA. | 21 Jan 09:00 | 0.1 s |
| 12 | `handoff_rejected` | The owner asks Al Noor Endoscopy Centre; they decline for capacity. Ownership stays with the referrer. | 21 Jan 09:00 | 0.1 s |
| 13 | `auto_rerouted` | The automation re-requests the next provider in the reroute order, Corniche Gastro Unit (`handoff_requested` from `system:automation`). | 21 Jan 09:00 | 0.1 s |
| 14 | `handoff_bounced` | Corniche never answers; three working days later the scheduler bounces the request. The list is used up (a registration with Riverside would first try Marina Digestive Health, and be bounced again), so the automation flags the case `reroute_exhausted` for a coordinator. | 26 Jan 10:00 | 0.2 s |
| 15 | `reassigned` | The coordinator re-routes the case back to the owner. | 26 Jan 10:00 | 0.1 s |
| 16 | `handoff_accepted` | The owner asks Corniche Gastro Unit again; Dr. Karim Nasser accepts and becomes the owner. | 26 Jan 10:00 | 0.1 s |
| 17 | `patient_reacknowledged` | The new owner phones the patient, who acknowledges again (a re-routed referral goes back through contact). | 26 Jan 10:00 | 0.1 s |
| 18 | `booked` | The colonoscopy is booked, 14 days out; the appointment confirmation is sent by the automation. | 26 Jan 10:00 | 0.1 s |
| 19 | `colonoscopy_done` | The clock advances to the appointment day. The evidence scan looks in HAPI and finds nothing: no closure proposal yet. | 9 Feb 09:00 | 0.2 s |
| 20 | `result_recorded` | The specialist records the colonoscopy: a `Procedure` and `DiagnosticReport` are created in HAPI and the case is told the result exists. | 9 Feb 09:00 | 0.1 s |
| 21 | `closure_proposed` | Six simulated hours later the evidence scan finds the `Procedure` and the automation proposes the closure (`closure_proposed`, evidence prefilled). Nothing is closed. | 9 Feb 15:00 | 0.1 s |
| 22 | `clinician_review` | The clinician reviews and signs as themselves (`Practitioner/<own id>`). | 9 Feb 15:00 | 0.0 s |
| 23 | `closed` | The clinician closes the case with the `Procedure` as evidence. | 9 Feb 15:00 | 0.1 s |

Things that differ from the one-line version of the story in the demo script, because the engine says so:

- **Acceptance stops the ladder** (ADR-014). A supervisor can accept rung 2 only before rung 3 exists, so showing all three rungs means the last one is accepted by its own target, the coordinator. The supervisor's role in the demo is moving the clock.
- **A re-routed referral goes back to `owner_accepted`**, so the new owner contacts the patient and the patient acknowledges again before the booking (steps 17 and 18).
- **The evidence scan does not ask HAPI twice within six simulated hours** about a case it found nothing for (`EVIDENCE_NEGATIVE_TTL`), which is why the proposal comes six hours after the result is recorded, not at once. HAPI's own search cache would hide a new resource for 60 s as well, so `docker-compose.yml` sets `hapi.fhir.reuse_cached_search_results_millis: "-1"` on the `fhir` service.
- **The scans are pinned.** The scheduler container runs the same sweep and scans every few seconds in wall time; every event they write has a deterministic id and the simulated time it was due at, so the script calling them first or second writes the same log. The one place the order of two writers could differ is a clock move that makes a sweep event and an automation event due at once, so the 2-working-day reminder gets its own move (step 5).

To rehearse the live part of the demo, stop the scripted part where the live part begins and carry on in the browser: `make demo-reset && make demo-scenario UNTIL=patient_acknowledged`, then switch roles in the web app (`make web-dev` with `VITE_API_ADAPTER=http`) from there. The web's supervisor role has an "Advance clock (days)" control in the header for the clock moves.

### Determinism (T-E9)

Event ids are uuid5 of the case and the step (the automation's are uuid5 of the case and the event that caused them), every clock move lands on an exact whole hour computed with the same working-day calendar as the sweep, and the scheduler sweep, the automation scan and the evidence scan are called directly. At the end the script prints a hash of the case's event log (type, time, actor, payload; the random draft and notification ids replaced by a placeholder). After `make demo-reset` two runs print the same hash:

```
EVENT LOG HASH 387a2c8f09f111d014d4606919373afc8a13d40d69e9940be99d7178047006ea
```

The script refuses to run unless the clock is at its start and the case is at `acknowledged` (where the automation leaves a new case); run `make demo-reset` first. It also needs the automation on: the scans answer `{"enabled": false}` without `AUTOMATION_ENABLED=true`, and the script says so and stops.

## The agency (A22)

The agency is the layer of agents that prepares work for people (one supervisor, five department heads, fourteen executioners; [docs/governance/agency.md](../../docs/governance/agency.md)). Agents are labour, not authority: the agency is preparation only. They write no case event, they draft messages only through the drafting route (the owner still approves), and the demo story above is unchanged by them.

- **Run it.** `make agency-run` (it logs in as the supervisor, or uses `AGENCY_SERVICE_TOKEN` if that is set in your shell, calls `POST /agency/run` and prints the run summary), then open the `/agency` page in the web app: the org chart, the last run and the inbox of work that needs a person. A second call at the same simulated time returns the same run.
- **Between sweeps: off in the demo stack.** The scheduler hook starts a run on every `AGENCY_EVERY_SWEEPS`-th sweep. The code default is 6, but `docker-compose.yml` sets `AGENCY_EVERY_SWEEPS: ${AGENCY_EVERY_SWEEPS:-0}` for the api: 0 means never, so no agency run fires from the scheduler container and the demo snapshot stays deterministic. To rehearse the scheduled runs: `AGENCY_EVERY_SWEEPS=6 docker compose up -d api` (and back to the default, or `make reset`, before taking a snapshot).
- **The T-E9 hash caveat.** A run drafts messages for cases in the contact phase, and a draft is a `message_drafted` event, so if a run happens while the demo case is in the contact phase the event-log hash printed by `scenario.py` differs from the one above. That cannot happen with the compose default (`AGENCY_EVERY_SWEEPS=0`), which is why `make demo-scenario` and `make test-demo` are hash-exact on a stack started with `make up`; a manual `make agency-run` during a rehearsal adds `message_drafted` events (they carry no text and nobody approves them unless you do), so `make demo-reset` afterwards.
- **Reset.** The task ledger (`agency_tasks`) is in the application database, so `make demo-reset` restores it with the rest: it holds whatever the snapshot held (nothing, if the snapshot was taken before any run). The state fingerprint does not include it.

## The second case

The live demo ends on a case that is ready to be reviewed and closed, so it has to exist before going on stage. `stage_second_case.py` (E9) prepares it through the real API; `make demo-stage` runs it, and `make demo-snapshot` runs it first, so the snapshot contains the staged case and every `make demo-reset` brings it back.

- **Which case.** The second positive FIT in seed order: row 1 of `data/results/manifest.json`, with the uuid5 case id `simulator/create_cases.py` gave it. `scenario.py` drives row 0 (the named patient), so the two never meet, and the scenario's check that its case is at `acknowledged` is unaffected. `python3 tools/demo/stage_second_case.py --show` prints the patient and the case id without touching the API; in the web worklist it is the case whose state reads "Confirmatory completed" and whose owner is Dr. Karim Nasser.
- **What it does**, as the demo logins (the same names as `scenario.py`). The case arrived acknowledged and assigned by the automation, to the default owner of the provider the patient is registered with (Dr. Layla Haddad for this patient, Riverside Primary Care; the script reads the owner from the case). That owner accepts ownership (the automation sends the pre-approved result notice) and hands the case to Corniche Gastro Unit; Dr. Karim Nasser (`specialist_owner`) accepts and records the phone contact; the patient acknowledges; Dr. Nasser books the colonoscopy (appointment `APPT-2026-0002`, 14 days after the simulation clock; the automation sends the confirmation) and records the result: `POST /cases/{id}/confirmatory-result` writes the `Procedure` and `DiagnosticReport` to HAPI, then `complete_confirmatory` carries the `Procedure` as `result_ref`. A last evidence scan finds the `Procedure` and the automation writes `closure_proposed`: the clinician's one-click sign-off has its evidence prefilled in the web (the banner on the case). The scan would do that within seconds anyway (the evidence is in HAPI, whatever the clock says), so it is done here, where the snapshot contains it, and not by the scheduler container after a reset, when the state fingerprint is being checked. The simulation clock is not moved, which `scenario.py` requires of the snapshot.
- **Safe to rerun.** Event ids are uuid5 of the case and the step and the `Procedure` id is deterministic. A case that is part-way (an earlier run stopped) continues from its state; one already at `confirmatory_completed`, or past it (reviewed or closed), is left alone and the script says so. A case in an unexpected state (including `result_received`, which means the automation is off), or at the target with another owner, is an error with the instruction to `make demo-reset`.
- **Exit codes.** 0 staged or already there; 1 the staging failed or the case cannot be staged (the message says why); 2 the API or HAPI is not reachable or ready. It prints a note on stderr if the clock has moved, since a snapshot taken then would not start at the clock's start.

## Snapshot and reset (T-E10)

HAPI stores its data in its own database (`hapi`) next to the application's (`closure`) on the same Postgres server, and `snapshot.sh` copies both together, so the pair of copies is the whole demo state: cases, events, drafts, notifications, the simulation clock and the FHIR resources. `snapshot.sh` makes the copy as a Postgres template copy (`CREATE DATABASE closure_snapshot TEMPLATE closure STRATEGY FILE_COPY`, and the same for `hapi_snapshot`), a file-level copy inside the server: a 3 GB database in 4 to 8 seconds, where `pg_dump` and `pg_restore` take many minutes. The event log is append-only (triggers forbid UPDATE and DELETE), so "undo the demo" can only mean replacing the database.

The API, scheduler, watchdog and HAPI are stopped around the copy (Postgres will not copy a database someone is connected to, the API keeps the clock in memory and rereads it when it starts, and HAPI must not keep caches or preallocated resource ids from before the restore; it is killed rather than stopped since its database is about to be replaced).

`make demo-reset` runs `snapshot.sh reset`: restore, `POST /clock/reset`, then compare the **state fingerprint** with the one stored at snapshot time (`data/demo-snapshot.fingerprint`). The fingerprint has the counts and md5 of cases, events, drafts and notifications, the sim clock, and the number of live FHIR resources per type; heartbeats, alerts and the users directory are left out because they change by themselves. The restore itself deletes the `heartbeats` rows it copied from the snapshot (they are stale by construction), so the watchdog starts from "no heartbeat yet" and does not raise and then clear a false `scheduler_down` in the first seconds after a reset. Measured on the development machine:

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

`POST /cases/{id}/confirmatory-result` (the case owner only, no body; `?completeness=complete|incomplete|ct_colonography`, default complete) creates a synthetic colonoscopy `Procedure` (SNOMED 73761001) and a final `DiagnosticReport` that states how the colonoscopy ended and that the Procedure links to, in HAPI for the case's patient, and returns their references. `close` accepts only a complete colonoscopy or a CT colonography (A24, ADR-036): `python3 tools/demo/create_colonoscopy_report.py --completeness incomplete ...` writes a stopped one, which `close` refuses with a 422 and the evidence scan flags. The ids are deterministic (`Procedure/colonoscopy-<case id>`), so recording twice is idempotent. `evidence_ref` in the answer is the Procedure: it is what `complete_confirmatory.result_ref` and `close.evidence_ref` take, and `close` checks it against HAPI. The web has the "Record colonoscopy result" button on it; `create_colonoscopy_report.py` does the same from the command line.
