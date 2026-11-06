# Offer: a 90-day read-only shadow pilot of positive-result follow-up

Draft offer of 7 November 2026, written so a buyer could read it in one screen and sign a letter of intent against it ([template](outreach/letter-of-intent.md)). **No buyer exists, nobody has been contacted, no price has been tested and no letter of intent has been signed.** The product runs on synthetic data only. Every figure carries its grade: **sourced** (read from a published page), **assumed** (our input, not a quote) or **simulated** (synthetic, from the model).

## The clock this is built around

Abu Dhabi's screening standard (DoH IFHAS V10, July 2025) sets **referral to secondary care within 15 working days of releasing a positive result**. This is sourced: [docs/evidence.md](../evidence.md) section 8b, row "IFHAS standard, results and referral" (IFHAS V10 sections 4.7.1, 4.9, 4.11, Appendix 9), read first-hand. The same standard says KPIs are reported within 21 days of quarter end. Two things we have not verified: whether DoH enforces the 15 days through a performance indicator, and whether the standard counts a referral request or the receiving provider's acceptance (we count the request; [pilot-plan.md](../pilot-plan.md) section 3, K4). Today nothing tells a network, case by case, which positives are about to miss it. In an audit of Abu Dhabi ambulatory clinics (BMC Cancer 2021, 2015 to 2016 data) only 30.5% of 1,429 positive results had a follow-up colonoscopy (sourced; an older audit, a different measure from the 15-day clock).

## What is offered

| | |
|---|---|
| Scope | **90 days**, read-only, **2 to 3 clinics** of one provider network. Positive FIT results (and positive blood-based tests if agreed). Modelled at 3 clinics, **155 expected positives** in the window (assumed volumes: 3,000 FIT results per clinic per year). |
| What the system does | Reads a periodic export (CSV or HL7 v2) of results, referrals, appointments and colonoscopy records. For each positive it computes a named owner, the 15-working-day clock and the 31-working-day colonoscopy clock, and an overdue flag. Coordinators get a weekly flag report. |
| What it never does | **Sends nothing to any patient, writes nothing into the provider's systems, assigns no task, gives no clinical advice.** Runs inside the provider's own approved environment, in the UAE. |
| Cost to the buyer | **At cost, by agreement.** Modelled cost to serve **AED 105,828** (about AED 106,000), **AED 681 per case**, from `python3 tools/pilot/cost_model.py` ("SHADOW PILOT" block; every input assumed, not a quote). Hosting 10,677; licences 2,959; support 22,192; export mapping 45,000; governance review 25,000. Raising shadow support from 0.3 to 0.5 FTE moves it to AED 120,622 ([commercial front page](README.md)). Active handling, if ever wanted, is priced separately. |

## What we measure (all from the export and the event log, not self-reported)

Baselines are unknown today: no figure for these exists in the repository, and the run produces them. Targets are proposals to be agreed and written into the letter of intent before day 1.

| # | KPI | Baseline | Target or threshold | Measurement method |
|---|---|---|---|---|
| K4 | **Referral requested within 15 working days of the result** (IFHAS V10) | Unknown; measured per case on the previous 12 months and the live window | Reported at day 90. Proposed for an active pilot: 90% acceptable, 95% desirable (borrowed from the national guideline's referral-to-colonoscopy levels; IFHAS gives no percentage in the text we read) | Result date to first referral request, in working days, UAE calendar, per case |
| K1 | **Time from result to a named owner** | Unknown | 90th percentile no more than 3 working days (2 + 1, the configured SLAs; a proposal) | Result date to the date an owner is identifiable from the provider's records. In a read-only run nobody accepts in our system, so this is read from the provider's records |
| K5 | Colonoscopy within 31 working days of the first referral request | Unknown | Reported. National guideline levels: above 90% acceptable, above 95% desirable (guideline, not IFHAS) | First referral request to colonoscopy completed; the clock never restarts after a rejection |
| S1 | Export completeness | n/a | At least 95% of positives appear with result date and identifier | Export against the laboratory's own count |
| S2 | Ownership visible with a running clock | n/a | At least 95% | Share of positives with an owner and a clock |
| S3 | Untracked positives found | Unknown | No target; this is the finding | Positives with no referral, appointment or activity for 30 days that coordinators confirm nobody was working |
| S4 | Flag agreement | n/a | At least 80% | Coordinators review a sample of overdue flags and say if each was right |
| S7 | Staff minutes by role | Assumed 57.1 minutes per case (all roles), simulated | Reported; **no saving is promised** | Time-and-motion sample in weeks 2 to 4 |
| S8 | Patient messages sent, writes into the provider's systems, privacy incidents | n/a | **Zero**; any other value stops the run | Audit of the event log |

Definitions of S1 to S8: [go-to-market.md](go-to-market.md) section 4. K1, K4, K5: [pilot-plan.md](../pilot-plan.md) section 3.

## Decision at day 90

The provider's sponsor decides against the targets agreed before day 1.

- **Go (propose an active pilot, gates G0 to G3 in [pilot-plan.md](../pilot-plan.md)):** S1 and S2 at least 95%, S4 at least 80%, S8 zero, and the sponsor judges the untracked positives (S3) worth acting on. An active pilot also needs a clinical lead's sign-off first.
- **Stop:** anything else. A stop is a valid result: it says the provider already tracks its positives, or the export is not good enough yet. All data is deleted or returned within 30 days with a deletion certificate. Either side can stop at any time on written notice.

## What the buyer provides

1. A named sponsor, and a coordinator for a 30-minute weekly flag review.
2. A periodic export of results, referrals, appointments and colonoscopy records for the window and the 12 months before it, with the export format agreed in week -6 to -1.
3. A clinical lead who confirms the pathway clocks; the clinical thresholds are still placeholders ([thresholds-to-sign.md](../clinical/thresholds-to-sign.md)).
4. Data-protection and information-security sign-off, a data-sharing agreement, and an approved in-country environment ([go-to-market.md](go-to-market.md) section 7).
5. Staff time for the minutes sample (S7) in weeks 2 to 4.

## Staff cost per case does not fall: it moves

The system frees coordinator minutes and adds clinician and specialist minutes, because more patients reach colonoscopy. Simulated, synthetic, per actionable case, today against the literature-anchored scenario at programme level ([pilot-plan.md](../pilot-plan.md) section 3; `python3 tools/pilot/cost_model.py`):

| Minutes per case | Today | With the system | Change |
|---|---|---|---|
| Coordinator | 46.6 | 29.8 | falls |
| Owner clinician | 4.5 | 8.7 | rises |
| Specialist | 6.1 | 7.4 | rises |
| All roles | 57.1 | 45.8 | -19.7% |
| Wage-weighted (coordinator-equivalent, clinician 4 times, specialist 10 times a coordinator; sourced-adjacent, not an official pay scale) | 125.0 | 138.7 | **+10.9%** (range +6.2% to +17.6%) |

**The 40% labour-reduction target is not met.** It is 19.7% at programme level and 36.0% for cases the system reaches; the 40.0% in the upper range assumes every case is reached and is a ceiling, not a forecast. Wage-weighted staff cost per case rises. The honest claim is capacity redeployment: coordinator time moves to clinician and specialist time and to more completed colonoscopies, which is the outcome. The shadow run does not change any of this; it measures real minutes (S7) to replace the assumed ones.

## Not claimed

No clinical effect, no saving, no price. The 51.0% to 66.8% colonoscopy-within-180-days gain is a fitted simulation result, not evidence of effect. No clinician or native Arabic speaker has reviewed the wording, and the reviews on file are AI-simulated. The assumed four-pathway licence and payback are behind an "assumed" label on the [front page](README.md). Decision memo for a regulator reader: [decision-memo.md](decision-memo.md).
