# Demo script: 7 minutes, Q&A and backup video

The live demo for the GITEX finals, if shortlisted, plus the answers to the questions we expect and the storyboard for the 3-minute submission video. **All data shown is synthetic**, and every number spoken comes from [`docs/results/results.md`](../results/results.md) (literature-anchored scenario) or [`docs/evidence.md`](../evidence.md); the source is given next to each line.

Timing rule: the script is written for 7:00 and must be rehearsed to 6:30 or less (T-E11). Rehearsals and their timings are logged by the human owner (H3) in `docs/demo/rehearsals.md`; recording the backup and submission videos is H4.

## Status of what this script depends on

| Dependency | Ticket | State on this branch | Fallback if not ready |
|---|---|---|---|
| Seeded scenario with one named synthetic patient, `tools/demo/scenario.py`, and `make demo-reset` | E8 | Being written on main; not on this branch | Walk a seeded case by hand through the UI; the mock adapter's **Reset demo** button restores its 16 fixture cases |
| `make demo-kill-scheduler` (stop, alarm, restart, verify nothing lost) | F11 | Planned | `docker compose stop scheduler`, then `docker compose start scheduler`; `make test-chaos` is the automated version (T-A32) |
| Offline run with outbound network blocked | F10 | Planned | Default config already makes no outbound calls: template drafting, model network off, bundled fonts |
| Recorded model outputs for the Arabic draft | D6 | Cache committed empty | The template draft is shown and labelled as the template (fallback badge) |
| Web app against the real API | C1 to C11 | Built; e2e suite runs on the mock adapter | `make web-dev` with the mock adapter (default) |

The scenario steps below are the plan's steps (`fhdic-challenge05-plan.md`, demo script), mapped to the screens and buttons that exist in `web/` today. When `tools/demo/scenario.py` lands, check each step's name against it and update this table and the step list in the same commit.

## Before going on stage (T-minus 15 minutes)

1. Laptop on mains power, notifications off, Wi-Fi off (the demo needs no network).
2. `make up`, then `make demo-reset` (E8): database and simulation clock back to the scenario start. Without E8: `make reset && make demo-seed`.
3. The scenario needs a `DiagnosticReport` or `Procedure` for the colonoscopy that exists in HAPI, because compose sets `FHIR_VERIFY_EVIDENCE=true` and `close` is refused with 422 otherwise (A15). The scenario script creates it; check `GET /cases/{id}/fhir` lists it.
4. Start the web app against the API: `cd web && VITE_API_ADAPTER=http VITE_API_URL=http://localhost:8000 npm run dev`.
5. Open four browser tabs: `/coordinator` (worklist), the scenario case at `/coordinator/<caseId>`, `/patient/<caseId>`, `/dashboard`. Open one terminal tab with the repo root, large font.
6. Check: the **SIMULATED TIME** badge shows the scenario start; `curl -s localhost:8000/alerts` (as coordinator) shows no open alert; `docker compose ps` shows the scheduler and watchdog up.
7. Have `docs/results/results.md` open at the headline table, and `docs/governance/decision-matrix.md` and `docs/governance/hazard-log.md` in a second window.

## The 7 minutes

Timings follow the plan file. One change in order: the watchdog raises the scheduler alert 60 to 70 seconds after the scheduler stops (measured 68 s, ADR-023), so the scheduler is stopped early in the live segment and the alert is collected about two minutes later instead of waiting on stage. As a result the overdue flag and the escalation, which the plan shows before the Arabic message, appear when the scheduler restarts (step 9): the catch-up is what proves nothing was lost.

### 0:00 to 0:40 The problem

On screen: a title slide with one number.

> "In Abu Dhabi, an audit of positive screening tests for bowel cancer found that only 30.5% of patients ever had the follow-up colonoscopy." (Almansoori et al., BMC Cancer 2021; [evidence.md](../evidence.md) section 8b)
>
> "Across 39 US health systems, about 44% had none within a year." (Mohl et al. 2023; evidence.md section 2)
>
> "Nobody decided to lose these patients. The result arrived, everyone assumed someone else had it, and nothing raised a flag. The dangerous failure here is silence."

### 0:40 to 1:00 What this is

> "This is an accountability and closure engine, not a diagnostic tool. Every positive result gets a named owner, a clock, an audit trail and a clinician-signed closure. Nothing is diagnosed, and no machine can close a case."

### 1:00 to 4:30 Live: catch and close

Each step names the screen, the action and the line to say. Clock jumps are scenario-script steps (`POST /clock/advance` as supervisor); there is no clock button in the UI.

| Time | Step | Screen and action | Say |
|---|---|---|---|
| 1:00 | 1. Result arrives | Worklist `/coordinator`: the scenario patient's new case at the top, state "result received", SIMULATED TIME badge visible | "A positive FIT for our synthetic patient has just arrived from the lab." |
| 1:10 | 2. Lab acknowledges | Case page, role **lab** or coordinator (header **Switch role**): **Acknowledge result** | "The lab acknowledges. The clock for ownership starts." |
| 1:20 | 3. Primary care accepts | **Switch role** to owner: **Accept ownership** | "Dr Haddad now owns this case. Her name is on it, and the timeline says so." |
| 1:30 | 4. Kill the scheduler | Terminal: `make demo-kill-scheduler` (F11), or `docker compose stop scheduler` | "Now the thing that runs every clock. I am killing it, on stage. Watch the dashboard later." Leave it stopped. |
| 1:40 | 5. Patient unreachable | **Record patient contacted**, then a scenario step advances the clock past the 7 working-day acknowledgement limit and far enough for the supervisor rung (3 working days after the due date). The scheduler is stopped, so no overdue flag appears yet: that is the point of step 9. | "Two weeks pass. The patient does not answer, and with the scheduler dead nothing would tell anyone." |
| 2:00 | 6. Arabic draft, owner approves | Case page, **Patient message drafts** panel: Language Arabic, Channel SMS, **Request draft message**. Point at the provider, the template-fallback badge if shown, and the prompt hash. **Approve draft**: "Sent". | "The model drafts, in Arabic, and is never shown the patient's name or number. It cannot send. Only the owner can approve, and the approval and the send are in the audit trail." |
| 2:30 | 7. Patient acknowledges on the PWA | Tab `/patient/<caseId>`: Arabic, right to left. Point at "I don't understand this". Tap **I understand** | "The patient sees plain language at about grade 6, who owns her case, and a button that gets a human to call her." |
| 2:50 | 8. Referral rejected with a reason, bounces, re-routed, accepted | Case page as owner: request hand-off to endoscopy; switch to specialist: **Decline hand-off** with reason "No capacity"; the red banner and the timeline entry; the coordinator re-routes (reassign panel) or a second endoscopy clinician uses **Accept hand-off to ...**. The pathway returns a re-routed case to `owner_accepted`, so the scenario script replays contact, acknowledgement and booking for the new owner in one step. | "Endoscopy says no, with a reason from a fixed list. Ownership never left primary care, so the case cannot fall between the two. It is re-routed and accepted, and the 31 working-day colonoscopy clock starts at that acceptance." |
| 3:30 | 9. The watchdog caught it | Tab `/dashboard`: red banner (scheduler down) since about step 5. Terminal: start the scheduler. Banner clears within about 10 s. Back on the case page: the overdue flag and the escalation rung appear, in order, with nothing skipped | "While we talked, the watchdog noticed the scheduler had stopped. Restart it: the alert clears, the missed overdue flag and escalation are emitted once each, in order. Nothing was lost." |
| 3:50 | 10. Supervisor accepts the escalation | **Switch role** to supervisor: **Accept escalation** | "Every rung needs a human to accept it. No escalation is fire-and-forget." |
| 4:00 | 11. Colonoscopy done, clinician signs closure | Scenario step: clock advanced, colonoscopy completed. As the clinician: **Evidence reference** = the scenario's `DiagnosticReport/...`, **Signed by** = the clinician, **Sign off and close case**. Heading shows "Closed", timeline "Case closed (signed)" with a human actor, no automated chip | "Closure needs evidence that resolves in the FHIR store and the signature of the clinician who closes it. A wrong reference is refused." |
| 4:15 | 12. Audit trail and the property test | Scroll the timeline. Terminal: `.venv/bin/pytest -q "api/tests/test_state_machine.py::test_never_closed_without_evidence_and_human_signoff"` | "This test throws 2,000 random event sequences at the engine every run. Not one reaches closed without evidence and a human sign-off." |

If the clock is behind at 3:30, drop step 10 (say it instead) and shorten step 8 to the rejection banner only.

### 4:30 to 5:30 The numbers

On screen: `/dashboard` for the live case, then the headline table in [results.md](../results/results.md).

> "These numbers come from a simulator on synthetic data, with every parameter sourced or labelled assumed. We quote the conservative scenario, held to what published patient-navigation trials achieve."
>
> "Within 180 days, 66% of patients reach a colonoscopy, against 52% under today's process." (results.md headline, `scenarios.literature_anchored.arms.*.completion_180d`: 66.2% and 52.1%)
>
> "Cases lost without anyone being told fall from 44.5% to 22.5%." (`silent_loss_rate`)
>
> "At 90 days, 47% are still open instead of 62%." (`open_90d`: 47.2% and 62.4%)
>
> "This is before capacity: escalations cost nothing in the model. We would size a pilot by the 53 escalations per 100 cases, not by the completion gain." (`escalations_per_100_cases`: 52.78)
>
> "Do not trust me: run `make evaluate`, change any parameter, and every number regenerates in about ten seconds."

### 5:30 to 6:15 Governance

On screen: the decision matrix, then the hazard log.

> "Three things a machine can never do here: close a case, approve a message, or change consent. The matrix lists every event, who may emit it and the test that proves it. The model can produce one event, a draft."
>
> "The consent ledger freezes outreach in the engine itself, not just the screen."
>
> "Twenty hazards, each with its control and the test that proves the control. A clinician and a native Arabic speaker are reviewing the wording and the rules before any pilot."

### 6:15 to 6:45 The ask

> "One programme, colorectal screening. Two providers. Ninety days, shadow mode first: for the first 30 days the engine watches and sends nothing. We measure time to ownership, time to safe closure, cases open at 90 days, and one number that must stay at zero: cases closed without evidence. If it ever moves, we stop." ([pilot-plan.md](../pilot-plan.md), KPIs K1 to K8 and the stop rule)

## Q&A preparation

**"Is this just a ticket tracker?"**
A ticket tracker records what people type. Here the rules are enforced by the engine: a case cannot close without evidence that resolves in the FHIR store and the closer's own signature, and a property test checks that over thousands of random event sequences (T-A1). Clocks run on a working-day calendar and escalate in order to named people who must accept. Silence is detected: idle cases are flagged and a stopped scheduler raises an alert (T-A30, T-A31). Consent is an append-only ledger that freezes outreach in the engine. Every event is projected to FHIR R4 with Provenance. And the evaluation reports escalation precision, 43.3% in the conservative scenario, so we can see whether escalations are acted on rather than assume it (results.md, `escalation_precision`).

**"What's real?"**
The engine, the tests, the FHIR projection and the evaluation code are real and run on this laptop. The data is synthetic: a Synthea cohort and a seeded simulator. Of 132 simulator parameters, 88 are assumed, 16 calibrated, 13 sourced and 15 sourced-adjacent (`meta.parameter_status_counts`), and the assumptions table lists 37 limitations ([assumptions-limitations.md](../assumptions-limitations.md)). Put the table on screen, then: "run `make evaluate` yourself, and change any parameter you doubt." Nothing is integrated with Malaffi or a provider today, and no message is really sent.

**"Why not use agents for this?"**
Because the transitions that matter are safety-critical: is this case overdue, who is told next, may it close. They must give the same answer every time, be replayable from the log and be explainable to a regulator line by line. A model can draft a message well, and we use it there, behind a human approval. A model that decides a closure can be wrong in a way that looks right, and the case then leaves every worklist and every timer. Here that is impossible by construction: an automated actor cannot emit `close`, and a model actor can emit only `message_drafted` (T-D2). If the model is down, nothing changes except that the draft comes from a template ([design-choices.md](../design-choices.md)).

**"What about capacity?"**
The model has none, and we say so in the results: escalations cost nothing, and the orchestrated arm raises about 53 per 100 cases in the conservative scenario (99 in the upper range). The completion gains would shrink without the people and colonoscopy slots to act on them. That is why the pilot starts in shadow mode, measures coordinator minutes per case and escalation precision on a sample, and why we size it by the escalation count. A capacity model is future work (L08).

**"Malaffi is HL7 v2, not FHIR."**
Agreed, and our plan assumes it. The official sources we read list Malaffi's minimum dataset as HL7 v2 message types, and no official document confirms a FHIR interface (evidence.md section 8c). FHIR is our internal model. The adapter plan puts an HL7 v2 adapter at the boundary: ORU for results, ADT for demographics, SIU for appointments, the message control id as the idempotency key, and an automated actor that can never close a case. It is a target, not a claim, and the pilot does not depend on it ([malaffi-adapter-plan.md](../governance/malaffi-adapter-plan.md)).

**"ADHICS does not allow cloud for health data."**
The version we read, ADHICS v0.9 control CM 4.2, prohibits cloud for health information unless DoH authorises it; the current V2 is to verify. So our hosting order is: the provider's own approved environment first, a UAE cloud with written DoH authorisation second, nothing else (ADR-026). The stack is containers and Postgres with no cloud-managed service, so it runs unchanged in the provider's data centre. Backups, disaster recovery and remote support stay in the UAE. The model host follows the same order, and without one the template is used.

**"Why these ages and these SLAs?"**
Eligibility of 40 to 75 with an annual FIT is sourced from the UAE National Guideline and DoH IFHAS V10 (evidence.md section 8a). The demo cohort loaded today was generated for 45 to 75 and is regenerated before the freeze (L02). The 31 working days for the colonoscopy is the national KPI, measured from referral, so our clock starts at referral acceptance (A13). The other SLAs (acknowledge 2, accept 2, contact 5, patient acknowledges 7, book 14, review 5, sign-off 3, re-route 2 working days) are design choices in a short YAML file; the clinician review (H1) will set them, and a change is a config edit and a rerun of `make evaluate`, not code.

## Backup video storyboard (3 minutes)

For the submission video (H4): 20 s problem, 2 min the catch-and-close scenario, 30 s metrics, 10 s pilot ask. Each shot maps to one scripted step above (T-E15). Screen recording at 1080p, captions burned in, voice-over recorded separately. The SIMULATED TIME badge must be legible in every app shot. Total 3:00.

| # | Time | Duration | Scripted step | Shot | On-screen caption |
|---|---|---|---|---|---|
| 1 | 0:00 | 0:10 | Problem | Title card, then the number | "Abu Dhabi audit: 30.5% of positive screening tests ever reached a colonoscopy (BMC Cancer 2021)" |
| 2 | 0:10 | 0:10 | What this is | Product name and positioning line over the worklist | "A named owner, a clock, an audit trail, a clinician-signed closure. Nothing is diagnosed. All data synthetic." |
| 3 | 0:20 | 0:10 | 1. Result arrives | Worklist, new case at the top | "A positive FIT arrives (synthetic patient)" |
| 4 | 0:30 | 0:10 | 2, 3. Acknowledge, accept | Case page: Acknowledge result, Accept ownership | "The lab acknowledges. Primary care takes ownership." |
| 5 | 0:40 | 0:10 | 4. Kill the scheduler | Terminal: scheduler stopped | "We stop the scheduler on purpose" |
| 6 | 0:50 | 0:10 | 5. Overdue | Clock jump, case page | "A week later: no answer from the patient" |
| 7 | 1:00 | 0:20 | 6. Arabic draft, approval | Drafts panel: request, provenance, approve, Sent | "The model only drafts. The owner approves. Every step is logged." |
| 8 | 1:20 | 0:10 | 7. Patient PWA | Patient page in Arabic, I understand | "Arabic, right to left, plain language, a button to reach a human" |
| 9 | 1:30 | 0:20 | 8. Referral rejected and re-routed | Decline hand-off with reason, bounce banner, re-route, accept | "Rejected with a reason. Ownership never left primary care. Re-routed and accepted." |
| 10 | 1:50 | 0:15 | 9. Watchdog | Dashboard banner, restart, banner clears, overdue and escalation appear | "The watchdog caught the dead scheduler. Restarted: nothing lost, nothing skipped." |
| 11 | 2:05 | 0:05 | 10. Escalation accepted | Supervisor accepts | "Every escalation needs a human to accept it" |
| 12 | 2:10 | 0:10 | 11, 12. Sign-off and proof | Sign off and close case, timeline, property test passing | "No closure without evidence and a clinician's signature: tested on 2,000 random sequences every run" |
| 13 | 2:20 | 0:30 | Numbers | Headline table from results.md, three rows highlighted | "Simulation on synthetic data, conservative scenario: colonoscopy within 180 days 52% to 66%; lost without anyone told 44.5% to 22.5%; open at 90 days 62% to 47%. Before capacity limits. Rerun: make evaluate" |
| 14 | 2:50 | 0:10 | Ask | Pilot slide | "Pilot ask: one programme, two providers, 90 days, shadow mode first. Cases closed without evidence: must stay zero." |

Governance (5:30 to 6:15 in the live demo) has no shot of its own in the 3-minute video; captions 7, 11 and 12 carry it, and the technical documentation covers it in full.

## If something fails on stage

| Failure | Recovery |
|---|---|
| API or database down | Switch the web app to the mock adapter (`make web-dev`), press **Reset demo**, and run the same steps; on the dashboard use **Simulate scheduler down** for step 9. Say that this is the in-browser fallback. |
| Watchdog banner slow to appear | Keep talking through step 8; the alert is due 60 to 70 s after the stop. If it has not appeared by 3:45, show `curl localhost:8000/alerts` and move on. |
| `close` refused with 422 | The evidence reference does not exist in HAPI. Use the scenario's reference from `GET /cases/{id}/fhir`; say "and that refusal is the point". |
| Anything else | Play the backup video from the matching shot. |
