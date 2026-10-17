# Tracker

One row per ticket. Tickets come from this file and nowhere else. Plan: [PLAN.md](PLAN.md). Test cases: [TEST-CASES.md](TEST-CASES.md). Progress log: [LOG.md](LOG.md).

## Milestone status

| Milestone | Scope | Target (local, IST) | Tickets | Done | Status |
|---|---|---|---|---|---|
| M0 | Scaffold, persistence, cohort | 17 Oct (done) | 3 | 3 | done |
| M1 | Core loop | 18 Oct 04:00 | 13 | 5 | doing |
| M2 | Simulator and `make evaluate` | 18 Oct 10:00 | 17 | 0 | todo |
| M3 | Interfaces | 18 Oct 16:00 | 14 | 3 | doing |
| M4 | AI edge and governance | 18 Oct 18:00 | 11 | 0 | todo |
| M5 | Hardening, docs, demo | 18 Oct 22:30 | 14 | 0 | todo |
| Total | | | 72 | 11 | |

Tickets per cut: A 12, B 11, C 11, D 11, E 12, F 12 (69 planned, plus the 3 done M0 tickets). Human-owner tasks are listed separately at the bottom.

## How to update

- Status values: `todo`, `doing`, `review`, `done`, `blocked`. Put the reason in Notes when a ticket is `blocked`.
- An agent takes one ticket whose dependencies are all `done`, sets it to `doing`, and works only in its cut's directories (see PLAN.md section 3).
- A ticket moves to `done` only when every test case in its Test cases column passes (run the command in TEST-CASES.md) and `make check` is green on the branch.
- The ticket's code and tests go in one commit whose message starts with the ticket ID, for example `A3: scheduler SLA sweep`. Then a small follow-up commit `A3: tracker and log` sets the row to `done`, fills the Commit column with the first commit's short hash, and appends one line to LOG.md. (A commit cannot contain its own hash.)
- Edit only your own ticket's row and append to the end of LOG.md. If a merge conflicts in this file, keep both sides' rows and both sides' log lines.
- Milestone status counts are updated by whoever merges a cut into main at a milestone boundary.
- To add a ticket, append it to its cut with the next free ID and say why in Notes. Do not renumber existing IDs.

## Tickets

| ID | Cut | Ticket | Depends on | Status | Test cases | Commit | Notes |
|---|---|---|---|---|---|---|---|
| M0-1 | M0 | Scaffold: closure engine skeleton, pathway config, compose stack, closure-invariant property test, optional `.env` so compose validates on a fresh clone | - | done | T-A1, T-A2, T-A3, T-A4 | 4ab4af3, 86fca2d | Two commits: scaffold, then compose `.env` fix. |
| M0-2 | M0 | Persistence: append-only Postgres event log, transactional append with in-transaction transition check, migrations, integration tests | M0-1 | done | T-A4, T-A5, T-A6, T-A7, T-A8, T-A9, T-A10, T-A11 | 9b635af | Also covers the API tests in api/tests/test_api.py. |
| M0-3 | M0 | Cohort pipeline: containerised Synthea v3.3.0, HAPI loader, seeded FIT results, case creation, `make demo-seed` | M0-2 | done | T-B1 | 971c3f1 | Provenance in docs/data-provenance.md. |
| A1 | A | Working-day calendar module: UAE default (Mon-Fri working, Sat-Sun weekend), weekend set and optional holiday dates in config, `add_working_days` and `is_working_day` used by every SLA | M0-1 | done | T-A12, T-A13 | b8577c3 | M1. Holiday list contents decided here and recorded as an assumption in DECISIONS.md (ADR-009). |
| A2 | A | Side states and event vocabulary: add `overdue`, `escalated`, `referral_rejected`, `consent_withdrawn`, `reopened`, `lost_contact` transitions and the new event types to the pathway config and state machine; closure guard unchanged | M0-1, M0-2 | done | T-A14, T-A1, T-A2 | d0a6b43 | M1. Finalises the proposed event effects in PLAN.md section 6. Closure property tests must be re-run on the extended alphabet. |
| A3 | A | Scheduler SLA sweep on the sim clock: evaluate open cases against `slas_working_days` with the calendar, emit `overdue` once per breach, never close; add sim clock control route (advance, reset) for demo use | A1, A2 | done | T-A15, T-A16, T-A17 | 5ecf541 | M1. Replaces the scheduler stub. Clock control route is supervisor-only once A8 lands. |
| A4 | A | Escalation ladder: reminder, owner's supervisor, programme coordinator at the configured offsets after breach; each rung emits `escalated` to the target role and needs a human to accept | A3 | done | T-A18, T-A19 | 2e22be3 | M1. Regulator rung is on the cut line and not built. Event type for rung acceptance decided here and logged in DECISIONS.md. |
| A5 | A | Referral handoff contract: `handoff_requested`, `handoff_accepted`, `handoff_rejected` with the fixed reason taxonomy, ownership changes only on acceptance, unanswered referral bounces on a timer and escalates, every bounce logged | A2, A3, A8 | done | T-A20, T-A21, T-A22 | 4713a28 | M1. Duplicate-result deduplication is on the cut line and handled in B5, not here. Handoff pending/accept/reject in the read model (owner, provider, pending_handoff, last_handoff); timeout reason and bounce by the sweep after 3 working days (YAML); bounced referral has a 2 working day re-route SLA so it walks the ladder; owner scoping with a provider login claim; ADR-016. Existing tests that crossed owners were rewritten to go through the handoff. |
| A6 | A | Consent ledger: consent per contact channel and for data sharing as an append-only history via `consent_updated`; withdrawal freezes outreach and flags the case for human review | A2 | todo | T-A23, T-A24 | - | M1. Freeze must be enforced in the API layer, not only in the UI. |
| A7 | A | Notification mock: SMS/WhatsApp-style messages written to a `notifications` log table, never sent; honours consent freeze | A6 | todo | T-A25 | - | M1. The only outbound path in the system; D5 uses it for `message_sent`. |
| A8 | A | RBAC and identity: roles lab, primary_care_owner, specialist_owner, coordinator, patient, supervisor; dev token login; `GET /me`; event actor derived from identity; per-route role policy | M0-2 | done | T-A26 | 4515122 | M1. Changes the contract: `actor` in `POST /cases/{id}/events` is derived from identity. Update PLAN.md section 6 when merged. |
| A9 | A | Read routes: `GET /cases` with `state`, `owner`, `overdue` filters and `GET /cases/{id}/timeline` (immutable audit view per case) | A3, A8 | todo | T-A27, T-A28 | - | M1. Adds `owner`, `due_at`, `overdue` to the case summary shape. |
| A10 | A | Closure metrics: pure metric functions in `api/app/metrics.py` and `GET /metrics/closure` (P50/P90 time to closure, open by age bucket, breaches by provider, bottleneck by transition) | A9 | todo | T-A29 | - | M1. Cut B imports these functions so the dashboard and the evaluation can never disagree. |
| A11 | A | Watchdog service and scheduler heartbeat: flag open cases idle for `WATCHDOG_IDLE_DAYS` with `watchdog_flagged`; scheduler writes heartbeats; watchdog raises an alert if heartbeats stop for 60 s and clears it on resume | A3 | todo | T-A30, T-A31, T-A32 | - | M1. Moat 2. Split into two tickets if it runs past 3 hours. Heartbeats are not case events. |
| A12 | A | FHIR projection: write DiagnosticReport, Task, ServiceRequest, Appointment, Communication, Consent and Provenance resources to HAPI from the event stream | A2, A6 | todo | T-A33 | - | M2. Moat 4. Cut line: Appointment and Communication may be stubs. |
| B1 | B | Parameter registry: schema, loader and validator for `simulator/params.yaml` (value, source URL, status `assumed` or `sourced`), plus a generated parameter table for the docs | M0-3 | todo | T-B2 | - | M2 |
| B2 | B | Simulator core: seeded per-case random streams, patient behaviour model (acknowledgement delay, reachability, attendance by channel, referral outcome), runs on the injectable sim clock; patient language and channel assigned by the simulator | B1 | todo | T-B3, T-B4 | - | M2. Synthea has no Arabic or UAE demographics; language and channel assignment is a documented assumption. |
| B3 | B | Baseline arm: today's process with no named owner, no clocks and no escalation | B2 | todo | T-B5 | - | M2 |
| B4 | B | Orchestrated arm: drive cases through the real state machine, calendar, ladder, handoff and consent rules in process; 2,000 cases over 90 days in under a minute | B2, A4, A5, A6 | todo | T-B6 | - | M2. In-process, not over HTTP, to meet the time budget. |
| B5 | B | Messy-case injection: wrong contact, provider switch, duplicate result, conflicting acknowledgements, consent withdrawn | B4 | todo | T-B7 | - | M2. Duplicate-result deduplication is on the cut line; if cut, count duplicates as a reported hazard instead. |
| B6 | B | Metrics: time-to-ownership, time-to-safe-closure, open at 30/60/90 days, reopen rate, escalation precision, automation abstention rate, using the shared functions from A10 | B3, B4, A10 | todo | T-B8 | - | M2. Exact definition of escalation precision fixed here and written to docs/results/metrics.md. |
| B7 | B | Bootstrap confidence intervals over seeds for every metric | B6 | todo | T-B9 | - | M2 |
| B8 | B | Fairness split by language and contact channel with gap flag | B6 | todo | T-B10 | - | M2 |
| B9 | B | Results writer and `make evaluate`: run both arms, write docs/results/*.md and *.json, canonical and reproducible, within the two-minute budget | B7, B8 | todo | T-B11, T-B12 | - | M2. Replaces the `evaluate` stub in the Makefile. |
| B10 | B | Parameter override from the command line, and a checker that every number quoted in docs comes from docs/results | B9 | todo | T-B13, T-B14 | - | M2. Lets a judge change a parameter and rerun. |
| B11 | B | Replace assumed parameter values with sourced ones from the evidence register; keep honest `assumed` labels where none exists | B1, E1 | todo | T-B15, T-B2 | - | M2. Depends on E1 landing first inside M2. |
| C1 | C | Web scaffold: Vite + React PWA shell, typed API client from the OpenAPI schema, role-based dev login, i18n framework, compose service | A8 | todo | T-C1 | - | M3. `web/` is not scaffolded yet. |
| C2 | C | Coordinator worklist ordered by SLA breach then risk score | C1, A9 | todo | T-C2 | - | M3. Falls back to SLA-only ordering until D9 provides the score. |
| C3 | C | Case detail with timeline and the reason a case is overdue | C2 | todo | T-C3 | - | M3 |
| C4 | C | Case actions: accept, hand off, escalate, sign-off with evidence reference, with role-gated visibility | C3, A4, A5 | todo | T-C4, T-C5 | - | M3 |
| C5 | C | Patient PWA home: plain-language result, what happens next, who owns it, one-tap acknowledge | C1 | todo | T-C6 | - | M3. Wording at about grade 6; no diagnostic language. |
| C6 | C | Patient consent controls and the "I don't understand this" button creating `callback_requested` and a coordinator callback task | C5, A6 | todo | T-C7, T-C8 | - | M3 |
| C7 | C | Arabic and English string catalogue, RTL layout, readability and completeness checks, reviewer export of all patient strings | C5 | todo | T-C9, T-C10, T-C11 | - | M3. Arabic text is a draft until the native-speaker review (H2). |
| C8 | C | Closure dashboard: P50/P90, open by age bucket, breaches by provider, bottleneck by transition, permanent SIMULATED TIME badge | C2, A10 | todo | T-C12 | - | M3 |
| C9 | C | Dashboard panel "cases nobody is looking at" fed by the watchdog, with stale-scheduler banner | C8, A11 | todo | T-C16 | - | M3 |
| C10 | C | Draft review and approval UI in the coordinator app | C3, D5 | todo | T-C13 | - | M4. Needs the draft and approve routes from D4 and D5. |
| C11 | C | Playwright end-to-end suite (overdue, escalate, approve draft, sign-off; English and Arabic) plus accessibility and PWA checks | C4, C6, C7, C8, C10 | todo | T-C14, T-C15 | - | M4. Adds `make test-e2e`. |
| D1 | D | AI provider interface and `LLM_PROVIDER` selection (`template`, `azure_openai`, `jais`); default `template` | M0-1 | done | T-D6 | c67bedd | M3. Config key already exists in `.env.example` and `config.py`. Provider interface, TemplateProvider, stub azure_openai/jais, fail-fast UnknownProvider (T-D6, 18 tests). TemplateProvider renders via templates.py, which lands in D2, so D1 alone does not draft. |
| D2 | D | Deterministic message templates in Arabic and English for each message purpose and channel | D1 | done | T-D14 | ba762ab | M3. Template text is also the fallback; it is the first thing the Arabic reviewer sees. Templates en/ar for 5 purposes x 3 channels plus dated reminder variants; reviewer_pack_markdown() for E10 (T-D14). Arabic is an unreviewed draft pending H2. |
| D3 | D | Output validation: language, length, required fields, forbidden diagnosis and advice phrases, no extra personal data; invalid output falls back to the template and counts as an abstention | D2 | done | T-D3, T-D8 | a8325f6 | M3 validate(), forbidden_phrases.yaml, draft_with_fallback, in-memory AbstentionLog (T-D3, T-D8); docs/governance/ai-boundary.md. D11 wires the log to metrics. |
| D4 | D | `POST /cases/{id}/drafts`: create a draft, store the Provenance (prompt, draft, provider, timestamp), emit `message_drafted` | D3, A2, A8 | todo | T-D5 | - | M3. The only route a model actor can use. |
| D5 | D | `POST /drafts/{id}/approve`: owner approval gate, then `message_approved` and `message_sent` through the notification mock | D4, A7 | todo | T-D1, T-D2, T-D13 | - | M3. Structural gate: there is no other path to `message_sent`. |
| D6 | D | Output cache for the offline demo: record and replay keyed by a prompt hash; a miss falls back to the template | D4 | todo | T-D7 | - | M4 |
| D7 | D | Azure OpenAI and Jais adapters, network off by default, tested with mocked HTTP; unavailable provider falls back to the template | D1, D6 | todo | T-D4 | - | M4. Real calls are never made by tests or by the default configuration. |
| D8 | D | Loss-to-follow-up risk score: logistic regression on simulator features, seeded training | B4 | todo | T-D11 | - | M4. Depends on a working orchestrated arm for features and labels. |
| D9 | D | Serve the risk score as a worklist ordering field only; nothing escalates, closes or decides care on it | D8, A9 | todo | T-D10 | - | M4 |
| D10 | D | Calibration check and reliability plot, risk score card with tolerance and limits | D8 | todo | T-D9, T-D12 | - | M4. Proposed tolerance: expected calibration error at or below 0.05; confirm after first fit. Cut line: drop the score, keep SLA ordering. |
| D11 | D | Abstention accounting: record every template fallback with reason and expose the rate to the metrics | D3, A10 | todo | T-D15 | - | M4. Feeds the automation abstention rate in B6. |
| E1 | E | Evidence register (docs/evidence.md): public datasets and published figures for every simulator parameter, with URL, access date and extracted value; published loss-to-follow-up rates after abnormal screening | M0-3 | todo | T-E1 | - | M2. Needed by B11. Unverifiable figures stay marked `assumed`. |
| E2 | E | Assumptions and limitations table (docs/assumptions-limitations.md) and update of data provenance for the simulator, sim clock and cached model outputs | E1 | todo | T-E2 | - | M5. Fills the existing placeholder row. |
| E3 | E | Governance mapping: UAE PDPL, ADHICS, in-country hosting (Azure UAE North), data minimisation, retention, consent model, and a one-page Malaffi adapter plan stated as a target | E2 | review | T-E4 | 88232bb | M5. Names must be verified against official sources before citing. Built before its dependency E2 was done, at the coordinator's request. Status review: T-E4 is a manual reviewer check. Items marked "to verify" in regulatory-mapping.md (PDPL and health ICT law texts, ADHICS V2, DoH exception route, Malaffi interface) are open. |
| E4 | E | Design choices comparison: complete the table in docs/design-choices.md (auditability, replayability, latency, cost per case, failure modes, model unavailable), neutral tone | M0-2 | review | T-E5, T-E3 | c2898eb | M5. No competitor names anywhere. Status review: T-E5 manual read and T-E3 denylist check (local file) still to run. Table has eight dimensions (added regulator explainability and testability). |
| E5 | E | Automated-versus-human decision matrix covering every event type and actor, with evidence required | A2, D5 | todo | T-E6 | - | M5. Lives in docs/governance/. |
| E6 | E | Hazard log: missed escalation, wrong-patient message, silent failure, consent breach, with likelihood, impact, control and the test that proves the control | A11, F5 | todo | T-E7 | - | M5. Controls must cite T-IDs. |
| E7 | E | 90-day pilot plan: one programme, two or three providers, shadow mode first, named KPIs, commercial model, and cost per case from a script with an inputs file | B9 | review | T-E8 | 717634b | M5. Cost inputs are assumptions and labelled so. Built before dependency B9 was done, at the coordinator's request. Status review: T-E8 docs-check not yet wired; the plan embeds cost_model.py output verbatim. KPI targets K4 and K5 relayed from the coordinator (DoH IFHAS, via docs/evidence.md section 8), unverified by the author. |
| E8 | E | Demo scenario and one-click reset: one named synthetic patient, seeded and deterministic, with snapshot and `make demo-reset` | A12, D6, C11 | todo | T-E9, T-E10 | - | M5. tools/demo/. |
| E9 | E | 7-minute demo script with timings, Q&A prep, and the backup video storyboard | E8 | todo | T-E11, T-E15 | - | M5. Rehearsals and the recording are human-owner tasks. |
| E10 | E | Reviewer packs: clinician pack (pathway, states, SLAs, wording) and Arabic pack (all patient strings with context) | C7, E5 | todo | T-E17 | - | M5. The reviews themselves are H1 and H2. |
| E11 | E | Technical documentation assembly: the thirteen sections, opening with the outcome metric, numbers pulled from docs/results | E2, E3, E4, E5, E6, E7, B9 | todo | T-E12, T-E2 | - | M5 |
| E12 | E | README refresh: one-command run, architecture diagram, seed data, tests, licence | E11 | todo | T-E16 | - | M5. Keep the one-line positioning at the top. |
| F1 | F | `make check` aggregator running unit, property, integration, simulator, docs and security suites, plus the tracker and test-case cross-reference lint | M0-3 | todo | T-F1, T-F16 | - | M1. Heavy suites (full load sweep, chaos) run at reduced size inside `make check`; full sizes via their own targets. Recorded in DECISIONS.md if changed. |
| F2 | F | Separate test database: compose service or database name for tests, with a guard that refuses to run against the dev database | M0-2 | done | T-F2 | 91e0513 | M1. Today `make test-db` points at the dev compose Postgres. Test DB is closure_test (init script on a fresh volume, created by make test-db otherwise); guard aborts the session unless the name ends in _test; make test-db-reset; ADR-015. Dev DB wiped by make reset and reseeded with make demo-seed. |
| F3 | F | Fault-injection harness (test-only hooks) with duplicate-event and clock-skew chaos tests | F2 | todo | T-F3, T-F4 | - | M2. Adds `make test-chaos`. |
| F4 | F | Chaos: kill the scheduler mid-sweep, assert the watchdog alert, restart, assert no lost or duplicated events | A11, F3 | todo | T-F5 | - | M2 |
| F5 | F | Chaos: bad model output and consent withdrawn mid-transition | D5, A6, F3 | todo | T-F6, T-F7 | - | M4 |
| F6 | F | Load test: seed 100,000 open cases, time the scheduler sweep at 1k, 10k and 100k, write numbers to docs/results | A3 | todo | T-F8 | - | M2. Adds `make test-load`. |
| F7 | F | RBAC matrix test generated from the OpenAPI route list; every role denied outside its scope; routes without a policy fail | A8, D5 | todo | T-F9 | - | M4. Adds `make test-security`. |
| F8 | F | No PHI in logs or URLs: marker-based scan of service logs and access logs, route table review | A8, D5 | todo | T-F10, T-F11 | - | M4 |
| F9 | F | Secrets and repo hygiene: secret scan of tracked files, `.env` ignored, example file holds placeholders only | M0-1 | todo | T-F12 | - | M2 |
| F10 | F | Offline verification: run the demo with outbound network blocked | D6, E8 | todo | T-F14 | - | M5. Adds `make test-offline`. |
| F11 | F | Kill-the-scheduler rehearsal script: stop, alarm, restart, verify nothing lost, repeatable three times | F4, E8 | todo | T-F15 | - | M5. Adds `make demo-kill-scheduler`. |
| F12 | F | Fresh-clone verification script and checklist for the second machine | B9, E8 | todo | T-F13 | - | M5. The run on the second machine is a human-owner task. Pin the HAPI image tag (currently `latest`) before this check (ADR-006). |

## Human-owner tasks

These need a person. They start after the autonomous phase and carry their own deadlines. Status uses the same values.

| ID | Task | Depends on | Status | Test cases | When |
|---|---|---|---|---|---|
| H1 | Clinician review (30 minutes): pathway, states, SLAs, patient wording | E10 | todo | T-E13 | Book in week of 25 Oct; hold by 7 Nov |
| H2 | Native Arabic speaker review of all patient-facing strings | E10, C7 | todo | T-E14 | Book in week of 25 Oct; hold by 7 Nov |
| H3 | Rehearse the 7-minute demo ten times and log timings | E9 | todo | T-E11 | Before 14 Nov |
| H4 | Record the backup demo video and the 3-minute submission video | E9 | todo | T-E15 | Before 14 Nov |
| H5 | Fresh-clone run on a second machine | F12 | todo | T-F13 | Before 14 Nov |
| H6 | Confirm registration (closes 10 Nov), re-check submission form against the requirements shared at registration, submit | E12 | todo | - | Registration by 10 Nov; submit by 18 Nov |

H6 notes: the "replace the provisional judging criteria" part of H6 is done (official six criteria in PLAN.md section 1, commit "project: official judging criteria"). Still open under H6: confirm registration, re-check the submission form against the requirements shared at registration, and submit.
