# 90-day pilot plan

Status: draft for the provider, the clinical lead and the human owner to adjust. This is a plan and a set of proposals, not an agreement. Nothing here has been discussed with any provider or regulator. The prototype uses synthetic data only; a pilot with real patients needs the approvals in the go/no-go gates below. Regulatory references are in [governance/regulatory-mapping.md](governance/regulatory-mapping.md); integration with the Abu Dhabi health information exchange is a target described in [governance/malaffi-adapter-plan.md](governance/malaffi-adapter-plan.md) and is optional for the pilot.

## 1. Purpose and scope

**Question the pilot answers.** In a real colorectal screening programme, does giving every positive FIT result a named owner, a clock, an audit trail and a clinician-signed closure shorten the time to ownership and to safe closure, and reduce the number of cases that are still open at 90 days, without any case being closed without evidence?

**In scope.** One programme: colorectal cancer screening, from a positive FIT result to colonoscopy and documented closure. Two to three providers: the screening or primary care provider, the endoscopy provider(s) that receive referrals, and optionally a laboratory. The pathway, states and SLAs are the ones in `config/pathways/colorectal_fit.yaml`, subject to clinician review (T-E13).

**Out of scope.** Any diagnosis or clinical advice; screening invitation and recall; other programmes (a mammogram pathway exists only as a configuration example); integration with the health information exchange as a condition of the pilot; AI use beyond message drafting and worklist ordering (ADR-007).

**Eligibility** follows the participating programme's own rules. The DoH IFHAS standard (July 2025) is reported to set eligibility at ages 40 to 75 with an annual FIT (cited as "DoH IFHAS standard, July 2025, via docs/evidence.md section 8"; relayed by the project coordinator, not read by the author of this document, to verify). Note that the synthetic cohort in the prototype uses ages 45 to 75 ([data-provenance](data-provenance.md)); the difference matters only for simulation.

## 2. Timeline

| Phase | Days | What happens | Exit gate |
|---|---|---|---|
| Preparation | before day 1 (duration set with the provider) | Agreements, approvals, data feed built and tested on synthetic data, role set-up, staff briefing, baseline audit | G0 |
| Shadow mode | 1 to 30 | The engine observes the provider's real result feed and creates shadow cases. It computes owners, clocks, overdue flags and escalation suggestions. **It sends nothing and assigns nothing.** Staff keep acting through existing channels. Coordinators review the flags weekly. | G1 at day 30 |
| Active mode | 31 to 90 | Owners work from the worklist; escalations go to named people who accept them; drafted patient messages are sent after owner approval through the provider's existing channel; closures are signed by a clinician. | G2 at day 60, G3 at day 90 |
| Follow-up tail | 91 to 180 | No new enrolment beyond day 90. Cases enrolled in the window continue to be followed so that closure outcomes can be measured. This is a decision of this plan, not part of the original brief: a pathway with a 45 working-day completion SLA cannot be measured at day 90 for cases that arrive late in the window. | Final readout at day 180 |

Cases enrolled during shadow mode keep running in shadow with no intervention until day 30, then move to active handling at day 31. This gives the concurrent comparison group: what actually happened in the first 30 days under existing channels, next to what the engine would have flagged.

## 3. Key performance indicators

Definitions use the event log, so each KPI is computed from stored events and not self-reported. "Working days" use the configured UAE calendar (Monday to Friday) unless the provider's calendar differs.

| # | KPI | Definition | Baseline source | Target | Basis of target |
|---|---|---|---|---|---|
| K1 | Time to ownership | Working days from result received to a named owner accepting (`owner_accepted`); median and 90th percentile | Retrospective audit of the previous 12 months of positive FIT results (provider records), and the shadow-mode cases in days 1 to 30 | 90th percentile no more than 4 working days (the sum of the configured acknowledge and accept-ownership SLAs, 2 + 2) | Proposed; derived from the pathway config; to agree with the clinical lead |
| K2 | Time to safe closure | Working days from result received to `closed` with evidence and clinician sign-off; median and 90th percentile; cases still open are reported as censored, never dropped | Retrospective audit; shadow cases | Reduce the 90th percentile against the measured baseline; the size of the reduction is set after the baseline is known | Proposed; no figure is promised before a baseline exists |
| K3 | Open at 90 days | Share of enrolled cases with no closure 90 days after the result. Every open case must have a named owner and a dated next step | Retrospective audit; shadow cases | Lower than baseline; 100 percent of open cases have an owner and a dated next step | Proposed |
| K4 | Referral after a positive FIT, accepted | Share of positive FIT results for which a referral to colonoscopy is made and accepted by the receiving provider within the configured SLA | Retrospective audit; shadow cases | At least 90 percent (acceptable), 95 percent (desirable) | DoH IFHAS standard, July 2025, via docs/evidence.md section 8 (relayed, to verify; exact definition to reconcile with ours) |
| K5 | Colonoscopy within 31 working days of referral | Share of referrals with the colonoscopy completed within 31 working days of the referral | Retrospective audit; shadow cases | Above 90 percent | DoH IFHAS standard, July 2025, via docs/evidence.md section 8 (relayed, to verify) |
| K6 | Patient acknowledgement rate | Share of contacted patients who acknowledge within the configured SLA (7 working days) | Retrospective audit where the provider records outreach; otherwise measured from the first weeks of active mode and labelled as such | Higher than baseline; the amount is set after the baseline | Proposed |
| K7 | Escalation precision | Share of escalations that a coordinator reviewer, on a sample, judges to have been needed (the case really lacked progress and was not already being handled) | No baseline; measured in shadow mode by reviewing the engine's suggested escalations | At least 80 percent | Proposed |
| K8 | Cases closed without evidence | Count of cases in `closed` that lack an `evidence_ref` or a human `signed_by`, from an audit query run daily | Not applicable | **Zero.** Any non-zero value stops active mode | Design invariant (T-A1) |

Supporting measures reported with the KPIs: shadow-mode flag agreement (how often a coordinator confirms the engine's overdue flag), cases found that no one was tracking, number of drafted messages and the share approved without change, abstentions (template fallbacks), approval time, and coordinator minutes per case.

**How much the numbers can say.** With the placeholder volumes in the cost script (three providers at 3,000 FIT results per year each) about 155 actionable cases are expected to enter during the 90-day window. Percentages estimated on that many cases carry wide uncertainty and will be reported with intervals. The pilot is not a trial: the comparison is before and after plus the concurrent shadow period, with no randomisation, so differences are described as associations. Provider volumes are a data request below; the plan changes if they are much lower.

**Reconciliation needed.** The pathway config sets a 45 working-day SLA for completing the confirmatory test, while the standard reportedly measures colonoscopy within 31 working days of referral. The two probably start their clocks at different events (result received versus referral); the clinician review should settle the definitions and whether the config SLA changes.

## 4. Roles and staffing

Time commitments are assumptions to be agreed with each provider.

| Role | Organisation | Responsibility | Time (assumed) |
|---|---|---|---|
| Executive sponsor | Provider | Authorises the pilot; owns the go/no-go decisions at G0 and G3 | A few hours per month |
| Clinical lead | Provider (gastroenterology or primary care) | Reviews the pathway, SLAs and patient wording; signs the clinical safety case; reviews incidents | About 2 hours per week |
| Programme coordinator | Provider | Owns the escalation ladder's last rung; reviews flags weekly; works the worklist | 0.5 FTE per participating provider |
| Case owners (primary care, endoscopy) | Provider | Accept ownership, approve drafted messages, hand off, book | Within existing duties |
| Signing clinician | Provider | Signs closures with evidence | Within existing duties |
| Data protection and information security lead | Provider | Data sharing agreement, security assessment, access reviews | As required |
| Pilot manager | Us | Runs the plan, weekly review, reporting, liaison | 1.0 FTE in the window |
| Engineer on call | Us | Operations, data feed, fixes | Part of the support FTE in the cost model |
| Arabic reviewer | Provider or independent | Reviews patient-facing wording (T-E14) | Before active mode |
| Independent reviewer (optional) | Provider or a third party | Samples escalations for K7 and audits closures for K8 | A few hours per month |

## 5. Data needed from the provider

1. A feed of FIT results (date, result, laboratory, patient identifier) with timestamps, for new results during the pilot and for the previous 12 months for the baseline.
2. Referral, appointment and colonoscopy records for the same patients: referral date and receiving provider, acceptance or rejection and reason, appointment dates, attendance, completion date, outcome status.
3. Patient contact details, preferred language and channel, and consent or opt-out status for contact.
4. Records of outreach already made (calls, messages) for the baseline, if kept.
5. Staff roster and escalation contacts: owners, supervisors, coordinator, signing clinicians.
6. Programme definitions: eligibility, SLAs and targets currently used, and the provider's own reporting on follow-up.
7. Volumes per month, to size the pilot (the cost script uses a placeholder).
8. Written agreements: a data sharing and processing agreement, the provider's security requirements, and approval to use the data for follow-up tracking. Direct identifiers should be pseudonymised where the workflow allows.

All pilot data stays in an in-country environment approved by the provider and the regulator (governance mapping, sections 2, 3 and 6), with support access from within the UAE.

## 6. Go/no-go gates

Thresholds are proposals for the sponsor and clinical lead to set.

| Gate | When | Go if | No-go or pause if |
|---|---|---|---|
| G0 Start | Before day 1 | Data sharing agreement signed; regulator and provider approvals in place (to verify which are required); security assessment passed; hosting approved as in-country for health information; clinical safety sign-off of pathway and wording; Arabic review done; feed tested end to end with synthetic data; baseline audit done; named people for every role | Any item missing |
| G1 End of shadow | Day 30 | No patient-safety incident attributable to the system; data feed complete and timely (completeness threshold to agree, for example 95 percent); coordinators confirm the flags are useful (agreement rate and K7 at the agreed level); staff briefed and able to use the worklist | Unexplained gaps in the feed; flags mostly wrong; a safety incident; staff not ready |
| G2 Mid-active | Day 60 | K8 is zero; no wrong-patient message; no unsafe drafted message sent; escalation precision not falling; staff workload acceptable | Any case closed without evidence (stop at once, investigate); any wrong-patient message |
| G3 End | Day 90, readout at day 180 | KPI movement against baseline (K1 to K7) reported with intervals; K8 zero throughout; staff and patient feedback summarised | Provider decides not to continue, or a safety finding is unresolved |

Stop rules apply at any time: a case closed without evidence, a message to the wrong patient, or silent loss of the feed that the watchdog failed to flag stops active mode until a root cause is documented.

## 7. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Hosting not approved for health information | Pilot cannot start | Raise at G0; options in governance mapping section 6 (provider's own environment first) |
| Feed late, incomplete or in an unexpected format | Cases missing, false overdue flags | Completeness check and feed watchdog; shadow mode surfaces problems before any action |
| Wrong-patient attachment or message | Patient harm, privacy breach | Deterministic identifier match, human queue for ambiguity, owner approval for messages, hazard log (E6) |
| Alert fatigue or distrust | Flags ignored | Shadow mode review, K7 target, weekly tuning of the ladder with the coordinator |
| Low case volume | Wide uncertainty on KPIs | Report intervals, follow-up tail, add a provider if volumes are low |
| Baseline unavailable or poor quality | Cannot show improvement | Use shadow-period cases as the concurrent baseline; say so in the readout |
| Consent withdrawal or opt-outs higher than expected | Fewer reachable patients | Consent ledger freezes outreach and flags a person; report separately |
| Model drafting error | Unsafe or poor message | Validation, template fallback, owner approval (design-choices) |
| Arabic wording misunderstood | Patients do not respond | Native-speaker review, plain language, "I don't understand" route to a human |
| Staff workload rises | Resistance | Measure coordinator minutes per case; the cost model assumes a reduction that must be confirmed |
| Operator or regulator timelines | Delay | Pilot does not depend on health information exchange connectivity |
| Prototype-to-production gap | Pilot software is not yet production grade | Hardening list in the limitations table; pilot starts only after G0 |

## 8. Commercial model options

Two options are compared; a hybrid (small platform fee plus a lower per-unit fee) is also possible. Fees in the script are illustrative placeholders, not an offer.

| | A: per screened member per year | B: per actionable case |
|---|---|---|
| Unit | Each member screened in the year | Each FIT-positive case the engine tracks |
| Provider's budgeting | Predictable, scales with programme size | Scales with positivity and follow-up workload; lower fixed outlay |
| Alignment | Paid for coverage whether or not cases arise | Paid in proportion to the work that matters |
| Provider risk | Pays even when positivity is low | Cost rises if positivity is high |
| Operator risk | Fixed costs are covered when volume is high; revenue does not follow the case load | Revenue follows cases; low positivity leaves fixed costs uncovered |
| Measurement | Needs a count of screened members | Needs a definition of an actionable case and an audit of the count |
| Pilot fit | Awkward when only a subset of members is in scope | Natural: the pilot already counts enrolled cases |

Costs are largely fixed (hosting base, licences, support base, integration), so cost per case and per member fall with volume, and a pilot-sized deployment costs far more per case than a programme-sized one. The script output below is quoted verbatim; run `python3 tools/pilot/cost_model.py` to reproduce it, and see `tools/pilot/inputs.yaml` for the inputs. **Every input is assumed**; nothing is a quote, a forecast or a measurement.

```
COST TO SERVE, STEADY STATE (per year)
per year                         10,000 members  50,000 members  200,000 members
-------------------------------  --------------  --------------  ---------------
actionable cases (FIT-positive)             700           3,500           14,000
participating providers                       1               3               10
hosting                                 108,280         109,400          113,600
third-party licences                     24,000          24,000           24,000
support (FTE x loaded cost)             165,000         225,000          450,000
integration (amortised)                  40,000         120,000          400,000
total cost to serve                     337,280         478,400          987,600

COST PER UNIT
per year                  10,000 members  50,000 members  200,000 members
------------------------  --------------  --------------  ---------------
cost per actionable case           481.8           136.7             70.5
cost per screened member           33.73            9.57             4.94

COORDINATOR TIME (assumed minutes per case, with vs without the engine)
per year                 10,000 members  50,000 members  200,000 members
-----------------------  --------------  --------------  ---------------
coordinator hours saved             642           3,208           12,833
value of time saved              77,000         385,000        1,540,000

COMMERCIAL OPTIONS (per year; illustrative fees from the inputs file)
per year                         10,000 members  50,000 members  200,000 members
-------------------------------  --------------  --------------  ---------------
A: fee per screened member               60,000         300,000        1,200,000
A: provider net (saving - fee)           17,000          85,000          340,000
A: operator margin (fee - cost)        -277,280        -178,400          212,400
B: fee per actionable case               63,000         315,000        1,260,000
B: provider net (saving - fee)           14,000          70,000          280,000
B: operator margin (fee - cost)        -274,280        -163,400          272,400

90-DAY PILOT (enrolment window plus follow-up tail)
item                                    value
------------------------------------  -------
FIT results enrolled in window          2,219
expected actionable cases                 155
days funded (window + tail)               180
hosting                                53,322
third-party licences                   11,836
support                                92,466
integration (one-off, not amortised)  360,000
governance review (one-off)            60,000
total pilot cost                      577,624
pilot cost per actionable case          3,718
```

How to read it. At the smallest volume the operator's cost to serve is well above either illustrative fee, so a pilot would be priced as an investment or at cost recovery by agreement, not at a steady-state fee. At the largest volume both illustrative fees cover the operator's cost and still leave the provider better off than the assumed coordinator time saved, but that depends entirely on the assumed minutes per case, which the pilot measures (supporting measure: coordinator minutes per case).

## 9. Decisions made in this plan beyond the original brief

- A 90-day follow-up tail after enrolment closes, with a final readout at day 180, so that closure KPIs can be measured.
- Cases enrolled in shadow mode move to active handling at day 31 and are also used as the concurrent comparison.
- A stop rule that halts active mode on any case closed without evidence or any wrong-patient message.
- KPI K4 and K5 use the DoH IFHAS figures relayed by the coordinator; K1, K2, K3, K6 and K7 targets are proposals.
- The pilot does not depend on health information exchange connectivity.

## 10. To verify before use

1. IFHAS figures and wording (via docs/evidence.md section 8); this document cites them as relayed.
2. Which regulator approvals a pilot with real patients needs.
3. Provider volumes, the baseline data available, and the provider's calendar.
4. All cost inputs (hosting quotes, support cost, coordinator time and cost, fees).
5. Any dependency on a clinician-reviewed pathway (T-E13).
