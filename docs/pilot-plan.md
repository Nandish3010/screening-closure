# 90-day pilot plan

Status: draft for the provider, the clinical lead and the human owner to adjust. This is a plan and a set of proposals, not an agreement. Nothing here has been discussed with any provider or regulator. The prototype uses synthetic data only; a pilot with real patients needs the approvals in the go/no-go gates below. Regulatory references are in [governance/regulatory-mapping.md](governance/regulatory-mapping.md); integration with the Abu Dhabi health information exchange is a target described in [governance/malaffi-adapter-plan.md](governance/malaffi-adapter-plan.md) and is optional for the pilot.

## 1. Purpose and scope

**Question the pilot answers.** In a real colorectal screening programme, does giving every positive FIT result a named owner, a clock, an audit trail and a clinician-signed closure shorten the time to ownership and to safe closure, and reduce the number of cases that are still open at 90 days, without any case being closed without evidence?

**In scope.** One programme: colorectal cancer screening, from a positive FIT result to colonoscopy and documented closure. Two to three providers: the screening or primary care provider, the endoscopy provider(s) that receive referrals, and optionally a laboratory. The pathway, states and SLAs are the ones in `config/pathways/colorectal_fit.yaml`, subject to clinician review (T-E13).

**Out of scope.** Any diagnosis or clinical advice; screening invitation and recall; other programmes (a mammogram pathway exists only as a configuration example); integration with the health information exchange as a condition of the pilot; AI use beyond message drafting and worklist ordering (ADR-007).

**Eligibility** follows the participating programme's own rules. The DoH IFHAS standard V10 (July 2025) sets the FIT package for ages 40 to 75, annually. The standard was read first-hand in the official DoH PDF during the evidence research (docs/evidence.md section 8a, check marker P); this plan cites it through evidence.md and its author did not re-read it. The prototype's synthetic cohort is also 40 to 75 (regenerated, [data-provenance](data-provenance.md)). To verify with the participating programme: that it applies the same range and exclusions.

## 2. Timeline

| Phase | Days | What happens | Exit gate |
|---|---|---|---|
| Preparation | before day 1 (duration set with the provider) | Agreements, approvals, data feed built and tested on synthetic data, role set-up, staff briefing, baseline audit | G0 |
| Shadow mode | 1 to 30 | The engine observes the provider's real result feed and creates shadow cases. It computes owners, clocks, overdue flags and escalation suggestions. **It sends nothing and assigns nothing.** Staff keep acting through existing channels. Coordinators review the flags weekly. | G1 at day 30 |
| Active mode | 31 to 90 | Owners work from the worklist; escalations go to named people who accept them; drafted patient messages are sent after owner approval through the provider's existing channel; closures are signed by a clinician. | G2 at day 60, G3 at day 90 |
| Follow-up tail | 91 to 180 | No new enrolment beyond day 90. Cases enrolled in the window continue to be followed so that closure outcomes can be measured. This is a decision of this plan, not part of the original brief: a pathway with a 31 working-day colonoscopy timeline, counted from referral acceptance, cannot be measured at day 90 for cases that arrive late in the window. | Final readout at day 180 |

Cases enrolled during shadow mode keep running in shadow with no intervention until day 30, then move to active handling at day 31. This gives the concurrent comparison group: what actually happened in the first 30 days under existing channels, next to what the engine would have flagged.

## 3. Key performance indicators

Definitions use the event log, so each KPI is computed from stored events and not self-reported. "Working days" use the configured UAE calendar (Monday to Friday) unless the provider's calendar differs.

| # | KPI | Definition | Baseline source | Target | Basis of target |
|---|---|---|---|---|---|
| K1 | Time to ownership | Working days from result received to a named owner accepting (`owner_accepted`); median and 90th percentile | Retrospective audit of the previous 12 months of positive FIT results (provider records), and the shadow-mode cases in days 1 to 30 | 90th percentile no more than 3 working days (the sum of the configured acknowledge and accept-ownership SLAs in `config/pathways/colorectal_fit.yaml`, 2 + 1) | Proposed; derived from the pathway config; to agree with the clinical lead |
| K2 | Time to safe closure | Working days from result received to `closed` with evidence and clinician sign-off; median and 90th percentile; cases still open are reported as censored, never dropped | Retrospective audit; shadow cases | Reduce the 90th percentile against the measured baseline; the size of the reduction is set after the baseline is known | Proposed; no figure is promised before a baseline exists |
| K3 | Open at 90 days | Share of enrolled cases with no closure 90 days after the result. Every open case must have a named owner and a dated next step | Retrospective audit; shadow cases | Lower than baseline; 100 percent of open cases have an owner and a dated next step | Proposed |
| K4 | Referral requested within 15 working days of the result | Share of positive FIT results for which a referral to colonoscopy is requested (`handoff_requested`) within 15 working days of the result being released (pathway milestone `referral_requested`; acceptance by the receiving provider is not required to meet it) | Retrospective audit; shadow cases | At least 90 percent (acceptable), 95 percent (desirable): a proposal, taken from the guideline's referral-to-colonoscopy KPI levels; the 15 working days are the standard's, and no percentage for them was found in the standard as read for evidence.md | The 15 working days: DoH IFHAS standard V10, July 2025, "referral to secondary care within 15 working days of releasing results", read first-hand for docs/evidence.md section 8 (check marker P). To reconcile: our clock counts a referral request from `case_created`, taken as the result release, against the standard's own definition. The 90 and 95 percent levels: UAE National Guideline Appendix 2, same section |
| K5 | Colonoscopy within 31 working days of referral | Share of referrals with the colonoscopy completed within 31 working days of the referral (pathway milestone `confirmatory_completed`). Our clock starts at the latest referral acceptance by the receiving provider (`handoff_accepted`; it restarts on each later acceptance, and with no accepted referral it starts when the case enters `confirmatory_booked`) and stops at `complete_confirmatory`. This is a stated, slightly lenient reading of "from referral": the days between the referral request and its acceptance are not counted against the 31, and are bounded instead by the 3-working-day handoff response timer, after which an unanswered referral bounces back to the referrer | Retrospective audit; shadow cases | Above 90 percent (acceptable), above 95 percent (desirable) | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed. 2022, Appendix 2, via docs/evidence.md section 8. This is the guideline's figure, not an IFHAS one (the guideline is internally inconsistent: 15 working days in clause 12.5.2, 31 in Appendix 2, 31 days in Appendix 3) |
| K6 | Patient acknowledgement rate | Share of contacted patients who acknowledge within the configured SLA (5 working days, `patient_acknowledge`) | Retrospective audit where the provider records outreach; otherwise measured from the first weeks of active mode and labelled as such | Higher than baseline; the amount is set after the baseline | Proposed |
| K7 | Escalations judged necessary by the coordinator | Share of a reviewed sample of escalations that a coordinator judges were needed (the case really lacked progress and was not already being handled). This differs from the simulator's "escalation acted-on rate (within 5 working days)" (43.5% literature-anchored, synthetic), which measures whether someone acted on an escalation, not whether it was needed | No baseline; measured in shadow mode by reviewing the engine's suggested escalations | At least 80 percent | Proposed |
| K8 | Cases closed without evidence | Count of cases in `closed` that lack an `evidence_ref` or a human `signed_by`, from an audit query run daily | Not applicable | **Zero.** Any non-zero value stops active mode | Design invariant (T-A1) |

Supporting measures reported with the KPIs: shadow-mode flag agreement (how often a coordinator confirms the engine's overdue flag), cases found that no one was tracking, number of drafted messages and the share approved without change, abstentions (template fallbacks), approval time, and coordinator minutes per case.

**How much the numbers can say.** With the placeholder volumes in the cost script (three providers at 3,000 FIT results per year each) about 155 actionable cases are expected to enter during the 90-day window. Percentages estimated on that many cases carry wide uncertainty and will be reported with intervals. The pilot is not a trial: the comparison is before and after plus the concurrent shadow period, with no randomisation, so differences are described as associations. Provider volumes are a data request below; the plan changes if they are much lower.

**Reconciliation needed.** The pathway config carries the two national timelines as milestones (A18, ADR-028). First, a referral requested within 15 working days of the result's release (DoH IFHAS standard V10, July 2025): the clock runs from `case_created`, taken as the result release, to the first `handoff_requested`, which is a request and not an acceptance, and any handoff counts. Second, the colonoscopy completed within 31 working days from referral (UAE National Guideline, Appendix 2; this is the guideline's figure and not an IFHAS one, and the guideline is internally inconsistent: 15 working days in clause 12.5.2, 31 in Appendix 2, 31 days in Appendix 3): our clock starts at the latest referral acceptance (`handoff_accepted`, A13, ADR-020) and stops at `complete_confirmatory`. Counting the 31 from acceptance is a stated, slightly lenient reading of "from referral": the days between request and acceptance are bounded by the 3-working-day handoff response timer (`handoff_response_working_days: 3`) instead. The per-step SLAs before the referral (acknowledge 2, accept ownership 1, contact patient 3, patient acknowledges 5) add up to 11 working days so that they fit inside the 15; `complete_confirmatory` has no per-step SLA. All per-step values are design values, not reviewed by a clinician. Still to settle with the clinical lead (H1): whether the referral request or its acceptance should meet the first and start the second, whether a transfer between primary care owners should count as a referral (it does today), the 15 versus 31 working-day reading in the national guideline, and reporting the programme-level share within target alongside the per-case limits. The calendar counts Monday to Friday as whole working days (Saturday and Sunday are the weekend, timezone Asia/Dubai, no holidays); Friday is a half day in government (docs/evidence.md section 8d, recorded in `simulator/params.yaml` `working_week.friday_half_day`), but the calendars do not shorten it.

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
| G1 End of shadow | Day 30 | No patient-safety incident attributable to the system; data feed complete and timely (completeness threshold to agree, for example 95 percent); coordinators confirm the flags are useful (agreement rate and K7, escalations judged necessary, at the agreed level); staff briefed and able to use the worklist | Unexplained gaps in the feed; flags mostly wrong; a safety incident; staff not ready |
| G2 Mid-active | Day 60 | K8 is zero; no wrong-patient message; no unsafe drafted message sent; K7 (escalations judged necessary by the coordinator) not falling; staff workload acceptable | Any case closed without evidence (stop at once, investigate); any wrong-patient message |
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
| Prototype-to-production gap | Pilot software is not yet production grade | [Pilot-hardening checklist](governance/pilot-hardening.md) and the limitations table; pilot starts only after G0 |

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

### Who pays, in Abu Dhabi terms

Nothing below has been discussed with any buyer. The buyer types are a way to think about who could pay for follow-up tracking, not a list of prospects.

| Buyer | Option A (per screened member per year) | Option B (per actionable case) | Notes |
|---|---|---|---|
| The DoH / ADPHC screening programme | Programme-level purchase: pays for members screened across the participating providers; fits a programme that already counts uptake | Pays for each FIT-positive case tracked; fits a programme that reports follow-up KPIs | Programme-level purchase with KPI reporting (the standards' KPIs are programme-level shares, section 3). Funding route to verify |
| A payer such as Daman or Thiqa | Pays per covered member screened in the year | Pays per FIT-positive case tracked among covered members | To verify: whether a payer would fund follow-up tracking, and under which programme |
| Provider groups (public and private hospital and clinic networks running screening) | The group pays per member it screens; the saving is its coordinators' time | The group pays per FIT-positive case it tracks; lower fixed outlay | The provider-net lines in the output below show the assumed saving against each fee |

### A low-cost shadow-pilot option

Instead of a full integration, the engine reads a periodic flat-file (CSV) or HL7 v2 export of FIT results, referrals and colonoscopy records, and runs in shadow mode only: it computes owners, clocks, overdue flags and escalation suggestions, and sends nothing and assigns nothing. Coordinators review the flags weekly. This costs a fraction of the full pilot (the table below, from the model, with the `shadow_pilot` inputs in `tools/pilot/inputs.yaml`, every one assumed) and gives a buyer evidence from its own records before it commits to a feed and to active handling. Neither option fits it well, because no member is served and no case is handled: it would be priced as a flat fee at cost recovery by agreement, whichever buyer type pays. It answers the shadow-mode questions (flag agreement, cases no one was tracking) and not the closure KPIs, which need active mode.

### Cost model output

Costs are largely fixed (hosting base, licences, support base, integration), so cost per case and per member fall with volume, and a pilot-sized deployment costs far more per case than a programme-sized one. The script output below is quoted verbatim; run `python3 tools/pilot/cost_model.py` to reproduce it, and see `tools/pilot/inputs.yaml` for the inputs. **Every cost input is assumed**; nothing is a quote, a forecast or a measurement. The value table also reads simulated shares from `docs/results/results.json` (synthetic).

```
PILOT COST MODEL (illustrative)
Currency: AED. Inputs file: inputs.yaml.
Every input below is an assumption, not a quote, forecast or measured value.

INPUTS
input                                          value  status   unit
--------------------------------------------  ------  -------  ------------------------------------------------------------------
positivity_rate                                 0.07  assumed  share of screened members with a positive FIT
hosting_fixed_aed_per_month                     9000  assumed  AED per month
hosting_variable_aed_per_1000_cases_per_year     400  assumed  AED per 1,000 actionable cases per year
third_party_licences_aed_per_year              24000  assumed  AED per year
support_fte_base                                 0.5  assumed  FTE
support_fte_per_50k_members                     0.25  assumed  FTE per 50,000 screened members per year
support_fte_loaded_cost_aed_per_year          300000  assumed  AED per FTE per year
integration_one_off_aed_per_provider          120000  assumed  AED per participating provider, one-off
members_per_provider                           20000  assumed  screened members per year per provider
amortisation_years                                 3  assumed  years
coordinator_minutes_per_case_manual               90  assumed  minutes of coordinator time per actionable case without the engine
coordinator_minutes_per_case_with_engine          35  assumed  minutes of coordinator time per actionable case with the engine
coordinator_cost_aed_per_hour                    120  assumed  AED per hour
price_per_screened_member_per_year_aed           6.0  assumed  AED per screened member per year
price_per_actionable_case_aed                     90  assumed  AED per actionable (FIT-positive) case
pilot_providers                                    3  assumed  providers
pilot_fit_screened_per_provider_per_year        3000  assumed  FIT results per provider per year
pilot_days                                        90  assumed  days
followup_tail_days                                90  assumed  days
pilot_support_fte                                1.0  assumed  FTE during the enrolment window
tail_support_fte                                0.25  assumed  FTE during the follow-up tail
pilot_governance_review_one_off_aed            60000  assumed  AED, one-off
shadow_integration_one_off_aed_per_provider    15000  assumed  AED per participating provider, one-off
shadow_days                                       90  assumed  days
shadow_support_fte                               0.3  assumed  FTE during the shadow run
shadow_governance_review_one_off_aed           25000  assumed  AED, one-off
shadow_hosting_share_of_full                     0.4  assumed  share of the full-stack hosting cost
shadow_licences_share_of_full                    0.5  assumed  share of the third-party licence cost
crc_risk_on_time                                0.03  assumed  risk, any CRC, colonoscopy at 8-30 days
advanced_stage_risk_on_time                    0.008  assumed  risk, advanced stage, colonoscopy at 8-30 days
or_any_crc_10_to_12_months                      1.48  assumed  odds ratio, any CRC, 10-12 months vs 8-30 days
or_advanced_stage_10_to_12_months               1.97  assumed  odds ratio, advanced stage, 10-12 months vs 8-30 days
or_any_crc_over_12_months                       2.25  assumed  odds ratio, any CRC, over 12 months vs 8-30 days
or_advanced_stage_over_12_months                3.22  assumed  odds ratio, advanced stage, over 12 months vs 8-30 days

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

SHADOW PILOT (flat-file or HL7 v2 export, no outbound messages)
item                                     value
-------------------------------------  -------
FIT results in window                    2,219
cases in window (FIT-positive)             155
days                                        90
hosting                                 10,677
third-party licences                     2,959
support                                 22,192
integration (one-off, export mapping)   45,000
governance review (one-off)             25,000
total shadow pilot cost                105,828
shadow pilot cost per case                 681

VALUE SIDE: late-stage cancers potentially avoided (ILLUSTRATIVE, SYNTHETIC)
Simulated shares x published odds ratios; not a forecast, not a clinical claim; observational ORs,
confounding by indication; never-completers treated as if completing late.
Shares with no colonoscopy by day 270, read from results.json (synthetic): baseline 45.7%, literature-anchored orchestrated 32.7%, upper-range orchestrated 20.2%.
On-time risk (assumed): any CRC 0.030, advanced stage 0.008.
item                                                        per 1,000 cases  700 cases a year  3,500 cases a year  14,000 cases a year
----------------------------------------------------------  ---------------  ----------------  ------------------  -------------------
literature-anchored: cases moved to colonoscopy by day 270            130.9              91.6               458.0              1,831.9
  10-12 month ORs: cancers diagnosed earlier                           1.80              1.26                6.31                25.22
  10-12 month ORs: late-stage potentially avoided                      1.00              0.70                3.50                13.99
  >12 month ORs: cancers diagnosed earlier                             4.59              3.21               16.06                64.23
  >12 month ORs: late-stage potentially avoided                        2.27              1.59                7.93                31.71
upper-range: cases moved to colonoscopy by day 270                    255.3             178.7               893.5              3,573.9
  10-12 month ORs: cancers diagnosed earlier                           3.52              2.46               12.30                49.21
  10-12 month ORs: late-stage potentially avoided                      1.95              1.36                6.82                27.30
  >12 month ORs: cancers diagnosed earlier                             8.95              6.27               31.33               125.30
  >12 month ORs: late-stage potentially avoided                        4.42              3.09               15.47                61.87

READ THIS FIRST: all inputs are assumed. Cost per case falls with volume because hosting,
licences and the support base are fixed; the numbers show that shape, not a price.
```

How to read it. At the smallest volume the operator's cost to serve is well above either illustrative fee, so a pilot would be priced as an investment or at cost recovery by agreement, not at a steady-state fee. At the largest volume both illustrative fees cover the operator's cost and still leave the provider better off than the assumed coordinator time saved, but that depends entirely on the assumed minutes per case, which the pilot measures (supporting measure: coordinator minutes per case).

How to read the shadow pilot. Same three providers and the same placeholder volumes as the full pilot, so the same 155 expected cases in the window. The total is 105,828 against 577,624 for the full pilot, and 681 per case against 3,718. The difference comes from three assumed inputs: a much smaller one-off integration per provider (15,000 against 120,000, because it is an export mapping and not a live feed), less support, and a smaller hosting and licence footprint. These are assumptions chosen to be plausible, not quotes, and the export format each provider can actually produce is a data request (section 5).

How to read the value table. It multiplies two things. The first is the simulated difference in the share of cases with no colonoscopy by day 270: 45.7 percent at baseline, 32.7 percent orchestrated in the literature-anchored scenario, 20.2 percent in the upper-range scenario (synthetic; `delay_harm_share`). The second is the excess cancer risk of a late colonoscopy from Corley et al., JAMA 2017 (docs/evidence.md section 9): odds ratios of 1.48 (any cancer) and 1.97 (advanced stage) at 10 to 12 months, and 2.25 and 3.22 beyond 12 months, applied to an on-time risk of 3.0 percent (any cancer, from the same paper) and 0.8 percent (advanced stage, a placeholder: no figure is recorded in evidence.md, to verify against the paper). In the literature-anchored scenario about 131 of every 1,000 positive cases move from "no colonoscopy by day 270" to "by day 270"; with the 10 to 12 month odds ratios that is about 1.8 cancers diagnosed earlier and 1.0 late-stage cancer potentially avoided per 1,000 cases, and with the beyond-12-month odds ratios 4.6 and 2.3. Read these as an order of magnitude from stated assumptions, not a forecast and not a clinical claim: the odds ratios are observational and subject to confounding by indication, they come from a different population, the simulated shares are synthetic, and cases that never complete are treated as if they completed late. The value is not part of the cost to serve or of either fee.

## 9. Decisions made in this plan beyond the original brief

- A 90-day follow-up tail after enrolment closes, with a final readout at day 180, so that closure KPIs can be measured.
- Cases enrolled in shadow mode move to active handling at day 31 and are also used as the concurrent comparison.
- A stop rule that halts active mode on any case closed without evidence or any wrong-patient message.
- KPI K4 uses the DoH IFHAS 15-working-day figure (read first-hand for docs/evidence.md section 8) and K5 the National Guideline's 31 working days (Appendix 2), counted from referral acceptance; the percentage targets and K1, K2, K3, K6 and K7 are proposals or taken from the guideline's KPI table.
- The pilot does not depend on health information exchange connectivity.

## 10. To verify before use

1. That our KPI definitions match the IFHAS and National Guideline definitions (programme-level shares against per-case limits; request against acceptance). The figures were read first-hand for docs/evidence.md section 8 (check marker P); this document cites them through evidence.md.
2. Which regulator approvals a pilot with real patients needs.
3. Provider volumes, the baseline data available, and the provider's calendar.
4. All cost inputs (hosting quotes, support cost, coordinator time and cost, fees).
5. Any dependency on a clinician-reviewed pathway (T-E13).
