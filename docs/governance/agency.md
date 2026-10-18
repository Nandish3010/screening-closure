# The agency

One supervisor agent, five department heads and fourteen executioner agents that do the preparatory work around a screening case, and hand the result to a person. Agents are labour, not authority: the agency is preparation only. It acts only through the code paths a person uses, can emit no case event of its own, causes one thing (a message draft, through the drafting route, which then waits for the case owner's approval), and everything a person decides stays with a person. Design rationale: ADR-033 in [`project/DECISIONS.md`](../../project/DECISIONS.md) and the agency section of [design-choices.md](../design-choices.md). Code: `api/app/agency/`, `api/app/routers/agency.py`. Configuration: [`config/agency.yaml`](../../config/agency.yaml). Tests: T-A60 to T-A66 in [TEST-CASES.md](../../project/TEST-CASES.md) (`api/tests/test_agency.py`, `api/tests/test_agency_pg.py`, the cohort in `api/tests/agency_world.py`).

Related: who may do what ([decision-matrix.md](decision-matrix.md), section "Agency actors"), what could go wrong (HZ-26 to HZ-29 in [hazard-log.md](hazard-log.md)), where a model is used ([ai-boundary.md](ai-boundary.md), section 8), the generated role-by-route table ([rbac-matrix.md](rbac-matrix.md), which has an `agency` column).

**One sentence.** The agents prepare the work and a person decides; no agent can close a case, accept an escalation, approve or send a message, request or answer a referral, classify a reply or sign anything.

## 1. The org chart

```
Supervisor                         system:agency:supervisor
 |-- Outreach                      system:agency:outreach
 |     |-- draft_message
 |     `-- followup_call_prep
 |-- Referral and Scheduling       system:agency:referral_scheduling
 |     |-- reroute_referral
 |     |-- prepare_handoff_pack
 |     `-- booking_followup
 |-- Evidence and Closure          system:agency:evidence_closure
 |     |-- retrieve_evidence
 |     |-- prepare_closure_pack
 |     `-- flag_missing_evidence
 |-- Patient Support               system:agency:patient_support
 |     |-- triage_reply
 |     `-- callback_brief
 `-- Quality and Operations        system:agency:quality_ops
       |-- daily_summary
       |-- anomaly_check
       |-- fairness_watch
       `-- sla_risk_explainer
```

| Level | Agent | Actor | Does | May cause (`AGENCY_CAUSES`) |
|---|---|---|---|---|
| Supervisor | Supervisor | `system:agency:supervisor` | Reads the cases changed since the last completed run (plus every overdue or flagged open case), the metrics, the alerts and the ledger; routes every open case to the departments that need it by rules (section 3); writes one dispatch per department with its work items and a rationale. | Nothing. |
| Head | Outreach | `system:agency:outreach` | Turns its dispatch into `draft_message` and `followup_call_prep` tasks. | `message_drafted`, through the drafting route. |
| Head | Referral and Scheduling | `system:agency:referral_scheduling` | Turns its dispatch into `reroute_referral`, `prepare_handoff_pack` and `booking_followup` tasks. | Nothing. It recommends; a person requests the referral. |
| Head | Evidence and Closure | `system:agency:evidence_closure` | Turns its dispatch into `retrieve_evidence` and `prepare_closure_pack` tasks (and `flag_missing_evidence` as a follow-up). | Nothing. |
| Head | Patient Support | `system:agency:patient_support` | Turns its dispatch into one `triage_reply` task per waiting reply and `callback_brief` tasks. | Nothing. It queues replies for a person. |
| Head | Quality and Operations | `system:agency:quality_ops` | Adds the day's three case-less tasks (`daily_summary`, `anomaly_check`, `fairness_watch`) and an `sla_risk_explainer` task for each case that is overdue or escalated. | Nothing. |

A head's rules produce its candidate tasks. The planner (section 8) may put them in priority order and write a rationale, and nothing else. Of the six identities only Outreach has an entry in `AGENCY_CAUSES` (`message_drafted`); the other five have an empty one. An executioner runs under its own department's identity and with its own tool allow-list. The head's own tool is `read_ledger`.

## 2. The agents, one by one

Tools are the names in `api/app/agency/tools.py`. A tool is a read, or (`create_draft`, Outreach only) a request through the drafting route, named by the event that route writes. There is no tool that writes a case event, and none that closes, reviews, approves, accepts, reassigns, requests a handoff, classifies a reply or signs. Each call is recorded on the task (tool, the ids it was given, outcome, attempts), a tool off the allow-list is refused and recorded, and a tool that causes something is checked against `policy.authorize_agency` on every call.

**Outcome kinds.** Every task ends in one of three:

- `done`: nothing is needed from a person (the work was a lookup or a summary, or the case had moved on and the task says so).
- `needs_human`: the agent prepared material for a person and says who and what (`assignee_role`, `recommended_action`, a title and a summary). The task waits in the inbox (section 5).
- `failed`: the tool was refused or failed (`tool_denied`, `tool_failed`), the output did not validate (`invalid_output`), the agent raised (`exception`), the run's budget ran out before it started (`budget_exhausted`), or the run that held it died (`abandoned`). A failed task fails only itself.

Every output is a strict schema (unknown keys are an error, the summary is at most 300 characters) and is validated again before it is stored.

| Agent | Department | Tools (allow-list) | Output schema | Outcomes |
|---|---|---|---|---|
| `draft_message` | Outreach | `read_case`, `read_drafts`, `create_draft` | `DraftMessageOut` | `done`: the case has moved on, outreach is frozen by a consent withdrawal, or a draft is already waiting. `needs_human`: a draft was made (the case owner approves or rejects it), or the case has no owner yet (the coordinator assigns one first). `failed`: the drafting route refused. |
| `followup_call_prep` | Outreach | `read_case`, `read_notifications` | `CallPrepOut` | `done`: the case has moved on. `needs_human`: a call script and the contact history for the owner to call from; or, if the patient has switched phone contact off, a task to choose another channel. |
| `reroute_referral` | Referral and Scheduling | `read_case`, `read_providers` | `RerouteOut` | `done`: the case has moved on. Otherwise always `needs_human`, for the coordinator: the next untried provider is a recommendation (`recommended_provider`, `tried`, action `reassign_or_request_handoff`); every provider has been tried (`exhausted: true`, action `handle_exhausted_reroute`); or consent to share with a specialist is not given (the coordinator confirms it first). The person reassigns or requests the handoff through the existing routes. |
| `prepare_handoff_pack` | Referral and Scheduling | `read_case`, `read_providers` | `HandoffPackOut` | `done`: the case has moved on. `needs_human`: the referral details, the deadline and the next untried provider, for the owner to request the referral (or to confirm consent first). |
| `booking_followup` | Referral and Scheduling | `read_case` | `BookingOut` | `done`: the case has moved on, or the appointment has not passed. `needs_human`: no appointment date is recorded, or the appointment has passed with no result (record the result or rebook); the second also asks for a `retrieve_evidence` follow-up. |
| `retrieve_evidence` | Evidence and Closure | `read_case`, `find_evidence` | `EvidenceOut` | `done`: the case is closed, verification is off in this deployment, the evidence resolves, or none resolves (which asks for a `flag_missing_evidence` follow-up). `failed`: the evidence server did not answer after two tries. |
| `prepare_closure_pack` | Evidence and Closure | `read_case`, `find_evidence` | `ClosurePackOut` | `done`: the case has moved on. `needs_human`: the case history, milestones, escalations and evidence for the owning clinician to review and sign; the pack records that it does not close the case. If verification is on and nothing resolves, it asks for a `flag_missing_evidence` follow-up. |
| `flag_missing_evidence` | Evidence and Closure | `read_case`, `find_evidence` | `EvidenceOut` | `done`: the case is closed or the evidence now resolves. `needs_human`: no colonoscopy `Procedure` or `DiagnosticReport` resolves, so the owner records the result. |
| `triage_reply` | Patient Support | `read_case` | `TriageOut` | Always `needs_human`: the reply is queued for a person to read, with the classifier's labels if D13 has already written them. The agency never sees the text. |
| `callback_brief` | Patient Support | `read_case`, `read_notifications` | `CallbackOut` | `done`: the case has moved on. `needs_human`: the contact history and two opening lines for the coordinator calling the patient back. |
| `daily_summary` | Quality and Operations | `read_worklist`, `read_metrics`, `read_alerts`, `read_ledger` | `DailySummaryOut` | `done`: open, overdue, escalated and flagged counts, closed cases, open alerts and agency items waiting for a person. |
| `anomaly_check` | Quality and Operations | `read_metrics`, `read_alerts`, `read_ledger` | `FlagsOut` | `done`: no anomaly. `needs_human`: an open alert, replay failures, an unsafe closure or an agency failure rate above 20% (from ten finished tasks), for the supervisor. |
| `fairness_watch` | Quality and Operations | `read_abstention`, `read_worklist` | `FlagsOut` | `done`: no group is served measurably worse. `needs_human`: a language or channel group's drafting abstention rate, or a language's overdue share, is more than 10 percentage points above the overall rate (a group needs at least 5 cases or attempts), for the supervisor. |
| `sla_risk_explainer` | Quality and Operations | `read_case`, `explain_factors` | `ExplainOut` | `done`: one sentence for staff on why a case ranks high: overdue or on time, its state, its escalation level and the worklist ordering aid's top three factors. |

The department heads read no case text and carry no executioner's tools. The supervisor's tools are `read_active_cases`, `read_metrics`, `read_alerts` and `read_ledger` (`SUPERVISOR_TOOLS`). `retrieve_evidence` reads HAPI through the closure-evidence verifier and writes only a ledger output; it never completes the confirmatory test and never writes an event. The one tool that causes anything is `create_draft` (the drafting route's own function: `POST /cases/{id}/drafts`, with `ai.validate`, the template fallback, Provenance, `message_drafted` by `model:<provider>` and then the owner's approval gate). Every other tool is read only, over the API's read models, the metrics, the alerts and the ledger.

## 3. How a case is routed

`route_case` in `api/app/agency/agents.py` is rules only; the order of the worklist and the risk score are not read. A closed case, or one frozen by a consent withdrawal, is routed nowhere.

| Case condition | Department | Reason code | Task the head makes |
|---|---|---|---|
| `owner_accepted`, `patient_contacted` or `lost_contact`, with an owner | Outreach | `awaiting_contact`, `awaiting_acknowledgement`, `lost_contact` | `followup_call_prep` for a case in `lost_contact`, or in `patient_contacted` after two or more contact attempts. Otherwise `draft_message` (a result notification for an owned case with no message sent yet, else a reminder, on the first of SMS, WhatsApp and letter the patient allows, in the patient's language). Outreach does not draft when an automatic template for the same purpose was already sent on that case in the last 7 days (`message_sent {auto: true}` with that purpose in the log); such a case is deferred with the reason `automation_template_sent`. |
| `referral_rejected` | Referral and Scheduling | `referral_rejected` | `reroute_referral` |
| `patient_acknowledged`, no handoff pending, referral milestone not yet met | Referral and Scheduling | `referral_needed` | `prepare_handoff_pack` |
| `confirmatory_booked` | Referral and Scheduling | `booking` | `booking_followup` |
| `confirmatory_completed` | Evidence and Closure | `evidence_check` | `retrieve_evidence` |
| `clinician_reviewed` | Evidence and Closure | `awaiting_signoff` | `prepare_closure_pack` |
| A reply with no classification | Patient Support | `pending_reply` | One `triage_reply` per reply (queued for a person) |
| A callback requested and no human activity since | Patient Support | `callback_requested` | `callback_brief` |
| Overdue or escalated | Quality and Operations | `sla_breach` | `sla_risk_explainer` |

The head does not make a task if the case already has an open one of that type, if nothing has changed on the case since its last one, or if the case is inside the cooldown for that type (draft, 3 days; call sheet, call-back brief, explanation and referral pack, 1 day). A task that failed in a tool or in its output still counts as the case's last task for the "nothing has changed" test, but not for the cooldown; a task that was never started (`budget_exhausted`, `abandoned`) counts for neither and is planned afresh. The three daily tasks are made once per simulated day.

## 4. What stays with people

An agent cannot do any of these. There is no tool for it, the policy lists it in `AGENCY_NEVER`, and the state machine refuses any event from an agency actor.

| Stays with | Decision | Where it is done |
|---|---|---|
| The escalation target's role | Accepting an escalation (`escalation_accepted`), which stops the ladder. | The case page, as before. |
| The case owner (or a supervisor) | Approving or rejecting a message an agent drafted (`message_approved`, `message_rejected`); only then does the notification mock send (ADR-030). | The draft in the inbox links to the approval on the case page. |
| The owning clinician | Reviewing (`clinician_review`), signing and closing a case (`close`, with a resolving evidence reference and `signed_by`), and reopening it. | The case page. The closure pack is material; it does not close. |
| The receiving provider's clinician | Accepting or rejecting a referral (`handoff_accepted`, `handoff_rejected`); ownership changes only at acceptance. | The case page. |
| The coordinator or supervisor | Acting on a reroute recommendation (requesting the referral, `handoff_requested`), handling an exhausted reroute (every provider tried), and reassigning an owner (`reassign`). Getting a case that has no owner accepted by one (`accept_ownership` is refused to every agent; the draft agent asks the coordinator to arrange it). | The case page. A reroute is only a recommendation in the inbox, and an exhausted one is always a person's. |
| The patient (or the coordinator for them) | Consent, including withdrawal (`consent_updated`), and a callback request (`callback_requested`). | The patient page, or the case page. |
| A person reading | Every patient reply, whether or not D13 has labelled it. | The case page. |

What an agent causes, it causes through another actor's own path and under that path's rules: the only such request is a draft, which is the drafting route's (provider, validator, template fallback, consent and closed-case refusals, Provenance, `message_drafted` by a `model:` actor, the agency recorded as requester); an agent makes no other request.

## 5. The ledger and the inbox

**The ledger** (`agency_tasks`, migration `014_agency_tasks.sql`; an in-memory twin for tests and no-database runs) holds one row per task. A run is a tree: the supervisor's root row (its id is the run id), one dispatch row per department (agent type `department_head`), and the executioner tasks under them. A row records what the agent was asked (`input`: ids, references and labels, never text), what it produced (`output`, including each tool call), its provenance (`provider`, `model_id`, `fallback_used`, `prompt_hash`), simulation times (created, started, finished) and, for a task that waited for a person, who resolved it and why (`resolved_by`, `resolution`).

The ledger is append-only except for status. A task moves queued to running or failed; running to done, failed or needs_human; needs_human to done or failed; nothing else. A recorded value is never overwritten. A finished row must carry its provenance and its output. The database trigger refuses delete, truncate, a change to what a task was, a status move outside that graph and an overwrite; the application's database role has no `DELETE` on the table. Rules and tools record `provider = rules` with a model id of the form `agency-<type>-v1`; a draft records the drafting provider and prompt hash; a plan records the planner's. The ledger is not a case event and changes no case.

**Reading it.** `GET /agency/org` is the org chart with each agent's tools, output schema, the human actions it replaces and its counts. `GET /agency/runs` summarises runs (status, planner provenance, counts by status and department, rationale per department). `GET /agency/tasks` lists tasks with filters (status, department, case, run). All three are for the coordinator and supervisor.

**The inbox** (`GET /agency/inbox`) lists the tasks that need a person, newest first. Each item has the department, agent type, case, the role it is assigned to, a title, a summary, the recommended action, the material the agent prepared (call script, pack, brief, flags) and, for a draft, a link to it. An owner sees the items on cases they own; the coordinator and the supervisor see all. It offers no action. The person works on the case page, through the ordinary routes and checks. `GET /cases/{id}/agency` gives one case's tasks and counts (the owner on their own case, the coordinator, the supervisor).

**Resolution.** At the start of each run the supervisor reconciles the waiting tasks. A draft task is resolved when its draft is approved or rejected, naming the decider. A reply task is resolved when the reply is no longer pending. A call-back task is resolved when someone has called back. Any other task is resolved when its case has left the state it was planned for or has closed, naming the first human who acted on the case since the task was made (the supervisor if nobody did). A case-less item (a summary, an anomaly check, a fairness watch) is resolved as superseded when the next run makes a fresh one. A task left running by a run that died is failed as abandoned.

## 6. Running it, the schedule, the budget

**Starting a run.** `POST /agency/run` starts one run at the current simulation time. It needs a `supervisor` user or the agency service token (section 8); a coordinator or any other role is refused, and so are the scheduler and watchdog tokens. The run is synchronous and returns its summary.

**The scheduler hook.** After every sweep, `POST /scheduler/sweep` calls `after_sweep`. Every `AGENCY_EVERY_SWEEPS`-th sweep (default 6; 0 switches the hook off) starts a run with trigger `scheduler` in a background thread, unless one is already going. The hook never raises into the sweep, and the sweep's answer reports whether it triggered.

**Idempotent.** A run's id is `uuid5(trigger, simulation time)` and a task's id is `uuid5(run, case, task type, key)`. Running again with the same trigger at the same simulated instant finds the run and inserts nothing (`created: false`). The `manual` and `scheduler` triggers are different runs, but the second plans nothing the first left waiting for a person. Several concurrent calls make one run: a process lock, then the deterministic ids across processes.

**Bounded.** The supervisor reads facts only for the cases changed at or after the sim time of the previous completed run, plus every open case that is overdue or watchdog-flagged (tool `read_active_cases(since)`); on a first run, or when the previous run ran out of budget, it reads all cases. A wall-clock budget (`AGENCY_RUN_BUDGET_SECONDS`, default 20) covers the supervisor's fact-building, every head's planning and the executioners, and there is a cap on the number of tasks (`AGENCY_MAX_TASKS`, default 300). Past the cap, planning stops. If the budget runs out before a head has planned, its dispatch row is failed with `budget_exhausted`; past it during execution, each remaining task is failed unstarted with `budget_exhausted`. Either way the run's status is `budget_exhausted`, and nothing is carried over, because the next run plans afresh. A tool that fails transiently (the evidence server not answering) is tried twice in all and then the task fails; no other error is retried. A follow-up an executioner asks for is one level deep, for the same case, and runs under its own department's identity and allow-list.

## 7. The labour it absorbs

This table is the input to the effort accounting (B17): for each executioner, the human actions it replaces (the `replaces` field of its `ExecutionerSpec`, also returned by `GET /agency/org`) and who still decides. "Replaces" is the preparation, not the decision. No figure of minutes saved is claimed here; the counts of tasks by agent type and outcome are in the ledger (`GET /agency/tasks`), and B17 owns turning them into time.

| Executioner | Department | Human actions it replaces | Who still decides |
|---|---|---|---|
| `draft_message` | Outreach | Write a reminder or first message from scratch; pick the channel and language the patient can receive. | The case owner approves or rejects the draft; nothing is sent otherwise. |
| `followup_call_prep` | Outreach | Look up the contact history before calling; write a call script that discloses no result. | The owner or coordinator makes the call and records the contact. |
| `reroute_referral` | Referral and Scheduling | Work out which provider has not yet been asked after a rejection or bounce, and prepare the re-route for the coordinator. | The coordinator reassigns or requests the handoff; the receiving provider's clinician accepts (`handoff_accepted`); the coordinator or supervisor handles an exhausted reroute. |
| `prepare_handoff_pack` | Referral and Scheduling | Assemble the referral details and deadline; check consent to share before referring. | The owner requests the referral. |
| `booking_followup` | Referral and Scheduling | Check whether a booked appointment has passed without a result. | The owner records the result or rebooks. |
| `retrieve_evidence` | Evidence and Closure | Search FHIR for the colonoscopy report before closure. | The owning clinician signs the closure with a resolving evidence reference. |
| `prepare_closure_pack` | Evidence and Closure | Assemble the case history, milestones and evidence before signing. | The owning clinician reviews and signs. |
| `flag_missing_evidence` | Evidence and Closure | Notice that a closure has no resolving evidence. | The owner records the colonoscopy result. |
| `triage_reply` | Patient Support | Notice every incoming reply and queue it for a person, with the classifier's labels when D13 has written them. | A person reads every reply. |
| `callback_brief` | Patient Support | Gather the contact history before calling a patient back. | The coordinator calls back. |
| `daily_summary` | Quality and Operations | Compile the daily status report. | The supervisor reads it. |
| `anomaly_check` | Quality and Operations | Scan alerts, replay failures and unsafe closures each morning. | The supervisor acts on a flag. |
| `fairness_watch` | Quality and Operations | Check that no language or channel group is served worse. | The supervisor and the clinical lead act on a flag. |
| `sla_risk_explainer` | Quality and Operations | Explain to a colleague why a case is at the top of the worklist. | Nobody decides on it: it is a sentence for staff, and no dispatch reads it. |

## 8. Configuration

All are environment variables read by `api/app/config.py`.

| Variable | Meaning | Default |
|---|---|---|
| `AGENCY_EVERY_SWEEPS` | The scheduler hook starts a run on every N-th sweep. `0` switches the hook off; `POST /agency/run` still works. | `6` |
| `AGENCY_RUN_BUDGET_SECONDS` | Wall-clock seconds for one run, covering the supervisor's fact-building, every head's planning and the executioners; past it the dispatches not yet planned and the tasks not yet started are failed as `budget_exhausted`. | `20` |
| `AGENCY_MAX_TASKS` | Most executioner tasks one run may create; past it planning stops. | `300` |
| `AGENCY_SERVICE_TOKEN` | The agency supervisor's service token: maps to `system:agency:supervisor`, reaches `POST /agency/run` and no other route, and emits no event. Optional: empty switches the token off (a `supervisor` user and the scheduler hook still start runs). Outside `APP_ENV=dev`, if set, it must not be the built-in default, must be at least 32 bytes and must differ from the other service tokens; the API refuses to start otherwise. | the development default `dev-only-agency-service-token` |
| `AGENCY_CONFIG_PATH` | The agency's configuration file: the receiving providers in the order a reroute tries them, and the call script in English and Arabic. The Arabic lines are an unreviewed draft (H2). | `config/agency.yaml` |
| `LLM_PROVIDER` | The planner's provider, the same setting as drafting (ai-boundary section 3). `template` (default) plans by rules alone and calls no model. | `template` |
| `LLM_CACHE_MODE`, `LLM_CACHE_DIR`, `LLM_NETWORK_ENABLED` | The planner's record/replay cache (files under `<cache dir>/agency/<provider>/`) and network switch, the same as drafting. The cache is empty until someone records it, so with a model provider and nothing recorded every plan is the rule plan with the reason `cache_miss`. | `replay`, `api/app/ai/cache/`, `false` |

The agents hold no keys. The planner reaches a model only through the drafting provider's adapter, with the same rules (https only, temperature 0, a timeout falls back).

## 9. Events read from other branches

The agency was built on its own branch. It has no code from the two concurrent ones (A21, the automation service, branch cut-auto; D13, reply triage, branch cut-triage), and it neither calls them nor registers anything with them. `app.agency.compat` holds event-name constants only. When these events are in a case's log the agency reads them; it never writes them.

- **D13, reply triage**: `reply_received` and `reply_classified`. A `reply_received` with no `reply_classified` after it is a pending reply (an event id; never the text) and routes to Patient Support, which queues it for a person. Once D13 has labelled it, the queued item carries the labels.
- **A21, the automation service**: an automatic `message_sent {auto: true, purpose}` and `closure_proposed`. An automatic send is kept in the case facts as a purpose and a time (`CaseFacts.auto_messages`, never the text); Outreach does not draft a message for a purpose that an automatic template already covered on that case in the last 7 days. `closure_proposed` is noted in the facts.

Without those branches there is nothing to read and the agency behaves the same. There is no merge wiring: nothing to register, no function to provide and no setting to switch on. A reroute is always a recommendation and a reply is always read by a person, whether or not the other branches are merged.

## 10. What was measured

Two runs, both offline with the template planner (`LLM_PROVIDER=template`).

**The seeded demo cohort, as it stands.** The running stack's 22 cases were read through `GET /cases/{id}` (read only) into an in-memory copy and the agency was run on the copy at the stack's sim time (5 Jan 2026 08:00). 21 cases are at `result_received` with no owner and one is at `confirmatory_completed`, so there is little labour to take: 4 executioner tasks, all `done` (Evidence and Closure 1 `retrieve_evidence`; Quality and Operations `daily_summary`, `anomaly_check`, `fairness_watch`), 0 `needs_human`. Seven days later, after a sweep, the 21 unowned cases are overdue for acknowledgement and the run adds 21 `sla_risk_explainer` sentences (24 tasks, all `done`, 0 `needs_human`). Acknowledging a result and taking ownership are a person's (or, once A21 is merged, the automation service's); no agent does them.

**The 22-case test cohort** (`api/tests/agency_world.py`, the same ids walked to a spread of states). One run: 17 executioner tasks, 5 `done`, 12 `needs_human`, 0 `failed`, status `done`.

| Department | Tasks | done | needs_human | What |
|---|---|---|---|---|
| Outreach | 7 | 0 | 7 | 5 `draft_message` (drafts waiting for the owner), 2 `followup_call_prep` |
| Referral & Scheduling | 4 | 0 | 4 | 2 `prepare_handoff_pack`, 1 `reroute_referral` (a recommendation for the coordinator), 1 `booking_followup` |
| Evidence & Closure | 3 | 2 | 1 | 2 `retrieve_evidence` (one a follow-up of the passed appointment), 1 `prepare_closure_pack` |
| Patient Support | 0 | 0 | 0 | no replies or call-back requests in the cohort |
| Quality & Operations | 3 | 3 | 0 | `daily_summary`, `anomaly_check`, `fairness_watch` |

The only case events the run caused were 5 `message_drafted` by `model:template`; no case changed state. The simulated day (T-A64: 24 hourly sweeps, a run every 6) completes 4 runs, all `done`, with the owner approving the drafts from the inbox between runs and the agency resolving those tasks as `draft_approved`.

The tests do not measure benefit. They prove the boundary: nothing an agent does appears as a case event, a full run on the cohort changes no case state and leaves every case log byte for byte identical except for the drafts the drafting route wrote, which wait for approval (T-A61), a repeat is the same run and a run is bounded, with a budget that covers the supervisor's reading and the heads' planning and reads limited to changed, overdue and flagged cases (T-A62), every finished task has its provenance (T-A63), a simulated offline day with the rule planner completes (T-A64), the routes and the inbox behave as listed (T-A65), and no text reaches a planner (T-A66).

## 11. Limits

- Wall-clock budget: a slow machine plans the same day differently from a fast one. The next run plans afresh, so work is delayed, not lost.
- The run lock is per process. Several API replicas rely on the deterministic ids, which stop a second run at the same simulated instant but not two runs at different instants.
- The ledger grows with every run and nothing prunes it.
- The provider order for a reroute is the order in `config/agency.yaml`. It does not weigh capacity, distance or language, and the names are synthetic.
- The fairness and anomaly thresholds (10 percentage points, at least 5 cases; 20% failures from ten tasks) are not clinically reviewed (H1).
- The demo's event-log hash (T-E9) changes if the scheduler hook drafts on the demo case, because a draft is a `message_drafted` event. See [tools/demo/README.md](../../tools/demo/README.md).
- Nothing here has been used by a coordinator. How many items a person can work from an inbox in a day, and whether approval of a larger number of drafts stays careful (HZ-29), are not measured.
