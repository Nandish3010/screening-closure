# Plan

Hackathon entry: Future Health Digital Innovation Challenge, Challenge 05 "Closing the Screening-to-Care Loop". Prototype and documentation due 20 November 2026. Companion files: [TRACKER.md](TRACKER.md) (tickets), [TEST-CASES.md](TEST-CASES.md) (what must pass), [DECISIONS.md](DECISIONS.md) (why), [LOG.md](LOG.md) (what happened). The source brief is `/Users/NS/gitex/fhdic-challenge05-plan.md`.

## 1. Goal and positioning

**Positioning (one sentence):** Every actionable screening result has a named owner, a clock, an audit trail, and a clinician-signed closure. Nothing is diagnosed.

The product is a deterministic closure engine. Its core pathway is a positive FIT (faecal immunochemical test) result through a confirmatory colonoscopy to a documented closure (a second pathway, cervical screening, was added later as a configuration pack on the same engine: A37). Cases are tracked as an append-only event log, state is derived by replay, and the state machine refuses any closure that lacks closure evidence and a human sign-off. Timers run on a working-day calendar and an injectable simulation clock, so a 45-day pathway can be shown in minutes. When a clock runs out, the system emits an overdue event and walks an escalation ladder to named humans. It never closes a case itself. AI is used only at the edges: it may draft a patient message, and a human owner must approve it before it is "sent". All data is synthetic and every simulator parameter carries its source or is labelled assumed.

We are building autonomously with AI agents from 17 October until 18 October 22:30 local time (milestones M0 to M5 below). The human owner then continues: clinician review, Arabic review, video, and submission by 18 November (registration closes 10 November; feature freeze 10 November). The design argument we want judges to take away is that safety-critical transitions must be deterministic, replayable and explainable to a regulator, so the model drafts and humans decide, and the architecture makes anything else structurally impossible rather than merely against policy.

### Judging criteria and the one thing we show for each

The six official criteria, as published on the event page. The right-hand column is the one thing we put in front of the judge for each.

| # | Criterion (official) | The one thing we show |
|---|---|---|
| 1 | Health impact and clinical relevance | A published loss-to-follow-up figure for abnormal screening results, then a synthetic positive FIT that today's process would lose, caught, escalated to a named human and closed with a clinician's sign-off live. |
| 2 | Technical depth and architecture | The closure-invariant property test running: thousands of random event sequences over the full event vocabulary, no closure without evidence and a human sign-off, next to the append-only event log that replays to the same state. |
| 3 | Working solution and demonstration quality | The seeded 7-minute scenario run end to end from one command and reset in one command, with the scheduler killed on stage and the watchdog raising the alert, on the running stack with no network. |
| 4 | Responsible AI, safety and data governance | A drafted message that cannot leave until the owner approves it, with its Provenance (prompt, draft, approver, timestamp) on screen next to the automated-vs-human decision matrix, the hazard log and the synthetic-data declaration. |
| 5 | Scalability and deployment readiness | The measured scheduler sweep over 100,000 open cases, the FHIR R4 projection and the Malaffi adapter plan (stated as a target), and a stack that comes up on a fresh clone with one command. |
| 6 | Commercial viability and path to pilot | The 90-day pilot plan (one programme, two or three providers, shadow mode first) with cost per case from a script with a visible inputs file, and `make evaluate` regenerating the baseline-versus-orchestrated table with one parameter changed. |

### Product principles (2026-10-18)

The framing rule: **a council decides; our agency prepares.** People decide and people hold the conversations; automation and the agency do the preparation around both.

1. **People act on decisions and conversations; the system does the rest.** Routine steps (acknowledge, assign, template outreach, reminders, evidence detection, closure proposal, rerouting) are done by the system. A person accepts, approves, signs, and talks to the patient. The phone call after an escalation is the evidence-based intervention, not waste; automation prepares it, a person makes it.
2. **No model and no agent holds authority over a case.** Models are used in four places, drafting patient messages for human approval, ordering the worklist by a calibrated risk score, planning the agency's work queue, and classifying inbound patient replies, plus automatic sending of clinician-pre-approved deterministic templates; no model and no agent can move a case between states, approve a message, change consent or close a case. Models are labour, not authority.
3. **Grow screening without growing coordinators, and measure it honestly.** The commercial claim is capacity plus outcomes. Labour is reported by role, not as one blended number: coordinator time falls, specialist time rises with the outcome, owner-clinician time must not rise unnoticed. The time-and-motion study (pilot KPI K9) replaces assumed minutes with measured ones.

Tickets behind these principles, on their own branches with merge due the evening of 2026-10-18: A21 (automation: every step that is not a human decision), A22 (agency: agents as labour, not authority), B17 (effort: human minutes per case in both arms), D13 (reply triage). Future ticket, not started: **A23**, route decisions to the cheapest competent role; escalation batching; agency call briefs credited. A23 is blocked on H1 (clinician review), because routing a decision away from the owner clinician is a clinical-governance choice.

## 2. Six moats

Each is something that is hard to copy in a week and visible in the demo.

1. **Proven closure invariant.** A property test suite that tries thousands of random event sequences and proves no case ever reaches `closed` without closure evidence and a human sign-off. Show the test running in the demo.
2. **Silent-failure watchdog.** The dangerous failure in this domain is the absence of events, not a bad event. A watchdog flags any open case with no activity for N days, and separately flags the scheduler itself if it stops producing heartbeats. Demo it live by killing the scheduler.
3. **Judge-runnable evaluation.** `make evaluate` regenerates every number in the documentation from the seeded simulator in under two minutes. Judges can change a parameter and rerun.
4. **FHIR-native with a named integration target.** Every object is a real FHIR R4 resource. The docs contain a one-page adapter plan for Malaffi, Abu Dhabi's health information exchange, stated as a target, not a claim.
5. **Arabic-first patient experience.** Right-to-left layout, written at roughly grade-6 reading level, with an "I don't understand this" button that routes to a human. The native-speaker review (H2) is pending, so the Arabic text is unreviewed.
6. **Governance artefacts judges can hold.** Automated-vs-human decision matrix, hazard log, automation abstention rate, fairness split by language and channel, 90-day pilot plan with cost per case.

Which tickets carry which moat: 1 is already built (T-A1) and re-proved in A2; 2 is A11 and F4; 3 is B1 to B10; 4 is A12 and E3; 5 is C5 to C7; 6 is D10, D11, E5 to E7 and B8.

## 3. Cuts

Six independent workstreams. Each cut owns its directories and touches nothing else except through the contracts in section 6. "Exposes" is what other cuts may rely on. Tickets for each cut are in TRACKER.md; the test cases are in TEST-CASES.md.

### Cut A, Core loop

**Scope.**
- Scheduler SLA evaluation on a working-day calendar. UAE default: Monday to Friday are working days, Saturday and Sunday are the weekend; the weekend set (and an optional holiday list) is configurable.
- Overdue and escalation events per the pathway's escalation ladder (reminder at breach, supervisor after 3 days, programme coordinator after 7 days, as in `config/pathways/colorectal_fit.yaml`). Every rung needs a human to accept. The scheduler never closes a case.
- Ownership transfer and the referral handoff contract: the receiving provider must explicitly accept; rejection carries a reason from a fixed taxonomy (capacity, wrong service, missing info, patient declined); an unanswered referral bounces back on a timer and every bounce is logged.
- Consent ledger for contact channel and data sharing. Withdrawal freezes outreach and flags the case for human review.
- Notification mock: SMS and WhatsApp-style messages written to a log, never sent.
- RBAC with six roles: `lab`, `primary_care_owner`, `specialist_owner`, `coordinator`, `patient`, `supervisor`.
- Watchdog idle-case detection, and a silent-failure heartbeat so a dead scheduler is itself flagged.
- Read routes, closure metrics, and the FHIR projection of cases, referrals, appointments, messages, consent and audit entries.

**Owning directories.** `api/`, `scheduler/`, `watchdog/`, `config/`.

**Depends on.** The M0 event store and state machine (`api/app/events.py`, `state_machine.py`, `store_pg.py`), the pathway config, HAPI FHIR for the projection. No other cut.

**Exposes.** The HTTP routes, event types and roles in section 6; the OpenAPI schema; `api/app/metrics.py` (pure metric functions, also used by the simulator so the dashboard and the evaluation cannot disagree); the working-day calendar, escalation ladder, handoff and consent rules as importable modules (used in process by the simulator); the notification log; heartbeats and watchdog flags.

**Definition of done.** Tickets A1 to A12 are `done`. A seeded case can be driven from `result_received` to `closed` through overdue, escalation, a rejected-and-rerouted referral and a consent change, by API only, with the full timeline readable. Killing the scheduler container raises a watchdog alert within 60 seconds and restarting it loses no events. T-A1 to T-A33 pass and `make check` is green.

### Cut B, Simulator and evaluation

**Scope.**
- A behaviour simulator with literature-sourced parameters. Every parameter records value, source URL and status (`assumed` or `sourced`).
- Messy-case injection: wrong contact, provider switch, duplicate result, conflicting acknowledgements, consent withdrawn.
- A baseline arm (today's process: no owner, no clock, no escalation) compared with the orchestrated arm on the same simulated patients.
- Metrics: time-to-ownership, time-to-safe-closure, share of cases open at 30, 60 and 90 days, reopen rate, escalation acted-on rate (within 5 working days), automation abstention rate, and a fairness split by language and contact channel.
- Bootstrap confidence intervals over seeds.
- `make evaluate` writing `docs/results/*.md` and `*.json`.

**Owning directories.** `simulator/`, `docs/results/`.

**Depends on.** Cut A's rules as importable modules and the metric functions in `api/app/metrics.py`; the cohort manifest from M0 (`data/results/manifest.json`); the evidence register from Cut E (E1) for sourced values.

**Exposes.** `make evaluate`; `docs/results/*.json` and `*.md` (the only place documented numbers may come from); the generated parameter table; simulator features and labels used to train the risk score in Cut D.

**Definition of done.** Tickets B1 to B11 are `done`. A 90-day run of 2,000 cases completes in under a minute and the whole `make evaluate` in under two. Running it twice gives byte-identical JSON. Every parameter is `sourced` with a working URL or is honestly `assumed`. No case closes without evidence in either arm. T-B1 to T-B15 pass.

### Cut C, Interfaces

**Scope.**
- Coordinator web app: worklist ordered by SLA breach, then risk score; case timeline; accept, hand off, escalate and sign-off actions; draft review and approval.
- Patient PWA in Arabic and English with right-to-left layout and grade-6 reading level: what the result means, what happens next, who owns it, one-tap acknowledge, consent controls, and an "I don't understand this" button that creates a human callback task.
- Closure dashboard: P50 and P90 time to closure, open cases by age bucket, breaches by provider, bottleneck by transition, a permanent "SIMULATED TIME" badge, and the "cases nobody is looking at" panel from the watchdog.

**Owning directories.** `web/`.

**Depends on.** Cut A's routes and OpenAPI schema (`GET /me`, `GET /cases`, `GET /cases/{id}/timeline`, `POST /cases/{id}/events`, `GET /metrics/closure`); Cut D's draft and approve routes; the risk score field when D9 lands (SLA-only ordering until then).

**Exposes.** The three apps; the patient string catalogue and its reviewer export (used by Cut E for the Arabic review); `make test-web` and `make test-e2e`.

**Definition of done.** Tickets C1 to C11 are `done`. A stranger can open the app, find an overdue case, see why it is overdue, escalate it, approve a drafted message, and sign off a closure, in Arabic or English (T-C14 passes in both languages). Every page shows the SIMULATED TIME badge. T-C1 to T-C16 pass.

### Cut D, AI edge and governance

**Scope.**
- LLM message drafting behind owner approval, with a Provenance record (prompt, draft, approver, timestamp).
- Deterministic template fallback, output validation, and cached outputs so the demo runs offline.
- Provider switch `LLM_PROVIDER=template|azure_openai|jais`; `template` is the default and needs no network.
- One simple calibrated loss-to-follow-up risk score (logistic regression on simulator features: stage, days since result, prior escalations, prior referral bounces, consent withdrawn, contact valid, provider changed, preferred channel and age band, the last kept as the documented null feature; language is excluded because it is a proxy for nationality, D12; trained on simulator output only) used only to order the worklist, with a reliability plot and a risk score card. It never closes, escalates or decides care.
- Governance write-ups that belong with the code.

**Owning directories.** `api/app/ai/`, `docs/governance/`.

**Depends on.** Cut A's events, RBAC, notification mock and Provenance mapping; Cut B's simulator features for the risk score (D8 waits for the orchestrated arm).

**Exposes.** `POST /cases/{id}/drafts`, `POST /drafts/{id}/approve`; the `risk_score` field on the worklist; abstention counts for the metrics; `make risk-report`; the risk score card and AI use statement in `docs/governance/`.

**Definition of done.** Tickets D1 to D11 are `done`. No message can reach `message_sent` without an owner's approval (T-D1 is a property test), a bad or missing model always yields the template, every draft has its Provenance, and the demo scenario runs with the network off. The reliability plot is committed with its data. T-D1 to T-D15 pass.

### Cut E, Evidence, docs, demo

**Scope.**
- Public datasets and literature for the parameters (`docs/evidence.md`).
- Data provenance, design choices, the automated-versus-human decision matrix, hazard log, assumptions and limitations.
- 90-day pilot plan with cost per case.
- Governance mapping (UAE PDPL, ADHICS, in-country hosting) and the one-page Malaffi adapter plan, stated as a target.
- 7-minute demo script with a scripted seeded scenario and one-click snapshot reset; backup video storyboard.
- Technical documentation assembled from these, and the README.

**Owning directories.** `docs/`, `tools/demo/`.

**Depends on.** Results from Cut B for every number; behaviour of Cuts A, C and D for the matrix, hazard log and demo; test IDs from TEST-CASES.md for hazard controls.

**Exposes.** The documents judges read; `make docs-check`, `make demo-scenario`, `make demo-reset`; the reviewer packs for the clinician and the Arabic reviewer.

**Definition of done.** Tickets E1 to E12 are `done`. No `TODO` remains in `docs/` at freeze, every quoted number traces to `docs/results`, no other team is named, and the demo scenario is deterministic and resettable in one command. T-E1 to T-E12, T-E15, T-E16 and T-E17 pass. The clinician and Arabic reviews (T-E13, T-E14) are human-owner tasks.

### Cut F, Hardening and reproducibility

**Scope.**
- Failure injection: scheduler crash, duplicate events, clock skew, bad LLM output, consent withdrawn mid-transition. The system must never silently drop or auto-close a case.
- Load test: a sweep over 100,000 open cases.
- Security pass: RBAC tests, no PHI in logs or URLs, secrets out of the repo.
- `make check` running everything.
- Fresh-clone verification on a second machine.
- A separate test database.

**Owning directories.** `tests/`, `tools/` (except `tools/demo/`, which Cut E owns), `Makefile`.

**Depends on.** Every other cut: it tests their interfaces and may only change their code through a ticket for the owning cut.

**Exposes.** `make check`, `make test-chaos`, `make test-load`, `make test-security`, `make test-offline`, `make demo-kill-scheduler`; recorded load numbers in `docs/results`.

**Definition of done.** Tickets F1 to F12 are `done`. `make check` is green on a fresh checkout and fails when any suite fails; the fresh-clone run of `make up && make demo-seed && make evaluate` has been done on a second machine (H5). T-F1 to T-F16 pass.

## 4. Milestones

Times are local (IST). The tickets in each milestone are tagged in the Notes column of TRACKER.md.

| Milestone | Target | Scope | Exit criterion |
|---|---|---|---|
| M0 | 17 Oct, done | Scaffold, persistence, cohort | Commits `4ab4af3`, `86fca2d`, `9b635af`, `971c3f1`. Closure property test, Postgres append-only log, `make demo-seed`. |
| M1 | 18 Oct 04:00 | Core loop (A1 to A11, F1, F2) | Overdue, escalation, handoff, consent, RBAC, watchdog and heartbeat work through the API; `make check` exists. |
| M2 | 18 Oct 10:00 | Simulator and evaluate (B1 to B11, E1, A12, F3, F4, F6, F9) | `make evaluate` writes docs/results twice identically in under two minutes; evidence register exists; FHIR projection lands. |
| M3 | 18 Oct 16:00 | Interfaces (C1 to C9, D1 to D5) | The three apps work against the API in English and Arabic; the draft and approve routes exist. |
| M4 | 18 Oct 18:00 | AI edge and governance (C10, C11, D6 to D11, F5, F7, F8) | Draft approval in the UI; risk score and reliability plot; end-to-end test passes in both languages. |
| M5 | 18 Oct 22:30 | Hardening, docs, demo (E2 to E12, F10 to F12) | `make check` green; docs complete except human reviews; demo scenario and reset work offline. |

**Human-owner phase (from 19 October).**

| When | What |
|---|---|
| By 25 Oct | Book the clinician review and the native Arabic speaker (H1, H2). |
| 1 to 7 Nov | Hold both reviews; apply feedback; confirm registration and have the submission form open by 7 Nov. |
| 10 Nov | Registration closes. Feature freeze. No new screens after this date. |
| 8 to 14 Nov | Fresh-clone run on a second machine, demo rehearsals (ten, timed), record the backup demo video and the 3-minute submission video (H3 to H5). |
| By 18 Nov | Submit (H6). Re-check the form against the requirements shared at registration. |
| 19 to 20 Nov | Buffer. Fix only what blocks submission. Prototype and documentation due 20 Nov. |
| 20 to 30 Nov | Evaluation period. |
| 7 to 11 Dec | Finals at GITEX Dubai, if shortlisted. |

## 5. How we proceed

1. **One agent per ticket.** An agent picks a ticket from TRACKER.md whose dependencies are all `done`, sets it to `doing`, and stays inside its cut's directories.
2. **Tickets come from TRACKER.md.** Nothing is built that is not a ticket. A new ticket is appended with the next free ID and a note saying why.
3. **Each ticket ends in a commit that starts with its ID** (for example `A3: scheduler SLA sweep`). A small follow-up commit updates the tracker row and LOG.md with the first commit's hash.
4. **Parallel cuts run in separate git worktrees**, one branch per cut (for example `sc-cut-b` on branch `cut-b`, `sc-cut-c` on branch `cut-c`, siblings of the main checkout). A cut uses another cut's interface only after that cut is merged to main, or against the contract in section 6.
5. **Merges happen at milestone boundaries**, cut by cut, in dependency order (A first). Every merge runs `make check`; a red `make check` blocks the merge.
6. **The tracker and LOG are updated after every ticket.** Each agent appends one line to LOG.md when it finishes a ticket.
7. **A ticket is `done` only when its test cases in TEST-CASES.md pass.** "Looks right" is not done.
8. **Contracts change by editing section 6 in the same commit** that changes the code, so that other cuts see it at the next merge.
9. **Cut lines from the brief apply if time runs short.** Protect moats 1, 2 and 3 first (closure invariant, watchdog, `make evaluate`). Cut in this order: the mammogram stretch (config file only; a second pathway was built later as cervical screening, A37, see docs/governance/second-pathway.md); the regulator rung of the escalation ladder and duplicate-result deduplication; FHIR `Appointment` and `Communication` mapping (stub them); the risk score (keep SLA-based ordering); polish on the dashboard. Never cut: the closure guard, the human approval gate for messages, the synthetic-data declaration, the SIMULATED TIME badge.
10. **No real patient data, no real model calls in tests, no secrets in the repo.** `LLM_PROVIDER` defaults to `template`.

## 6. Integration contracts

Everything below is a working contract for Cuts C and D. "Exists" means in the code today (`api/app/routers/cases.py`, `api/app/main.py`); "planned" names the ticket that adds it. Shapes marked "proposed" are the starting point and are finalised, and this section updated, by the owning ticket. The event vocabulary below is final (A2).

### Routes

| Route | Status | Request | Response |
|---|---|---|---|
| `GET /health` | exists | none | `{status, sim_time}` |
| `GET /pathways` | exists | none | the pathway config as parsed from YAML (`?name=` picks another loaded pathway; A37) |
| `POST /cases` | exists | `{case_id?, patient_ref?, result_ref?, contact_valid?, pathway?}` (`pathway`: the name of a loaded pathway, default the first one loaded, bowel screening; a row field, never an event payload; 422 for an unknown name, A37) (`contact_valid`: `false` = no valid contact details, or `{"sms": false}` per channel; kept in the genesis payload for the notification mock) | 201 with the case: `{case_id, state, read_model, events[]}`; 409 `CaseAlreadyExists` |
| `GET /cases?state&owner&overdue&provider&flagged` | exists (A9) | query filters, all optional: `state`; `owner` (an actor string, a bare `user_id` or a display name); `overdue` (`true`/`false`); `provider`; `flagged` (`true`: has an open watchdog flag) | list of worklist rows, oldest case first (genesis time, then case id): `{case_id, pathway, state, created_at, updated_at, patient_ref, owner: {actor, display_name, role} \| null, provider, due_at, overdue, escalation_level, consent_frozen, last_event_at, watchdog_flagged: {reason, idle_days?, at} \| null, pending_handoff, risk_score: null}`. `due_at` is the current state's SLA expiry in sim time (the breach's due date once overdue; null where no SLA applies: closed, consent withdrawn); `risk_score` stays null until D9. Every role may call it: staff see all cases, a patient only their own (matched by `patient_ref`) |
| `GET /cases/{id}` | exists | none | `{case_id, state, read_model, events[], actors}` plus the worklist fields of the row above (`owner` resolved, `due_at`, `provider`, ...); `actors` maps every actor in the log to `{actor, display_name, role}`; 404 if unknown |
| `POST /auth/dev-login` | exists (A8) | `{role, name, patient_ref?, provider?}` (patient_ref only for role `patient`; provider only for `primary_care_owner` and `specialist_owner`, default: the name) | `{token, token_type: "bearer", user_id, role, display_name, patient_ref?, provider?}`; `user_id` is an opaque 12-hex slug (A14); the name is stored only in the `users` table; 403 for an automated role name, 422 for anything else invalid; 404 when `DEV_LOGIN_ENABLED=false` |
| `POST /cases/{id}/events` | exists | `{type, payload, at?, event_id?}` (no `actor`: 422 if present) | 201 with the case (same shape as `GET /cases/{id}`); 404; 409 for an invalid transition or an `event_id` used on another case |
| `POST /cases/{id}/confirmatory-result` | exists (E8) | none (owner of the case only) | 201 `{case_id, evidence_ref, procedure_ref, report_ref, written, recorded_at}`. Creates a synthetic colonoscopy `Procedure` and `DiagnosticReport` in HAPI (`Procedure/colonoscopy-<case_id>`, idempotent) and returns their references; `evidence_ref` is the Procedure, what `complete_confirmatory.result_ref` and `close.evidence_ref` take. primary_care_owner / specialist_owner who own the case (403 otherwise); 409 before the colonoscopy is booked; 503 when HAPI is down. Appends no event |
| `GET /me` | exists (A8) | bearer token | `{user_id, role, display_name, patient_ref?, provider?}`; `user_id` is the opaque slug (uuid5 of role + name, short form, A14), and the actor recorded for this user is `role:user_id` |
| `GET /cases/{id}/notifications` | exists (A7) | none | list, oldest first, of `{id, case_id, channel, language, purpose, body, status, reason, event_id, created_at}`; `status` is `queued`, `sent`, `blocked_consent` or `blocked_contact`; `event_id` is the `message_sent` event (null unless `sent`). primary_care_owner / specialist_owner (their own case), coordinator, supervisor; 404 if unknown |
| `GET /cases/{id}/timeline` | exists (A9) | none | `{case_id, sim_time, items[], actors}`: one list ordered by `at` (ties: the source event's position). `items[].kind` is `event` (the raw log row: `seq, event_id, type, at, actor, payload`), `sla_started` (`at` = clock start; `state, sla, working_days, due_at, clock_start`), `sla_breached` (`at` = due date; `detected_at, sla, state, due_at`), `escalation` (`rung, level, target_role, after_breach_days, accepted, accepted_at, accepted_by`), `handoff_bounce` (`to_provider, returned_to, requested_at, reason`) or `watchdog_flag` (`reason, idle_days, cleared_at`). Derived items are computed from the log, never stored; the path has no write method. Same access as `GET /cases/{id}` (patients: their own case, else 404) |
| `GET /cases/{id}/fhir` | exists (A12) | none | `{case_id, projection_enabled, fhir_base_url, events_waiting, task, consent, service_requests[], appointments[], communications[], provenance[], practitioners[], devices[]}`: the ids (`Type/id`) of the FHIR resources the projection writes for the case, computed from the log, and how many events are still in the retry queue. primary_care_owner / specialist_owner (their own case), coordinator, supervisor; 404 if unknown. The projection itself is described in ADR-024 |
| `GET /metrics/closure` | exists (A10) | none | coordinator, supervisor. `{sim_time, cases_total, cases_open, cases_closed, closures_measured, unsafe_closures, p50_days, p90_days, p50_working_days, p90_working_days, open_by_age_bucket: {"0-30","31-60","61-90","90+"}, breaches_by_provider: [{provider, count}], bottleneck_by_transition: [{transition: "a -> b", median_days, median_working_days, n}], lost_to_follow_up, lost_never_surfaced, cases_flagged, abstention: {attempts, abstentions, rate} \| null}`. Percentiles are over the first safe `close` of each closed case (calendar days from `case_created`; working days from the calendar), linear interpolation at q x (n - 1), null with no closures. `open_by_age_bucket` counts cases not currently closed by whole days since the result (90+ = more than 90). `abstention` is read from the AI cut's `drafts` table and is null when that table does not exist. Definitions follow docs/results/metrics.md where they overlap; the pure functions are in `api/app/metrics.py` (ADR-022) |
| `POST /cases/{id}/drafts` | planned (D4) | `{purpose, language, channel}` (proposed) | `{draft_id, case_id, text, provider, used_fallback, status, provenance_ref}` |
| `POST /drafts/{id}/approve` | planned (D5) | none | `{draft_id, status, approved_by, approved_at}` (proposed) |
| `POST /clock/advance` | exists (A3) | `{days?, hours?}`, each >= 0, at least one > 0 (else 422); the clock never moves backwards | `{sim_time}`; supervisor only; persisted in the `sim_clock` table |
| `POST /clock/reset` | exists (A3) | none | `{sim_time}` (the configured start); supervisor only |
| `POST /scheduler/sweep` | exists (A3) | none | `{sim_time, cases_evaluated, cases_skipped, mark_overdue, escalate, handoff_bounced, rejected, errors[]}` (`cases_skipped`: open cases not read because their `next_due_at` is in the future, A20); the scheduler service token only |
| `POST /scheduler/heartbeat` | exists (A11) | `{sweep_ok, consecutive_failures?, interval_seconds?, sim_time?}` (no other keys) | `{ok: true}`; the scheduler service token only. Posted on every scheduler loop, also after a failed sweep. Stored in the `heartbeats` table (migration 005) with the arrival time; not a case event (ADR-023) |
| `POST /watchdog/scan` | exists (A11) | `{uptime_seconds?, idle_days?}` | `{scan: {sim_time, idle_days, cases_evaluated, flagged, rejected, errors[]}, scheduler: {status: ok\|warming\|failing\|down, ...}, alerts_open[]}`; the watchdog service token only. Records the watchdog's own heartbeat, flags idle cases on the sim clock (`watchdog_flagged {reason: idle, idle_days, last_activity_at}`, once per idle episode) and opens or clears the `scheduler_down` / `scheduler_failing` alert from the scheduler's heartbeat age (older than 60 s, or missing once the watchdog has been up for 60 s) |
| `GET /heartbeats` | exists (A11) | none | `{scheduler: {service, last_beat_at, age_seconds, sweep_ok, consecutive_failures, interval_seconds, sim_time} \| null, watchdog: {service, last_beat_at, age_seconds, uptime_seconds} \| null}`; the age is computed by the database (wall clock). coordinator, supervisor (the watchdog token may read it) |
| `GET /alerts?active=` | exists (A11) | optional `active=true` | list, newest first, of `{id, kind, since, cleared_at, detail}`; `cleared_at` is null while open; kinds `scheduler_down`, `scheduler_failing`, and a derived `watchdog_down` (`derived: true`) when the watchdog's own heartbeat is older than 60 s. coordinator, supervisor (the watchdog token may read it). `since` and `cleared_at` are wall-clock times |

**CORS (E8).** The web app is served from its own local port, so the API allows the browser origins in `CORS_ALLOW_ORIGINS` (default: `http://localhost` and `http://127.0.0.1` on 5173, 4173 and 4175; never a wildcard) with the `Authorization` and `Content-Type` headers and the methods GET, POST and OPTIONS.

Event shape (all routes): `{seq, event_id, type, at, actor, payload}`. `event_id` is the idempotency key: a retry with the same id inserts once. `at` defaults to the sim clock, never the wall clock, and may not be later than the sim clock (422); backdating is allowed. `actor` is derived from the caller's identity (`role:user_id` with an opaque `user_id`, A14, ADR-019; or `system:scheduler` for the scheduler service) and a client-supplied `actor` is rejected with 422. Errors are JSON with a `detail` string.

Consent changes, callbacks and flags use `POST /cases/{id}/events`, not separate routes.

**Authentication and authorisation (A8, ADR-012; the note for Cut C).**
- Every route except `GET /health` and `POST /auth/dev-login` needs `Authorization: Bearer <token>`. Missing or invalid token: 401. Valid token, wrong role: 403 and nothing is written. A route that has no entry in `api/app/policy.py` is refused for everyone (403).
- Web login flow: `POST /auth/dev-login {role, name}` (patients also send `patient_ref`, the `Patient/<id>` whose case they own), keep the returned `token` in memory, send it as the bearer header, call `GET /me` for the role to drive what the UI shows. Tokens do not expire in development. Logging in as `system`, `scheduler`, `watchdog` or `model` is refused: automated actors cannot authenticate as users.
- Per event type, the roles that may post it are in `api/app/policy.py` (`EVENT_POLICY`) and summarised below. The UI should hide actions the role cannot take, but the API is the authority; the state machine can still answer 409 for an allowed role in the wrong state.
- Patients see only their own case: `GET /cases/{id}` and `POST /cases/{id}/events` return 404 for any case whose genesis `patient_ref` is not theirs (indistinguishable from an unknown case). `GET /cases` is staff-only until A9 adds the patient's own list.

### Event types

Final as of A2 (`config/pathways/colorectal_fit.yaml`, `api/app/state_machine.py`). "State change" rows are transitions; "annotation" rows are accepted in any open state (never in `closed`) and leave the state alone. Automated actors (`system:`, `scheduler`, `watchdog`, `model:`) can never emit a human-only event; the emitter column says who is expected to, and A8 enforces it per route.

| Type | Status | Emitted by | Effect |
|---|---|---|---|
| `case_created` | exists | system intake | genesis marker; starts the first SLA clock; no state change |
| `acknowledge` | exists | lab | state change: `result_received` to `acknowledged` |
| `accept_ownership` | exists | primary_care_owner | state change: `acknowledged` to `owner_accepted`; `owner` becomes the actor |
| `contact_patient` | exists | primary_care_owner | state change: `owner_accepted` to `patient_contacted`, or `lost_contact` back to `patient_contacted`. Refused while consent is withdrawn |
| `patient_acknowledge` | exists | patient | state change: `patient_contacted` to `patient_acknowledged` |
| `book_confirmatory` | exists | specialist_owner or primary_care_owner | state change: `patient_acknowledged` to `confirmatory_booked` |
| `complete_confirmatory` | exists | specialist_owner | state change: `confirmatory_booked` to `confirmatory_completed` |
| `clinician_review` | exists | owner role acting as the clinician | state change: `confirmatory_completed` to `clinician_reviewed`, or `reopened` to `clinician_reviewed` |
| `close` | exists | a human clinician | state change: `clinician_reviewed` to `closed`; requires `evidence_ref` and `signed_by`; automated actors refused |
| `reopen` | A2 | a human clinician | state change: `closed` to `reopened`; requires `reason`; human only. The case can only reach `closed` again through `clinician_review` and a fresh `close` |
| `mark_overdue` | A3 (emitted by the sweep) | scheduler | annotation: sets `overdue` for the current state. Payload `{state, due_at}`; refused unless `state` is the case's state, that state has an SLA, `due_at` is in the past on the event's `at`, and the case is not already overdue |
| `escalate` | A4 (emitted by the sweep) | scheduler; or coordinator/supervisor by hand | annotation: raises `escalation_level` by exactly one. The sweep's payload is `{rung, level, target, action, state, due_at, rung_due_at}`; `rung` is `reminder` (target `owner`, at breach+0), `supervisor` (+3 working days) or `coordinator` (+7). Refused unless the case is overdue, rungs are in ladder order (never skipped or repeated), and no human has accepted the latest rung |
| `escalation_accepted` | A4 | a human whose role is in the rung's `accepted_by` (reminder: owner roles; supervisor rung: supervisor; coordinator rung: coordinator) | annotation: sets `escalation_accepted`; stops further rungs for this breach (ADR-014). Optional payload `{rung}` must name the latest rung. Human only; changes no ownership or state |
| `handoff_requested` | A5 | current owner (or supervisor on their behalf) | annotation: payload `{to_provider}` (non-empty string). Needs an owner and no other request pending. Sets `pending_handoff`; the owner does not change until acceptance. Refused while consent is withdrawn (A6) |
| `handoff_accepted` | A5 | a clinician at the provider the request names | annotation: needs a pending request; `owner` becomes the actor, `provider` the request's `to_provider` (or the payload's optional `provider`), `pending_handoff` clears. The requester or the owner it came from cannot accept it. From `referral_rejected` it is the re-route: state change to `owner_accepted`. Human only |
| `handoff_rejected` | A5 | receiving clinician, or the scheduler on a bounce | needs a pending request. `reason` is one of `capacity`, `wrong_service`, `missing_info`, `patient_declined` (anything else is 422); the one other value is `timeout`, reserved for the scheduler's bounce (`system:scheduler` with `bounced: true`). From `patient_acknowledged` or `confirmatory_booked`: state change to `referral_rejected` (ownership stays with the referrer, who must re-route within 2 working days or the ladder starts). From any other open state: annotation that clears the request and leaves the state alone. Sets `last_handoff` |
| `reassign` | A2 | supervisor or coordinator | state change: `referral_rejected` to `owner_accepted`; requires `owner`, which becomes the new owner; optional `provider`; clears any pending request |
| `consent_updated` | A6 | patient (own case), or coordinator recording the patient's instruction | payload: any subset of the booleans `channel_sms`, `channel_whatsapp`, `channel_phone`, `channel_letter`, `share_with_specialist`, `withdrawn` (at least one; a non-boolean or an empty payload is 422). Each event is appended to the case's consent history and merged into `consent`. `withdrawn: true` from any open state: state change to `consent_withdrawn` (outreach frozen, prior state remembered) and the API then adds `review_requested`. `withdrawn: false` from `consent_withdrawn`: state change back to the prior state. Anything else: annotation. Human only |
| `mark_lost_contact` | A2 | owner, coordinator or supervisor | state change: `patient_contacted` to `lost_contact`; requires `failed_attempts` of at least 3 |
| `review_requested` | A6 | `system:consent` (the API itself; no route can post it) | annotation: payload `{reason: "consent_withdrawn", source_event_id}`. Sets `review_reason` / `review_requested_at` in the read model so the case is on the human-review list; cleared when consent is restored. `system:consent` may emit this and nothing else |
| `message_drafted` | A2 defines, D4 uses | model or template actor | annotation. Refused while consent is withdrawn |
| `message_approved` | A2 defines, D5 uses | owner role | annotation; human only. Refused while consent is withdrawn |
| `message_sent` | A2 defines, A7 writes | `system:notifier`, only through `app.notify.send` after approval | annotation. Refused while consent is withdrawn |
| `callback_requested` | A2 defines, C6 uses | patient | annotation: creates a human callback task; human only |
| `watchdog_flagged` | A11 | the watchdog service (`system:watchdog`), via `POST /watchdog/scan` or the events route with its own token | annotation: payload `{reason: idle, idle_days, last_activity_at}`; sets the read model's `watchdog_flag` and the case appears in "cases nobody is looking at" (`GET /cases?flagged=true`). Written once per idle episode (uuid5 of case and last real event), cleared by any later event. A case that is closed never gets one |

**Payloads (A14, ADR-019; authority `api/app/payloads.py`).** The event log is immutable, so free text must not enter it. Every event type has a strict schema: a key that is not listed is rejected with 422 (nothing is written), string values must have the shape of an id or a code (not prose), `reason` fields take only their taxonomy, and the only free text is `note`, **at most 200 characters, on the events a person posts**. Staff must not put patient identifiers, names or clinical detail in a note (the log cannot be erased); a note is for a short operational remark such as "called twice, no answer". Display names live in the `users` table, draft text in the `drafts` table and message bodies in the `notifications` table; the log holds only their ids and hashes. In particular `message_drafted` carries `draft_id, provider, fallback_used, prompt_hash, language` and no text; the web reads the text with `GET /cases/{id}/drafts` (the drafts router belongs to the AI cut, D4, and is not in main yet; until it lands the UI shows "draft text unavailable").

| Event type | Allowed payload keys (`?` = optional) | `note` |
|---|---|---|
| `case_created` | `patient_ref?`, `result_ref?`, `contact_valid?`, `pathway?` | no |
| `acknowledge` | (none) | yes |
| `accept_ownership` | `provider?` | yes |
| `contact_patient` | `channel?`, `draft_id?` | yes |
| `patient_acknowledge` | `lang?` | yes |
| `book_confirmatory` | `appointment_ref?` | yes |
| `complete_confirmatory` | `result_ref?` | yes |
| `clinician_review` | `signed_by?` | yes |
| `close` | `evidence_ref`, `signed_by` | yes |
| `reopen` | `reason` | yes |
| `mark_overdue` | `state`, `due_at`, `sla_event?`, `sla_working_days?` | no |
| `escalate` | `rung`, `state`, `level?`, `target?`, `action?`, `due_at?`, `rung_due_at?` | yes |
| `escalation_accepted` | `rung?` | yes |
| `handoff_requested` | `to_provider` | yes |
| `handoff_accepted` | `provider?` | yes |
| `handoff_rejected` | `reason`, `bounced?`, `to_provider?`, `returned_to?`, `requested_at?`, `due_at?`, `working_days?` | yes |
| `reassign` | `owner`, `provider?` | yes |
| `consent_updated` | `channel_sms?`, `channel_whatsapp?`, `channel_phone?`, `channel_letter?`, `share_with_specialist?`, `withdrawn?` | yes |
| `mark_lost_contact` | `failed_attempts` | yes |
| `review_requested` | `reason`, `source_event_id` | no |
| `message_drafted` | `draft_id`, `provider`, `fallback_used`, `prompt_hash`, `language` | no |
| `message_approved` | `draft_id` | yes |
| `message_sent` | `notification_id`, `channel`, `language`, `purpose`, `draft_id?` | no |
| `callback_requested` | `reason?`, `lang?` | yes |
| `watchdog_flagged` | `reason`, `idle_days?`, `last_activity_at?` | no |

Taxonomies for `reason`: `reopen` takes `new_finding`, `evidence_inadequate`, `closed_in_error`, `patient_request`, `other`; `callback_requested` takes `patient_does_not_understand`, `wants_to_speak_to_clinician`, `cannot_attend`, `other`; `handoff_rejected` takes the pathway's four human reasons or the scheduler-only `timeout`; `watchdog_flagged` takes `idle`, `scheduler_down`; `review_requested` takes `consent_withdrawn`. `reassign.owner` and `handoff_rejected.returned_to` are actor strings (`role:user_id`). The in-process emitters (sweep, consent, notification mock) validate against the same schemas before they append. The schema is enforced at the API boundary and in those emitters; the pure state machine and the simulator are not bound by it.

**Closure evidence (A15, ADR-021).** `close` needs `evidence_ref` = `DiagnosticReport/<id>` or `Procedure/<id>` (shape checked always; 422 otherwise) and, when `FHIR_VERIFY_EVIDENCE=true` (set in compose), the resource must exist in HAPI (422 if not, 503 if HAPI cannot be asked, nothing written). `signed_by` on `close` (and on `clinician_review` when given) must be the caller's own actor (`role:user_id`) or `Practitioner/<user_id>`, the Practitioner the FHIR projection creates for them; anything else is 403.

Heartbeats from the scheduler (and the watchdog) are not case events: they are rows in the `heartbeats` table, and a dead scheduler is a row in `system_alerts` (`GET /alerts`), because there is no case to put it on (A11, ADR-023).

### Read model

Every case route that returns a case includes `read_model`, derived by replaying the events (never stored separately, ADR-002):

```json
{
  "state": "patient_contacted",
  "overdue": true,
  "escalation_level": 1,
  "escalation_accepted": false,
  "owner": "primary_care_owner:dr_k",
  "consent_frozen": false,
  "last_event_at": "2026-01-19T08:00:00+00:00",
  "last_activity_at": "2026-01-19T08:00:00+00:00",
  "watchdog_flag": null,
  "resume_state": null,
  "clock_started_at": "2026-01-08T08:00:00+00:00",
  "breach_due_at": "2026-01-19T08:00:00+00:00",
  "referral_accepted_at": null,
  "consent": {"channel_sms": true, "channel_whatsapp": true, "channel_phone": true, "channel_letter": true, "share_with_specialist": true, "withdrawn": false, "updated_at": null, "updated_by": null},
  "consent_history": [{"at": "2026-01-06T09:00:00+00:00", "actor": "patient:pat", "changes": {"channel_sms": false}}],
  "review_reason": null,
  "review_requested_at": null,
  "provider": "endoscopy-unit-1",
  "pending_handoff": null,
  "last_handoff": {"outcome": "accepted", "to_provider": "endoscopy-unit-1", "by": "specialist_owner:dr_s", "at": "2026-01-09T08:00:00+00:00"}
}
```

`state` is a main or side state. `overdue` and `escalation_level` are flags, not states, so a case can be overdue and in `patient_contacted` (ADR-011). They reset when the state changes, except for a consent-withdrawn excursion, which keeps them. `resume_state` is set only while in `consent_withdrawn` and is derived by replay, never taken from the client. `clock_started_at` is when the current state's SLA clock started (state entry, except for the confirmatory SLA, which runs from the latest `handoff_accepted` when there was one: `sla_clock_start` in the YAML, A13, ADR-020; `referral_accepted_at` is that acceptance, null before any); `breach_due_at` is set with `overdue`. Cut C can rely on the first six keys; `clock_started_at` and `breach_due_at` are extras for the timeline. `last_activity_at` is the time of the latest event that is not a watchdog flag (the idle clock, A11); `watchdog_flag` is `{reason, idle_days, at}` while a `watchdog_flagged` is the newest word on the case and null as soon as any other event arrives.

Consent (A6, ADR-017): `consent` is the current value, derived by replaying every `consent_updated` in order over the programme defaults (everything allowed until the patient says otherwise; `updated_at` is null until then); `consent_history` lists every change, oldest first, as `{at, actor, changes}`. `consent_frozen` is true exactly while `consent.withdrawn` is true (state `consent_withdrawn`); while frozen the API answers 409 "consent withdrawn" to `contact_patient`, `message_drafted`, `message_approved`, `message_sent` and `handoff_requested`. `review_reason` is `consent_withdrawn` from the moment the API flags the withdrawal until consent is restored; `GET /cases?state=consent_withdrawn` is the human-review list.

Referral handoff (A5, ADR-016): `owner` changes only on `accept_ownership`, `handoff_accepted` or `reassign`, never when a handoff is merely requested. `provider` is the provider the owner acts for (set by `handoff_accepted`). `pending_handoff` is `{to_provider, requested_by, from_owner, requested_at}` while a request is unanswered, else null. `last_handoff` is `{outcome: "accepted"|"rejected", to_provider, by, at}` plus `reason` and `bounced` when rejected (a scheduler bounce has `reason: "timeout"`, `bounced: true`). An unanswered request is bounced by the scheduler sweep after `handoff_response_working_days` (3) working days, which is logged as a `handoff_rejected` event.

### Roles

Final as of A8. Authority is `api/app/policy.py`; the decision matrix in E5 is checked against it.

| Role | Purpose |
|---|---|
| `lab` | Reports and acknowledges screening results; creates cases. |
| `primary_care_owner` | Accepts ownership, contacts the patient (via approved messages), hands off, approves messages, and signs off as the responsible clinician. |
| `specialist_owner` | Accepts referrals, books and completes the confirmatory test, and signs off as the responsible clinician. |
| `coordinator` | Sees the worklist and dashboard, records the patient's instructions (acknowledge, consent, callback), escalates and reassigns, accepts the coordinator rung. Cannot sign off a closure. |
| `patient` | Sees only their own case, acknowledges, controls consent, requests a callback. |
| `supervisor` | Accepts the supervisor rung, accepts ownership on behalf of an owner, reassigns, controls the demo clock. Cannot sign off a closure. |
| `scheduler` (service) | Not a user role. Authenticates with the scheduler service token as `system:scheduler`; may emit only `mark_overdue`, `escalate` and the handoff bounce (`handoff_rejected` with reason `timeout`), and post its heartbeat. |
| `watchdog` (service) | Not a user role. Authenticates with the watchdog service token (`WATCHDOG_SERVICE_TOKEN`) as `system:watchdog`; may run the scan, post nothing but `watchdog_flagged`, and read `/heartbeats` and `/alerts`. It cannot read or create cases. |

Who may post which event type through `POST /cases/{id}/events` (a missing type is refused for everyone):

| Event type | Roles |
|---|---|
| `acknowledge` | lab, coordinator, supervisor |
| `accept_ownership` | primary_care_owner, specialist_owner, supervisor |
| `contact_patient`, `mark_lost_contact` | primary_care_owner, specialist_owner, coordinator, supervisor |
| `patient_acknowledge`, `callback_requested`, `consent_updated` | patient (own case), coordinator |
| `book_confirmatory`, `message_drafted` | primary_care_owner, specialist_owner, coordinator |
| `complete_confirmatory` | specialist_owner |
| `clinician_review`, `close`, `reopen`, `message_approved` | primary_care_owner, specialist_owner |
| `handoff_accepted` | primary_care_owner, specialist_owner |
| `handoff_rejected` | primary_care_owner, specialist_owner, scheduler (only the `timeout` bounce) |
| `handoff_requested` | primary_care_owner, specialist_owner, supervisor |
| `reassign` | supervisor, coordinator |
| `escalate` | scheduler, coordinator, supervisor |
| `escalation_accepted` | primary_care_owner, specialist_owner, supervisor, coordinator (the state machine also requires the actor's role to be in the rung's `accepted_by`) |
| `mark_overdue` | scheduler |
| `watchdog_flagged` | watchdog (service token) |
| `review_requested` | nobody (the API emits it itself as `system:consent` when consent is withdrawn) |
| `message_sent` | nobody over HTTP. Only `app.notify.send` (A7) writes it, as `system:notifier`; the state machine refuses any other actor, so D5's approval gate calls `notify.send(case_id, channel, language, purpose, body)` and there is no other path |

**Owner scoping (A5).** A `primary_care_owner` or `specialist_owner` may post an event only on a case they own (`read_model.owner` equals their `role:name`). Exceptions: `accept_ownership` on a case with no owner, and `handoff_accepted` / `handoff_rejected` on a case whose `pending_handoff.to_provider` equals the caller's provider (login `provider`, default the name). Anything else is 403 and nothing is written. Supervisors, coordinators, lab and patients are not owner-scoped (patients stay limited to their own case). Hand a case on with `handoff_requested` and let the receiver accept before they act on it.

Other routes: `GET /cases/{id}/notifications` primary_care_owner and specialist_owner (own case only), coordinator, supervisor; `POST /cases` lab, coordinator, supervisor; `GET /cases` every user role except patient; `GET /cases/{id}` every user role (patients: own case only); `GET /pathways` and `GET /me` any valid identity; clock control supervisor only (A3).

Whether a coordinator may ever sign off, and which roles count as "clinician", is a question for the clinician review (H1).

## 7. Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Scope exceeds the time: 69 planned tickets in about 22 hours of autonomous work. | Cut lines in section 5, with moats 1 to 3 protected; milestones are merge points so partial work still lands; tickets sized for one agent run; unfinished tickets roll to the human phase instead of being half-merged. |
| 2 | Parallel cuts drift apart: contract changes, merge conflicts, a regression of the closure guard when side states are added. | Contracts in section 6 changed in the same commit as the code; merges only at milestone boundaries in dependency order; `make check` on every merge; A2 re-runs the closure property tests on the extended event set; shared metric functions (A10) so the dashboard and the evaluation cannot disagree. |
| 3 | Simulator numbers look made up if parameters are unsourced. | Every parameter is `assumed` or `sourced` with a URL (B1); evidence register early (E1) so B11 can replace guesses; results framed as illustrative of mechanism, not forecasts; limitations table is explicit; `make evaluate` lets a judge change any value. |
| 4 | No clinician or Arabic reviewer secured in time, so wording and SLAs are unreviewed. | Book both by 25 October (H1, H2); reviewer packs ready at M5 (E10) so each review is 30 minutes; if a review is missed, label the text "not clinically reviewed" and "Arabic draft, unreviewed" in the submission rather than implying review. |
| 5 | The live demo fails on stage, especially the scheduler kill. | Deterministic seeded scenario with one-click reset (E8); rehearsal script repeated three times (F11); backup video recorded from the same script (H4); offline mode with cached model outputs (D6, F10). |
| 6 | The model provider (Jais or Azure OpenAI) is unavailable, slow or non-deterministic. | `template` is the default and fully offline; adapters are optional (D7); every demo output is cached (D6); invalid output falls back to the template and is counted as an abstention (D3, D11); tests never call a real provider. |
