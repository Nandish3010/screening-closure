# AI incidents and monitoring

How the programme notices, classifies, reports and learns from failures of the AI components, which switches stop them, how drift is watched and when a model may be retrained. Cards for each component: [model-cards/](model-cards/README.md). Overall framing: [responsible-ai.md](responsible-ai.md). Related: [ai-boundary.md](ai-boundary.md), [hazard-log.md](hazard-log.md), [operations.md](operations.md), [deployment.md](deployment.md), [dpia.md](dpia.md), [adhics-controls.md](adhics-controls.md), [red-team.md](red-team.md).

**Status.** This is a process on paper plus the switches and metrics that already exist in code. Nothing has been deployed, nobody has been appointed to the roles below, and the external reporting route is not confirmed (section 3). The prototype runs on synthetic data only; the process is written so it can be used from the first pilot day.

## 1. What counts as an AI incident

An **AI incident** is an event in which an AI component (reply triage, risk score, Claude drafting, Claude planner) or its safety wrapper (validator, approval gate, template fallback, cache) produced, or let through, an output that harmed a patient, could have harmed a patient, or broke a rule in [ai-boundary.md](ai-boundary.md). A **near miss** is the same kind of event caught before it reached a patient or changed anything (for example the validator rejected a disclosing draft and the template was used; or a coordinator rejected a draft that should not have been proposed). Near misses are recorded and reviewed monthly; they are not reported externally unless the clinical safety officer decides they are.

| Severity | Meaning | Examples specific to this system |
|---|---|---|
| **S1 Critical** | A patient was, or is about to be, harmed or wrongly informed, or a hard boundary was broken | A message that states or hints at the screening result reached a patient (breach of the non-disclosure rule, ai-boundary.md section 4a); a model output moved case state, approved a message, changed consent or closed a case (breach of the actor table); a draft was sent without human approval (HZ-29); an opt-out was missed by triage **and** the patient was contacted afterwards (HZ-25); personal data was sent to a model endpoint (HZ-39); a validator bypass by a real model output that contained a diagnosis or reassurance and was approved |
| **S2 High** | The control failed but nothing reached a patient, or a safeguard has degraded across many cases | A validator bypass caught by the owner at approval; a triage `opt_out` labelled as another intent with high confidence and auto-handled (flag raised for the wrong reason) but caught by the coordinator; a fairness gap between languages opens beyond the tolerances in section 7 on a gated metric; a fallback storm after a provider outage lasting more than one hour in which patient messages are delayed; abstention rate for a language above the section 5 threshold for a week; a signed policy edited without a version bump |
| **S3 Moderate** | Quality is degraded, safe state held | Drift beyond the section 5 thresholds without a patient-facing effect; triage `needs_human` rate rises sharply (more work, not less safety); token budget exhausted (`budget_exhausted`); planner replies repeatedly invalid so every plan is the rule order; risk score `null` for many cases |
| **S4 Low / near miss** | Control worked as designed | A forbidden-phrase rejection followed by the template; one invalid planner reply; a coordinator rejects a draft |

Not an AI incident (use [operations.md](operations.md) failure modes): a stopped scheduler, a database outage, an HL7 error. A personal-data breach is also handled under the data-protection route in section 3, whether or not AI is involved.

## 2. Who is told, and how fast

Roles are placeholders until people are named. "Clock" starts when anyone on the team first sees the event.

| Role | Responsibility |
|---|---|
| On-call engineer (to be named) | First responder: contain with a switch from section 4, preserve evidence, open the record |
| Clinical safety officer (to be named, H1) | Decides severity, patient-safety actions, whether to report externally; owns the model cards |
| Data protection officer (to be named) | Any personal-data element; PDPL and ADHICS duties; the breach route |
| Programme lead (to be named) | Informs the provider organisation and DoH contact; decides on pausing the pilot |

| Severity | On-call engineer | Contain | Clinical safety officer | Data protection officer | Programme lead | External |
|---|---|---|---|---|---|---|
| S1 | Page immediately | Within 1 hour: apply the relevant switch(es) | Within 1 hour | Within 1 hour if any personal data is involved | Within 4 hours | Decide within 24 hours whether it is reportable and report via section 3 |
| S2 | Page in hours | Within 1 working day | Within 1 working day | If personal data is involved | Same working day | Clinical safety officer decides |
| S3 | Ticket | Within 3 working days | Weekly review | No | Weekly review | No |
| S4 | Log | none | Monthly review | No | Monthly review | No |

These internal clocks are proposals; no regulator has set them for this system. They are deliberately shorter than the reporting windows other sources suggest (section 3).

**Patient-safety first.** For S1 and S2 involving a patient, the case owner contacts the patient through the normal human route (call), the case is flagged for the clinician, and the case log is preserved. A model-caused wrong message is never "unsent"; a corrective message is a human decision.

## 3. External route

| Obligation | What is established | Status |
|---|---|---|
| **DoH Abu Dhabi, Policy on Use of AI in the Healthcare Sector** (Policy/AI/0.9, 30 April 2018), [policy](https://www.doh.gov.ae/-/media/E9C1470A575146B18015DEBE57E47F8D.ashx) | Applies to licensed providers and every end user of Abu Dhabi data in AI work (regulatory-mapping.md section 4). A law-firm summary ([Al Tamimi](https://www.tamimi.com/news/new-policy-on-artificial-intelligence-in-healthcare-issued-by-the-department-of-health-abu-dhabi/)) says DoH monitors compliance partly through reactive reporting of incidents affecting patient safety. The policy text on a channel, form or time limit was not found. | **(to verify)** channel, form, timeline |
| **DoH Responsible AI Standard V1** (DoH/ST/DDGO/RAI/V1/2025), [standard](https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/Responsible-AI-Standard-V1.ashx) | Read first-hand. Requires designated owners (3.1.7.1.1); assigned responsibility for monitoring outputs and exceptions, reviewing flagged cases and "reporting adverse outcomes or ethical concerns to governance bodies" (3.1.7.1.2.1 to 3.1.7.1.2.3); continuous post-deployment monitoring for degradation with automated alerts and manual review triggers (3.1.2.1.4, 3.1.3.1.3); review and corrective action on significant decline or unexpected behaviour (3.1.3.1.4); drift to trigger revalidation (3.2.3.3); risk reports communicated to stakeholders including DoH (3.3.1.4.1); periodic reporting of compliance, deviations and risks to DoH (5.1). It names no incident form, address or time limit. | Duties confirmed; **(to verify)** the incident channel and deadline with the DoH Data and Digital Governance Office |
| **DoH medical-device incident reporting** | A separate DoH standard covers medical-device incidents and adverse events ([Standard on Reporting Suspected Adverse Drug Reactions and Adverse Events following Immunization](https://www.doh.gov.ae/-/media/Feature/Resources/Standards/DOH-Standard-on-Reporting-Suspected-Adverse-Drug-Reactions-and-Adverse-Events-following-Immunization.ashx) is the ADR/AEFI one, not this). Whether the closure system is a medical device or falls under it is not established. | **(to verify)** |
| **ADHICS (information security incidents)** | Incident rows IM-1 to IM-4 in [adhics-controls.md](adhics-controls.md) section 3.11 are "not met"; one vendor page reports a 72-hour breach form and a 4-hour containment window for high-risk incidents, another no universal period. | Unverified; keep the 72-hour figure as a planning assumption only **(to verify)** |
| **UAE PDPL (Federal Decree-Law 45 of 2021)** | Personal-data breach notification to the regulator and, where required, to affected people: duties and timelines unverified ([dpia.md](dpia.md) "Breach notification"; adhics-controls.md IM-4). | **(to verify)** with the data protection officer and counsel |

**What the programme does until the route is confirmed.** For S1 (and S2 at the clinical safety officer's discretion) the programme lead sends a written notice to the DoH contact the pilot partner names and keeps the incident record (section 9). The notice is a human decision; no software sends it. The first pilot task is to ask DoH in writing what the incident channel, form and time limit are, and to record the answer here.

## 4. Kill switches that already exist

Every name below was grepped in the code. All switches fail safe: the system degrades to deterministic templates and to people reading things; no switch stops case tracking, timers or escalation.

| What to stop | How | Where | What the system then does |
|---|---|---|---|
| All outbound model calls | `LLM_NETWORK_ENABLED=false` (anything but `true` keeps the network off) and restart | `api/app/ai/adapters.py:65` (`network_enabled`); `adapters.py:131` and `api/app/ai/claude.py:256` raise `ProviderUnavailable("LLM_NETWORK_ENABLED is not true...")` | Drafts: recorded outputs are still served in `replay` cache mode (the default, `api/app/ai/cache.py:54`); an unrecorded prompt gives the template with abstention reason `provider_unavailable` or `cache_miss`. Planner: rule order. Compose default is already `false` (`docker-compose.yml:133`). Replay still serves previously recorded model text, so use the next row if a recorded output is the problem |
| Model drafting (and anything routed through the drafting provider) | `LLM_PROVIDER=template` and restart; unset means `template` | `api/app/ai/provider.py:239` (read), `:257` (unknown value fails startup); compose default is `anthropic` (`docker-compose.yml:131`), so set it explicitly | Every draft is the validated template; no abstention is counted (nothing to abstain from, ai-boundary.md section 5). Also set the cache to `LLM_CACHE_MODE=replay` with an empty `LLM_CACHE_DIR` if recorded outputs must not be served |
| Claude planner | `AGENCY_PLANNER=template` and restart | `api/app/agency/planner.py:272` (read), `:281` (`get_planner`); compose default `claude` (`docker-compose.yml:134`) | Plans by rules alone, `provider=rules`, `fallback_used` recorded |
| Agency runs from the scheduler | `AGENCY_EVERY_SWEEPS=0` and restart | `api/app/agency/runner.py:490`; `api/app/routers/scheduler.py:141`; compose default `0` (`docker-compose.yml:140`), code default 6 (`api/app/config.py:104`) | No scheduled run; `POST /agency/run` by a supervisor still works. Already-created inbox items stay |
| Reply triage | `TRIAGE_ENABLED=0` in the API environment and restart (the failure is latched per process) | `api/app/triage/classify.py:202` (docstring `classify.py:21`) | Every reply is `{intent: other, confidence: 0, model_id: unavailable, needs_human: true}` and goes to a person. Compose does not pass this variable; add it to the API service environment (to verify) |
| Risk score | No flag. Remove or invalidate `api/app/risk/model.json` and restart | `api/app/routers/cases.py:85-97` (warnings "risk score unavailable") | `risk_score` is null; the worklist is ordered by deadline. To verify on the deployed stack |
| All automation (acknowledge, assign, reroute, outreach, evidence proposals) | `AUTOMATION_ENABLED=false` and restart | `api/app/config.py:81` (default false); `api/app/automation.py:498` (startup warning); `api/app/routers/scheduler.py:179` (route answers `{"enabled": false}`); compose default `true` (`docker-compose.yml:120`) | No automatic steps; people see the same worklist; timers and the ladder still run |
| Automatic template messages | Leave the policy unsigned (`approved_by: null` in `config/outreach_policy.yaml`) and `OUTREACH_DEMO_APPROVAL=0` | `api/app/outreach_policy.py:181-186` (`signed_by`); `api/app/auth.py:343` (refuses demo approval outside dev); compose default `1` (`docker-compose.yml:126`) | The append-time guard refuses every automatic `message_sent` and logs `outreach_held {reason: policy_unsigned}` once per case. To stop an already signed policy, bump `template_set_version` without re-approval: the policy fails to load and automatic messaging switches itself off (comment in `config/outreach_policy.yaml`) |
| Spend | `LLM_DAILY_TOKEN_BUDGET`, `LLM_MAX_TOKENS_PER_CALL`, `LLM_MAX_INPUT_TOKENS_PER_CALL` | `api/app/ai/claude.py:9-14`, `:92`, `:96` | A call over budget is refused (`budget_exhausted`) and the template is used. Whether `0` is accepted as "no calls" is untested here (to verify) |

**Order of response to a drafting incident:** (1) `LLM_PROVIDER=template`; (2) clear or move the replay cache directory if a recorded output is the cause; (3) `LLM_NETWORK_ENABLED=false` stays the default; (4) record which drafts were approved and sent in the window (`GET /cases/{id}/drafts`, provenance `prompt_hash`). **Order for a triage incident:** (1) `TRIAGE_ENABLED=0`; (2) list `reply_classified` events in the window and have the coordinator re-read the replies; (3) check holds (`opt_out`, `wrong_number`) on the cases.

All switches are environment variables; changing one is a deployment change and needs the on-call engineer to record who changed what and when (section 9). There is no web toggle.

## 5. Drift monitoring

### What exists in code today

| Signal | Source | Where |
|---|---|---|
| Draft abstention rate overall, by reason, by language and by channel | `GET /metrics/abstention` returns `attempts`, `abstentions`, `rate`, `by_reason`, `by_language`, `by_channel` (reasons `validation_failed`, `provider_unavailable`, `provider_error`, `invalid_result`, `cache_miss`, plus `budget_exhausted`) | `api/app/routers/drafts.py:380`, `api/app/metrics_abstention.py` |
| LLM token use | Prometheus `closure_llm_tokens_total{provider,kind}`; Grafana panel "LLM tokens (per hour)" | `api/app/observability.py` (`record_llm_usage`), `config/grafana/dashboards/closure.json:670` |
| Platform health | `closure_cases_open{state}`, `closure_cases_overdue`, `closure_outbox_pending`, `closure_sweeps_total{result}`, `closure_sweep_duration_seconds`, `closure_leader{lease}`, `closure_service_heartbeat_age_seconds{service}`, `http_server_request_duration_seconds` | `api/app/observability.py`; alert rules in `config/prometheus/alerts.yml` (SchedulerNoLeader, SweepErrors, OutboxBacklog, OverdueCasesGrowing, ApiHighErrorRate, MetricsMissing and others; see [deployment.md](deployment.md) "SLOs and alerts") |
| Triage classification | `reply_classified {intent, confidence, model_id, needs_human}` events in the case log; stored replies | `api/app/routers/replies.py` |
| Agency planner fallbacks and failures | Ledger rows (`fallback_used`, status `failed`, `needs_human`), `GET /agency/runs` | `api/app/routers/agency.py` |
| Closure outcomes | `GET /metrics/closure` | `api/app/metrics.py` |

**No Prometheus alert rule in `config/prometheus/alerts.yml` concerns an AI signal today.** The two AI-related signals that can be looked at are the abstention endpoint (pull, not scraped) and the token panel.

### Proposed (not built)

These are thresholds to adopt in the pilot, then tune on real volume. They are proposals; none is a measured guarantee. They need a small change to expose `reply_classified` and abstention counts as Prometheus gauges (proposed names below) or a daily job that reads the endpoint and events.

| Signal | Proposed metric / query | Baseline | Threshold | Action |
|---|---|---|---|---|
| Draft abstention rate overall | `rate` from `/metrics/abstention` (proposed gauge `closure_draft_abstention_rate`) | 0.0 for `template`; for a model, the first four weeks | Above 0.30 for 7 days (S3); above 0.60 for 24 h (S2, fallback storm) | Investigate reasons; if `provider_*`, check outage and use `LLM_PROVIDER=template`; if `validation_failed`, review the prompt and phrase list |
| Abstention rate by language | `by_language[x].rate` | Overall rate | Any language more than 0.15 above the overall rate with at least 30 attempts (S2, fairness) | Clinical safety officer review; native-speaker review of that language's drafts |
| Validator rejections by rule | `by_reason` and the `validation_failed:<rule>` suffix | Four-week mean | A single rule more than 3 times its mean (S3); any `result_disclosure` forbidden phrase on a real model output (S2) | Prompt review; add attack text to the red-team set |
| Triage `needs_human` rate | share of `reply_classified` with `needs_human=true` (proposed `closure_triage_needs_human_ratio`) | 0.42 on the synthetic test set (1 minus `coverage` 0.581, `docs/results/triage/eval.json`); first four weeks of real data | Moves by more than 0.15 absolute in either direction over 7 days (S3) | Down: possible over-confidence, run a sample read; up: workload and model check |
| Triage confidence distribution | median of `confidence`; share of replies at or above the 0.553 threshold | `eval.json` reliability bins and `threshold` | Median moves by more than 0.10 over 14 days (S3) | Re-read a sample of 50 auto-handled replies |
| Triage per-language needs-human rate | by `detect_language` of the stored reply (or the case language) | Per-language `auto_n` over `n` in `eval.json` (ar 0.56, arabizi 0.42, en 0.68, mixed 0.59 auto-handled) | Any language more than 0.15 from its baseline (S2 if Arabizi or Arabic) | Native-speaker sample review |
| Opt-out safety | Any patient contacted after an `opt_out` reply or a human-found opt-out the triage labelled otherwise | 0 leaked of 61 in the test set | Any case (S1 if contacted, S2 if caught) | Section 2; `TRIAGE_ENABLED=0` pending review |
| Planner fallback rate | share of dispatch rows with `fallback_used=true` | first four weeks | Above 0.50 for 7 days (S3) | Check cache and provider; `AGENCY_PLANNER=template` |
| Token spend | `closure_llm_tokens_total` per day against `LLM_DAILY_TOKEN_BUDGET` | 200,000 default | Above 80% of budget on 3 days (S3) | Review traffic; budget refusal is itself fail-safe |
| Risk score distribution and calibration (needs real labelled outcomes) | Mean predicted vs observed loss, per language | `risk.json` `heldout.ece` 0.0264 | ECE above 0.05 or any language gap above 0.02 on shadow data (S2) | Do not retrain on the fly; follow section 6 |

A weekly report with these figures goes to the clinical safety officer and the programme lead; the report is part of the audit trail (DoH Responsible AI Standard 3.1.3.1.5).

## 6. Retraining policy

1. **No retraining on production data during the prototype or before the pilot.** The triage model and the risk score were trained on synthetic data only. They may be retrained on real data only after the pilot has produced reviewed, consented, de-identified data and after DoH approval for use of production data in AI work (DoH Responsible AI Standard: production data is not used to train models without approval; regulatory-mapping.md section 4; triage and risk cards). The earliest that can happen is after H2 (native-speaker review) and a shadow period.
2. **Who approves.** A retrain is proposed by an engineer, reviewed by the clinical safety officer, approved by the programme lead, and, for real data, cleared by the data protection officer and DoH as above. Four eyes at least; one person cannot propose and approve.
3. **Gates that must pass before the PR is merged:**
   - Triage: `tools/triage/evaluate.py` target met, that is precision of auto-handled replies at or above 0.95 on the test split (`TEST_PRECISION_TARGET`; validation target 0.97), zero opt-outs leaked, no language below 0.95 unless explicitly accepted with a written reason on the card (today Arabizi is at 0.81 and is not accepted for unattended handling), and calibration reported (`ece_calibrated`).
   - Risk: `make risk-check` (the committed files equal a fresh retrain, ECE at most 0.05, Brier better than the base rate) and no language group flagged without an explanation (risk card section 8); language remains excluded.
   - Fairness: `make rai-fairness-check` per-language gaps within the tolerances set in [fairness.md](../results/fairness.md) (`make rai-fairness` regenerates it, `make rai-check` is a `make check` stage), with `docs/results/fairness.json` regenerated and no new flag left without a mitigation.
   - Safety wrapper: `tests/redteam` suite with 0 failures and 0 exposed, and the risk-boundary and triage contract tests.
4. **How it ships.** Only through a pull request that also updates the model card (`docs/governance/model-cards/`), the long-form card, the changelog and the review date, and records the new `model_id` / coefficient hash. The merged model is identified in every event by `model_id`, so a past classification can be traced to its weights. No retrain and no model swap by environment variable on a live system.
5. **Prompts, phrase lists and templates** are controlled the same way: a change to `SYSTEM_PROMPT`, `forbidden_phrases.yaml` or a template is a PR with red-team results attached; a template change also bumps `template_set_version` and needs re-approval of the outreach policy.
6. **Hosted-model version changes** (a new Claude model id) are treated as a retrain: update `DEFAULT_DRAFT_MODEL` or `DEFAULT_PLAN_MODEL` only through a PR, re-record the cache, re-run the red-team suite, update the card.

## 7. Fairness gaps

Language is not a risk-score input, and the drafting and planner components never see patient attributes, but outcomes can still differ by language. The triage Arabizi gap (auto-handled precision 0.81 against 0.96 to 1.00 elsewhere, `docs/results/triage/eval.json` `per_language`) is already a known open item. A new gap beyond tolerance is an S2 incident. Evidence and tolerances live in [fairness.md](../results/fairness.md) (generated by `tools/rai/fairness.py`, data in `docs/results/fairness.json`); oversight evidence in [oversight.md](../results/oversight.md) (`tools/rai/oversight.py`).

## 8. Review of this process

The clinical safety officer reviews this document, the model cards and the kill-switch table at least every six months and after every S1 or S2. The kill-switch table is re-verified by grep and by a deployment drill (apply each switch on a staging stack, confirm the stated behaviour) before any real-data pilot starts. Last reviewed 2026-10-25 by AI-assisted engineering review only; no clinician or native-Arabic review yet.

## 9. Incident record template

Copy into the incident register (location to be chosen with the data protection officer; the record must hold no patient identifier, only case ids).

```
Incident ID:                      AI-YYYY-NNN
Date and time first seen (UTC):
Reported by (role):
Component:                        reply triage | risk score | Claude drafting | Claude planner | template fallback | other
Model / version id:               (model_id, prompt_hash, coefficient hash, template_set_version)
Severity (S1 to S4) and why:
Near miss?                        yes / no
What happened (no patient identifiers; case ids only):
How it was detected:              alert | user | audit | test | other
Patients affected / could have been:   count, languages
Did a message reach a patient?    yes / no; if yes, which, approved by whom
Did case state change?            yes / no (event ids)
Personal data involved?           yes / no; DPO informed at:
Containment (switch used, who, when, file:line of the variable):
Roles informed and times:         on-call | clinical safety officer | DPO | programme lead
External report decided?          yes / no, by whom, when, to whom, reference
Root cause:
Corrective actions (owner, due):
Model card / hazard log / red-team updated?   links
Retrain or prompt change needed?  yes / no; PR link
Closed on / by:
```
