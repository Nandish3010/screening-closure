# Plan

Hackathon entry: Future Health Digital Innovation Challenge, Challenge 05 "Closing the Screening-to-Care Loop". Prototype and documentation due 20 November 2026. Companion files: [TRACKER.md](TRACKER.md) (tickets), [TEST-CASES.md](TEST-CASES.md) (what must pass), [DECISIONS.md](DECISIONS.md) (why), [LOG.md](LOG.md) (what happened). The source brief is `/Users/NS/gitex/fhdic-challenge05-plan.md`.

## 1. Goal and positioning

**Positioning (one sentence):** Every actionable screening result has a named owner, a clock, an audit trail, and a clinician-signed closure. Nothing is diagnosed.

The product is a deterministic closure engine for one pathway: a positive FIT (faecal immunochemical test) result through a confirmatory colonoscopy to a documented closure. Cases are tracked as an append-only event log, state is derived by replay, and the state machine refuses any closure that lacks closure evidence and a human sign-off. Timers run on a working-day calendar and an injectable simulation clock, so a 45-day pathway can be shown in minutes. When a clock runs out, the system emits an overdue event and walks an escalation ladder to named humans. It never closes a case itself. AI is used only at the edges: it may draft a patient message, and a human owner must approve it before it is "sent". All data is synthetic and every simulator parameter carries its source or is labelled assumed.

We are building autonomously with AI agents from 17 October until 18 October 22:30 local time (milestones M0 to M5 below). The human owner then continues: clinician review, Arabic review, video, and submission by 18 November (registration closes 10 November; feature freeze 10 November). The design argument we want judges to take away is that safety-critical transitions must be deterministic, replayable and explainable to a regulator, so the model drafts and humans decide, and the architecture makes anything else structurally impossible rather than merely against policy.

### Judging criteria and the one thing we show for each

> **Provisional.** The official six criteria were not in the files this plan was written from (the brief says the full submission requirements are shared after registration). The six rows below are working criteria derived from the documentation sections the brief asks for. Replace the left column with the official wording and re-check the right column when the requirements are in hand. This is task H6 in TRACKER.md.

| # | Criterion (working) | The one thing we show |
|---|---|---|
| 1 | Problem and impact | A published loss-to-follow-up figure for abnormal screening results, then a synthetic positive FIT that today's process would lose, caught and closed live. |
| 2 | Technical soundness and architecture | The closure-invariant property test running: thousands of random event sequences, no closure without evidence and a human sign-off. |
| 3 | Responsible AI and governance | A drafted message that cannot leave until the owner approves it, with its Provenance (prompt, draft, approver, timestamp) on screen next to the automated-vs-human decision matrix. |
| 4 | Evidence and evaluation | `make evaluate` regenerating the baseline-versus-orchestrated table with confidence intervals and every parameter's source, run in front of the judge, with one parameter changed. |
| 5 | Usability and inclusion | The patient PWA in Arabic (right-to-left, grade-6 wording) where "I don't understand this" creates a callback task for a human. |
| 6 | Scalability and pilot readiness | The 90-day pilot plan with cost per case, next to the measured scheduler sweep over 100,000 open cases. |

## 2. Six moats

Each is something that is hard to copy in a week and visible in the demo.

1. **Proven closure invariant.** A property test suite that tries thousands of random event sequences and proves no case ever reaches `closed` without closure evidence and a human sign-off. Show the test running in the demo.
2. **Silent-failure watchdog.** The dangerous failure in this domain is the absence of events, not a bad event. A watchdog flags any open case with no activity for N days, and separately flags the scheduler itself if it stops producing heartbeats. Demo it live by killing the scheduler.
3. **Judge-runnable evaluation.** `make evaluate` regenerates every number in the documentation from the seeded simulator in under two minutes. Judges can change a parameter and rerun.
4. **FHIR-native with a named integration target.** Every object is a real FHIR R4 resource. The docs contain a one-page adapter plan for Malaffi, Abu Dhabi's health information exchange, stated as a target, not a claim.
5. **Arabic-first patient experience.** Right-to-left layout, reviewed by a native speaker, written at roughly grade-6 reading level, with an "I don't understand this" button that routes to a human.
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
- Metrics: time-to-ownership, time-to-safe-closure, share of cases open at 30, 60 and 90 days, reopen rate, escalation precision, automation abstention rate, and a fairness split by language and contact channel.
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
- One simple calibrated loss-to-follow-up risk score (logistic regression on simulator features) used only to order the worklist, with a reliability plot and a risk score card. It never closes, escalates or decides care.
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
9. **Cut lines from the brief apply if time runs short.** Protect moats 1, 2 and 3 first (closure invariant, watchdog, `make evaluate`). Cut in this order: the mammogram stretch (config file only); the regulator rung of the escalation ladder and duplicate-result deduplication; FHIR `Appointment` and `Communication` mapping (stub them); the risk score (keep SLA-based ordering); polish on the dashboard. Never cut: the closure guard, the human approval gate for messages, the synthetic-data declaration, the SIMULATED TIME badge.
10. **No real patient data, no real model calls in tests, no secrets in the repo.** `LLM_PROVIDER` defaults to `template`.

## 6. Integration contracts

Everything below is a working contract for Cuts C and D. "Exists" means in the code today (`api/app/routers/cases.py`, `api/app/main.py`); "planned" names the ticket that adds it. Shapes marked "proposed" are the starting point and are finalised, and this section updated, by the owning ticket.

### Routes

| Route | Status | Request | Response |
|---|---|---|---|
| `GET /health` | exists | none | `{status, sim_time}` |
| `GET /pathways` | exists | none | the pathway config as parsed from YAML |
| `POST /cases` | exists | `{case_id?, patient_ref?, result_ref?}` | 201 with the case: `{case_id, state, events[]}`; 409 `CaseAlreadyExists` |
| `GET /cases?state=` | exists | query `state` | list of `{case_id, pathway, state, created_at, updated_at}` |
| `GET /cases/{id}` | exists | none | `{case_id, state, events[]}`; 404 if unknown |
| `POST /cases/{id}/events` | exists | `{type, actor, payload, at?, event_id?}` | 201 with the case; 404; 409 for an invalid transition or an `event_id` used on another case |
| `GET /me` | planned (A8) | identity token | `{user_id, role, display_name}` |
| `GET /cases?state&owner&overdue` | planned (A9) | query filters | list as above plus `owner`, `due_at`, `overdue`, and `risk_score` once D9 lands |
| `GET /cases/{id}/timeline` | planned (A9) | none | read-only list of events with `seq, event_id, type, at, actor, payload` |
| `GET /metrics/closure` | planned (A10) | none | `{p50_days, p90_days, open_by_age_bucket, breaches_by_provider, bottleneck_by_transition, abstention_rate, sim_time}` (proposed) |
| `POST /cases/{id}/drafts` | planned (D4) | `{purpose, language, channel}` (proposed) | `{draft_id, case_id, text, provider, used_fallback, status, provenance_ref}` |
| `POST /drafts/{id}/approve` | planned (D5) | none | `{draft_id, status, approved_by, approved_at}` (proposed) |
| clock control (advance, reset) | planned (A3) | e.g. `{days}` or `{hours}`; route names proposed | `{sim_time}`; supervisor role only |

Event shape (all routes): `{seq, event_id, type, at, actor, payload}`. `event_id` is the idempotency key: a retry with the same id inserts once. `at` defaults to the sim clock, never the wall clock. Errors are JSON with a `detail` string. After A8 the `actor` field is derived from the caller's identity and a client-supplied `actor` is ignored or rejected.

Consent changes, callbacks and flags use `POST /cases/{id}/events`, not separate routes.

### Event types

| Type | Status | Emitted by (proposed) | Effect (proposed, final in A2) |
|---|---|---|---|
| `case_created` | exists | system intake | genesis marker, no state change |
| `acknowledge` | exists | lab | `result_received` to `acknowledged` |
| `accept_ownership` | exists | primary_care_owner | `acknowledged` to `owner_accepted` |
| `contact_patient` | exists | primary_care_owner | `owner_accepted` to `patient_contacted` |
| `patient_acknowledge` | exists | patient | `patient_contacted` to `patient_acknowledged` |
| `book_confirmatory` | exists | specialist_owner or primary_care_owner | `patient_acknowledged` to `confirmatory_booked` |
| `complete_confirmatory` | exists | specialist_owner | `confirmatory_booked` to `confirmatory_completed` |
| `clinician_review` | exists | owner role acting as the clinician | `confirmatory_completed` to `clinician_reviewed` |
| `close` | exists | a human clinician; requires `evidence_ref` and `signed_by`; automated actors refused | `clinician_reviewed` to `closed` |
| `overdue` | planned (A3) | scheduler | enters the `overdue` side state for the current transition |
| `escalated` | planned (A4) | scheduler | enters `escalated`; payload names the rung and target role; needs a human to accept |
| `handoff_requested` | planned (A5) | current owner | ownership pending, no state change |
| `handoff_accepted` | planned (A5) | receiving owner | ownership changes |
| `handoff_rejected` | planned (A5) | receiving owner, or scheduler on bounce | enters `referral_rejected`; payload `reason` from the taxonomy |
| `consent_updated` | planned (A6) | patient, or coordinator recording the patient's instruction | withdrawal enters `consent_withdrawn` and freezes outreach |
| `message_drafted` | planned (A2 defines, D4 uses) | model or template actor | no state change |
| `message_approved` | planned (A2 defines, D5 uses) | owner role | no state change |
| `message_sent` | planned (A2 defines, D5 uses) | system, only after approval | no state change |
| `callback_requested` | planned (A2 defines, C6 uses) | patient | creates a human callback task, no state change |
| `watchdog_flagged` | planned (A11) | watchdog | no state change; case appears in "cases nobody is looking at" |

Heartbeats from the scheduler are not case events. Their storage is decided in A11. The event type for a human accepting an escalation rung is decided in A4 and added here.

### Roles (proposed, final in A8 and checked against the decision matrix in E5)

| Role | Purpose |
|---|---|
| `lab` | Reports and acknowledges screening results for the cases it originated. |
| `primary_care_owner` | Accepts ownership, contacts the patient (via approved messages), hands off, and can sign off as the responsible clinician. |
| `specialist_owner` | Accepts referrals, books and completes the confirmatory test, and can sign off as the responsible clinician. |
| `coordinator` | Sees the worklist and dashboard, takes callback tasks, accepts the top escalation rung. Cannot sign off a closure. |
| `patient` | Sees only their own case, acknowledges, controls consent, requests a callback. |
| `supervisor` | Accepts escalations for their owners, controls the demo clock. |

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
