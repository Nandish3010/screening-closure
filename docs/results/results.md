# Evaluation results: baseline vs orchestrated

generated_at: 2026-10-22 18:14:10 UTC (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)

All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.

- Seed 20261017, 20 replications x 2,000 cases x 360 days of follow-up, 1000 bootstrap resamples
- Parameters hash `b90c557e531268dc` (upper range), `d48f20dc2668a41f` (literature anchored), pathway `colorectal_fit` v0.2 hash `234c780c96de6fb1`, git commit `626c62db5955`
- Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).

## Headline: baseline vs the two orchestrated scenarios

There are two orchestrated scenarios and one baseline. **Quote the literature-anchored scenario.** It holds the orchestrated arm to what published studies report: the completion effect equals the pooled patient-navigation risk ratio of 1.27, SMS reminders add nothing beyond the no-reminder attendance rate (the lower bound of the published range), and escalating to a supervisor or coordinator is no more effective than re-contacting the patient. In it, 65% of patients reach a confirmatory colonoscopy within 180 days against 51% at baseline (ratio 1.27). To hold the average effect to the published figure the scenario lets the orchestrated process reach only 55% of cases, so 21% of cases are still lost without a human being told (programme level; among the cases it reaches the figure is 0%); that is a device for calibrating the average effect, not a prediction of how many cases a real programme would miss. **The upper-range scenario shows the mechanism ceiling**: what ownership, timers, escalation and re-contact could deliver if every case got the full orchestrated process and every escalation worked as assumed. It reaches 78% within 180 days (ratio 1.53), far above anything published for navigation, so read it as an upper bound on the mechanism and not as a forecast. Both scenarios use the same synthetic patients, the same random numbers and the same baseline arm; the baseline was calibrated to published unmanaged completion, and the only orchestrated-arm fit is the reach parameter, set so the literature-anchored 180-day ratio equals the navigation risk ratio.

| Metric | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Colonoscopy completed within 90 days | 41.9% (41.5% to 42.3%) | 57.5% (57.0% to 57.9%) | 73.1% (72.7% to 73.4%) |
| Colonoscopy completed within 180 days | 51.1% (50.6% to 51.6%) | 64.9% (64.4% to 65.4%) | 78.3% (77.9% to 78.7%) |
| Colonoscopy completed within 360 days | 54.1% (53.5% to 54.6%) | 66.4% (65.9% to 67.0%) | 78.5% (78.1% to 78.8%) |
| Completion ratio vs baseline at 180 days (navigation RR for comparison: 1.27) | 1.00 | 1.27 | 1.53 |
| Cases still open at 90 days | 63.1% (62.7% to 63.5%) | 47.1% (46.7% to 47.5%) | 31.0% (30.6% to 31.3%) |
| Cases closed (evidence and human sign-off) by end of follow-up | 52.3% (51.7% to 52.9%) | 65.6% (65.0% to 66.1%) | 78.5% (78.1% to 78.9%) |
| Time to safe closure, median days (among closed cases only) | 70.2 (69.9 to 70.5) | 64.0 (63.7 to 64.3) | 60.4 (60.1 to 60.8) |
| Time to safe closure, Kaplan-Meier median days (censoring-aware) | 210.7 (194.9 to 228.2) | 83.2 (82.6 to 83.8) | 67.1 (66.8 to 67.5) |
| Time to safe closure, restricted mean days (censoring-aware) | 216.3 (215.0 to 217.7) | 171.9 (170.6 to 173.3) | 128.2 (127.2 to 129.2) |
| Time to first patient contact, median days | 12.4 (12.2 to 12.6) | 10.9 (10.8 to 10.9) | 10.0 (10.0 to 10.1) |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 45.7% (45.1% to 46.2%) | 33.5% (33.0% to 34.0%) | 21.5% (21.1% to 21.8%) |
| Lost AND never surfaced to a human: programme level (all cases) | 45.7% (45.1% to 46.2%) | 20.8% (20.3% to 21.2%) | 0.0% (0.0% to 0.0%) |
| Lost AND never surfaced to a human: in reach (cases the programme reached) | 45.7% (45.2% to 46.3%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| No colonoscopy by the delay-harm threshold (270 days) | 46.8% (46.3% to 47.4%) | 34.1% (33.6% to 34.6%) | 21.6% (21.2% to 21.9%) |
| Outreach after consent withdrawn: programme level (all cases) | 0.4% (0.3% to 0.5%) | 0.2% (0.1% to 0.2%) | 0.0% (0.0% to 0.0%) |
| Outreach after consent withdrawn: in reach (cases the programme reached) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Escalations per 100 cases (cost nothing in the model, see capacity caveat) | 0.00 (0.00 to 0.00) | 134.87 (133.25 to 136.45) | 232.28 (230.69 to 234.05) |
| Escalation acted-on rate (within 5 working days) | n/a | 40.4% (39.9% to 40.9%) | 43.7% (43.4% to 44.1%) |

**Lost and never surfaced, and outreach after consent withdrawn: two denominators each.** The programme-level rows count every case. The in-reach rows count only the cases the programme reached (`orchestration.programme_reach`; in the baseline column, the same patients under today's process, so the two columns compare like with like). In the orchestrated arm both in-reach rows are 0% by construction: a lost or unreachable case in reach is flagged to a human and the consent ledger blocks outreach after a withdrawal. The programme-level figures follow from the `programme_reach` assumption (0.55 in the literature-anchored scenario): cases outside the reach are handled as today and can still be lost silently or contacted after a withdrawal, so those figures are not a measured property of the orchestration.

Metric definitions (numerator, denominator, clocks, censoring) are in [`metrics.md`](metrics.md).

## Capacity caveat: escalations cost nothing in this model

**Every gain in the headline and the comparison tables is a gain before capacity constraints.** In that model cases are independent: there are no shared endoscopy slots, no staff workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however many others are open. The orchestrated arm raises about 232 escalations per 100 cases in the upper-range scenario (135 in the literature-anchored one), roughly one for every case. A real programme needs people and colonoscopy slots to work them. The section **Under finite capacity** below (ticket B19) charges for both: a shared booking calendar and a daily staff-minutes budget, with a capacity and a staffing sensitivity; it shows how much of the gain survives. Quote that section when the question is what a unit with finite slots and staff would see. What remains future work: per-endoscopist rosters, priority classes (urgent and symptomatic patients), overbooking, and a measured rather than assumed supply of slots and staff. The staff minutes an escalation takes are counted in the Human effort section. Definitions: [`metrics.md`](metrics.md).

<!-- capacity-section:begin (written by make evaluate-capacity; make evaluate copies it from capacity.json) -->
## Under finite capacity

Generated by `make evaluate-capacity` (seed 20261017, 20 replications x 2,000 cases x 360 days, same cohorts and random numbers as the rest of this file; `docs/results/capacity.json`, commit `1f0b7ceff748`). Everything above this section is the unconstrained model: cases independent, any number of colonoscopies on any day, escalations free.

**What changes.** Every case in a run now shares (1) a booking calendar with a fixed number of colonoscopy slots per working day at each of three providers: a booking takes the earliest free slot after its lead time, a no-show frees the slot only if the unit refills it (probability 0.3), a patient who withdraws consent and declines care cancels and frees it; and (2) a daily minutes budget for the supervisor and the programme coordinator, spent on escalations and watchdog flags (2 minutes to accept, plus 8.7 to phone the patient when the case is waiting for them), served first come first served, the overflow waiting for the next working day. Nothing else changes: same patients, same random numbers, same baseline logic. With so much capacity that nothing ever waits, the event log is identical to the unconstrained one (a test pins it).

**How big the base capacity is.** Slot supply is 6 sessions a week per 1,000 cases at 6 colonoscopies a session (a four-hour list is 12 points, a colonoscopy 2 points; NHS Improving Quality), over 3 providers, which fills 89% of the slots under today's process between day 30 and day 150. Staffing is 45 supervisor and 90 coordinator minutes a day per 1,000 cases. Sources and grades: [`docs/evidence.md`](../evidence.md) section 12.

### Headline outcomes at base capacity, both scenarios

**Upper range (mechanism ceiling).** Means over replications; the difference column is orchestrated minus baseline with its 95% interval.

| Metric | Baseline, no limit | Orchestrated, no limit | Difference, no limit | Baseline, base capacity | Orchestrated, base capacity | Difference, base capacity |
|---|---|---|---|---|---|---|
| Colonoscopy completed within 90 days | 41.9% | 73.1% | +31.2pp (+30.8pp to +31.6pp) | 31.6% | 32.6% | +1.0pp (+0.7pp to +1.4pp) |
| Colonoscopy completed within 180 days | 51.1% | 78.3% | +27.2pp (+26.8pp to +27.6pp) | 50.9% | 74.8% | +23.9pp (+23.4pp to +24.5pp) |
| Colonoscopy completed within 360 days | 54.1% | 78.5% | +24.4pp (+24.0pp to +24.9pp) | 54.0% | 82.3% | +28.3pp (+27.8pp to +28.7pp) |
| Time to colonoscopy, median among completers (days) | 61.1 | 52.2 | -8.9 (-9.3 to -8.5) | 80.5 | 105.9 | +25.4 (+24.8 to +26.1) |
| Time to colonoscopy, Kaplan-Meier median (days) | 161.2 | 58.8 | -102.4 (-118.2 to -91.5) | 168.3 | 123.5 | -44.9 (-61.0 to -33.8) |
| Cases still open at 90 days | 63.1% | 31.0% | -32.2pp (-32.6pp to -31.7pp) | 74.0% | 72.6% | -1.5pp (-1.8pp to -1.1pp) |
| Cases closed by end of follow-up | 52.3% | 78.5% | +26.2pp (+25.7pp to +26.6pp) | 52.3% | 82.3% | +30.0pp (+29.6pp to +30.5pp) |
| Lost to follow-up | 45.7% | 21.5% | -24.2pp (-24.6pp to -23.7pp) | 45.7% | 17.7% | -28.0pp (-28.4pp to -27.6pp) |
| No colonoscopy by 270 days | 46.8% | 21.6% | -25.3pp (-25.7pp to -24.8pp) | 46.9% | 17.8% | -29.2pp (-29.6pp to -28.7pp) |

**Literature anchored (the one to quote).** Means over replications; the difference column is orchestrated minus baseline with its 95% interval.

| Metric | Baseline, no limit | Orchestrated, no limit | Difference, no limit | Baseline, base capacity | Orchestrated, base capacity | Difference, base capacity |
|---|---|---|---|---|---|---|
| Colonoscopy completed within 90 days | 41.9% | 57.5% | +15.6pp (+15.3pp to +15.9pp) | 31.6% | 31.8% | +0.2pp (-0.1pp to +0.5pp) |
| Colonoscopy completed within 180 days | 51.1% | 64.9% | +13.8pp (+13.5pp to +14.2pp) | 50.9% | 65.6% | +14.7pp (+14.3pp to +15.1pp) |
| Colonoscopy completed within 360 days | 54.1% | 66.4% | +12.3pp (+12.0pp to +12.7pp) | 54.0% | 67.6% | +13.6pp (+13.2pp to +14.0pp) |
| Time to colonoscopy, median among completers (days) | 61.1 | 55.7 | -5.4 (-5.7 to -5.1) | 80.5 | 93.8 | +13.3 (+12.8 to +13.9) |
| Time to colonoscopy, Kaplan-Meier median (days) | 161.2 | 73.4 | -87.8 (-101.9 to -77.2) | 168.3 | 128.0 | -40.3 (-55.9 to -29.5) |
| Cases still open at 90 days | 63.1% | 47.1% | -16.0pp (-16.3pp to -15.7pp) | 74.0% | 73.3% | -0.7pp (-1.0pp to -0.4pp) |
| Cases closed by end of follow-up | 52.3% | 65.6% | +13.2pp (+12.9pp to +13.6pp) | 52.3% | 66.8% | +14.5pp (+14.1pp to +14.9pp) |
| Lost to follow-up | 45.7% | 33.5% | -12.2pp (-12.5pp to -11.9pp) | 45.7% | 32.3% | -13.5pp (-13.8pp to -13.1pp) |
| No colonoscopy by 270 days | 46.8% | 34.1% | -12.8pp (-13.1pp to -12.4pp) | 46.9% | 32.9% | -14.0pp (-14.4pp to -13.6pp) |

### Slots, waiting and the escalation queue at base capacity

| Slots and waiting at base capacity | Baseline | Orchestrated, upper range | Orchestrated, literature anchored |
|---|---|---|---|
| Slots booked / offered, days 30 to 150 | 88.5% | 96.4% | 94.9% |
| Slots used (attended or refilled) / offered | 80.8% | 88.4% | 85.3% |
| Appointments booked per 100 cases | 64.01 | 97.14 | 82.48 |
| Request to appointment, median (days) | 41.3 | 71.8 | 57.5 |
| Request to appointment, p90 (days) | 62.8 | 118.6 | 91.8 |
| Of which waiting for a free slot, mean (days) | 12.7 | 44.0 | 29.8 |
| Appointments pushed back a day or more by a full calendar | 54.9% | 78.3% | 71.8% |

| Escalation handling at base staffing (orchestrated arm) | Upper range | Literature anchored |
|---|---|---|
| Escalations and watchdog flags to work, per 100 cases | 442.44 | 231.04 |
| Supervisor budget used, days 30 to 150 | 95.3% | 92.5% |
| Coordinator budget used, days 30 to 150 | 97.6% | 81.1% |
| Wait before an escalation is worked, mean (days) | 53.4 | 13.9 |
| Wait before an escalation is worked, p90 (days) | 130.0 | 43.7 |
| Escalations that waited half a day or more | 93.5% | 57.1% |
| Escalations waiting at the end of a working day, mean | 2647 | 531 |
| Escalations waiting at the end of a working day, worst day | 3745 | 826 |
| Escalations that were stale when their turn came (the case had moved on) | 47.6% | 26.4% |

### Capacity sensitivity: 70, 85, 100 and 115% of base slot supply

**Upper range.** Slot supply moved to 70, 85, 100 and 115% of base; escalation staffing held at base. Differences are orchestrated minus baseline (the gain from the orchestration), with 95% intervals.

| Slot supply | Utilisation, baseline | Utilisation, orchestrated | Request to appointment, baseline (median days) | Request to appointment, orchestrated (median days) | Gain in 90-day completion | Gain in 180-day completion | Gain in 360-day completion | Time to colonoscopy, baseline (median days) | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|
| no limit | n/a | n/a | n/a | n/a | +31.2pp (+30.8pp to +31.6pp) | +27.2pp (+26.8pp to +27.6pp) | +24.4pp (+24.0pp to +24.9pp) | 61.1 | 52.2 |
| 70% | 93.8% | 97.5% | 60.0 | 105.0 | -0.0pp (-0.3pp to +0.2pp) | +4.8pp (+4.4pp to +5.2pp) | +28.2pp (+27.7pp to +28.7pp) | 102.7 | 142.9 |
| 85% | 92.7% | 97.1% | 48.6 | 85.1 | +0.4pp (+0.1pp to +0.7pp) | +14.6pp (+14.1pp to +15.1pp) | +28.3pp (+27.8pp to +28.7pp) | 89.6 | 120.8 |
| 100% (base) | 88.5% | 96.4% | 41.3 | 71.8 | +1.0pp (+0.7pp to +1.4pp) | +23.9pp (+23.4pp to +24.5pp) | +28.3pp (+27.8pp to +28.7pp) | 80.5 | 105.9 |
| 115% | 77.8% | 95.8% | 35.8 | 62.1 | +2.2pp (+1.9pp to +2.5pp) | +29.6pp (+29.1pp to +30.0pp) | +28.3pp (+27.8pp to +28.7pp) | 74.2 | 95.0 |

**Literature anchored.** Slot supply moved to 70, 85, 100 and 115% of base; escalation staffing held at base. Differences are orchestrated minus baseline (the gain from the orchestration), with 95% intervals.

| Slot supply | Utilisation, baseline | Utilisation, orchestrated | Request to appointment, baseline (median days) | Request to appointment, orchestrated (median days) | Gain in 90-day completion | Gain in 180-day completion | Gain in 360-day completion | Time to colonoscopy, baseline (median days) | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|
| no limit | n/a | n/a | n/a | n/a | +15.6pp (+15.3pp to +15.9pp) | +13.8pp (+13.5pp to +14.2pp) | +12.3pp (+12.0pp to +12.7pp) | 61.1 | 55.7 |
| 70% | 93.8% | 96.5% | 60.0 | 84.9 | -0.5pp (-0.7pp to -0.3pp) | +2.7pp (+2.4pp to +3.0pp) | +13.6pp (+13.1pp to +14.0pp) | 102.7 | 124.0 |
| 85% | 92.7% | 95.7% | 48.6 | 68.6 | -0.2pp (-0.4pp to +0.1pp) | +9.8pp (+9.3pp to +10.2pp) | +13.6pp (+13.2pp to +14.0pp) | 89.6 | 106.1 |
| 100% (base) | 88.5% | 94.9% | 41.3 | 57.5 | +0.2pp (-0.1pp to +0.5pp) | +14.7pp (+14.3pp to +15.1pp) | +13.6pp (+13.2pp to +14.0pp) | 80.5 | 93.8 |
| 115% | 77.8% | 94.3% | 35.8 | 49.9 | +0.7pp (+0.4pp to +1.0pp) | +15.0pp (+14.6pp to +15.3pp) | +13.6pp (+13.2pp to +14.0pp) | 74.2 | 85.1 |

### Which limit does what: slots only, staff only, both

**Upper range.** Orchestrated arm with one limit at a time. The gain is against the baseline arm under the same slot limit (the baseline raises no escalations, so staff limits never touch it).

| Which limit binds | 90-day completion | 180-day completion | 360-day completion | Gain at 90 days | Gain at 180 days | Gain at 360 days | Change in Kaplan-Meier median time to colonoscopy (days) |
|---|---|---|---|---|---|---|---|
| No limit (reference) | 73.1% | 78.3% | 78.5% | +31.2pp (+30.8pp to +31.6pp) | +27.2pp (+26.8pp to +27.6pp) | +24.4pp (+24.0pp to +24.9pp) | -102.4 (-118.2 to -91.5) |
| Slots limited, staff unlimited | 32.6% | 73.8% | 78.4% | +1.0pp (+0.7pp to +1.3pp) | +22.9pp (+22.4pp to +23.4pp) | +24.4pp (+23.9pp to +24.8pp) | -44.5 (-59.7 to -33.3) |
| Staff limited, slots unlimited | 74.3% | 82.1% | 82.4% | +32.4pp (+32.0pp to +32.8pp) | +31.0pp (+30.5pp to +31.4pp) | +28.4pp (+27.9pp to +28.8pp) | -102.1 (-117.4 to -90.5) |
| Both limited (base capacity) | 32.6% | 74.8% | 82.3% | +1.0pp (+0.7pp to +1.4pp) | +23.9pp (+23.4pp to +24.5pp) | +28.3pp (+27.8pp to +28.7pp) | -44.9 (-61.0 to -33.8) |

**Literature anchored.** Orchestrated arm with one limit at a time. The gain is against the baseline arm under the same slot limit (the baseline raises no escalations, so staff limits never touch it).

| Which limit binds | 90-day completion | 180-day completion | 360-day completion | Gain at 90 days | Gain at 180 days | Gain at 360 days | Change in Kaplan-Meier median time to colonoscopy (days) |
|---|---|---|---|---|---|---|---|
| No limit (reference) | 57.5% | 64.9% | 66.4% | +15.6pp (+15.3pp to +15.9pp) | +13.8pp (+13.5pp to +14.2pp) | +12.3pp (+12.0pp to +12.7pp) | -87.8 (-101.9 to -77.2) |
| Slots limited, staff unlimited | 31.8% | 64.4% | 66.3% | +0.2pp (-0.0pp to +0.5pp) | +13.5pp (+13.2pp to +13.8pp) | +12.3pp (+11.9pp to +12.6pp) | -40.4 (-55.9 to -29.4) |
| Staff limited, slots unlimited | 58.3% | 66.3% | 67.8% | +16.4pp (+16.1pp to +16.7pp) | +15.2pp (+14.8pp to +15.6pp) | +13.7pp (+13.3pp to +14.1pp) | -88.6 (-104.7 to -77.8) |
| Both limited (base capacity) | 31.8% | 65.6% | 67.6% | +0.2pp (-0.1pp to +0.5pp) | +14.7pp (+14.3pp to +15.1pp) | +13.6pp (+13.2pp to +14.0pp) | -40.3 (-55.9 to -29.5) |

### Staffing sensitivity: escalation budgets at 50 to 300% of base

**Upper range.** Supervisor and coordinator budgets moved together to 50, 75, 100, 150, 200 and 300% of base; slot supply held at base. Gains are against the baseline arm at base capacity (the baseline raises no escalations, so staffing does not touch it).

| Staffing | Supervisor budget used | Coordinator budget used | Wait to be worked, mean (days) | Wait to be worked, p90 (days) | Escalations waiting, mean | Stale when worked | 180-day completion, orchestrated | Gain in 90-day completion | Gain in 180-day completion | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|---|
| 50% | 91.7% | 95.1% | 132.4 | 228.9 | 3787 | 60.0% | 74.4% | +1.2pp (+0.9pp to +1.5pp) | +23.5pp (+23.0pp to +24.0pp) | 105.8 |
| 75% | 94.5% | 96.6% | 94.3 | 195.8 | 3260 | 56.9% | 74.7% | +1.1pp (+0.8pp to +1.4pp) | +23.8pp (+23.3pp to +24.4pp) | 106.1 |
| 100% (base) | 95.3% | 97.6% | 53.4 | 130.0 | 2647 | 47.6% | 74.8% | +1.0pp (+0.7pp to +1.3pp) | +23.9pp (+23.4pp to +24.5pp) | 105.9 |
| 150% | 97.3% | 90.5% | 21.6 | 63.2 | 1457 | 31.6% | 74.8% | +1.0pp (+0.7pp to +1.3pp) | +23.9pp (+23.3pp to +24.4pp) | 105.5 |
| 200% | 83.0% | 70.1% | 11.0 | 33.5 | 751 | 21.9% | 74.7% | +1.1pp (+0.8pp to +1.5pp) | +23.8pp (+23.2pp to +24.3pp) | 104.9 |
| 300% | 53.4% | 46.4% | 2.9 | 13.4 | 194 | 13.8% | 74.3% | +0.9pp (+0.6pp to +1.2pp) | +23.5pp (+23.0pp to +23.9pp) | 104.4 |

**Literature anchored.** Supervisor and coordinator budgets moved together to 50, 75, 100, 150, 200 and 300% of base; slot supply held at base. Gains are against the baseline arm at base capacity (the baseline raises no escalations, so staffing does not touch it).

| Staffing | Supervisor budget used | Coordinator budget used | Wait to be worked, mean (days) | Wait to be worked, p90 (days) | Escalations waiting, mean | Stale when worked | 180-day completion, orchestrated | Gain in 90-day completion | Gain in 180-day completion | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|---|
| 50% | 91.6% | 95.1% | 69.4 | 156.7 | 1577 | 50.2% | 65.6% | +0.2pp (-0.0pp to +0.5pp) | +14.7pp (+14.4pp to +15.0pp) | 94.3 |
| 75% | 94.6% | 95.8% | 27.3 | 78.8 | 944 | 38.4% | 65.8% | +0.2pp (-0.1pp to +0.4pp) | +14.9pp (+14.5pp to +15.3pp) | 94.2 |
| 100% (base) | 92.5% | 81.1% | 13.9 | 43.7 | 531 | 26.4% | 65.6% | +0.2pp (-0.1pp to +0.5pp) | +14.7pp (+14.3pp to +15.1pp) | 93.8 |
| 150% | 60.3% | 53.5% | 4.3 | 17.2 | 158 | 15.4% | 65.3% | +0.2pp (-0.0pp to +0.5pp) | +14.4pp (+14.0pp to +14.7pp) | 93.5 |
| 200% | 43.6% | 41.2% | 1.5 | 7.6 | 57 | 9.1% | 64.8% | +0.1pp (-0.2pp to +0.3pp) | +13.9pp (+13.6pp to +14.2pp) | 93.0 |
| 300% | 28.6% | 27.5% | 0.1 | 0.3 | 5 | 1.6% | 64.4% | +0.2pp (-0.0pp to +0.5pp) | +13.5pp (+13.1pp to +13.9pp) | 92.7 |

### How much the benefit shrinks when capacity binds

**In plain English, for the literature-anchored scenario.** Give the programme a finite number of colonoscopy slots and a finite number of staff minutes and the benefit in *how soon* patients get their colonoscopy shrinks sharply (the 90-day advantage is almost gone and the advantage in median time to colonoscopy roughly halves), while the benefit in *whether* they get one within six to twelve months mostly survives at base capacity and then falls away quickly if slots are scarcer than that.

- **Completion within 90 days.** Without a limit, orchestration completes +15.6 percentage points more patients within 90 days than today's process. At base capacity the gain is almost gone (1% of the unconstrained gain is left): +0.2 percentage points. The share of cases still open at 90 days improves by 0.7 points against 16.0 without a limit. The calendar is 89% full under today's process and 95% full with the orchestration, which books 82 appointments per 100 cases against 64: the patients it brings in queue behind patients already booked, and the median wait from request to appointment is 58 days against 41.
- **Time to colonoscopy.** The censoring-aware (Kaplan-Meier) median time to colonoscopy is shortened by 88 days without a limit and by 40 days at base capacity (the gain shrinks to 46% of the unconstrained gain). The plain median among patients who had a colonoscopy goes the other way (-5 days without a limit, +13 days at base capacity, orchestrated minus baseline), because the orchestration brings in extra patients who are, on average, later than the ones who would have come anyway; the Kaplan-Meier figure is the fair one.
- **Completion within 180 and 360 days.** The 180-day gain holds (106% of the unconstrained gain) (+14.7 percentage points against +13.8 percentage points without a limit) and the 360-day gain is larger (110% of the unconstrained gain) (+13.6 percentage points). A full calendar delays a colonoscopy; at base capacity it does not stop most of the extra patients from getting one within six months. Part of why the gain is not smaller is a model effect explained under *Which limit does it*.
- **If slots are scarcer, the 180-day gain goes quickly.** It is +15.0 percentage points at 115% of base slot supply, +14.7 percentage points at 100%, +9.8 percentage points at 85% and +2.7 percentage points at 70%. The orchestration needs room for roughly 29% more appointments than today's process; once the calendar cannot take them the extra patients wait past the 180-day mark, and no escalation can make a slot appear. The 360-day gain stays at +13.6 percentage points even at 70%, because the demand is a single wave and the queue always clears in the end; with a steady inflow of new positives it would not (see the limits below).
- **Staff minutes.** At base staffing an escalation waits a mean of 13.9 days to be worked, with 531 waiting on an average day. Halving the staff takes the wait to 69.4 days; doubling them takes it to 1.5 and tripling them to 0.1: a team sized to 85% of the average workload needs roughly twice that size to keep escalations from queueing. Completion hardly moves with it: the 180-day gain is +14.7 percentage points at 50% staffing and +13.5 percentage points at 300%.
- **Which limit does it.** The slots do the damage to speed. Limiting only the slots leaves a 90-day gain of +0.2 percentage points, a 180-day gain of +13.5 percentage points and a 360-day gain of +12.3 percentage points. Limiting only the staff does not lower completion at all in this model: it leaves +16.4 percentage points, +15.2 percentage points and +13.7 percentage points, which is *more* than with no limit. That is a property of the model's ladder, not a finding about staffing: an escalation worked days late is more often moot (the case moved on, stale), so fewer chases and referral bounces go out, and each chase that goes unanswered or bounce that is rejected can push a patient further from care (`patients.dropout_prob_per_lapse`, `handoff.max_attempts`). The both-limits gain at 180 and 360 days therefore sits about a point above the slots-only gain for that reason, and the staffing rows must be read as statements about waiting and workload, not as evidence that understaffing is harmless.

For the upper-range scenario (the mechanism ceiling) the 90-day gain goes from +31.2 percentage points to +1.0 percentage points and the 180-day gain from +27.2 percentage points to +23.9 percentage points; the orchestration there books 97 appointments per 100 cases and the calendar is 96% full. Its escalation queue is far longer (53 days mean wait at base staffing), because it raises far more escalations than the staff were sized for.

**How far to trust it.** The size of these effects follows from two sizing choices that are assumptions, not measurements, and the tables above show what moves when they change:

1. *The base calendar is sized to today's demand.* Slot supply is set so that the baseline arm fills about 85% of the offered slots between day 30 and day 150, because the ticket asked for 80 to 90% and no public source says how many slots a catchment of positive-FIT patients has. A real unit with more slack than that would show smaller losses; a unit already full with symptomatic and surveillance patients would show larger ones. The 70 to 115% rows answer that question.
2. *The base staffing is sized to the literature-anchored workload.* Supervisor and coordinator budgets are set so that the unconstrained workload is about 85% of the budget over the same window. No public staffing ratio exists. The work arrives as one wave (every result arrives within 30 days), so even a team sized to 85% of the average has a queue while the wave passes.

**Things the model does not do.** The demand is a single wave: all 2,000 results arrive within 30 days and nothing else uses the calendar apart from the refill of no-shows, so a calendar that is too small only postpones colonoscopies, and a team that is too small only postpones escalations; with a steady inflow of positives the queue would not clear and the 360-day figures would be worse. The calendar is flat (the same slots every working day, equal providers). Priorities (urgent and symptomatic patients, the cancer pathway) are not modelled, and neither is overbooking, which units use against no-shows. A stale escalation still costs the reviewer its minutes. The slot supply and the staff budgets are sized to the workload, not measured, so the absolute waits are properties of that sizing; the sweeps above show how they move.

**Why the headline at the top is unchanged.** The headline of this document remains the unconstrained model, which is what the earlier results, the sensitivity sweep and the risk score were built on. Quote the figures in this section whenever the question is what a unit with finite slots and finite staff would see.

### Capacity parameters

| Parameter (simulator/params.capacity.yaml) | Value | Status |
|---|---|---|
| `capacity.dna_backfill_prob` | 0.3 | assumed |
| `capacity.providers` | 3 | assumed |
| `capacity.scale` | 1.0 | assumed |
| `capacity.sessions_per_week_per_1000_cases` | 6.0 | calibrated |
| `capacity.slots_per_session` | 6 | sourced-adjacent |
| `capacity.staff.accept_minutes` | 2.0 | assumed |
| `capacity.staff.contact_minutes` | 8.7 | sourced-adjacent |
| `capacity.staff.coordinator_minutes_per_day_per_1000_cases` | 90.0 | calibrated |
| `capacity.staff.scale` | 1.0 | assumed |
| `capacity.staff.supervisor_minutes_per_day_per_1000_cases` | 45.0 | calibrated |
| `capacity.utilisation_window_end_day` | 150 | assumed |
| `capacity.utilisation_window_start_day` | 30 | assumed |

Definitions of every capacity metric: [`metrics.md`](metrics.md) section 12.
<!-- capacity-section:end -->

## Summary

**Literature anchored.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 65% of patients to a confirmatory colonoscopy within 180 days, against 51% at baseline (+13.8 percentage points, 95% CI +13.5 to +14.1, which excludes zero). At 90 days 47% of orchestrated cases were still open against 63% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 64 days orchestrated against 70 at baseline; that conditional median only covers the 52% of baseline cases that closed at all (66% orchestrated), so an arm that closes more slow cases looks slower on it. Counting the cases that never closed as censored at the end of follow-up instead of dropping them, the Kaplan-Meier median is 83 days orchestrated against 211 at baseline and the restricted mean time to closure within 360 days is 172 against 216 days. 46% of baseline cases were lost without anyone being told, against 21% in the orchestrated arm at programme level; among the 55% of cases the programme reached the orchestrated figure is 0% against 46% for the same patients at baseline, and the programme-level remainder is cases outside the programme's reach, which are handled as today.

**Upper range.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 78% of patients to a confirmatory colonoscopy within 180 days, against 51% at baseline (+27.2 percentage points, 95% CI +26.7 to +27.6, which excludes zero). At 90 days 31% of orchestrated cases were still open against 63% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 60 days orchestrated against 70 at baseline; that conditional median only covers the 52% of baseline cases that closed at all (78% orchestrated), so an arm that closes more slow cases looks slower on it. Counting the cases that never closed as censored at the end of follow-up instead of dropping them, the Kaplan-Meier median is 67 days orchestrated against 211 at baseline and the restricted mean time to closure within 360 days is 128 against 216 days. 46% of baseline cases were lost without anyone being told, against 0% in the orchestrated arm at programme level, where lost or unreachable cases are flagged to a human and never closed automatically. These results come from a model, not a trial: 103 of 138 parameters are assumed or calibrated rather than measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml` or `simulator/params.conservative.yaml`.

## Arm comparison: literature anchored scenario (the one to quote)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 41.9% (41.5% to 42.3%) | 57.5% (57.0% to 57.9%) | +15.6pp (+15.3pp to +15.9pp) |
| Colonoscopy completed within 180 days | 51.1% (50.6% to 51.6%) | 64.9% (64.4% to 65.4%) | +13.8pp (+13.5pp to +14.1pp) |
| Colonoscopy completed within 360 days | 54.1% (53.5% to 54.6%) | 66.4% (65.9% to 67.0%) | +12.3pp (+12.0pp to +12.7pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.6% (99.5% to 99.7%) | -0.2pp (-0.2pp to -0.1pp) |
| Cases still open at 60 days | 82.3% (82.1% to 82.6%) | 72.4% (72.0% to 72.9%) | -9.9pp (-10.3pp to -9.6pp) |
| Cases still open at 90 days | 63.1% (62.7% to 63.5%) | 47.1% (46.7% to 47.5%) | -16.0pp (-16.3pp to -15.7pp) |
| Time to safe closure, median (among closed cases only) | 70.2 (69.9 to 70.5) | 64.0 (63.7 to 64.3) | -6.2 (-6.4 to -6.0) |
| Time to safe closure, p90 (among closed cases only) | 143.1 (140.1 to 146.0) | 108.4 (107.0 to 109.7) | -34.7 (-36.9 to -32.8) |
| Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up) | 210.7 (194.9 to 228.2) | 83.2 (82.6 to 83.8) | -127.5 (-144.0 to -112.1) |
| Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware) | 216.3 (215.0 to 217.7) | 171.9 (170.6 to 173.3) | -44.4 (-45.5 to -43.4) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 52.3% (51.7% to 52.9%) | 65.6% (65.0% to 66.1%) | +13.2pp (+12.9pp to +13.7pp) |
| Time to named owner, median | n/a | 5.0 (4.9 to 5.0) | n/a |
| Time to named owner, p90 | n/a | 8.7 (8.5 to 8.9) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 10.9 (10.8 to 10.9) | -1.5 (-1.6 to -1.4) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 20.1 (20.0 to 20.3) | -3.0 (-3.2 to -2.9) |
| Time to colonoscopy, median (among completers only) | 61.1 (60.7 to 61.5) | 55.7 (55.4 to 55.9) | -5.4 (-5.7 to -5.1) |
| Time to colonoscopy, Kaplan-Meier median (censoring-aware) | 161.2 (150.1 to 176.8) | 73.4 (72.6 to 74.1) | -87.8 (-102.5 to -76.7) |
| Time to colonoscopy, restricted mean (censoring-aware) | 206.9 (205.5 to 208.4) | 164.0 (162.5 to 165.3) | -42.9 (-43.9 to -42.0) |
| Reopen rate (reopened / closed) | 3.0% (2.7% to 3.2%) | 3.0% (2.8% to 3.1%) | -0.0pp (-0.1pp to +0.1pp) |
| Escalation acted-on rate (within 5 working days) | n/a | 40.4% (39.9% to 40.9%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 134.87 (133.25 to 136.45) | +134.87 (+133.12 to +136.46) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 45.7% (45.1% to 46.2%) | 33.5% (33.0% to 34.0%) | -12.2pp (-12.5pp to -11.9pp) |
| Lost AND never surfaced to a human, programme level (all cases) | 45.7% (45.1% to 46.2%) | 20.8% (20.3% to 21.2%) | -24.9pp (-25.3pp to -24.5pp) |
| Lost AND never surfaced to a human, in reach (cases the programme reached) | 45.5% (45.0% to 46.2%) | 0.0% (0.0% to 0.0%) | -45.5pp (-46.2pp to -45.0pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 46.8% (46.3% to 47.4%) | 34.1% (33.6% to 34.6%) | -12.8pp (-13.1pp to -12.4pp) |
| Outreach sent after consent withdrawn, programme level (all cases) | 0.4% (0.3% to 0.5%) | 0.2% (0.1% to 0.2%) | -0.2pp (-0.3pp to -0.2pp) |
| Outreach sent after consent withdrawn, in reach (cases the programme reached) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |
| Cases within the programme's reach (denominator of the in-reach rows) | 54.7% (54.2% to 55.2%) | 54.7% (54.2% to 55.2%) | +0.0pp (+0.0pp to +0.0pp) |

## Arm comparison: upper range scenario (mechanism ceiling)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 41.9% (41.5% to 42.3%) | 73.1% (72.7% to 73.4%) | +31.2pp (+30.8pp to +31.6pp) |
| Colonoscopy completed within 180 days | 51.1% (50.6% to 51.6%) | 78.3% (77.9% to 78.7%) | +27.2pp (+26.7pp to +27.6pp) |
| Colonoscopy completed within 360 days | 54.1% (53.5% to 54.6%) | 78.5% (78.1% to 78.8%) | +24.4pp (+23.9pp to +24.9pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.4% (99.3% to 99.5%) | -0.4pp (-0.5pp to -0.3pp) |
| Cases still open at 60 days | 82.3% (82.1% to 82.6%) | 61.4% (60.9% to 61.9%) | -21.0pp (-21.5pp to -20.5pp) |
| Cases still open at 90 days | 63.1% (62.7% to 63.5%) | 31.0% (30.6% to 31.3%) | -32.2pp (-32.6pp to -31.7pp) |
| Time to safe closure, median (among closed cases only) | 70.2 (69.9 to 70.5) | 60.4 (60.1 to 60.8) | -9.8 (-10.2 to -9.4) |
| Time to safe closure, p90 (among closed cases only) | 143.1 (140.1 to 146.0) | 91.3 (90.8 to 91.8) | -51.8 (-54.7 to -49.1) |
| Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up) | 210.7 (194.9 to 228.2) | 67.1 (66.8 to 67.5) | -143.6 (-160.4 to -128.0) |
| Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware) | 216.3 (215.0 to 217.7) | 128.2 (127.2 to 129.2) | -88.1 (-89.4 to -86.8) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 52.3% (51.7% to 52.9%) | 78.5% (78.1% to 78.9%) | +26.2pp (+25.6pp to +26.6pp) |
| Time to named owner, median | n/a | 5.0 (5.0 to 5.0) | n/a |
| Time to named owner, p90 | n/a | 8.6 (8.5 to 8.7) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 10.0 (10.0 to 10.1) | -2.4 (-2.5 to -2.2) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 15.4 (15.3 to 15.5) | -7.8 (-8.1 to -7.6) |
| Time to colonoscopy, median (among completers only) | 61.1 (60.7 to 61.5) | 52.2 (51.9 to 52.4) | -8.9 (-9.3 to -8.5) |
| Time to colonoscopy, Kaplan-Meier median (censoring-aware) | 161.2 (150.1 to 176.8) | 58.8 (58.4 to 59.2) | -102.4 (-117.2 to -90.9) |
| Time to colonoscopy, restricted mean (censoring-aware) | 206.9 (205.5 to 208.4) | 121.8 (120.7 to 122.8) | -85.1 (-86.3 to -83.8) |
| Reopen rate (reopened / closed) | 3.0% (2.7% to 3.2%) | 3.1% (2.9% to 3.2%) | +0.1pp (-0.0pp to +0.2pp) |
| Escalation acted-on rate (within 5 working days) | n/a | 43.7% (43.4% to 44.1%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 232.28 (230.69 to 234.05) | +232.28 (+230.70 to +233.89) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 45.7% (45.1% to 46.2%) | 21.5% (21.1% to 21.8%) | -24.2pp (-24.6pp to -23.7pp) |
| Lost AND never surfaced to a human, programme level (all cases) | 45.7% (45.1% to 46.2%) | 0.0% (0.0% to 0.0%) | -45.7pp (-46.3pp to -45.1pp) |
| Lost AND never surfaced to a human, in reach (cases the programme reached) | 45.7% (45.2% to 46.3%) | 0.0% (0.0% to 0.0%) | -45.7pp (-46.3pp to -45.2pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 46.8% (46.3% to 47.4%) | 21.6% (21.2% to 21.9%) | -25.3pp (-25.7pp to -24.8pp) |
| Outreach sent after consent withdrawn, programme level (all cases) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |
| Outreach sent after consent withdrawn, in reach (cases the programme reached) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |
| Cases within the programme's reach (denominator of the in-reach rows) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | +0.0pp (+0.0pp to +0.0pp) |

Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. `n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.

## Human effort

Human minutes per case in each arm and the reduction automation buys, from the event logs of the same replications as the rest of this report (95% CIs are percentile bootstrap over replications; the reduction is paired by replication). Definitions: [`metrics.md`](metrics.md) section 11. Sources and grades of every minutes figure: [`evidence.md`](../evidence.md) section 11.

| Measure | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Human minutes per case | 57.3 (57.1 to 57.6) | 46.8 (46.6 to 47.1) | 36.0 (35.5 to 36.4) |
| Human minutes per closed case | 109.6 (108.7 to 110.5) | 71.5 (70.7 to 72.2) | 45.8 (45.2 to 46.5) |
| Human actions per case | 9.72 (9.68 to 9.76) | 9.43 (9.35 to 9.50) | 8.55 (8.43 to 8.66) |
| Automation share (automated actions / all actions) | 0.0% (0.0% to 0.0%) | 59.0% (58.8% to 59.3%) | 74.1% (73.8% to 74.3%) |
| **Labour reduction**, minutes per case (1 - orchestrated / baseline) | - | 18.3% (17.8% to 18.8%) | 37.3% (36.4% to 38.1%) |
| Labour reduction, minutes per closed case | - | 34.8% (34.2% to 35.4%) | 58.2% (57.6% to 58.8%) |
| Reduction in human actions per case | - | 3.0% (2.1% to 3.9%) | 12.0% (10.6% to 13.2%) |
| Among the cases the programme reaches: human minutes per case | 57.4 (57.1 to 57.6) | 38.2 (37.7 to 38.6) | 36.0 (35.5 to 36.3) |
| Among the cases the programme reaches: **labour reduction** | - | 33.4% (32.5% to 34.4%) | 37.3% (36.4% to 38.1%) |

**What is counted.** Staff minutes per case in each arm, computed from the simulated event log (`simulator/sim/effort.py`; no outcome in this report depends on it). A person's time is counted only when a person acts. Today's process is manual at every step (look up and acknowledge the result, phone or write to the patient, send the referral, book, record that the patient called back, review the report, file the closure), and the model adds the chasing that leaves no event in the log (up to 3 attempts every 14 days when a patient, a referral or a colonoscopy is overdue). In the orchestrated process the engine does the following by machine and they cost no human minutes: acknowledge on ingest; owner auto-assignment; pre-approved template outreach and reminders; evidence detection; closure proposal; referral acceptance carries the appointment; reroute down a provider list; bounce; overdue; escalation emission; reply handling; consent ledger; delivery tracking; de-duplication; state machine. A person acts only to accept an escalation, approve a drafted message, sign the closure and handle an exhausted reroute list, plus the steps automation cannot do (recording a patient's call-back, a consent withdrawal, a transfer decision, a rebooking, a corrected contact, a reopened case, and the specialist's own accept or reject). In the literature-anchored scenario only 55% of orchestrated-arm cases receive the orchestrated process (`programme_reach`); the rest stay manual, so the programme-level reduction is diluted by construction and the in-reach figure is the one that describes the automation itself.

**Result.** In the literature-anchored scenario the programme-level reduction is 18.3%, 95% CI 17.8% to 18.8%: the 40% target is **not met**. Among the cases the programme reaches it is 33.4%, 95% CI 32.5% to 34.4% (target not met); per closed case it is 34.8%, 95% CI 34.2% to 35.4%. In the upper-range scenario the reduction is 37.3%, 95% CI 36.4% to 38.1% (target not met; the lower end of the interval is 36.4%); per closed case it is 58.2%, 95% CI 57.6% to 58.8%.

**What drives the remaining minutes.** In the literature-anchored scenario the orchestrated arm needs 46.8 human minutes per case against 57.3 at baseline. The largest orchestrated contributors are specialist review of referrals (7.3 min per case), coordinator alternate-route contacts (4.7 min per case), hand-over to a clinician (4.2 min per case), chasing patients (3.9 min per case). Escalation handling alone (accepting, phoning, approving) is 11.9 minutes per case, because the orchestrated arm escalates far more than today's process ever does; the orchestrated arm also takes 13.6 automated actions per case that the baseline does not do at all, so the reduction is in people's time, not in the work done. 9.4 human actions per case remain, many of them short. The time also moves between roles: coordinator minutes change by -35% but owner-clinician minutes by +109% (accepting escalations, calling, approving and signing are clinician decisions) and specialist minutes by +19%; whether that is cheaper depends on wages, which are not in the model.

**How far to trust the minutes.** 66% of baseline minutes and 61% of orchestrated minutes rest on a published minutes figure from an adjacent setting (care coordinators and navigators in Australia and the United States); none was measured in the Abu Dhabi pathway, and none is graded `sourced` (a direct measurement of the same task in the same population). The rest (34% of baseline and 39% of orchestrated minutes) is assumed. Moving every figure to its low or its high end together barely changes the reduction, because it is a ratio, but the relative sizes matter: the table above shows how far individual figures move it, and the least favourable corner (baseline-only actions at their low end, orchestrated-only actions at their high end) gives -48%. Three modelling choices carry most of the weight: the baseline's chasing (without it the reduction is 10.2%, and baseline chasing was 10.4 minutes per case), whether a person still records every booking in the orchestrated arm (if so, 12.0%), and whether the human escalation outreach is a phone call or a drafted message that a person only approves (if only approved, 29.7%). All three are flagged for confirmation with the engine ticket (A21).

**Limits.** Human minutes are not capacity: there is no queue, no shift pattern and no shared workload, so a coordinator who accepts three escalations in an hour is not slowed down (see the capacity caveat). The orchestrated escalation volume is a simulator output (the SLA timers breach often), and a real programme could reduce it by batching or by tuning the ladder. The simulator has no model-drafted messages (every message is a pre-approved template), so the approval charge is a proxy placed on the human-contact messages. Time spent by patients, the endoscopy unit's procedure and reception work, and management or training time are outside the count.

### Human minutes per case by role

| Measure | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Coordinator: minutes per case | 46.8 (46.7 to 47.0) | 30.4 (30.1 to 30.6) | 14.9 (14.6 to 15.3) |
| Owner clinician: minutes per case | 4.4 (4.3 to 4.4) | 9.2 (9.1 to 9.3) | 12.7 (12.6 to 12.8) |
| Specialist: minutes per case | 6.1 (6.0 to 6.2) | 7.3 (7.2 to 7.4) | 8.3 (8.3 to 8.4) |
| Coordinator: reduction | - | 35.2% (34.6% to 35.7%) | 68.2% (67.4% to 68.9%) |
| Owner clinician: reduction | - | -109.2% (-112.4% to -106.1%) | -189.6% (-195.1% to -184.3%) |
| Specialist: reduction | - | -19.5% (-20.2% to -18.9%) | -36.6% (-37.7% to -35.5%) |

### How robust is the reduction

| Variant (labour reduction, minutes per case, point estimate) | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|
| Headline (the table above) | +18.3% | +37.3% |
| Baseline chasing not costed (only logged events count in the baseline) | +10.2% | +23.3% |
| Baseline chases harder (up to 5 attempts, every 7 days) | +22.9% | +45.2% |
| Specialist minutes left out (coordinator and owner clinician only) | +22.8% | +46.1% |
| Orchestrated arm: a person still records every booking | +12.0% | +25.7% |
| No charge for approving drafts (a phone call needs no approval) | +20.7% | +41.2% |
| Escalation outreach is an approved drafted message, not a phone call (calls cost no minutes) | +29.7% | +55.9% |
| Every minutes figure at its low end | +17.6% | +36.1% |
| Every minutes figure at its high end | +17.9% | +36.6% |
| Least favourable corner: baseline-only actions at their low end, orchestrated-only at their high end | -47.7% | -74.8% |

The five minutes figures that move the literature anchored reduction most, one at a time (central 18.3%):

| Minutes figure (top 5 by swing) | Grade | Low / central / high (min) | Reduction at low | Reduction at high |
|---|---|---|---|---|
| Coordinator contacts the patient by the alternate route (`O_COORDINATOR_CALL`) | sourced-adjacent | 3.9 / 7.2 / 12.1 | 22.1% | 12.7% |
| Look up the positive result, review the chart, open a work item (`M_RESULT_LOOKUP`) | assumed | 3 / 5 / 14 | 17.0% | 23.2% |
| Chase a patient who has not answered (phone call, up to N attempts) (`M_CHASE_PATIENT`) | sourced-adjacent | 3.9 / 7.2 / 12.1 | 15.6% | 21.7% |
| Coordinator accepts an escalation or a watchdog flag (`O_ACCEPT_ESC_COORDINATOR`) | assumed | 1 / 2 / 4 | 20.3% | 14.3% |
| Send or fax the referral to the endoscopy service (`M_REFERRAL_SEND`) | sourced-adjacent | 6.2 / 12.4 / 18.6 | 15.4% | 20.8% |

### The minutes table

Every human action, the minutes used, the range used in the sweep and where the figure comes from. `sourced-adjacent` = a published figure for a related task or setting; `assumed` = no published figure found. Steps the engine automates cost no human minutes and are listed in `results.json` under `effort.model.automated_steps`.

| Human action | Role | Process | Minutes (range) | Grade | Basis |
|---|---|---|---|---|---|
| `M_RESULT_LOOKUP` Look up the positive result, review the chart, open a work item | coordinator | today's process | 5 (3 to 14) | assumed | assumed: 5 minutes; E11-1 'reading medical history/report' (14.0 minutes, complex-care coordinators) is the upper bound |
| `M_RESULT_ACK` Acknowledge the result in the record | coordinator | today's process | 2 (1 to 4) | assumed | assumed: no time study of result acknowledgement found |
| `M_HANDOVER` Decide who follows the case up and hand it over (no named owner today) | coordinator | today's process | 9.5 (4.75 to 10.3) | sourced-adjacent | E11-1 'handover' 9.5 minutes (SD 4.1), 'prioritising patients' 10.3; care coordinators in a hospital-community service |
| `M_PHONE_ATTEMPT` Phone the patient (one attempt, with documentation) | coordinator | today's process | 7.2 (3.9 to 12.1) | sourced-adjacent | E11-1 'follow-up call after discharge' 7.2 minutes (SD 3.5); E11-2: 15.5 minutes on calls over a typical 4 calls = 3.9 (low), 48.5 total per patient / 4 = 12.1 (high) |
| `M_SMS_ATTEMPT` Send the patient an SMS or WhatsApp message by hand | coordinator | today's process | 1.5 (1 to 3) | assumed | assumed: 1.5 minutes; E11-5 assumed 1 minute per reminder call (the floor) |
| `M_LETTER` Prepare and post a letter | coordinator | today's process | 4 (2 to 8) | assumed | assumed: 4 minutes; E11-4 reports 17.5 minutes to compile and mail materials per woman over 18 months (number of mailings not reported) |
| `M_REFERRAL_SEND` Send or fax the referral to the endoscopy service | coordinator | today's process | 12.4 (6.2 to 18.6) | sourced-adjacent | E11-1 'referral - external' 12.4 minutes (SD 9.1, n=102); E11-6 about 12 minutes per manual referral corroborates (secondary report) |
| `M_COMPLETION_RECORD` Record that the colonoscopy happened (find and file the report) | coordinator | today's process | 5 (2.5 to 10) | assumed | assumed: 5 minutes |
| `M_CHASE_PATIENT` Chase a patient who has not answered (phone call, up to N attempts) | coordinator | today's process | 7.2 (3.9 to 12.1) | sourced-adjacent | as M_PHONE_ATTEMPT: E11-1 follow-up call 7.2 minutes; E11-2 3.9 to 12.1 per call |
| `M_CHASE_SPECIALIST` Chase the specialist's office that has not replied (up to N attempts) | coordinator | today's process | 8.1 (4 to 12.2) | sourced-adjacent | E11-1 'contracting/liaising with provider - external' 8.1 minutes (SD 11.7, n=192) |
| `M_CHASE_COMPLETION` Check whether an overdue colonoscopy happened (up to N attempts) | coordinator | today's process | 5 (2.5 to 10) | assumed | assumed: 5 minutes |
| `M_DUPLICATE_ITEM` Process a duplicate result from a second lab as a new work item | coordinator | today's process | 5 (3 to 14) | assumed | assumed: same as M_RESULT_LOOKUP |
| `M_CLINICIAN_REVIEW` Clinician reads the colonoscopy report and documents the finding | owner clinician | today's process | 5 (3 to 10) | assumed | assumed: 5 minutes |
| `M_CLOSE_FILE` Clinician files the closure | owner clinician | today's process | 3 (2 to 6) | assumed | assumed: 3 minutes |
| `X_RECORD_CONTACT` Record that the patient called back or walked in | coordinator | both | 2 (1 to 4) | assumed | assumed: 2 minutes |
| `X_BOOK_APPT` Book or rebook the colonoscopy appointment | coordinator | both | 8 (4 to 15.1) | sourced-adjacent | E11-4 'scheduling appointments and sending reminders' 8.0 minutes per woman; E11-3 'scheduling or rescheduling appointments' 15.1 minutes (high) |
| `X_SPECIALIST_REVIEW` Specialist reviews a referral and accepts or rejects it | specialist | both | 9.5 (4.75 to 15.7) | sourced-adjacent | E11-1 'accepting/discussing referral' 9.5 minutes (SD 6.2, n=230); range is the mean plus or minus 1 SD (low halved) |
| `X_REOPEN_REVIEW` Clinician reviews a late finding that reopens a closed case | owner clinician | both | 5 (3 to 10) | assumed | assumed: 5 minutes |
| `O_ACCEPT_ESC_SUPERVISOR` Supervisor accepts an escalation | owner clinician | orchestrated | 2 (1 to 4) | assumed | assumed: 2 minutes to open the case, read the context and accept |
| `O_ACCEPT_ESC_COORDINATOR` Coordinator accepts an escalation or a watchdog flag | coordinator | orchestrated | 2 (1 to 4) | assumed | assumed: 2 minutes |
| `O_SUPERVISOR_CALL` Supervisor phones the patient after an escalation | owner clinician | orchestrated | 7.2 (3.9 to 12.1) | sourced-adjacent | as M_PHONE_ATTEMPT: E11-1 follow-up call 7.2 minutes; E11-2 3.9 to 12.1 per call |
| `O_COORDINATOR_CALL` Coordinator contacts the patient by the alternate route | coordinator | orchestrated | 7.2 (3.9 to 12.1) | sourced-adjacent | as M_PHONE_ATTEMPT: E11-1 follow-up call 7.2 minutes; E11-2 3.9 to 12.1 per call |
| `O_APPROVE_DRAFT` Approve a model-drafted message before it is sent | owner clinician | orchestrated | 1.5 (1 to 3) | assumed | assumed: 1.5 minutes. The simulator has no model drafts (every message is a pre-approved template), so one approval is charged on every human-contact message, the only non-template outreach |
| `O_SIGN_CLOSURE` Review the machine-prepared evidence and sign the closure | owner clinician | orchestrated | 5 (3 to 8) | assumed | assumed: 5 minutes (the clinician still reads the evidence the engine attached; 8 minutes = today's review plus filing) |
| `O_EXHAUSTED_REROUTE` Handle an exhausted reroute list (find another service by hand) | coordinator | orchestrated | 15 (8 to 30) | assumed | assumed: 15 minutes, about one external referral plus follow-up calls (E11-1 12.4 and 8.1) |
| `O_LOST_REVIEW` Review a case flagged lost (unreachable, declined, repeated no-show) and record the outcome | coordinator | orchestrated | 8 (4 to 15) | assumed | assumed: 8 minutes |
| `O_CONSENT_REVIEW` Review a consent withdrawal | owner clinician | orchestrated | 5 (3 to 10) | assumed | assumed: 5 minutes |
| `O_CONTACT_RECOVERED` Find and record a corrected contact for an unreachable patient | coordinator | orchestrated | 5 (3 to 10) | assumed | assumed: 5 minutes |
| `O_TRANSFER_DECISION` Receiving clinician decides on a transfer of the case after a provider switch | owner clinician | orchestrated | 5 (3 to 9.5) | assumed | assumed: 5 minutes; E11-1 'accepting/discussing referral' (9.5) is the upper bound |

Work that today's process never does (so it costs the baseline nothing, and the orchestrated arm does it by machine): appointment reminders before the colonoscopy; noticing a missed appointment and rebooking it; overdue tracking against working-day SLAs and the escalation ladder; noticing a rejected referral and rerouting it (only a share of rejections is noticed today); recording a consent withdrawal and stopping outreach; merging duplicate results and resolving conflicting ownership claims; surfacing a lost case to a human.

## Calibration check: does the baseline reproduce published completion?

The baseline arm is the unmanaged process. Its completion curve was tuned (parameters with status `calibrated`) to the Mohl et al. 2023 curve for positive stool tests in US health systems. The orchestrated arm is compared with the actively managed Kaiser Permanente curve (Corley et al. 2017), which is the nearest published ceiling. The baseline is the same in both scenarios.

| Follow-up | Published unmanaged (Mohl 2023) | Simulated baseline | Gap | Published managed (Corley 2017) | Simulated orchestrated, literature anchored | Simulated orchestrated, upper range |
|---|---|---|---|---|---|---|
| 90 days | 43.0% | 41.9% (41.5% to 42.3%) | -1.1pp | 74.2% | 57.5% (57.0% to 57.9%) | 73.1% (72.7% to 73.4%) |
| 180 days | 51.0% | 51.1% (50.6% to 51.6%) | +0.1pp | 80.6% | 64.9% (64.4% to 65.4%) | 78.3% (77.9% to 78.7%) |
| 360 days | 56.0% | 54.1% (53.5% to 54.6%) | -1.9pp | 83.2% | 66.4% (65.9% to 67.0%) | 78.5% (78.1% to 78.8%) |

The baseline lands within 1.9 percentage points of the published curve at every horizon shown.
Among baseline completers the simulated time to colonoscopy is median 61 days (IQR 46 to 85). The Sea Mar FQHC figure in the parameter file (83 days, IQR 52 to 131) describes a slower, lower-completion setting (22.9% at 90 days); Mohl's higher 90-day completion implies a faster completer median, so the simulator follows the Mohl completion curve and does not try to match both.

### Orchestrated-to-baseline completion ratio against the navigation risk ratio

| Follow-up | Literature anchored | Upper range | Pooled navigation RR (J Gen Intern Med 2026) |
|---|---|---|---|
| 90 days | 1.37 | 1.75 | 1.27 |
| 180 days | 1.27 | 1.53 | 1.27 |
| 360 days | 1.23 | 1.45 | 1.27 |

The upper-range ratio at 180 days is 1.53 against a pooled risk ratio of 1.27 for patient navigation in published trials. Orchestration here bundles ownership, timers, escalation and human outreach, and the ratio is larger than the navigation figure because the unmanaged baseline loses many cases to missed hand-offs that a managed programme prevents; it is the mechanism ceiling and is bounded above by the managed-programme ceiling. The literature-anchored scenario caps the effect at the navigation figure (ratio 1.27 at 180 days) by reaching only part of the case load (`orchestration.programme_reach`, calibrated), and is the scenario to quote.

## Fairness: completion and time to closure by language and channel (literature anchored)

### Language

| Language | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| ar | 51.0% (50.2% to 51.8%) | 70.9 (70.3 to 71.5) | 65.1% (64.3% to 65.8%) | 64.2 (63.7 to 64.6) |
| en | 59.0% (58.2% to 59.9%) | 69.1 (68.4 to 69.8) | 68.7% (68.0% to 69.4%) | 64.2 (63.7 to 64.6) |
| other | 52.2% (51.3% to 53.2%) | 71.8 (70.8 to 72.9) | 65.4% (64.4% to 66.5%) | 64.0 (63.5 to 64.6) |

Censoring-aware closure time by language (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Language | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| ar | 287.3 (254.4 to 322.2) | 226.1 (224.1 to 228.0) | 85.6 (84.5 to 86.9) | 176.3 (174.1 to 178.6) |
| en | 117.8 (113.6 to 121.9) | 200.5 (198.6 to 202.5) | 79.9 (78.8 to 80.8) | 164.5 (162.4 to 166.5) |
| other | 250.2 (226.0 to 275.8) | 222.5 (220.2 to 224.8) | 85.0 (82.9 to 87.0) | 175.2 (172.0 to 178.2) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.6% (7.5% to 9.5%) | 5.0% (4.1% to 5.8%) | -3.6pp (-4.5pp to -2.7pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.1 (3.2 to 4.9) | 2.0 (1.6 to 2.4) | -2.1 (-3.1 to -1.1) |
| Kaplan-Meier median days to closure, max gap between groups | 162.0 (138.6 to 183.1) | 9.0 (7.8 to 10.2) | -153.6 (-175.4 to -128.7) |
| Restricted mean days to closure, max gap between groups | 27.0 (24.7 to 29.1) | 15.7 (13.4 to 18.0) | -11.3 (-13.8 to -9.0) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| letter | 54.1% (52.7% to 55.4%) | 70.7 (69.3 to 72.0) | 65.6% (64.4% to 66.9%) | 67.6 (66.6 to 68.6) |
| phone | 53.7% (52.7% to 54.6%) | 70.9 (70.0 to 71.9) | 66.8% (65.8% to 67.9%) | 63.5 (62.6 to 64.5) |
| sms | 54.3% (53.6% to 54.9%) | 69.9 (69.3 to 70.6) | 66.5% (65.9% to 67.1%) | 63.8 (63.6 to 64.1) |
| whatsapp | 54.0% (53.0% to 54.9%) | 70.2 (69.5 to 70.9) | 66.5% (65.6% to 67.3%) | 63.3 (62.8 to 63.8) |

Censoring-aware closure time by preferred contact channel (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Preferred contact channel | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| letter | 185.5 (157.5 to 218.6) | 216.0 (212.6 to 219.9) | 86.6 (85.0 to 88.2) | 176.1 (172.8 to 179.6) |
| phone | 209.9 (183.8 to 238.3) | 217.5 (215.0 to 220.1) | 81.2 (79.8 to 82.8) | 169.6 (166.5 to 172.9) |
| sms | 201.0 (179.6 to 226.2) | 215.7 (213.6 to 218.0) | 83.1 (81.9 to 84.4) | 171.6 (169.5 to 173.5) |
| whatsapp | 196.0 (179.7 to 213.8) | 216.8 (214.8 to 219.0) | 82.8 (81.6 to 84.0) | 171.5 (169.6 to 173.4) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.0% (4.2% to 5.7%) | 4.5% (3.7% to 5.7%) | -0.5pp (-1.7pp to +0.8pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.5 (3.7 to 5.2) | 5.4 (4.4 to 6.3) | +0.9 (-0.5 to +2.1) |
| Kaplan-Meier median days to closure, max gap between groups | 85.4 (64.9 to 109.2) | 8.4 (7.3 to 9.6) | -77.0 (-99.2 to -54.2) |
| Restricted mean days to closure, max gap between groups | 13.1 (10.7 to 15.4) | 12.9 (10.0 to 15.8) | -0.1 (-3.5 to +3.9) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Fairness: completion and time to closure by language and channel (upper range)

### Language

| Language | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| ar | 51.0% (50.2% to 51.8%) | 70.9 (70.3 to 71.5) | 78.2% (77.8% to 78.7%) | 60.7 (60.3 to 61.1) |
| en | 59.0% (58.2% to 59.9%) | 69.1 (68.4 to 69.8) | 78.7% (78.0% to 79.5%) | 60.7 (60.2 to 61.2) |
| other | 52.2% (51.3% to 53.2%) | 71.8 (70.8 to 72.9) | 78.7% (77.9% to 79.6%) | 60.0 (59.4 to 60.6) |

Censoring-aware closure time by language (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Language | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| ar | 287.3 (254.4 to 322.2) | 226.1 (224.1 to 228.0) | 67.5 (67.0 to 67.9) | 129.2 (127.9 to 130.5) |
| en | 117.8 (113.6 to 121.9) | 200.5 (198.6 to 202.5) | 67.3 (66.5 to 68.0) | 127.4 (125.6 to 129.8) |
| other | 250.2 (226.0 to 275.8) | 222.5 (220.2 to 224.8) | 66.6 (66.0 to 67.2) | 127.3 (124.6 to 129.8) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.6% (7.5% to 9.5%) | 3.0% (2.4% to 3.6%) | -5.5pp (-6.8pp to -4.3pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.1 (3.2 to 4.9) | 2.2 (1.8 to 2.6) | -1.9 (-2.9 to -0.8) |
| Kaplan-Meier median days to closure, max gap between groups | 162.0 (138.6 to 183.1) | 2.7 (2.0 to 3.4) | -159.4 (-180.1 to -134.6) |
| Restricted mean days to closure, max gap between groups | 27.0 (24.7 to 29.1) | 8.9 (7.2 to 10.7) | -18.1 (-21.0 to -14.8) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| letter | 54.1% (52.7% to 55.4%) | 70.7 (69.3 to 72.0) | 77.2% (76.4% to 78.1%) | 64.8 (64.1 to 65.5) |
| phone | 53.7% (52.7% to 54.6%) | 70.9 (70.0 to 71.9) | 79.4% (78.4% to 80.5%) | 58.8 (58.2 to 59.3) |
| sms | 54.3% (53.6% to 54.9%) | 69.9 (69.3 to 70.6) | 78.5% (77.9% to 79.0%) | 60.4 (59.9 to 60.9) |
| whatsapp | 54.0% (53.0% to 54.9%) | 70.2 (69.5 to 70.9) | 78.7% (78.0% to 79.4%) | 59.1 (58.8 to 59.5) |

Censoring-aware closure time by preferred contact channel (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Preferred contact channel | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| letter | 185.5 (157.5 to 218.6) | 216.0 (212.6 to 219.9) | 72.0 (71.4 to 72.8) | 135.0 (132.7 to 137.5) |
| phone | 209.9 (183.8 to 238.3) | 217.5 (215.0 to 220.1) | 65.1 (64.3 to 65.9) | 124.1 (120.5 to 127.4) |
| sms | 201.0 (179.6 to 226.2) | 215.7 (213.6 to 218.0) | 67.1 (66.5 to 67.8) | 128.2 (126.4 to 129.9) |
| whatsapp | 196.0 (179.7 to 213.8) | 216.8 (214.8 to 219.0) | 66.0 (65.6 to 66.3) | 127.0 (124.9 to 129.0) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.0% (4.2% to 5.7%) | 4.4% (3.4% to 5.5%) | -0.6pp (-1.8pp to +0.6pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.5 (3.7 to 5.2) | 6.5 (5.6 to 7.4) | +2.0 (+0.9 to +3.2) |
| Kaplan-Meier median days to closure, max gap between groups | 85.4 (64.9 to 109.2) | 7.4 (6.5 to 8.4) | -77.9 (-100.5 to -54.6) |
| Restricted mean days to closure, max gap between groups | 13.1 (10.7 to 15.4) | 15.1 (11.6 to 18.4) | +2.0 (-1.8 to +6.4) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Messy-case handling (literature anchored)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 44.5% (43.2% to 45.9%) | 21.0% (19.7% to 22.3%) | 42.9% (41.5% to 44.5%) | 68.6% (68.0% to 69.0%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 48.7% (46.5% to 50.8%) | 13.9% (12.3% to 15.6%) | 37.5% (35.2% to 39.7%) | 67.3% (66.8% to 67.8%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 53.8% (51.5% to 56.1%) | 53.2% (51.2% to 55.4%) | 65.3% (63.8% to 66.8%) | 66.5% (66.0% to 67.1%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 55.4% (53.8% to 57.2%) | 56.2% (54.0% to 58.5%) | 68.0% (66.2% to 69.9%) | 66.4% (65.8% to 66.9%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 63.7% (58.2% to 68.6%) | 64.9% (61.6% to 68.8%) | 61.7% (57.8% to 65.2%) | 66.5% (66.0% to 67.1%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute. In the literature-anchored scenario, cases outside the programme's reach are handled as today, so the orchestrated handled rates are below 100%.

## Messy-case handling (upper range)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 91.0% (89.9% to 92.1%) | 21.0% (19.7% to 22.3%) | 72.3% (70.6% to 74.0%) | 79.1% (78.7% to 79.4%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 13.9% (12.3% to 15.6%) | 62.7% (59.7% to 65.8%) | 78.9% (78.5% to 79.3%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 53.2% (51.2% to 55.4%) | 77.1% (75.9% to 78.4%) | 78.6% (78.2% to 79.0%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 56.2% (54.0% to 58.5%) | 79.8% (78.2% to 81.4%) | 78.4% (78.1% to 78.8%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 64.9% (61.6% to 68.8%) | 60.6% (56.7% to 64.7%) | 78.8% (78.4% to 79.2%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute.

## Parameters

Parameter files: `simulator/params.yaml`. Status counts: 87 assumed, 16 calibrated, 8 empirical, 13 sourced, 14 sourced-adjacent. `sourced` and `sourced-adjacent` values are from public documents (details and caveats in `docs/evidence.md`); `calibrated` values were tuned to the published baseline curve; `assumed` values have no public source. This table is the upper-range scenario; the literature-anchored scenario changes only the keys listed below it.

| Parameter | Value | Status | Source |
|---|---|---|---|
| `acknowledgement_delay_days` | 2 | assumed | No public measurement of staff time-to-acknowledge a positive result. Policy anchors only: UAE National Guideline cl. 12.3 (result to participant < 15 working days), DoH IFHAS V10 s.4.9.2.1 (results documented within 10 days of initial visit). |
| `appointment.booking_lead_median_days` | 24 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 14, no public source |
| `appointment.booking_lead_sigma` | 0.6 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.5, no public source |
| `appointment.max_attempts` | 3 | assumed | assumed, no public source |
| `appointment.rebook_lead_median_days` | 7 | assumed | assumed, no public source |
| `appointment.reminder_days_before` | [7, 1] | assumed | assumed, no public source |
| `appointment.responsiveness_noshow_shift` | 1.12 | empirical | Fitted (B18): between-patient SD (tau) of the logit of no-show, random-intercept logistic regression on SMS_received, ln(lead days) and age, 11,734 patients with at least two appointments booked 3+ days ahead (31,322 appointments), Vitoria (Brazil) 2016, OpenML 43439, CC BY-NC-SA 4.0. https://www.openml.org/search?type=data&id=43439 |
| `baseline.channel_share.letter` | 0.5 | assumed | assumed, no public source |
| `baseline.channel_share.sms` | 0.5 | assumed | assumed, no public source |
| `baseline.default_language` | en | assumed | assumed, no public source |
| `baseline.noshow_self_rebook_median_days` | 30 | assumed | assumed, no public source |
| `baseline.noshow_self_rebook_prob` | 0.45 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.30, no public source |
| `calendar.holidays` | [] | assumed | assumed, no public source |
| `colonoscopy_completion_12m` | 0.56 | sourced | Mohl et al. 2023, 56.1% at 360 d (median organisation 53.4%, range 17.4-71.8%). https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_completion_12m_integrated_ref` | 0.832 | sourced | Corley et al. 2017, 83.2% within 12 months. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `colonoscopy_completion_3m` | 0.43 | sourced | Mohl et al., JAMA Netw Open 2023;6(1):e2251384, 43.3% at 90 d, 32,769 stool-test positives, 39 US organisations. https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_completion_3m_integrated_ref` | 0.742 | sourced | Corley et al., JAMA 2017;317:1631, 74.2% within 3 months of 81,518 FIT positives, Kaiser Permanente. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `colonoscopy_completion_6m` | 0.51 | sourced | Mohl et al. 2023, 51.4% at 180 d. https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_completion_6m_integrated_ref` | 0.806 | sourced | Corley et al. 2017, 80.6% within 6 months. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `colonoscopy_never_completes_12m` | 0.44 | sourced-adjacent | Derived: 1 - 0.561 from Mohl et al. 2023. https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_uptake_after_positive_england` | 0.76 | empirical | Fitted (B18), NHS England Bowel cancer screening annual report 2024-25, Table 12 (BCSP-S11), OGL v3: 69,956 diagnostic-test attendances of 92,107 definitive FOBt+ patients = 76.0% (95% CI 75.7 to 76.2). https://assets.publishing.service.gov.uk/media/6a4e6212f79c9db2eb438f07/Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx |
| `colonoscopy_uptake_after_positive_scotland` | 0.75 | empirical | Fitted (B18), Public Health Scotland KPI report appendix A4, OGL v3: 26,358 colonoscopies performed of 35,133 positive tests (invites 2022-05 to 2024-04). https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx |
| `delay_harm_threshold_days` | 270 | sourced-adjacent | Systematic review, Clin Gastroenterol Hepatol 2021 (PMID 33010414): colonoscopy 'should not be delayed beyond 9 months' after positive FIT. https://doi.org/10.1016/j.cgh.2020.09.048 . Corley JAMA 2017: first significant excess at 10-12 months, any CRC OR 1.48 (1.05-2.08), advanced stage OR 1.97 (1.14-3.42); >12 months OR 2.25 and 3.22. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `dna_rate` | 0.119 | sourced-adjacent | Lam et al. 2021 standard-care arm, colonoscopy non-attendance 11.9%. https://doi.org/10.1111/jgh.15218 |
| `eligible_age_max` | 75 | sourced | UAE National Guideline s.3.4 and DoH IFHAS V10 Appendix (40-75). Ages 76-85 decided individually. https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf |
| `eligible_age_min` | 40 | sourced | UAE National Guideline s.3.4 (ages 40-75); DoH IFHAS V10 Appendix (FIT, 40-75 years, annually); ADPHC 24 Sep 2019 (40 and older). https://www.doh.gov.ae/-/media/EE758C3B77314BC394F2479AEF803AA2.ashx |
| `fit_interval_months` | 12 | sourced | UAE National Guideline Standard 2 (FIT every year); DoH IFHAS V10; ADPHC 2019. The earlier 2-year interval (HAAD 2016) was superseded in 2019. |
| `fit_positivity_rate` | 0.07 | sourced-adjacent | Ding et al., Cancers 2022;14(4):1073, pooled positivity 7.28% across 93 studies, https://pubmed.ncbi.nlm.nih.gov/35205821/ ; AltaMed FQHC 7.0%, https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ |
| `fit_positivity_rate_england_120ug` | 0.0171 | empirical | Fitted (B18), NHS England Bowel cancer screening annual report 2024-25, Table 5 (BCSP-S02), OGL v3: 89,030 further tests needed of 5,195,407 adequately screened = 1.71% (95% CI 1.70 to 1.72). https://assets.publishing.service.gov.uk/media/6a4e6212f79c9db2eb438f07/Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx |
| `followup_target_working_days` | 31 | sourced | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed. 2022, Appendix 2: colonoscopy within 31 working days of referral after positive FIT (>90% acceptable, >95% desirable). https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf |
| `gi_referral_made_rate` | 0.9 | sourced-adjacent | Sea Mar FQHC: 642 of 711 abnormal FIT (90%) had a GI referral. https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
| `gi_referral_non_attendance_abu_dhabi` | 0.25 | sourced-adjacent | Derived: 36.3% of the ~992 positives without colonoscopy were 'referred to GI, did not attend' (about 360 of 1,429 positives), Almansoori et al., BMC Cancer 2021;21:897. https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ |
| `handoff.baseline_rejection_notice_median_days` | 21 | assumed | assumed, no public source |
| `handoff.baseline_rejection_noticed_prob` | 0.55 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.40, no public source |
| `handoff.declined_reengage_prob` | 0.5 | assumed | assumed, no public source |
| `handoff.max_attempts` | 4 | assumed | assumed, no public source |
| `handoff.no_response_prob` | 0.04 | assumed | assumed, no public source |
| `handoff.rejection_reason_share.capacity` | 0.35 | assumed | assumed, no public source |
| `handoff.rejection_reason_share.missing_info` | 0.3 | assumed | assumed, no public source |
| `handoff.rejection_reason_share.patient_declined` | 0.2 | assumed | assumed, no public source |
| `handoff.rejection_reason_share.wrong_service` | 0.15 | assumed | assumed, no public source |
| `handoff.reroute_delay_median_days` | 2.0 | assumed | assumed, no public source |
| `handoff.response_delay_median_days` | 2.0 | assumed | assumed, no public source |
| `handoff.response_delay_sigma` | 0.7 | assumed | assumed, no public source |
| `handoff.response_sla_working_days` | 3 | assumed | assumed, no public source |
| `malaffi_integration_assumption` | HL7 v2 message feeds; FHIR R4 not confirmed | sourced | DoH Circular 2026/10, 19 Jan 2026: mandatory Malaffi minimum dataset listed as HL7 message types (ADT, PPR, ORU, OMP, RDS, RAS, VXU, SIU incl. future appointments). https://www.doh.gov.ae/-/media/84A213F71AA545D69E01A23A0A99C598.ashx |
| `messy.baseline_conflict_delay_days` | 10 | assumed | assumed, no public source |
| `messy.baseline_switch_delay_days` | 30 | assumed | assumed, no public source |
| `messy.baseline_switch_loss_prob` | 0.7 | assumed | assumed, no public source |
| `messy.conflicting_ack_rate` | 0.04 | assumed | assumed, no public source |
| `messy.consent_after_booking_rate` | 0.02 | assumed | assumed, no public source |
| `messy.duplicate_delay_max_days` | 10 | assumed | assumed, no public source |
| `messy.duplicate_result_rate` | 0.06 | assumed | assumed, no public source |
| `messy.rate_multiplier` | 1.0 | assumed | assumed, no public source |
| `messy.switch_window_days` | 60 | assumed | assumed, no public source |
| `metrics.escalation_precision_window_working_days` | 5 | assumed | assumed, no public source |
| `metrics.ltfu_idle_days` | 30 | assumed | assumed, no public source |
| `metrics.min_group_size` | 30 | assumed | assumed, no public source |
| `navigation_effect` | 1.27 | sourced | Pooled risk ratio for colonoscopy completion, patient navigation vs usual care, 8 navigation studies, moderate certainty, J Gen Intern Med 2026 (PMID 42618855), RR 1.27 (95% CI 1.10-1.46). https://doi.org/10.1007/s11606-026-10706-x |
| `navigation_time_reduction_days` | 15 | sourced-adjacent | Coronado et al., Cancer Epidemiol Biomarkers Prev 2021 (RCT, Kaiser NW): median time to colonoscopy 73 d navigation vs 88 d usual care. https://pmc.ncbi.nlm.nih.gov/articles/PMC9273475/ |
| `no_show_rate_by_channel.letter` | 0.119 | sourced-adjacent | Lam et al. 2021, standard-care arm (written appointment slip only): 11.9%. https://doi.org/10.1111/jgh.15218 |
| `no_show_rate_by_channel.phone_call` | 0.089 | sourced-adjacent | Set equal to SMS: Cochrane CD007458 (Gurol-Urganci 2013), text vs phone call RR attendance 0.99 (0.95-1.02). https://www.cochrane.org/evidence/CD007458_mobile-phone-messaging-reminders-attendance-healthcare-appointments |
| `no_show_rate_by_channel.sms` | 0.089 | sourced-adjacent | Lam et al., J Gastroenterol Hepatol 2021 (RCT, 2,225 booked colonoscopy patients, Hong Kong): 8.9% with SMS 7-10 d before. https://doi.org/10.1111/jgh.15218 |
| `orchestration.consent_review_delay_median_days` | 2.0 | assumed | assumed, no public source |
| `orchestration.contact_recovery_delay_median_days` | 2.0 | assumed | assumed, no public source |
| `orchestration.contact_recovery_prob.on_failure` | 0.4 | assumed | assumed, no public source |
| `orchestration.contact_recovery_prob.owner` | 0.25 | assumed | assumed, no public source |
| `orchestration.contact_recovery_prob.owner_supervisor` | 0.5 | assumed | assumed, no public source |
| `orchestration.contact_recovery_prob.programme_coordinator` | 0.7 | assumed | assumed, no public source |
| `orchestration.delivery_failure_detect_days.letter` | 7.0 | assumed | assumed, no public source |
| `orchestration.delivery_failure_detect_days.phone` | 0.1 | assumed | assumed, no public source |
| `orchestration.delivery_failure_detect_days.sms` | 0.25 | assumed | assumed, no public source |
| `orchestration.delivery_failure_detect_days.whatsapp` | 0.25 | assumed | assumed, no public source |
| `orchestration.escalation_effect_prob.owner` | 0.5 | assumed | assumed, no public source |
| `orchestration.escalation_effect_prob.owner_supervisor` | 0.85 | assumed | assumed, no public source |
| `orchestration.escalation_effect_prob.programme_coordinator` | 1.0 | assumed | assumed, no public source |
| `orchestration.ladder_exhausted_wait_working_days` | 5 | assumed | assumed, no public source |
| `orchestration.programme_reach` | 1.0 | assumed | Design choice, not evidence: the upper-range scenario assumes every case in the orchestrated arm receives the orchestrated process. simulator/params.conservative.yaml lowers it (literature-anchored scenario) |
| `orchestration.reaction_delay_median_days` | 1.0 | assumed | assumed, no public source |
| `orchestration.reaction_delay_sigma` | 0.6 | assumed | assumed, no public source |
| `orchestration.signoff_sla_working_days` | 3 | assumed | assumed, no public source |
| `orchestration.timer_miss_prob` | 0.02 | assumed | assumed, no public source |
| `orchestration.transfer_reestablish_days` | 5.0 | assumed | assumed, no public source |
| `orchestration.watchdog_effect_prob` | 0.9 | assumed | assumed, no public source |
| `orchestration.watchdog_idle_days` | 14 | assumed | assumed, no public source |
| `orchestration.watchdog_reaction_delay_days` | 2.0 | assumed | assumed, no public source |
| `patient_response.coordinator_route_response_rate` | 0.7 | assumed | assumed, no public source |
| `patient_response.dropout_residual_multiplier` | 0.25 | assumed | assumed, no public source |
| `patient_response.human_dropout_residual_multiplier` | 0.6 | assumed | assumed, no public source |
| `patient_response.language_mismatch_multiplier` | 0.8 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.6, no public source |
| `patient_response.response_delay_median_days.letter` | 6.0 | assumed | assumed, no public source |
| `patient_response.response_delay_median_days.phone` | 0.5 | assumed | assumed, no public source |
| `patient_response.response_delay_median_days.sms` | 1.5 | assumed | assumed, no public source |
| `patient_response.response_delay_median_days.whatsapp` | 1.0 | assumed | assumed, no public source |
| `patient_response.response_delay_sigma` | 0.9 | assumed | assumed, no public source |
| `patient_response.response_rate_by_channel.letter` | 0.85 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.55, no public source |
| `patient_response.response_rate_by_channel.phone` | 0.95 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.80, no public source |
| `patient_response.response_rate_by_channel.sms` | 0.92 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.70, no public source |
| `patient_response.response_rate_by_channel.whatsapp` | 0.93 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.75, no public source |
| `patient_response.self_presentation_median_days` | 120 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 60, no public source |
| `patient_response.self_presentation_prob` | 0.38 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.10, no public source |
| `patient_response.staff_call_response_rate` | 0.65 | assumed | assumed, no public source |
| `patients.channel_share.letter` | 0.15 | assumed | assumed, no public source |
| `patients.channel_share.phone` | 0.15 | assumed | assumed, no public source |
| `patients.channel_share.sms` | 0.4 | assumed | assumed, no public source |
| `patients.channel_share.whatsapp` | 0.3 | assumed | assumed, no public source |
| `patients.consent_withdrawal_declines_care_prob` | 0.3 | assumed | assumed, no public source |
| `patients.consent_withdrawal_prob` | 0.02 | assumed | assumed, no public source |
| `patients.consent_withdrawal_window_days` | 90 | assumed | assumed, no public source |
| `patients.dropout_prob_per_lapse` | 0.15 | assumed | assumed, no public source |
| `patients.language_share.ar` | 0.45 | assumed | assumed, no public source |
| `patients.language_share.en` | 0.35 | assumed | assumed, no public source |
| `patients.language_share.other` | 0.2 | assumed | assumed, no public source |
| `patients.provider_switch_prob` | 0.05 | assumed | assumed, no public source |
| `patients.refusal_prob` | 0.12 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.12, no public source |
| `patients.responsiveness_sd` | 0.8 | assumed | assumed, no public source |
| `referral_rejection_rate` | 0.05 | sourced-adjacent | Derived 4.6% (15 of 324 abnormal FIT had a referral problem, e.g. insurance authorisation), AltaMed FQHC, https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ . Range 1.2% (incomplete forms, Champlain GI central intake, https://pmc.ncbi.nlm.nih.gov/articles/PMC10872000/) to 8.48% (Saudi SMARC e-referral, https://pmc.ncbi.nlm.nih.gov/articles/PMC12647054/). |
| `reopen.after_closure_prob` | 0.03 | assumed | assumed, no public source |
| `reopen.lag_median_days` | 30 | assumed | assumed, no public source |
| `reopen.lag_sigma` | 0.6 | assumed | assumed, no public source |
| `reopen.resume_state` | owner_accepted | assumed | assumed, no public source |
| `simulation.intake_window_days` | 30 | assumed | assumed, no public source |
| `simulation.start_date` | 2026-01-05 | assumed | assumed, no public source |
| `sms_reminder_effect` | 0.167 | empirical | Fitted (B18) from the Vitoria (Brazil, 2016) Medical Appointment No Shows data, OpenML 43439, CC BY-NC-SA 4.0: logistic regression of no-show on SMS_received, ln(lead days) and age, appointments booked at least 3 days ahead (60,017 of 110,527); standardised relative reduction in no-show probability 0.167 (95% CI 0.146 to 0.187), odds ratio 0.77. https://www.openml.org/search?type=data&id=43439 |
| `staff.delay_median_days.accept_ownership` | 2 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 1.0, no public source |
| `staff.delay_median_days.book_confirmatory` | 6 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 4.0, no public source |
| `staff.delay_median_days.clinician_review` | 4.0 | assumed | assumed, no public source |
| `staff.delay_median_days.close` | 2.0 | assumed | assumed, no public source |
| `staff.delay_median_days.contact_patient` | 5 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 3.0, no public source |
| `staff.delay_sigma` | 0.8 | assumed | assumed, no public source |
| `staff.forget_prob` | 0.015 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.04, no public source |
| `time_to_colonoscopy_iqr_days` | [52, 131] | sourced-adjacent | Sea Mar FQHC, IQR 52-131 d (same source as the median). https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
| `time_to_colonoscopy_lognormal_sigma` | 0.63 | empirical | Fitted (B18), Public Health Scotland KPI report, OGL v3: log-scale SD of time from referral to colonoscopy, from the 4- and 8-week shares (0.63; the 31- and 62-day shares of appendix A6 give 0.64; the Sea Mar IQR 52-131 gives 0.69). https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx |
| `time_to_colonoscopy_managed_ref_iqr_days` | [26.0, 61.1] | empirical | Fitted (B18), same lognormal as time_to_colonoscopy_managed_ref_median_days (Public Health Scotland KPI report, OGL v3): 25th and 75th percentiles. https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx |
| `time_to_colonoscopy_managed_ref_median_days` | 39.9 | empirical | Fitted (B18) from Public Health Scotland, Scottish Bowel Screening Programme KPI report (2025-03-04; invites 2022-05 to 2024-04), OGL v3: lognormal through the 4-week and 8-week shares of KPI 4 (28.8% within 4 weeks, 29.6% over 8 weeks), checked against the reported median of 40 days. https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx |
| `time_to_colonoscopy_median_days` | 83 | sourced-adjacent | Sea Mar FQHC, completers, median 83 d. BMC Gastroenterol 2021, https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
| `working_week` | {working_days: [Mon, Tue, Wed, Thu, Fri], friday_half_day: True, friday_end: 12:00, weekend: [Sat, Sun]} | sourced | u.ae, Working hours in the public sector: federal government Mon-Thu 07:30-15:30, Fri until 12:00, Sat-Sun weekend since 1 Jan 2022; Abu Dhabi government adopted a similar system. https://u.ae/en/information-and-services/jobs/Sector-of-employment/working-in-uae-government-sector/working-hours-in-the-public-sector |
| `wrong_contact_rate` | 0.08 | sourced-adjacent | Derived 8.3% (59 of 711 abnormal FIT: provider unable to reach patient), Sea Mar FQHC, BMC Gastroenterol 2021, https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |

### Overrides in the literature-anchored scenario

Overlay files: `simulator/params.conservative.yaml`. Every other parameter, including everything the baseline arm reads, is inherited unchanged (evaluate refuses to write results if any replication's baseline event log differs between the scenarios).

| Parameter | Upper range | Literature anchored | Status | Source |
|---|---|---|---|---|
| `orchestration.contact_recovery_prob.owner_supervisor` | 0.5 | 0.25 | assumed | Capped at orchestration.contact_recovery_prob.owner (0.25): a supervisor's call recovers no more wrong contacts than the owner's re-contact. |
| `orchestration.contact_recovery_prob.programme_coordinator` | 0.7 | 0.25 | assumed | Capped at orchestration.contact_recovery_prob.owner (0.25): the coordinator's alternate route recovers no more wrong contacts than the owner's re-contact. |
| `orchestration.escalation_effect_prob.owner_supervisor` | 0.85 | 0.5 | assumed | Capped at orchestration.escalation_effect_prob.owner (0.50): no published evidence that escalating to a supervisor adds anything beyond re-contacting the owner (docs/evidence.md, Gaps). |
| `orchestration.escalation_effect_prob.programme_coordinator` | 1.0 | 0.5 | assumed | Capped at orchestration.escalation_effect_prob.owner (0.50): no published evidence that escalating to a coordinator adds anything beyond re-contacting the owner (docs/evidence.md, Gaps). |
| `orchestration.programme_reach` | 1.0 | 0.55 | calibrated | Calibrated so the orchestrated/baseline completion ratio at 180 days equals navigation_effect = 1.27 (pooled RR 1.27, 95% CI 1.10-1.46, 8 navigation studies, moderate certainty, J Gen Intern Med 2026, PMID 42618855, https://doi.org/10.1007/s11606-026-10706-x; 12-month RCT: 55.1% vs 42.1%, Annals 2025, https://pmc.ncbi.nlm.nih.gov/articles/PMC13007779/). Fit of 2026-10-18 (refit after A18 milestones and A19 coalescing): 0.55 gives a literature-anchored 180-day completion ratio of 1.272 (66.3% against 52.1%; 0.54 gives 1.268, 0.56 gives 1.276). Procedure and fitted ratios: docs/results/results.md. |
| `patient_response.human_dropout_residual_multiplier` | 0.6 | 0.25 | assumed | Capped at patient_response.dropout_residual_multiplier (0.25): a human call does not re-engage a disengaged patient better than a digital re-contact. |
| `sms_reminder_effect` | 0.167 | 0.0 | sourced-adjacent | Lower bound of the sensitivity range in docs/evidence.md section 4: Mahmud et al., JAMA Netw Open 2021 (753 patients, nine text messages, no effect on attendance with good prep, 53.1% vs 54.4%, P=.73). https://doi.org/10.1001/jamanetworkopen.2020.34553 |
