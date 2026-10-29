# Evaluation results: baseline vs orchestrated

generated_at: 2026-10-29 18:49:15 UTC (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)

All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.

- Seed 20261017, 20 replications x 2,000 cases x 360 days of follow-up, 1000 bootstrap resamples
- Parameters hash `dc2e0fc8c3a1d414` (upper range), `e44bff57f22e5043` (literature anchored), pathway `colorectal_fit` v0.3 hash `db06101ee042a55f`, git commit `d83c94b759ab`
- Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).

## Headline: baseline vs the two orchestrated scenarios

There are two orchestrated scenarios and one baseline. **Quote the literature-anchored scenario.** It holds the orchestrated arm to what published studies report: the completion effect is held near the pooled patient-navigation risk ratio of 1.27 (95% CI 1.10 to 1.46), SMS reminders add nothing beyond the no-reminder attendance rate (the lower bound of the published range), and escalating to a supervisor or coordinator is no more effective than re-contacting the patient. In it, 67% of patients reach a confirmatory colonoscopy within 180 days against 51% at baseline (ratio 1.31). **The ratio is calibrated to published navigation trials, not a validated result**: `orchestration.programme_reach` was fitted in B12 so the ratio matches the pooled navigation risk ratio. The model shows the mechanism and what drives it, not new evidence of effect; only the Azulay comparison in validation.md is independent of that fit. To hold the average effect to the published figure the scenario lets the orchestrated process reach only 55% of cases. Silent loss (lost without a human being told) is 45.3% -> 20.6% at programme level; 0% among the cases the programme reaches, which is true by construction (every reached case has an owner and a timer). The reach is a device for calibrating the average effect, not a prediction of how many cases a real programme would miss. **The upper-range scenario shows the mechanism ceiling**: what ownership, timers, escalation and re-contact could deliver if every case got the full orchestrated process and every escalation worked as assumed. It reaches 83% within 180 days (ratio 1.62), far above anything published for navigation, so read it as an upper bound on the mechanism and not as a forecast. Both scenarios use the same synthetic patients, the same random numbers and the same baseline arm; the baseline was calibrated to published unmanaged completion, and the only orchestrated-arm fit is the reach parameter, set in B12 so the literature-anchored 180-day ratio equalled the navigation risk ratio. It was deliberately not refitted after the B21 timing mixture (the pooled estimate probably includes two of the trials the validation scores the ratio against, so refitting would be circular), which is why the ratio is now 1.31 rather than 1.27; it stays inside the pooled interval (1.10 to 1.46). See validation.md.

| Metric | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.8% (42.4% to 43.1%) | 60.5% (60.1% to 60.8%) | 78.2% (77.9% to 78.6%) |
| Colonoscopy completed within 180 days | 51.0% (50.5% to 51.5%) | 66.8% (66.3% to 67.3%) | 82.7% (82.3% to 83.1%) |
| Colonoscopy completed within 360 days | 54.5% (53.9% to 55.0%) | 68.7% (68.1% to 69.2%) | 83.0% (82.6% to 83.4%) |
| Completion ratio vs baseline at 180 days (navigation RR for comparison: 1.27) | 1.00 | 1.31 | 1.62 |
| Cases still open at 90 days | 60.6% (60.2% to 61.0%) | 42.3% (41.9% to 42.7%) | 23.9% (23.6% to 24.3%) |
| Cases closed (evidence and human sign-off) by end of follow-up | 52.8% (52.2% to 53.3%) | 67.8% (67.3% to 68.4%) | 83.0% (82.6% to 83.4%) |
| Time to safe closure, median days (among closed cases only) | 57.5 (57.0 to 57.9) | 51.6 (51.2 to 52.0) | 48.9 (48.7 to 49.1) |
| Time to safe closure, Kaplan-Meier median days (censoring-aware) | 208.3 (194.7 to 223.2) | 70.6 (70.0 to 71.2) | 54.6 (54.1 to 55.0) |
| Time to safe closure, restricted mean days (censoring-aware) | 210.9 (209.5 to 212.4) | 158.8 (157.4 to 160.1) | 106.4 (105.2 to 107.5) |
| Time to first patient contact, median days | 3.0 (3.0 to 3.0) | 2.7 (2.7 to 2.8) | 2.5 (2.5 to 2.5) |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 45.3% (44.7% to 45.9%) | 31.2% (30.7% to 31.7%) | 17.0% (16.6% to 17.3%) |
| Lost AND never surfaced to a human: programme level (all cases) | 45.3% (44.7% to 45.8%) | 20.6% (20.2% to 21.0%) | 0.0% (0.0% to 0.0%) |
| Lost AND never surfaced to a human: in reach (cases the programme reached) | 45.3% (44.7% to 45.8%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| No colonoscopy by the delay-harm threshold (270 days) | 46.5% (46.0% to 47.1%) | 31.9% (31.4% to 32.5%) | 17.1% (16.7% to 17.4%) |
| Outreach after consent withdrawn: programme level (all cases) | 0.1% (0.1% to 0.1%) | 0.1% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) |
| Outreach after consent withdrawn: in reach (cases the programme reached) | 0.1% (0.1% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Escalations per 100 cases (cost nothing in the model, see capacity caveat) | 0.00 (0.00 to 0.00) | 84.44 (83.09 to 85.85) | 142.06 (140.23 to 143.86) |
| Escalation acted-on rate (within 5 working days) | n/a | 30.5% (29.8% to 31.2%) | 35.0% (34.6% to 35.4%) |

**Lost and never surfaced, and outreach after consent withdrawn: two denominators each.** The programme-level rows count every case. The in-reach rows count only the cases the programme reached (`orchestration.programme_reach`; in the baseline column, the same patients under today's process, so the two columns compare like with like). In the orchestrated arm both in-reach rows are 0% by construction: a lost or unreachable case in reach is flagged to a human and the consent ledger blocks outreach after a withdrawal. The programme-level figures follow from the `programme_reach` assumption (0.55 in the literature-anchored scenario): cases outside the reach are handled as today and can still be lost silently or contacted after a withdrawal, so those figures are not a measured property of the orchestration.

Metric definitions (numerator, denominator, clocks, censoring) are in [`metrics.md`](metrics.md).

## Which timing figures may be quoted (held-out validation, B21)

The completion ratio at 180 days is the headline figure, calibrated to published navigation trials (not new evidence of effect). Timing figures in this file (days to colonoscopy or to closure, shares by 30 or 60 days, days saved) may be quoted only where the held-out validation in [`validation.md`](validation.md) passes; the others stay withdrawn. Generated from `validation.json` by the same rule as the table in validation.md.

| Timing claim | Simulated vs observed (held-out validation) | Status |
|---|---|---|
| Share with a colonoscopy within 30 days of the positive result, unmanaged setting (baseline arm) | 11.7% vs 14.7% | **passes: may be quoted, with the validation row** |
| Share with a colonoscopy within 30 days, actively managed pathway (orchestrated arm, literature-anchored) | 17.4% vs 33.3% | **fails: withdrawn** |
| Share with a colonoscopy within 60 days, actively managed pathway (orchestrated arm, literature-anchored) | 49.8% vs 63.6% | **fails: withdrawn** |
| Median days to colonoscopy among completers, actively managed pathway (orchestrated arm, literature-anchored) | 43 d vs 37 d | **passes: may be quoted, with the validation row** |
| Quartiles of days to colonoscopy among completers, actively managed pathway (orchestrated arm, literature-anchored) | 30 d vs 23 d; 63 d vs 62 d | **fails: withdrawn** |
| Median and quartiles of days to colonoscopy among completers, US usual care (baseline arm) | 48 d vs 184 d; 32 d vs 140 d; 81 d vs 232 d | **fails: withdrawn** |
| Share completing by 90 days, US FQHC usual care (baseline arm) | 42.8% vs 22.9% | **fails: withdrawn** |
| Share of completers within 4 and 8 weeks in a managed national programme (baseline, programme-managed) | 22.4% vs 28.8%; 66.9% vs 70.4% | **excluded: in-sample calibration rows, so it cannot pass (not validated)** |
| Days saved: orchestrated minus baseline median time to colonoscopy | 43 d vs 37 d; 48 d vs 184 d | **fails: withdrawn** |

## Capacity caveat: escalations cost nothing in this model

**Every gain in the headline and the comparison tables is a gain before capacity constraints.** In that model cases are independent: there are no shared endoscopy slots, no staff workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however many others are open. The orchestrated arm raises about 142 escalations per 100 cases in the upper-range scenario (84 in the literature-anchored one), roughly one for every case. A real programme needs people and colonoscopy slots to work them. The section **Under finite capacity** below (ticket B19) charges for both: a shared booking calendar and a daily staff-minutes budget, with a capacity and a staffing sensitivity; it shows how much of the gain survives. Quote that section when the question is what a unit with finite slots and staff would see. What remains future work: per-endoscopist rosters, priority classes (urgent and symptomatic patients), overbooking, and a measured rather than assumed supply of slots and staff. The staff minutes an escalation takes are counted in the Human effort section. Definitions: [`metrics.md`](metrics.md).

<!-- capacity-section:begin (written by make evaluate-capacity; make evaluate copies it from capacity.json) -->
## Under finite capacity

Generated by `make evaluate-capacity` (seed 20261017, 20 replications x 2,000 cases x 360 days, same cohorts and random numbers as the rest of this file; `docs/results/capacity.json`, commit `d83c94b759ab`). Everything above this section is the unconstrained model: cases independent, any number of colonoscopies on any day, escalations free.

**What changes.** Every case in a run now shares (1) a booking calendar with a fixed number of colonoscopy slots per working day at each of three providers: a booking takes the earliest free slot after its lead time, a no-show frees the slot only if the unit refills it (probability 0.3), a patient who withdraws consent and declines care cancels and frees it; and (2) a daily minutes budget for the supervisor and the programme coordinator, spent on escalations and watchdog flags (2 minutes to accept, plus 8.7 to phone the patient when the case is waiting for them), served first come first served, the overflow waiting for the next working day. Nothing else changes: same patients, same random numbers, same baseline logic. With so much capacity that nothing ever waits, the event log is identical to the unconstrained one (a test pins it).

**How big the base capacity is.** Slot supply is 6 sessions a week per 1,000 cases at 6 colonoscopies a session (a four-hour list is 12 points, a colonoscopy 2 points; NHS Improving Quality), over 3 providers, which fills 84% of the slots under today's process between day 30 and day 150. Staffing is 45 supervisor and 90 coordinator minutes a day per 1,000 cases. Sources and grades: [`docs/evidence.md`](../evidence.md) section 12.

### Headline outcomes at base capacity, both scenarios

**Upper range (mechanism ceiling).** Means over replications; the difference column is orchestrated minus baseline with its 95% interval.

| Metric | Baseline, no limit | Orchestrated, no limit | Difference, no limit | Baseline, base capacity | Orchestrated, base capacity | Difference, base capacity |
|---|---|---|---|---|---|---|
| Colonoscopy completed within 90 days | 42.8% | 78.2% | +35.5pp (+35.0pp to +36.0pp) | 38.1% | 39.2% | +1.1pp (+0.7pp to +1.5pp) |
| Colonoscopy completed within 180 days | 51.0% | 82.7% | +31.6pp (+31.2pp to +32.1pp) | 50.9% | 79.3% | +28.4pp (+27.9pp to +28.8pp) |
| Colonoscopy completed within 360 days | 54.5% | 83.0% | +28.5pp (+28.0pp to +29.0pp) | 54.5% | 83.0% | +28.6pp (+28.0pp to +29.0pp) |
| Time to colonoscopy, median among completers (days) | 48.4 | 40.7 | -7.7 (-8.1 to -7.2) | 65.9 | 94.0 | +28.1 (+27.5 to +28.8) |
| Time to colonoscopy, Kaplan-Meier median (days) | 164.8 | 46.0 | -118.8 (-131.3 to -108.7) | 167.7 | 109.3 | -58.4 (-71.3 to -48.6) |
| Cases still open at 90 days | 60.6% | 23.9% | -36.7pp (-37.3pp to -36.2pp) | 67.1% | 65.4% | -1.7pp (-2.0pp to -1.3pp) |
| Cases closed by end of follow-up | 52.8% | 83.0% | +30.3pp (+29.7pp to +30.8pp) | 52.7% | 83.0% | +30.3pp (+29.8pp to +30.8pp) |
| Lost to follow-up | 45.3% | 17.0% | -28.3pp (-28.8pp to -27.8pp) | 45.3% | 17.0% | -28.4pp (-28.8pp to -27.9pp) |
| No colonoscopy by 270 days | 46.5% | 17.1% | -29.5pp (-29.9pp to -29.0pp) | 46.6% | 17.1% | -29.5pp (-30.0pp to -29.1pp) |

**Literature anchored (the one to quote).** Means over replications; the difference column is orchestrated minus baseline with its 95% interval.

| Metric | Baseline, no limit | Orchestrated, no limit | Difference, no limit | Baseline, base capacity | Orchestrated, base capacity | Difference, base capacity |
|---|---|---|---|---|---|---|
| Colonoscopy completed within 90 days | 42.8% | 60.5% | +17.7pp (+17.3pp to +18.0pp) | 38.1% | 38.8% | +0.7pp (+0.5pp to +0.9pp) |
| Colonoscopy completed within 180 days | 51.0% | 66.8% | +15.8pp (+15.5pp to +16.1pp) | 50.9% | 66.6% | +15.7pp (+15.4pp to +16.0pp) |
| Colonoscopy completed within 360 days | 54.5% | 68.7% | +14.2pp (+13.8pp to +14.5pp) | 54.5% | 68.6% | +14.2pp (+13.8pp to +14.5pp) |
| Time to colonoscopy, median among completers (days) | 48.4 | 42.7 | -5.6 (-5.9 to -5.3) | 65.9 | 80.9 | +15.0 (+14.5 to +15.6) |
| Time to colonoscopy, Kaplan-Meier median (days) | 164.8 | 60.5 | -104.2 (-115.6 to -94.8) | 167.7 | 114.8 | -52.9 (-65.3 to -42.9) |
| Cases still open at 90 days | 60.6% | 42.3% | -18.3pp (-18.5pp to -18.0pp) | 67.1% | 66.3% | -0.9pp (-1.1pp to -0.6pp) |
| Cases closed by end of follow-up | 52.8% | 67.8% | +15.1pp (+14.7pp to +15.4pp) | 52.7% | 67.8% | +15.1pp (+14.7pp to +15.4pp) |
| Lost to follow-up | 45.3% | 31.2% | -14.0pp (-14.4pp to -13.8pp) | 45.3% | 31.3% | -14.1pp (-14.4pp to -13.7pp) |
| No colonoscopy by 270 days | 46.5% | 31.9% | -14.6pp (-14.9pp to -14.3pp) | 46.6% | 31.9% | -14.6pp (-14.9pp to -14.3pp) |

### Slots, waiting and the escalation queue at base capacity

| Slots and waiting at base capacity | Baseline | Orchestrated, upper range | Orchestrated, literature anchored |
|---|---|---|---|
| Slots booked / offered, days 30 to 150 | 84.2% | 99.9% | 99.8% |
| Slots used (attended or refilled) / offered | 76.7% | 91.9% | 89.7% |
| Appointments booked per 100 cases | 63.48 | 95.57 | 82.14 |
| Request to appointment, median (days) | 43.3 | 74.1 | 60.2 |
| Request to appointment, p90 (days) | 69.9 | 121.6 | 96.0 |
| Of which waiting for a free slot, mean (days) | 10.5 | 44.3 | 28.9 |
| Appointments pushed back a day or more by a full calendar | 48.6% | 77.6% | 69.7% |

| Escalation handling at base staffing (orchestrated arm) | Upper range | Literature anchored |
|---|---|---|
| Escalations and watchdog flags to work, per 100 cases | 337.74 | 179.28 |
| Supervisor budget used, days 30 to 150 | 95.5% | 74.0% |
| Coordinator budget used, days 30 to 150 | 97.4% | 78.6% |
| Wait before an escalation is worked, mean (days) | 42.4 | 9.5 |
| Wait before an escalation is worked, p90 (days) | 108.0 | 33.0 |
| Escalations that waited half a day or more | 91.6% | 51.7% |
| Escalations waiting at the end of a working day, mean | 1885 | 280 |
| Escalations waiting at the end of a working day, worst day | 2978 | 705 |
| Escalations that were stale when their turn came (the case had moved on) | 36.4% | 15.1% |

### Capacity sensitivity: 70, 85, 100 and 115% of base slot supply

**Upper range.** Slot supply moved to 70, 85, 100 and 115% of base; escalation staffing held at base. Differences are orchestrated minus baseline (the gain from the orchestration), with 95% intervals.

| Slot supply | Utilisation, baseline | Utilisation, orchestrated | Request to appointment, baseline (median days) | Request to appointment, orchestrated (median days) | Gain in 90-day completion | Gain in 180-day completion | Gain in 360-day completion | Time to colonoscopy, baseline (median days) | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|
| no limit | n/a | n/a | n/a | n/a | +35.5pp (+35.0pp to +36.0pp) | +31.6pp (+31.2pp to +32.1pp) | +28.5pp (+28.0pp to +29.0pp) | 48.4 | 40.7 |
| 70% | 99.7% | 100.0% | 63.1 | 107.9 | -1.0pp (-1.2pp to -0.8pp) | +7.7pp (+7.2pp to +8.3pp) | +28.5pp (+28.0pp to +29.0pp) | 88.5 | 131.3 |
| 85% | 97.5% | 99.9% | 50.9 | 87.7 | -0.0pp (-0.3pp to +0.3pp) | +19.2pp (+18.7pp to +19.7pp) | +28.5pp (+28.1pp to +29.0pp) | 75.0 | 109.1 |
| 100% (base) | 84.2% | 99.9% | 43.3 | 74.1 | +1.1pp (+0.7pp to +1.5pp) | +28.4pp (+27.9pp to +28.8pp) | +28.6pp (+28.0pp to +29.0pp) | 65.9 | 94.0 |
| 115% | 73.3% | 99.9% | 37.8 | 64.5 | +4.9pp (+4.5pp to +5.3pp) | +31.5pp (+31.0pp to +31.9pp) | +28.6pp (+28.0pp to +29.0pp) | 59.4 | 82.9 |

**Literature anchored.** Slot supply moved to 70, 85, 100 and 115% of base; escalation staffing held at base. Differences are orchestrated minus baseline (the gain from the orchestration), with 95% intervals.

| Slot supply | Utilisation, baseline | Utilisation, orchestrated | Request to appointment, baseline (median days) | Request to appointment, orchestrated (median days) | Gain in 90-day completion | Gain in 180-day completion | Gain in 360-day completion | Time to colonoscopy, baseline (median days) | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|
| no limit | n/a | n/a | n/a | n/a | +17.7pp (+17.3pp to +18.0pp) | +15.8pp (+15.5pp to +16.1pp) | +14.2pp (+13.8pp to +14.5pp) | 48.4 | 42.7 |
| 70% | 99.7% | 99.9% | 63.1 | 87.7 | -1.2pp (-1.3pp to -1.0pp) | +5.2pp (+4.8pp to +5.7pp) | +14.2pp (+13.8pp to +14.5pp) | 88.5 | 112.3 |
| 85% | 97.5% | 99.9% | 50.9 | 71.3 | -0.9pp (-1.2pp to -0.7pp) | +13.6pp (+13.2pp to +13.9pp) | +14.2pp (+13.8pp to +14.4pp) | 75.0 | 93.7 |
| 100% (base) | 84.2% | 99.8% | 43.3 | 60.2 | +0.7pp (+0.5pp to +0.9pp) | +15.7pp (+15.4pp to +16.0pp) | +14.2pp (+13.8pp to +14.5pp) | 65.9 | 80.9 |
| 115% | 73.3% | 98.8% | 37.8 | 52.2 | +3.1pp (+2.8pp to +3.5pp) | +15.8pp (+15.5pp to +16.1pp) | +14.2pp (+13.9pp to +14.5pp) | 59.4 | 71.5 |

### Which limit does what: slots only, staff only, both

**Upper range.** Orchestrated arm with one limit at a time. The gain is against the baseline arm under the same slot limit (the baseline raises no escalations, so staff limits never touch it).

| Which limit binds | 90-day completion | 180-day completion | 360-day completion | Gain at 90 days | Gain at 180 days | Gain at 360 days | Change in Kaplan-Meier median time to colonoscopy (days) |
|---|---|---|---|---|---|---|---|
| No limit (reference) | 78.2% | 82.7% | 83.0% | +35.5pp (+35.0pp to +36.0pp) | +31.6pp (+31.2pp to +32.1pp) | +28.5pp (+28.0pp to +29.0pp) | -118.8 (-131.3 to -108.7) |
| Slots limited, staff unlimited | 39.1% | 79.3% | 82.9% | +1.0pp (+0.7pp to +1.4pp) | +28.4pp (+27.9pp to +28.8pp) | +28.4pp (+28.0pp to +28.9pp) | -58.1 (-70.6 to -47.8) |
| Staff limited, slots unlimited | 77.2% | 82.8% | 83.2% | +34.4pp (+34.0pp to +34.9pp) | +31.8pp (+31.3pp to +32.2pp) | +28.6pp (+28.2pp to +29.1pp) | -117.5 (-129.9 to -107.4) |
| Both limited (base capacity) | 39.2% | 79.3% | 83.0% | +1.1pp (+0.7pp to +1.5pp) | +28.4pp (+27.9pp to +28.8pp) | +28.6pp (+28.0pp to +29.0pp) | -58.4 (-71.3 to -48.6) |

**Literature anchored.** Orchestrated arm with one limit at a time. The gain is against the baseline arm under the same slot limit (the baseline raises no escalations, so staff limits never touch it).

| Which limit binds | 90-day completion | 180-day completion | 360-day completion | Gain at 90 days | Gain at 180 days | Gain at 360 days | Change in Kaplan-Meier median time to colonoscopy (days) |
|---|---|---|---|---|---|---|---|
| No limit (reference) | 60.5% | 66.8% | 68.7% | +17.7pp (+17.3pp to +18.0pp) | +15.8pp (+15.5pp to +16.1pp) | +14.2pp (+13.8pp to +14.5pp) | -104.2 (-115.6 to -94.8) |
| Slots limited, staff unlimited | 38.8% | 66.5% | 68.5% | +0.6pp (+0.4pp to +0.8pp) | +15.6pp (+15.3pp to +15.9pp) | +14.1pp (+13.7pp to +14.4pp) | -53.0 (-65.8 to -43.4) |
| Staff limited, slots unlimited | 60.4% | 67.0% | 68.8% | +17.6pp (+17.3pp to +17.9pp) | +16.0pp (+15.7pp to +16.3pp) | +14.3pp (+14.0pp to +14.6pp) | -103.8 (-116.7 to -93.5) |
| Both limited (base capacity) | 38.8% | 66.6% | 68.6% | +0.7pp (+0.5pp to +0.9pp) | +15.7pp (+15.4pp to +16.0pp) | +14.2pp (+13.8pp to +14.5pp) | -52.9 (-65.3 to -42.9) |

### Staffing sensitivity: escalation budgets at 50 to 300% of base

**Upper range.** Supervisor and coordinator budgets moved together to 50, 75, 100, 150, 200 and 300% of base; slot supply held at base. Gains are against the baseline arm at base capacity (the baseline raises no escalations, so staffing does not touch it).

| Staffing | Supervisor budget used | Coordinator budget used | Wait to be worked, mean (days) | Wait to be worked, p90 (days) | Escalations waiting, mean | Stale when worked | 180-day completion, orchestrated | Gain in 90-day completion | Gain in 180-day completion | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|---|
| 50% | 93.1% | 95.8% | 146.5 | 276.6 | 2984 | 56.4% | 79.0% | +1.2pp (+0.9pp to +1.6pp) | +28.1pp (+27.5pp to +28.6pp) | 93.5 |
| 75% | 93.5% | 96.7% | 81.2 | 169.0 | 2516 | 46.2% | 79.1% | +1.2pp (+0.8pp to +1.5pp) | +28.2pp (+27.7pp to +28.8pp) | 93.8 |
| 100% (base) | 95.5% | 97.4% | 42.4 | 108.0 | 1885 | 36.4% | 79.3% | +1.1pp (+0.8pp to +1.5pp) | +28.4pp (+27.9pp to +28.9pp) | 94.0 |
| 150% | 91.1% | 86.8% | 15.9 | 50.1 | 849 | 18.6% | 79.3% | +1.1pp (+0.7pp to +1.5pp) | +28.4pp (+28.0pp to +29.0pp) | 94.0 |
| 200% | 64.7% | 67.6% | 6.9 | 23.0 | 361 | 10.7% | 79.3% | +1.1pp (+0.7pp to +1.4pp) | +28.4pp (+27.9pp to +29.0pp) | 94.1 |
| 300% | 40.3% | 44.9% | 0.9 | 4.2 | 43 | 3.2% | 79.3% | +1.0pp (+0.7pp to +1.4pp) | +28.4pp (+27.9pp to +28.9pp) | 94.2 |

**Literature anchored.** Supervisor and coordinator budgets moved together to 50, 75, 100, 150, 200 and 300% of base; slot supply held at base. Gains are against the baseline arm at base capacity (the baseline raises no escalations, so staffing does not touch it).

| Staffing | Supervisor budget used | Coordinator budget used | Wait to be worked, mean (days) | Wait to be worked, p90 (days) | Escalations waiting, mean | Stale when worked | 180-day completion, orchestrated | Gain in 90-day completion | Gain in 180-day completion | Time to colonoscopy, orchestrated (median days) |
|---|---|---|---|---|---|---|---|---|---|---|
| 50% | 93.3% | 95.7% | 55.7 | 128.8 | 1157 | 39.9% | 66.6% | +0.8pp (+0.6pp to +1.0pp) | +15.7pp (+15.3pp to +16.0pp) | 80.9 |
| 75% | 94.3% | 93.8% | 21.1 | 65.7 | 623 | 26.4% | 66.7% | +0.8pp (+0.5pp to +1.0pp) | +15.8pp (+15.5pp to +16.2pp) | 80.9 |
| 100% (base) | 74.0% | 78.6% | 9.5 | 33.0 | 280 | 15.1% | 66.6% | +0.7pp (+0.5pp to +0.9pp) | +15.7pp (+15.4pp to +16.0pp) | 80.9 |
| 150% | 46.1% | 52.4% | 1.8 | 7.8 | 52 | 5.2% | 66.6% | +0.7pp (+0.5pp to +0.9pp) | +15.7pp (+15.4pp to +16.0pp) | 81.0 |
| 200% | 33.3% | 39.8% | 0.4 | 1.2 | 10 | 1.5% | 66.5% | +0.7pp (+0.5pp to +0.9pp) | +15.6pp (+15.3pp to +15.9pp) | 81.0 |
| 300% | 21.7% | 26.4% | 0.0 | 0.0 | 1 | 0.1% | 66.5% | +0.7pp (+0.5pp to +0.9pp) | +15.6pp (+15.3pp to +15.9pp) | 81.0 |

### How much the benefit shrinks when capacity binds

**In plain English, for the literature-anchored scenario.** Give the programme a finite number of colonoscopy slots and a finite number of staff minutes and the benefit in *how soon* patients get their colonoscopy shrinks sharply (the 90-day advantage is almost gone and the advantage in median time to colonoscopy roughly halves), while the benefit in *whether* they get one within six to twelve months mostly survives at base capacity and then falls away quickly if slots are scarcer than that.

- **Completion within 90 days.** Without a limit, orchestration completes +17.7 percentage points more patients within 90 days than today's process. At base capacity the gain is almost gone (4% of the unconstrained gain is left): +0.7 percentage points. The share of cases still open at 90 days improves by 0.9 points against 18.3 without a limit. The calendar is 84% full under today's process and 100% full with the orchestration, which books 82 appointments per 100 cases against 63: the patients it brings in queue behind patients already booked, and the median wait from request to appointment is 60 days against 43.
- **Time to colonoscopy.** The censoring-aware (Kaplan-Meier) median time to colonoscopy is shortened by 104 days without a limit and by 53 days at base capacity (the gain shrinks to 51% of the unconstrained gain). The plain median among patients who had a colonoscopy goes the other way (-6 days without a limit, +15 days at base capacity, orchestrated minus baseline), because the orchestration brings in extra patients who are, on average, later than the ones who would have come anyway; the Kaplan-Meier figure is the fair one.
- **Completion within 180 and 360 days.** The 180-day gain holds (99% of the unconstrained gain) (+15.7 percentage points against +15.8 percentage points without a limit) and the 360-day gain holds (100% of the unconstrained gain) (+14.2 percentage points). A full calendar delays a colonoscopy; at base capacity it does not stop most of the extra patients from getting one within six months. Part of why the gain is not smaller is a model effect explained under *Which limit does it*.
- **If slots are scarcer, the 180-day gain goes quickly.** It is +15.8 percentage points at 115% of base slot supply, +15.7 percentage points at 100%, +13.6 percentage points at 85% and +5.2 percentage points at 70%. The orchestration needs room for roughly 29% more appointments than today's process; once the calendar cannot take them the extra patients wait past the 180-day mark, and no escalation can make a slot appear. The 360-day gain stays at +14.2 percentage points even at 70%, because the demand is a single wave and the queue always clears in the end; with a steady inflow of new positives it would not (see the limits below).
- **Staff minutes.** At base staffing an escalation waits a mean of 9.5 days to be worked, with 280 waiting on an average day. Halving the staff takes the wait to 55.7 days; doubling them takes it to 0.4 and tripling them to 0.0: a team sized to 85% of the average workload needs roughly twice that size to keep escalations from queueing. Completion hardly moves with it: the 180-day gain is +15.7 percentage points at 50% staffing and +15.6 percentage points at 300%.
- **Which limit does it.** The slots do the damage to speed. Limiting only the slots leaves a 90-day gain of +0.6 percentage points, a 180-day gain of +15.6 percentage points and a 360-day gain of +14.1 percentage points. Limiting only the staff does not lower completion at all in this model: it leaves +17.6 percentage points, +16.0 percentage points and +14.3 percentage points, which is *more* than with no limit. That is a property of the model's ladder, not a finding about staffing: an escalation worked days late is more often moot (the case moved on, stale), so fewer chases and referral bounces go out, and each chase that goes unanswered or bounce that is rejected can push a patient further from care (`patients.dropout_prob_per_lapse`, `handoff.max_attempts`). The both-limits gain at 180 and 360 days therefore sits about a point above the slots-only gain for that reason, and the staffing rows must be read as statements about waiting and workload, not as evidence that understaffing is harmless.

For the upper-range scenario (the mechanism ceiling) the 90-day gain goes from +35.5 percentage points to +1.1 percentage points and the 180-day gain from +31.6 percentage points to +28.4 percentage points; the orchestration there books 96 appointments per 100 cases and the calendar is 100% full. Its escalation queue is far longer (42 days mean wait at base staffing), because it raises far more escalations than the staff were sized for.

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

<!-- refusal-section:begin (written by make evaluate-refusal; make evaluate copies it from refusal.json) -->
## Secondary scenario: with the informed-refusal pathway (not the headline)

Generated by `make evaluate-refusal` (seed 20261017, 20 replications x 2,000 cases x 360 days, the same cohorts and random numbers as the rest of this file; parameters `simulator/params.yaml` + `simulator/params.refusal.yaml`, hashes `5c99112d018d4da0` (upper range), `d7e54ad60153fee5` (literature anchored); summary in `docs/results/refusal.json`).

**The headline remains 36.0%** labour reduction in reach (literature anchored) with `orchestration.refusal_path_share` at its default 0: refusers are flagged lost and the escalation ladder runs on for them. This section switches the informed-refusal path (A33, ADR-042) on for every orchestrated-arm refuser (`refusal_path_share` 1); the baseline arm is unchanged. In reach the labour reduction becomes **45.2%** against 36.0%. **The 40% target is not claimed on this basis**: the path rests on the assumptions below, none of them measured, and adopting it is the results owner's decision after clinical review.

**Assumptions (all assumed, none sourced):**

- the owner counsels a refuser 5 working days after the process notices the refusal (`simulator/sim/engine.py`, `COUNSELLING_DELAY_WD`);
- a different physician signs the declined closure 3 working days later (`orchestration.signoff_sla_working_days`, the pathway's `close` SLA);
- counselling and the counselling record take 15 minutes of the owner clinician's time (`O_RECORD_REFUSAL`; the sign-off is charged as a closure signature);
- the re-offer date is 12 months after the refusal (`informed_refusal.re_offer_months`), an assumption: the AI-simulated clinical review proposed 3 months; the re-offer is not simulated within the follow-up, so it does not move these numbers;
- every refuser takes the path (share 1.0), the upper end; in practice some refusers will not be reached for counselling.

Literature-anchored scenario; headline column from the committed `results.json` (path off), the other from `refusal.json` (path on). Means with percentile-bootstrap 95% intervals over replications.

| Metric | Headline (path off) | With the informed-refusal path |
|---|---|---|
| **Labour reduction among the cases the programme reaches** | 36.0% (34.9% to 36.9%) | 45.2% (44.4% to 45.8%) |
| Labour reduction, programme level (all cases) | 19.7% (19.1% to 20.2%) | 24.7% (24.2% to 25.1%) |
| Labour reduction per closed case, in reach | 57.9% (57.3% to 58.6%) | 63.9% (63.5% to 64.4%) |
| Coordinator minutes, reduction, in reach | 66.0% (65.2% to 67.0%) | 80.4% (80.0% to 80.9%) |
| Owner-clinician minutes, reduction, in reach (negative = more) | -170.9% (-177.1% to -164.6%) | -203.4% (-211.1% to -195.8%) |
| Colonoscopy completed within 180 days, orchestrated | 66.8% (66.3% to 67.3%) | 66.8% (66.3% to 67.3%) |
| Cases closed with evidence and sign-off (colonoscopy), orchestrated | 67.8% (67.3% to 68.4%) | 67.8% (67.3% to 68.4%) |
| Cases closed as declined after counselling and a second physician, orchestrated | 0.0% (0.0% to 0.0%) | 6.5% (6.2% to 6.8%) |
| Lost to follow-up, orchestrated | 31.2% (30.7% to 31.7%) | 31.2% (30.7% to 31.7%) |
| Escalations per 100 cases, orchestrated | 84.4 (83.1 to 85.8) | 84.4 (83.1 to 85.8) |

Upper range (mechanism ceiling), labour reduction in reach: 40.0% (39.1% to 40.8%) with the path off, 49.3% (48.7% to 49.9%) with it on.

**Reading.** The extra saving is coordinator time: the coordinator's reduction in reach goes from 66.0% (65.2% to 67.0%) to 80.4% (80.0% to 80.9%), because a refuser who has been counselled and closed as declined is no longer chased as a lost case. Escalations per 100 cases barely move (84.4 against 84.4), so the saving is not in the ladder. The owner clinician pays for it (counselling, and a second physician's signature), so owner-clinician minutes rise further than in the headline. Colonoscopy completion and the lost-to-follow-up share (no colonoscopy and no progress) do not change: a refuser has no colonoscopy either way; the declined closures are reported apart from the colonoscopy closures. Timing figures inherit the simulator's unvalidated timing ([`validation.md`](validation.md)).
<!-- refusal-section:end -->

<!-- abudhabi-section:begin (written by make evaluate-abudhabi; make evaluate copies it from abudhabi.json) -->
## Sensitivity: an Abu Dhabi-shaped baseline (not the headline)

Generated by `make evaluate-abudhabi` (seed 20261017, 20 replications x 2,000 cases x 360 days, the same cohorts and random numbers as the rest of this file; parameters `simulator/params.yaml` + `simulator/params.abudhabi.yaml`, hashes `270f94a04540df8f` (upper range), `1db2ab9549834bfc` (literature anchored); summary in `docs/results/abudhabi.json`).

**This is a sensitivity run, not the headline.** The headline baseline is the US usual-care curve (Mohl 2023). Here the baseline is calibrated to the Abu Dhabi follow-up audit instead: 436 of 1,429 positives (30.5%) ever had a colonoscopy (Almansoori et al., BMC Cancer 2021, [`docs/evidence.md`](../evidence.md)). That audit covers Emirati nationals in 2015-2016, before IFHAS V10, and gives one 'ever' figure, with a median of 72 days among those who had one and more than half of them within 90 days; the baseline is fitted to the 360-day share, to that median and to that 90-day split. **Programme reach (`orchestration.programme_reach`, 0.55, calibrated against the US baseline and the pooled navigation risk ratio) and every orchestration parameter are unchanged. The ratio in this section is therefore not evidence of effect**: it follows from the lower baseline and the unchanged reach, and no study measured it in Abu Dhabi.

**If Abu Dhabi today looks like the 30.5% audit, the same mechanism moves it to 38.8% at 360 days (literature anchored, simulated, sensitivity run), from 30.6% simulated for the baseline.** Today's baseline may be better or worse than 2015-2016. **The overlay is conservative about the mechanism**: the loss the audit does not explain is placed in `patients.refusal_prob`, a patient-level trait in both arms that the orchestration cannot recover, so the gain here probably understates what ownership and timers could do on the part of that loss that is really missed hand-offs.

**Literature anchored.** Means with percentile-bootstrap 95% intervals over replications; gain is orchestrated minus baseline, in percentage points.

| Colonoscopy completed within | Baseline | Orchestrated | Gain (points) | Ratio |
|---|---|---|---|---|
| 90 days | 19.4% (19.2% to 19.7%) | 32.2% (31.9% to 32.5%) | +12.8 (+12.5 to +13.1) | 1.66 |
| 180 days | 27.4% (27.0% to 27.7%) | 37.2% (36.8% to 37.6%) | +9.8 (+9.6 to +10.1) | 1.36 |
| 360 days | 30.6% (30.2% to 31.0%) | 38.8% (38.4% to 39.2%) | +8.2 (+7.9 to +8.5) | 1.27 |

| Other | Baseline | Orchestrated |
|---|---|---|
| Silent loss, programme level (every case) | 69.3% (68.9% to 69.7%) | 31.5% (31.1% to 31.8%) |
| Silent loss, among the cases the programme reaches | 69.1% (68.6% to 69.7%) | 0.0% (0.0% to 0.0%) |
| Median days from positive result to colonoscopy, among completers within 360 days | 70 d (69 to 71) | 47 d (47 to 48) |
| Cases closed with evidence and sign-off | 29.6% (29.1% to 30.0%) | 38.3% (38.0% to 38.8%) |

**Upper range.** Means with percentile-bootstrap 95% intervals over replications; gain is orchestrated minus baseline, in percentage points.

| Colonoscopy completed within | Baseline | Orchestrated | Gain (points) | Ratio |
|---|---|---|---|---|
| 90 days | 19.4% (19.2% to 19.7%) | 44.4% (44.0% to 45.0%) | +25.0 (+24.6 to +25.5) | 2.29 |
| 180 days | 27.4% (27.0% to 27.7%) | 46.9% (46.5% to 47.4%) | +19.6 (+19.2 to +20.0) | 1.72 |
| 360 days | 30.6% (30.2% to 31.0%) | 47.1% (46.7% to 47.6%) | +16.5 (+16.1 to +16.9) | 1.54 |

| Other | Baseline | Orchestrated |
|---|---|---|
| Silent loss, programme level (every case) | 69.3% (68.9% to 69.7%) | 0.0% (0.0% to 0.0%) |
| Silent loss, among the cases the programme reaches | 69.3% (68.9% to 69.7%) | 0.0% (0.0% to 0.0%) |
| Median days from positive result to colonoscopy, among completers within 360 days | 70 d (69 to 71) | 40 d (40 to 41) |
| Cases closed with evidence and sign-off | 29.6% (29.1% to 30.0%) | 47.1% (46.7% to 47.6%) |

Silent loss is 69.3% -> 31.5% at programme level (literature anchored); the 0% among the cases the programme reaches is true by construction, because every reached case has an owner and a timer. The upper-range scenario is the mechanism ceiling and is not a forecast.

**What the overlay changes** (everything else is inherited from `simulator/params.yaml`; the literature-anchored scenario then adds `simulator/params.conservative.yaml` as usual). Status vocabulary as in `simulator/params.yaml`: sourced, sourced-adjacent, calibrated (fitted here to the audit) or assumed.

| Key | Overlay value | Status | Basis |
|---|---|---|---|
| `colonoscopy_completion_12m` | 0.305 | sourced | Almansoori et al., BMC Cancer 2021;21:897, 436 of 1,429 positives (30.5%) 'ever' had follow-up colonoscopy, Emirati nationals, Abu Dhabi ambulatory clinics, 2015-2016. https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ |
| `appointment.fast_lane_share.unmanaged` | 0.4 | calibrated | Calibrated (E20b) so the baseline reproduces the 30.5% Abu Dhabi audit |
| `patients.refusal_prob` | 0.5 | calibrated | Calibrated (E20b) so the baseline reproduces the 30.5% Abu Dhabi audit |

`gi_referral_non_attendance_abu_dhabi` (0.25) is not read by the engine. It is carried by `patients.refusal_prob`, which applies in both arms, so the orchestration is assumed to recover none of those patients. The audit's 25% does not explain the whole 69.5% not followed up, so `patients.refusal_prob` is calibrated higher and the rest is treated the same way. A smaller change to the unmanaged fast-lane share fits the completer median; slow-lane timing is at its default. 3 and 6 month targets keep their US values.
<!-- abudhabi-section:end -->

## Summary

**Literature anchored.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 67% of patients to a confirmatory colonoscopy within 180 days, against 51% at baseline (+15.8 percentage points, 95% CI +15.5 to +16.1, which excludes zero). At 90 days 42% of orchestrated cases were still open against 61% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 52 days orchestrated against 57 at baseline; that conditional median only covers the 53% of baseline cases that closed at all (68% orchestrated), so an arm that closes more slow cases looks slower on it. Counting the cases that never closed as censored at the end of follow-up instead of dropping them, the Kaplan-Meier median is 71 days orchestrated against 208 at baseline and the restricted mean time to closure within 360 days is 159 against 211 days. 45% of baseline cases were lost without anyone being told, against 21% in the orchestrated arm at programme level; among the 55% of cases the programme reached the orchestrated figure is 0% against 45% for the same patients at baseline, and the programme-level remainder is cases outside the programme's reach, which are handled as today.

**Upper range.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 83% of patients to a confirmatory colonoscopy within 180 days, against 51% at baseline (+31.6 percentage points, 95% CI +31.2 to +32.1, which excludes zero). At 90 days 24% of orchestrated cases were still open against 61% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 49 days orchestrated against 57 at baseline; that conditional median only covers the 53% of baseline cases that closed at all (83% orchestrated), so an arm that closes more slow cases looks slower on it. Counting the cases that never closed as censored at the end of follow-up instead of dropping them, the Kaplan-Meier median is 55 days orchestrated against 208 at baseline and the restricted mean time to closure within 360 days is 106 against 211 days. 45% of baseline cases were lost without anyone being told, against 0% in the orchestrated arm at programme level, where lost or unreachable cases are flagged to a human and never closed automatically. These results come from a model, not a trial: 108 of 144 parameters are assumed or calibrated rather than measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml` or `simulator/params.conservative.yaml`.

## Arm comparison: literature anchored scenario (the one to quote)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.8% (42.4% to 43.1%) | 60.5% (60.1% to 60.8%) | +17.7pp (+17.4pp to +18.0pp) |
| Colonoscopy completed within 180 days | 51.0% (50.5% to 51.5%) | 66.8% (66.3% to 67.3%) | +15.8pp (+15.5pp to +16.1pp) |
| Colonoscopy completed within 360 days | 54.5% (53.9% to 55.0%) | 68.7% (68.1% to 69.2%) | +14.2pp (+13.8pp to +14.5pp) |
| Cases still open at 30 days | 95.4% (95.3% to 95.6%) | 92.9% (92.7% to 93.1%) | -2.6pp (-2.7pp to -2.4pp) |
| Cases still open at 60 days | 72.0% (71.6% to 72.4%) | 57.5% (57.0% to 58.0%) | -14.5pp (-14.9pp to -14.2pp) |
| Cases still open at 90 days | 60.6% (60.2% to 61.0%) | 42.3% (41.9% to 42.7%) | -18.3pp (-18.5pp to -18.1pp) |
| Time to safe closure, median (among closed cases only) | 57.5 (57.0 to 57.9) | 51.6 (51.2 to 52.0) | -5.9 (-6.3 to -5.5) |
| Time to safe closure, p90 (among closed cases only) | 152.3 (148.7 to 156.1) | 106.3 (104.6 to 108.1) | -46.0 (-48.6 to -43.7) |
| Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up) | 208.3 (194.7 to 223.2) | 70.6 (70.0 to 71.2) | -137.9 (-152.2 to -124.2) |
| Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware) | 210.9 (209.5 to 212.4) | 158.8 (157.4 to 160.1) | -52.1 (-53.0 to -51.1) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 52.8% (52.2% to 53.3%) | 67.8% (67.3% to 68.4%) | +15.1pp (+14.7pp to +15.4pp) |
| Cases closed as DECLINED after counselling and a physician's sign-off (informed refusal, A33; not in the row above, not a colonoscopy) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | +0.0pp (+0.0pp to +0.0pp) |
| Time to named owner, median | n/a | 2.4 (2.4 to 2.5) | n/a |
| Time to named owner, p90 | n/a | 5.6 (5.5 to 5.8) | n/a |
| Time to first patient contact, median | 3.0 (3.0 to 3.0) | 2.7 (2.7 to 2.8) | -0.3 (-0.3 to -0.3) |
| Time to first patient contact, p90 | 13.1 (12.9 to 13.3) | 8.6 (8.4 to 8.9) | -4.5 (-4.7 to -4.3) |
| Time to colonoscopy, median (among completers only) | 48.4 (48.0 to 48.7) | 42.7 (42.5 to 43.0) | -5.6 (-5.9 to -5.3) |
| Time to colonoscopy, Kaplan-Meier median (censoring-aware) | 164.8 (154.9 to 177.1) | 60.5 (59.8 to 61.3) | -104.2 (-116.2 to -94.4) |
| Time to colonoscopy, restricted mean (censoring-aware) | 201.2 (199.9 to 202.8) | 150.6 (149.1 to 152.0) | -50.6 (-51.7 to -49.7) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.0% (2.8% to 3.1%) | -0.0pp (-0.1pp to +0.1pp) |
| Escalation acted-on rate (within 5 working days) | n/a | 30.5% (29.8% to 31.2%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 84.44 (83.09 to 85.85) | +84.44 (+83.05 to +86.05) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 45.3% (44.7% to 45.9%) | 31.2% (30.7% to 31.7%) | -14.0pp (-14.4pp to -13.7pp) |
| Lost AND never surfaced to a human, programme level (all cases) | 45.3% (44.7% to 45.8%) | 20.6% (20.2% to 21.0%) | -24.7pp (-25.1pp to -24.3pp) |
| Lost AND never surfaced to a human, in reach (cases the programme reached) | 45.1% (44.6% to 45.7%) | 0.0% (0.0% to 0.0%) | -45.1pp (-45.7pp to -44.6pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 46.5% (46.0% to 47.1%) | 31.9% (31.4% to 32.5%) | -14.6pp (-14.9pp to -14.3pp) |
| Outreach sent after consent withdrawn, programme level (all cases) | 0.1% (0.1% to 0.1%) | 0.1% (0.0% to 0.1%) | -0.0pp (-0.1pp to -0.0pp) |
| Outreach sent after consent withdrawn, in reach (cases the programme reached) | 0.1% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | -0.1pp (-0.1pp to -0.1pp) |
| Cases within the programme's reach (denominator of the in-reach rows) | 54.7% (54.2% to 55.2%) | 54.7% (54.2% to 55.2%) | +0.0pp (+0.0pp to +0.0pp) |

## Arm comparison: upper range scenario (mechanism ceiling)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.8% (42.4% to 43.1%) | 78.2% (77.9% to 78.6%) | +35.5pp (+35.0pp to +35.9pp) |
| Colonoscopy completed within 180 days | 51.0% (50.5% to 51.5%) | 82.7% (82.3% to 83.1%) | +31.6pp (+31.2pp to +32.1pp) |
| Colonoscopy completed within 360 days | 54.5% (53.9% to 55.0%) | 83.0% (82.6% to 83.4%) | +28.5pp (+28.0pp to +29.0pp) |
| Cases still open at 30 days | 95.4% (95.3% to 95.6%) | 90.3% (90.0% to 90.5%) | -5.2pp (-5.5pp to -5.0pp) |
| Cases still open at 60 days | 72.0% (71.6% to 72.4%) | 42.6% (42.1% to 43.1%) | -29.4pp (-29.9pp to -28.9pp) |
| Cases still open at 90 days | 60.6% (60.2% to 61.0%) | 23.9% (23.6% to 24.3%) | -36.7pp (-37.3pp to -36.2pp) |
| Time to safe closure, median (among closed cases only) | 57.5 (57.0 to 57.9) | 48.9 (48.7 to 49.1) | -8.6 (-9.0 to -8.2) |
| Time to safe closure, p90 (among closed cases only) | 152.3 (148.7 to 156.1) | 84.5 (83.9 to 85.0) | -67.9 (-72.0 to -64.4) |
| Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up) | 208.3 (194.7 to 223.2) | 54.6 (54.1 to 55.0) | -153.8 (-168.3 to -140.0) |
| Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware) | 210.9 (209.5 to 212.4) | 106.4 (105.2 to 107.5) | -104.5 (-106.0 to -103.0) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 52.8% (52.2% to 53.3%) | 83.0% (82.6% to 83.4%) | +30.3pp (+29.7pp to +30.7pp) |
| Cases closed as DECLINED after counselling and a physician's sign-off (informed refusal, A33; not in the row above, not a colonoscopy) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | +0.0pp (+0.0pp to +0.0pp) |
| Time to named owner, median | n/a | 2.4 (2.4 to 2.5) | n/a |
| Time to named owner, p90 | n/a | 5.6 (5.4 to 5.7) | n/a |
| Time to first patient contact, median | 3.0 (3.0 to 3.0) | 2.5 (2.5 to 2.5) | -0.5 (-0.6 to -0.5) |
| Time to first patient contact, p90 | 13.1 (12.9 to 13.3) | 6.1 (6.0 to 6.2) | -7.0 (-7.2 to -6.9) |
| Time to colonoscopy, median (among completers only) | 48.4 (48.0 to 48.7) | 40.7 (40.4 to 41.0) | -7.7 (-8.1 to -7.2) |
| Time to colonoscopy, Kaplan-Meier median (censoring-aware) | 164.8 (154.9 to 177.1) | 46.0 (45.6 to 46.4) | -118.8 (-131.1 to -108.9) |
| Time to colonoscopy, restricted mean (censoring-aware) | 201.2 (199.9 to 202.8) | 99.5 (98.4 to 100.6) | -101.7 (-103.1 to -100.2) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.1% (2.9% to 3.2%) | +0.1pp (-0.1pp to +0.2pp) |
| Escalation acted-on rate (within 5 working days) | n/a | 35.0% (34.6% to 35.4%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 142.06 (140.23 to 143.86) | +142.06 (+140.19 to +143.90) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 45.3% (44.7% to 45.9%) | 17.0% (16.6% to 17.3%) | -28.3pp (-28.8pp to -27.8pp) |
| Lost AND never surfaced to a human, programme level (all cases) | 45.3% (44.7% to 45.8%) | 0.0% (0.0% to 0.0%) | -45.3pp (-45.8pp to -44.7pp) |
| Lost AND never surfaced to a human, in reach (cases the programme reached) | 45.3% (44.7% to 45.8%) | 0.0% (0.0% to 0.0%) | -45.3pp (-45.9pp to -44.8pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 46.5% (46.0% to 47.1%) | 17.1% (16.7% to 17.4%) | -29.5pp (-29.9pp to -29.0pp) |
| Outreach sent after consent withdrawn, programme level (all cases) | 0.1% (0.1% to 0.1%) | 0.0% (0.0% to 0.0%) | -0.1pp (-0.1pp to -0.1pp) |
| Outreach sent after consent withdrawn, in reach (cases the programme reached) | 0.1% (0.1% to 0.1%) | 0.0% (0.0% to 0.0%) | -0.1pp (-0.1pp to -0.1pp) |
| Cases within the programme's reach (denominator of the in-reach rows) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | +0.0pp (+0.0pp to +0.0pp) |

Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. `n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.

## Human effort

Human minutes per case in each arm and the reduction automation buys, from the event logs of the same replications as the rest of this report (95% CIs are percentile bootstrap over replications; the reduction is paired by replication). Definitions: [`metrics.md`](metrics.md) section 11. Sources and grades of every minutes figure: [`evidence.md`](../evidence.md) section 11.

| Measure | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Human minutes per case | 57.1 (56.9 to 57.3) | 45.8 (45.6 to 46.1) | 34.3 (33.8 to 34.7) |
| Human minutes per closed case | 108.2 (107.4 to 109.1) | 67.6 (66.8 to 68.4) | 41.3 (40.6 to 41.9) |
| Human actions per case | 9.68 (9.65 to 9.72) | 8.95 (8.88 to 9.03) | 7.70 (7.57 to 7.82) |
| Automation share (automated actions / all actions) | 0.0% (0.0% to 0.0%) | 54.3% (54.1% to 54.6%) | 71.2% (70.9% to 71.5%) |
| **Labour reduction**, minutes per case (1 - orchestrated / baseline) | - | 19.7% (19.1% to 20.2%) | 40.0% (39.0% to 40.9%) |
| Labour reduction, minutes per closed case | - | 37.5% (37.0% to 38.1%) | 61.9% (61.3% to 62.5%) |
| Reduction in human actions per case | - | 7.6% (6.6% to 8.5%) | 20.5% (19.0% to 21.8%) |
| Among the cases the programme reaches: human minutes per case | 57.1 (56.8 to 57.3) | 36.5 (36.0 to 37.0) | 34.3 (33.8 to 34.7) |
| Among the cases the programme reaches: **labour reduction** | - | 36.0% (34.9% to 36.9%) | 40.0% (39.1% to 40.8%) |

**What is counted.** Staff minutes per case in each arm, computed from the simulated event log (`simulator/sim/effort.py`; no outcome in this report depends on it). A person's time is counted only when a person acts. Today's process is manual at every step (look up and acknowledge the result, phone or write to the patient, send the referral, book, record that the patient called back, review the report, file the closure), and the model adds the chasing that leaves no event in the log (up to 3 attempts every 14 days when a patient, a referral or a colonoscopy is overdue). In the orchestrated process the engine does the following by machine and they cost no human minutes: acknowledge on ingest; owner auto-assignment; pre-approved template outreach and reminders; evidence detection; closure proposal; referral acceptance carries the appointment; reroute down a provider list; bounce; overdue; escalation emission; reply handling; consent ledger; delivery tracking; de-duplication; state machine. A person acts only to accept an escalation, approve a drafted message, sign the closure and handle an exhausted reroute list, plus the steps automation cannot do (recording a patient's call-back, a consent withdrawal, a transfer decision, a rebooking, a corrected contact, a reopened case, and the specialist's own accept or reject). In the literature-anchored scenario only 55% of orchestrated-arm cases receive the orchestrated process (`programme_reach`); the rest stay manual, so the programme-level reduction is diluted by construction and the in-reach figure is the one that describes the automation itself.

**Result.** In the literature-anchored scenario the programme-level reduction is 19.7%, 95% CI 19.1% to 20.2%: the 40% target is **not met**. Among the cases the programme reaches it is 36.0%, 95% CI 34.9% to 36.9% (target not met); per closed case it is 37.5%, 95% CI 37.0% to 38.1%. In the upper-range scenario the reduction is 40.0%, 95% CI 39.0% to 40.9% (target not met; the lower end of the interval is 39.0%); per closed case it is 61.9%, 95% CI 61.3% to 62.5%.

**What drives the remaining minutes.** In the literature-anchored scenario the orchestrated arm needs 45.8 human minutes per case against 57.1 at baseline. The largest orchestrated contributors are specialist review of referrals (7.4 min per case), coordinator alternate-route contacts (5.0 min per case), hand-over to a clinician (4.2 min per case), chasing patients (3.9 min per case). Escalation handling alone (accepting, phoning, approving) is 11.1 minutes per case, because the orchestrated arm escalates far more than today's process ever does; the orchestrated arm also takes 10.6 automated actions per case that the baseline does not do at all, so the reduction is in people's time, not in the work done. 8.9 human actions per case remain, many of them short. The time also moves between roles: coordinator minutes change by -36% but owner-clinician minutes by +94% (accepting escalations, calling, approving and signing are clinician decisions) and specialist minutes by +23%; whether that is cheaper depends on wages, which are not in the model.

**How far to trust the minutes.** 66% of baseline minutes and 63% of orchestrated minutes rest on a published minutes figure from an adjacent setting (care coordinators and navigators in Australia and the United States); none was measured in the Abu Dhabi pathway, and none is graded `sourced` (a direct measurement of the same task in the same population). The rest (34% of baseline and 37% of orchestrated minutes) is assumed. Moving every figure to its low or its high end together barely changes the reduction, because it is a ratio, but the relative sizes matter: the table above shows how far individual figures move it, and the least favourable corner (baseline-only actions at their low end, orchestrated-only actions at their high end) gives -41%. Three modelling choices carry most of the weight: the baseline's chasing (without it the reduction is 12.0%, and baseline chasing was 10.3 minutes per case), whether a person still records every booking in the orchestrated arm (if so, 12.1%), and whether the human escalation outreach is a phone call or a drafted message that a person only approves (if only approved, 31.5%). All three are flagged for confirmation with the engine ticket (A21).

**Limits.** Human minutes are not capacity: there is no queue, no shift pattern and no shared workload, so a coordinator who accepts three escalations in an hour is not slowed down (see the capacity caveat). The orchestrated escalation volume is a simulator output (the SLA timers breach often), and a real programme could reduce it by batching or by tuning the ladder. The simulator has no model-drafted messages (every message is a pre-approved template), so the approval charge is a proxy placed on the human-contact messages. Time spent by patients, the endoscopy unit's procedure and reception work, and management or training time are outside the count.

### Human minutes per case by role

| Measure | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Coordinator: minutes per case | 46.6 (46.4 to 46.7) | 29.8 (29.5 to 30.0) | 13.9 (13.5 to 14.2) |
| Owner clinician: minutes per case | 4.5 (4.4 to 4.5) | 8.7 (8.6 to 8.8) | 11.7 (11.6 to 11.9) |
| Specialist: minutes per case | 6.1 (6.0 to 6.1) | 7.4 (7.4 to 7.5) | 8.7 (8.6 to 8.7) |
| Coordinator: reduction | - | 36.1% (35.6% to 36.6%) | 70.2% (69.4% to 71.0%) |
| Owner clinician: reduction | - | -94.1% (-97.4% to -90.8%) | -162.6% (-168.0% to -157.1%) |
| Specialist: reduction | - | -22.6% (-23.3% to -21.9%) | -43.3% (-44.6% to -42.0%) |

### How robust is the reduction

| Variant (labour reduction, minutes per case, point estimate) | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|
| Headline (the table above) | +19.7% | +40.0% |
| Baseline chasing not costed (only logged events count in the baseline) | +12.0% | +26.7% |
| Baseline chases harder (up to 5 attempts, every 7 days) | +24.0% | +47.5% |
| Specialist minutes left out (coordinator and owner clinician only) | +24.7% | +49.9% |
| Orchestrated arm: a person still records every booking | +12.1% | +26.5% |
| No charge for approving drafts (a phone call needs no approval) | +22.2% | +44.0% |
| Escalation outreach is an approved drafted message, not a phone call (calls cost no minutes) | +31.5% | +59.4% |
| Every minutes figure at its low end | +18.9% | +38.6% |
| Every minutes figure at its high end | +19.7% | +40.1% |
| Least favourable corner: baseline-only actions at their low end, orchestrated-only at their high end | -40.9% | -61.6% |

The five minutes figures that move the literature anchored reduction most, one at a time (central 19.7%):

| Minutes figure (top 5 by swing) | Grade | Low / central / high (min) | Reduction at low | Reduction at high |
|---|---|---|---|---|
| Coordinator contacts the patient by the alternate route (`O_COORDINATOR_CALL`) | sourced-adjacent | 3.9 / 7.2 / 12.1 | 23.7% | 13.8% |
| Look up the positive result, review the chart, open a work item (`M_RESULT_LOOKUP`) | assumed | 3 / 5 / 14 | 18.4% | 24.5% |
| Chase a patient who has not answered (phone call, up to N attempts) (`M_CHASE_PATIENT`) | sourced-adjacent | 3.9 / 7.2 / 12.1 | 17.1% | 22.9% |
| Specialist reviews a referral and accepts or rejects it (`X_SPECIALIST_REVIEW`) | sourced-adjacent | 4.75 / 9.5 / 15.7 | 22.1% | 16.9% |
| Send or fax the referral to the endoscopy service (`M_REFERRAL_SEND`) | sourced-adjacent | 6.2 / 12.4 / 18.6 | 17.0% | 22.1% |

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
| `O_RECORD_REFUSAL` Counsel a patient who declines and record the informed refusal (A33) | owner clinician | orchestrated | 15 (8 to 25) | assumed | assumed: 15 minutes for the conversation and the counselling record (the sign-off is charged as O_SIGN_CLOSURE) |
| `O_CONSENT_REVIEW` Review a consent withdrawal | owner clinician | orchestrated | 5 (3 to 10) | assumed | assumed: 5 minutes |
| `O_CONTACT_RECOVERED` Find and record a corrected contact for an unreachable patient | coordinator | orchestrated | 5 (3 to 10) | assumed | assumed: 5 minutes |
| `O_TRANSFER_DECISION` Receiving clinician decides on a transfer of the case after a provider switch | owner clinician | orchestrated | 5 (3 to 9.5) | assumed | assumed: 5 minutes; E11-1 'accepting/discussing referral' (9.5) is the upper bound |

Work that today's process never does (so it costs the baseline nothing, and the orchestrated arm does it by machine): appointment reminders before the colonoscopy; noticing a missed appointment and rebooking it; overdue tracking against working-day SLAs and the escalation ladder; noticing a rejected referral and rerouting it (only a share of rejections is noticed today); recording a consent withdrawal and stopping outreach; merging duplicate results and resolving conflicting ownership claims; surfacing a lost case to a human.

## Calibration check: does the baseline reproduce published completion?

The baseline arm is the unmanaged process. Its completion curve was tuned (parameters with status `calibrated`) to the Mohl et al. 2023 curve for positive stool tests in US health systems. The orchestrated arm is compared with the actively managed Kaiser Permanente curve (Corley et al. 2017), which is the nearest published ceiling. The baseline is the same in both scenarios.

| Follow-up | Published unmanaged (Mohl 2023) | Simulated baseline | Gap | Published managed (Corley 2017) | Simulated orchestrated, literature anchored | Simulated orchestrated, upper range |
|---|---|---|---|---|---|---|
| 90 days | 43.0% | 42.8% (42.4% to 43.1%) | -0.2pp | 74.2% | 60.5% (60.1% to 60.8%) | 78.2% (77.9% to 78.6%) |
| 180 days | 51.0% | 51.0% (50.5% to 51.5%) | +0.0pp | 80.6% | 66.8% (66.3% to 67.3%) | 82.7% (82.3% to 83.1%) |
| 360 days | 56.0% | 54.5% (53.9% to 55.0%) | -1.5pp | 83.2% | 68.7% (68.1% to 69.2%) | 83.0% (82.6% to 83.4%) |

The baseline lands within 1.5 percentage points of the published curve at every horizon shown.
Among baseline completers the simulated time to colonoscopy is median 48 days (IQR 32 to 81). The Sea Mar FQHC figure in the parameter file (83 days, IQR 52 to 131) describes a slower, lower-completion setting (22.9% at 90 days); Mohl's higher 90-day completion implies a faster completer median, so the simulator follows the Mohl completion curve and does not try to match both.

### Orchestrated-to-baseline completion ratio against the navigation risk ratio

| Follow-up | Literature anchored | Upper range | Pooled navigation RR (J Gen Intern Med 2026) |
|---|---|---|---|
| 90 days | 1.41 | 1.83 | 1.27 |
| 180 days | 1.31 | 1.62 | 1.27 |
| 360 days | 1.26 | 1.52 | 1.27 |

The upper-range ratio at 180 days is 1.62 against a pooled risk ratio of 1.27 for patient navigation in published trials. Orchestration here bundles ownership, timers, escalation and human outreach, and the ratio is larger than the navigation figure because the unmanaged baseline loses many cases to missed hand-offs that a managed programme prevents; it is the mechanism ceiling and is bounded above by the managed-programme ceiling. The literature-anchored scenario holds the effect near the navigation figure (ratio 1.31 at 180 days) by reaching only part of the case load (`orchestration.programme_reach`, calibrated in B12 and not refitted after B21), and is the scenario to quote. That ratio is calibrated to the navigation trials, so it is not independent evidence of effect.

## Fairness: completion and time to closure by language and channel (literature anchored)

### Language

| Language | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| ar | 51.5% (50.7% to 52.3%) | 58.7 (58.0 to 59.6) | 67.4% (66.7% to 68.1%) | 52.0 (51.4 to 52.6) |
| en | 59.5% (58.7% to 60.4%) | 55.7 (55.0 to 56.5) | 70.8% (70.1% to 71.5%) | 51.3 (50.7 to 52.0) |
| other | 52.5% (51.5% to 53.4%) | 59.5 (58.5 to 60.6) | 67.8% (67.0% to 68.7%) | 51.8 (51.1 to 52.6) |

Censoring-aware closure time by language (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Language | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| ar | 272.8 (241.6 to 305.1) | 220.6 (218.6 to 222.6) | 72.5 (71.5 to 73.4) | 162.9 (160.8 to 165.0) |
| en | 119.8 (114.0 to 125.3) | 194.5 (192.5 to 196.9) | 67.7 (66.3 to 69.1) | 151.8 (149.5 to 153.9) |
| other | 248.3 (223.5 to 273.8) | 218.0 (215.4 to 220.8) | 72.5 (70.3 to 74.5) | 161.8 (159.1 to 164.6) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.9% to 9.8%) | 4.4% (3.6% to 5.1%) | -4.5pp (-5.4pp to -3.6pp) |
| Median days to closure (among closed cases only), max gap between groups | 5.1 (3.8 to 6.2) | 2.7 (2.1 to 3.3) | -2.4 (-3.4 to -1.2) |
| Kaplan-Meier median days to closure, max gap between groups | 151.9 (130.1 to 172.8) | 8.4 (6.8 to 10.1) | -144.7 (-166.6 to -120.9) |
| Restricted mean days to closure, max gap between groups | 28.5 (26.3 to 31.1) | 14.5 (12.1 to 17.0) | -14.0 (-16.1 to -12.0) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| letter | 54.5% (52.9% to 55.8%) | 58.3 (56.4 to 60.3) | 67.7% (66.4% to 69.0%) | 56.5 (55.5 to 57.4) |
| phone | 54.2% (53.2% to 55.1%) | 57.8 (56.8 to 59.0) | 69.2% (68.2% to 70.4%) | 50.3 (49.4 to 51.2) |
| sms | 54.7% (54.1% to 55.4%) | 57.1 (56.4 to 57.8) | 68.7% (68.1% to 69.4%) | 51.2 (50.7 to 51.7) |
| whatsapp | 54.4% (53.5% to 55.3%) | 57.9 (57.2 to 58.8) | 68.8% (68.1% to 69.5%) | 50.5 (50.0 to 51.1) |

Censoring-aware closure time by preferred contact channel (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Preferred contact channel | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| letter | 206.5 (172.8 to 243.3) | 210.9 (207.1 to 215.0) | 76.4 (74.5 to 78.2) | 164.4 (160.7 to 168.1) |
| phone | 204.4 (183.2 to 227.2) | 211.8 (209.2 to 214.6) | 67.9 (65.9 to 70.1) | 155.6 (152.4 to 159.0) |
| sms | 201.6 (182.7 to 222.1) | 210.0 (207.5 to 212.4) | 70.4 (69.0 to 71.8) | 158.5 (156.4 to 160.5) |
| whatsapp | 208.6 (190.5 to 230.7) | 211.7 (209.9 to 214.0) | 69.7 (68.6 to 70.8) | 158.1 (156.4 to 159.7) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.0% (4.3% to 5.6%) | 4.3% (3.4% to 5.4%) | -0.7pp (-1.7pp to +0.6pp) |
| Median days to closure (among closed cases only), max gap between groups | 5.6 (4.3 to 6.9) | 7.2 (6.4 to 8.0) | +1.6 (+0.2 to +2.8) |
| Kaplan-Meier median days to closure, max gap between groups | 88.6 (67.2 to 110.9) | 11.4 (9.8 to 12.9) | -77.2 (-99.6 to -55.6) |
| Restricted mean days to closure, max gap between groups | 13.8 (11.0 to 16.4) | 14.1 (10.4 to 17.5) | +0.3 (-4.0 to +5.0) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Fairness: completion and time to closure by language and channel (upper range)

### Language

| Language | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| ar | 51.5% (50.7% to 52.3%) | 58.7 (58.0 to 59.6) | 82.8% (82.3% to 83.4%) | 48.9 (48.6 to 49.3) |
| en | 59.5% (58.7% to 60.4%) | 55.7 (55.0 to 56.5) | 83.0% (82.5% to 83.6%) | 48.9 (48.6 to 49.2) |
| other | 52.5% (51.5% to 53.4%) | 59.5 (58.5 to 60.6) | 83.5% (82.9% to 84.2%) | 48.4 (47.9 to 48.9) |

Censoring-aware closure time by language (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Language | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| ar | 272.8 (241.6 to 305.1) | 220.6 (218.6 to 222.6) | 54.8 (54.3 to 55.3) | 107.0 (105.3 to 108.8) |
| en | 119.8 (114.0 to 125.3) | 194.5 (192.5 to 196.9) | 54.6 (54.0 to 55.2) | 106.6 (104.8 to 108.3) |
| other | 248.3 (223.5 to 273.8) | 218.0 (215.4 to 220.8) | 53.6 (52.9 to 54.2) | 104.5 (102.4 to 106.4) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.9% to 9.8%) | 2.4% (1.9% to 2.9%) | -6.5pp (-7.6pp to -5.3pp) |
| Median days to closure (among closed cases only), max gap between groups | 5.1 (3.8 to 6.2) | 1.4 (1.0 to 1.8) | -3.7 (-4.9 to -2.4) |
| Kaplan-Meier median days to closure, max gap between groups | 151.9 (130.1 to 172.8) | 2.6 (2.2 to 3.0) | -149.2 (-171.4 to -126.2) |
| Restricted mean days to closure, max gap between groups | 28.5 (26.3 to 31.1) | 7.1 (5.4 to 8.9) | -21.4 (-24.8 to -17.8) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| letter | 54.5% (52.9% to 55.8%) | 58.3 (56.4 to 60.3) | 81.3% (80.2% to 82.4%) | 55.3 (54.6 to 56.1) |
| phone | 54.2% (53.2% to 55.1%) | 57.8 (56.8 to 59.0) | 83.8% (82.8% to 84.8%) | 46.7 (46.0 to 47.3) |
| sms | 54.7% (54.1% to 55.4%) | 57.1 (56.4 to 57.8) | 83.2% (82.8% to 83.6%) | 48.6 (48.2 to 49.0) |
| whatsapp | 54.4% (53.5% to 55.3%) | 57.9 (57.2 to 58.8) | 83.3% (82.7% to 83.9%) | 47.4 (47.0 to 47.8) |

Censoring-aware closure time by preferred contact channel (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Preferred contact channel | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| letter | 206.5 (172.8 to 243.3) | 210.9 (207.1 to 215.0) | 61.4 (60.5 to 62.2) | 116.6 (113.6 to 119.7) |
| phone | 204.4 (183.2 to 227.2) | 211.8 (209.2 to 214.6) | 51.6 (50.8 to 52.5) | 102.0 (98.7 to 105.1) |
| sms | 201.6 (182.7 to 222.1) | 210.0 (207.5 to 212.4) | 54.0 (53.4 to 54.7) | 105.5 (104.1 to 107.0) |
| whatsapp | 208.6 (190.5 to 230.7) | 211.7 (209.9 to 214.0) | 52.4 (52.0 to 53.0) | 104.7 (102.7 to 106.5) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.0% (4.3% to 5.6%) | 4.3% (3.3% to 5.5%) | -0.7pp (-2.0pp to +0.7pp) |
| Median days to closure (among closed cases only), max gap between groups | 5.6 (4.3 to 6.9) | 9.1 (8.3 to 9.9) | +3.5 (+2.0 to +4.9) |
| Kaplan-Meier median days to closure, max gap between groups | 88.6 (67.2 to 110.9) | 10.3 (9.0 to 11.5) | -78.3 (-100.8 to -56.2) |
| Restricted mean days to closure, max gap between groups | 13.8 (11.0 to 16.4) | 17.8 (14.3 to 21.8) | +3.9 (-0.8 to +8.7) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Messy-case handling (literature anchored)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 43.5% (42.3% to 44.8%) | 21.3% (20.1% to 22.6%) | 43.4% (42.3% to 44.8%) | 71.0% (70.4% to 71.5%) |
| Patient switches provider mid-pathway | 55 | 0.0% (0.0% to 0.0%) | 37.6% (34.1% to 40.7%) | 8.4% (7.0% to 9.9%) | 19.1% (16.4% to 21.8%) | 69.6% (69.1% to 70.2%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 53.8% (51.5% to 56.1%) | 53.6% (51.7% to 55.8%) | 67.9% (66.4% to 69.4%) | 68.7% (68.2% to 69.3%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 55.4% (53.8% to 57.2%) | 56.8% (54.6% to 59.0%) | 70.6% (68.7% to 72.7%) | 68.6% (68.1% to 69.1%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 65.1% (60.3% to 69.6%) | 65.0% (61.6% to 68.6%) | 61.8% (58.5% to 64.9%) | 68.8% (68.3% to 69.3%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute. In the literature-anchored scenario, cases outside the programme's reach are handled as today, so the orchestrated handled rates are below 100%.

## Messy-case handling (upper range)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 94.0% (93.2% to 94.9%) | 21.3% (20.1% to 22.6%) | 75.7% (74.5% to 77.0%) | 83.7% (83.3% to 84.1%) |
| Patient switches provider mid-pathway | 55 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 8.4% (7.0% to 9.9%) | 41.5% (37.4% to 45.9%) | 83.5% (83.2% to 83.9%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 53.6% (51.7% to 55.8%) | 81.9% (80.7% to 83.3%) | 83.1% (82.7% to 83.5%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 56.8% (54.6% to 59.0%) | 84.7% (83.3% to 86.2%) | 83.0% (82.6% to 83.3%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 65.0% (61.6% to 68.6%) | 60.4% (56.9% to 64.1%) | 83.4% (83.1% to 83.8%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute.

## Parameters

Parameter files: `simulator/params.yaml`. Status counts: 89 assumed, 19 calibrated, 8 empirical, 13 sourced, 15 sourced-adjacent. `sourced` and `sourced-adjacent` values are from public documents (details and caveats in `docs/evidence.md`); `calibrated` values were tuned to the published baseline curve; `assumed` values have no public source. This table is the upper-range scenario; the literature-anchored scenario changes only the keys listed below it.

| Parameter | Value | Status | Source |
|---|---|---|---|
| `acknowledgement_delay_days` | 2 | assumed | No public measurement of staff time-to-acknowledge a positive result. Policy anchors only: UAE National Guideline cl. 12.3 (result to participant < 15 working days), DoH IFHAS V10 s.4.9.2.1 (results documented within 10 days of initial visit). |
| `appointment.fast_lane_lead_median_days` | 25.4 | calibrated | Calibrated (B21) so the programme-managed configuration (baseline.programme_managed true, no forgotten steps, automatic referral) reproduces the Public Health Scotland KPI 4 bands from the referral (taken as the accept_ownership event) to colonoscopy: 28.8% within 28 days, 70.4% within 56 days (calibration data, B18; OGL v3): simulator/sim/timing_fit.py. https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx |
| `appointment.fast_lane_lead_sigma` | 0.64 | calibrated | Calibrated (B21) with fast_lane_lead_median_days to the Public Health Scotland KPI 4 bands (simulator/sim/timing_fit.py) |
| `appointment.fast_lane_share.managed` | 0.994 | sourced-adjacent | NHS England bowel cancer screening annual report 2024-25 tables, BCSP-S06, OGL v3: 90,888 of 91,396 referrals (99.4%) offered a specialist screening practitioner appointment within 14 days, i.e. a managed programme books essentially every positive promptly. docs/results/calibration.md dataset 3. https://assets.publishing.service.gov.uk/media/6a4e6212f79c9db2eb438f07/Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx |
| `appointment.fast_lane_share.unmanaged` | 0.84 | calibrated | Calibrated (B21) with slow_lane_lead_* so the unmanaged baseline reproduces the Mohl 2023 completion curve (43.3, 51.4, 56.1% at 90, 180, 360 days): simulator/sim/timing_fit.py, docs/results/validation.md |
| `appointment.max_attempts` | 3 | assumed | assumed, no public source |
| `appointment.rebook_lead_median_days` | 7 | assumed | assumed, no public source |
| `appointment.reminder_days_before` | [7, 1] | assumed | assumed, no public source |
| `appointment.responsiveness_noshow_shift` | 1.12 | empirical | Fitted (B18): between-patient SD (tau) of the logit of no-show, random-intercept logistic regression on SMS_received, ln(lead days) and age, 11,734 patients with at least two appointments booked 3+ days ahead (31,322 appointments), Vitoria (Brazil) 2016, OpenML 43439, CC BY-NC-SA 4.0. https://www.openml.org/search?type=data&id=43439 |
| `appointment.slow_lane_lead_median_days` | 49 | calibrated | Calibrated (B21) with fast_lane_share.unmanaged and slow_lane_lead_sigma to the Mohl 2023 completion curve (simulator/sim/timing_fit.py); the slow lane is the unmanaged delay between deciding to book and the procedure (patient deferral, waiting lists, lost bookings recovered late) |
| `appointment.slow_lane_lead_sigma` | 0.84 | calibrated | Calibrated (B21) with slow_lane_lead_median_days to the Mohl 2023 completion curve (simulator/sim/timing_fit.py) |
| `baseline.channel_share.letter` | 0.5 | assumed | assumed, no public source |
| `baseline.channel_share.sms` | 0.5 | assumed | assumed, no public source |
| `baseline.default_language` | en | assumed | assumed, no public source |
| `baseline.noshow_self_rebook_median_days` | 30 | assumed | assumed, no public source |
| `baseline.noshow_self_rebook_prob` | 0.45 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.30, no public source |
| `baseline.programme_managed` | False | assumed | Design choice (B21): false = US usual care (no programme books the colonoscopy; the setting the Mohl 2023 calibration describes); true = a managed national programme that issues the referral and books it (Scotland, England), which raises the fast-lane share to appointment.fast_lane_share.managed |
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
| `orchestration.refusal_path_share` | 0.0 | assumed | Design choice, off by default (A33, ADR-042). 0 keeps the committed headline results as they were; simulator/params.refusal.yaml (make evaluate-refusal) sets 1.0 |
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
| `reopen.resume_state` | reopened | assumed | config/pathways/colorectal_fit.yaml: the pathway's reopen transition (closed -> reopened, then clinician_review and a fresh close) |
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
