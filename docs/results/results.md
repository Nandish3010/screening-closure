# Evaluation results: baseline vs orchestrated

generated_at: 2026-10-18 03:42:58 UTC (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)

All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.

- Seed 20261017, 20 replications x 2,000 cases x 360 days of follow-up, 1000 bootstrap resamples
- Parameters hash `3d2d984bb4cb25aa` (upper range), `b2caa28b05979d15` (literature anchored), pathway `colorectal_fit` v0.2 hash `610903ae1b24a5de`, git commit `7d07a7424218`
- Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).

## Headline: baseline vs the two orchestrated scenarios

There are two orchestrated scenarios and one baseline. **Quote the literature-anchored scenario.** It holds the orchestrated arm to what published studies report: the completion effect equals the pooled patient-navigation risk ratio of 1.27, SMS reminders add nothing beyond the no-reminder attendance rate (the lower bound of the published range), and escalating to a supervisor or coordinator is no more effective than re-contacting the patient. In it, 65% of patients reach a confirmatory colonoscopy within 180 days against 52% at baseline (ratio 1.25). To hold the average effect to the published figure the scenario lets the orchestrated process reach only 50% of cases, so 22% of cases are still lost without a human being told; that is a device for calibrating the average effect, not a prediction of how many cases a real programme would miss. **The upper-range scenario shows the mechanism ceiling**: what ownership, timers, escalation and re-contact could deliver if every case got the full orchestrated process and every escalation worked as assumed. It reaches 79% within 180 days (ratio 1.52), far above anything published for navigation, so read it as an upper bound on the mechanism and not as a forecast. Both scenarios use the same synthetic patients, the same random numbers and the same baseline arm; the baseline was calibrated to published unmanaged completion, and the only orchestrated-arm fit is the reach parameter, set so the literature-anchored 180-day ratio equals the navigation risk ratio.

| Metric | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 57.2% (56.8% to 57.6%) | 74.2% (73.9% to 74.6%) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 64.9% (64.4% to 65.4%) | 79.3% (78.9% to 79.6%) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 66.6% (66.1% to 67.1%) | 79.5% (79.1% to 79.8%) |
| Completion ratio vs baseline at 180 days (navigation RR for comparison: 1.27) | 1.00 | 1.25 | 1.52 |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 47.6% (47.2% to 48.0%) | 29.8% (29.4% to 30.2%) |
| Cases closed (evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 65.7% (65.1% to 66.2%) | 79.5% (79.1% to 79.8%) |
| Time to safe closure, median days (closed cases only) | 70.3 (70.0 to 70.6) | 64.2 (63.9 to 64.6) | 60.0 (59.7 to 60.3) |
| Time to first patient contact, median days | 12.4 (12.2 to 12.6) | 10.9 (10.9 to 11.0) | 10.0 (10.0 to 10.1) |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 33.3% (32.8% to 33.8%) | 20.5% (20.2% to 20.9%) |
| Lost AND never surfaced to a human | 44.5% (44.0% to 45.1%) | 22.5% (22.0% to 22.9%) | 0.0% (0.0% to 0.0%) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 33.9% (33.4% to 34.5%) | 20.6% (20.2% to 20.9%) |
| Escalations per 100 cases (cost nothing in the model, see capacity caveat) | 0.00 (0.00 to 0.00) | 111.47 (109.68 to 113.32) | 211.63 (210.06 to 213.36) |
| Escalation precision (acted on within 5 working days) | n/a | 43.3% (42.8% to 43.8%) | 46.6% (46.2% to 47.0%) |

Metric definitions (numerator, denominator, clocks, censoring) are in [`metrics.md`](metrics.md).

## Capacity caveat: escalations cost nothing in this model

**Every gain above is a gain before capacity constraints.** Cases are independent in the simulator: there are no shared endoscopy slots, no staff workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however many others are open. The orchestrated arm raises about 212 escalations per 100 cases in the upper-range scenario (111 in the literature-anchored one), roughly one for every case. A real programme needs people and colonoscopy slots to work them, and the completion and time gains would shrink if they are not there. A capacity model (staff time per escalation, slot supply, queueing) is future work; until it exists, use `escalations_per_100_cases` to size a pilot rather than the completion gains. Definitions: [`metrics.md`](metrics.md).

## Summary

**Literature anchored.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 65% of patients to a confirmatory colonoscopy within 180 days, against 52% at baseline (+12.8 percentage points, 95% CI +12.5 to +13.1, which excludes zero). At 90 days 48% of orchestrated cases were still open against 62% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 64 days orchestrated against 70 at baseline; the baseline figure only covers the 53% of cases that closed at all, so read it together with the closure share (66% orchestrated). 45% of baseline cases were lost without anyone being told, against 22% in the orchestrated arm, where lost or unreachable cases that receive the orchestrated process are flagged to a human and never closed automatically; the remainder are cases outside the programme's reach, which are handled as today.

**Upper range.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 79% of patients to a confirmatory colonoscopy within 180 days, against 52% at baseline (+27.2 percentage points, 95% CI +26.7 to +27.6, which excludes zero). At 90 days 30% of orchestrated cases were still open against 62% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 60 days orchestrated against 70 at baseline; the baseline figure only covers the 53% of cases that closed at all, so read it together with the closure share (79% orchestrated). 45% of baseline cases were lost without anyone being told, against 0% in the orchestrated arm, where lost or unreachable cases that receive the orchestrated process are flagged to a human and never closed automatically. These results come from a model, not a trial: 104 of 132 parameters are assumed or calibrated rather than measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml` or `simulator/params.conservative.yaml`.

## Arm comparison: literature anchored scenario (the one to quote)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 57.2% (56.8% to 57.6%) | +14.5pp (+14.3pp to +14.8pp) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 64.9% (64.4% to 65.4%) | +12.8pp (+12.5pp to +13.1pp) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 66.6% (66.1% to 67.1%) | +11.4pp (+11.1pp to +11.7pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.6% (99.5% to 99.7%) | -0.2pp (-0.2pp to -0.1pp) |
| Cases still open at 60 days | 82.0% (81.8% to 82.3%) | 72.6% (72.2% to 73.0%) | -9.4pp (-9.8pp to -9.1pp) |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 47.6% (47.2% to 48.0%) | -14.8pp (-15.1pp to -14.6pp) |
| Time to safe closure, median (closed cases only) | 70.3 (70.0 to 70.6) | 64.2 (63.9 to 64.6) | -6.1 (-6.4 to -5.8) |
| Time to safe closure, p90 (closed cases only) | 143.2 (140.4 to 146.2) | 110.3 (108.9 to 111.8) | -33.0 (-34.9 to -31.1) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 65.7% (65.1% to 66.2%) | +12.2pp (+11.9pp to +12.6pp) |
| Time to named owner, median | n/a | 5.0 (4.9 to 5.0) | n/a |
| Time to named owner, p90 | n/a | 8.7 (8.5 to 8.9) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 10.9 (10.9 to 11.0) | -1.4 (-1.6 to -1.3) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 20.4 (20.2 to 20.6) | -2.8 (-3.0 to -2.6) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.0% (2.8% to 3.1%) | +0.0pp (-0.1pp to +0.1pp) |
| Escalation precision (acted on within 5 working days) | n/a | 43.3% (42.8% to 43.8%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 111.47 (109.68 to 113.32) | +111.47 (+109.71 to +113.38) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 33.3% (32.8% to 33.8%) | -11.3pp (-11.6pp to -11.0pp) |
| Lost AND never surfaced to a human | 44.5% (44.0% to 45.1%) | 22.5% (22.0% to 22.9%) | -22.1pp (-22.5pp to -21.7pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 33.9% (33.4% to 34.5%) | -11.8pp (-12.1pp to -11.5pp) |
| Outreach sent after consent withdrawn | 0.4% (0.3% to 0.5%) | 0.2% (0.2% to 0.2%) | -0.2pp (-0.2pp to -0.1pp) |

## Arm comparison: upper range scenario (mechanism ceiling)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 74.2% (73.9% to 74.6%) | +31.5pp (+31.1pp to +31.9pp) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 79.3% (78.9% to 79.6%) | +27.2pp (+26.7pp to +27.6pp) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 79.5% (79.1% to 79.8%) | +24.3pp (+23.8pp to +24.7pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.3% (99.3% to 99.4%) | -0.4pp (-0.5pp to -0.4pp) |
| Cases still open at 60 days | 82.0% (81.8% to 82.3%) | 60.1% (59.6% to 60.7%) | -21.9pp (-22.4pp to -21.4pp) |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 29.8% (29.4% to 30.2%) | -32.6pp (-33.1pp to -32.1pp) |
| Time to safe closure, median (closed cases only) | 70.3 (70.0 to 70.6) | 60.0 (59.7 to 60.3) | -10.3 (-10.7 to -10.0) |
| Time to safe closure, p90 (closed cases only) | 143.2 (140.4 to 146.2) | 90.6 (90.1 to 91.2) | -52.6 (-55.7 to -49.8) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 79.5% (79.1% to 79.8%) | +26.1pp (+25.5pp to +26.6pp) |
| Time to named owner, median | n/a | 5.0 (5.0 to 5.0) | n/a |
| Time to named owner, p90 | n/a | 8.6 (8.5 to 8.7) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 10.0 (10.0 to 10.1) | -2.4 (-2.5 to -2.2) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 15.4 (15.3 to 15.5) | -7.8 (-8.1 to -7.6) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.0% (2.9% to 3.2%) | +0.1pp (-0.1pp to +0.2pp) |
| Escalation precision (acted on within 5 working days) | n/a | 46.6% (46.2% to 47.0%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 211.63 (210.06 to 213.36) | +211.63 (+210.06 to +213.26) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 20.5% (20.2% to 20.9%) | -24.0pp (-24.5pp to -23.5pp) |
| Lost AND never surfaced to a human | 44.5% (44.0% to 45.1%) | 0.0% (0.0% to 0.0%) | -44.5pp (-45.2pp to -44.0pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 20.6% (20.2% to 20.9%) | -25.2pp (-25.7pp to -24.6pp) |
| Outreach sent after consent withdrawn | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |

Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. `n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.

## Calibration check: does the baseline reproduce published completion?

The baseline arm is the unmanaged process. Its completion curve was tuned (parameters with status `calibrated`) to the Mohl et al. 2023 curve for positive stool tests in US health systems. The orchestrated arm is compared with the actively managed Kaiser Permanente curve (Corley et al. 2017), which is the nearest published ceiling. The baseline is the same in both scenarios.

| Follow-up | Published unmanaged (Mohl 2023) | Simulated baseline | Gap | Published managed (Corley 2017) | Simulated orchestrated, literature anchored | Simulated orchestrated, upper range |
|---|---|---|---|---|---|---|
| 90 days | 43.0% | 42.7% (42.3% to 43.1%) | -0.3pp | 74.2% | 57.2% (56.8% to 57.6%) | 74.2% (73.9% to 74.6%) |
| 180 days | 51.0% | 52.1% (51.5% to 52.6%) | +1.1pp | 80.6% | 64.9% (64.4% to 65.4%) | 79.3% (78.9% to 79.6%) |
| 360 days | 56.0% | 55.2% (54.6% to 55.8%) | -0.8pp | 83.2% | 66.6% (66.1% to 67.1%) | 79.5% (79.1% to 79.8%) |

The baseline lands within 1.1 percentage points of the published curve at every horizon shown.
Among baseline completers the simulated time to colonoscopy is median 61 days (IQR 47 to 85). The Sea Mar FQHC figure in the parameter file (83 days, IQR 52 to 131) describes a slower, lower-completion setting (22.9% at 90 days); Mohl's higher 90-day completion implies a faster completer median, so the simulator follows the Mohl completion curve and does not try to match both.

### Orchestrated-to-baseline completion ratio against the navigation risk ratio

| Follow-up | Literature anchored | Upper range | Pooled navigation RR (J Gen Intern Med 2026) |
|---|---|---|---|
| 90 days | 1.34 | 1.74 | 1.27 |
| 180 days | 1.25 | 1.52 | 1.27 |
| 360 days | 1.21 | 1.44 | 1.27 |

The upper-range ratio at 180 days is 1.52 against a pooled risk ratio of 1.27 for patient navigation in published trials. Orchestration here bundles ownership, timers, escalation and human outreach, and the ratio is larger than the navigation figure because the unmanaged baseline loses many cases to missed hand-offs that a managed programme prevents; it is the mechanism ceiling and is bounded above by the managed-programme ceiling. The literature-anchored scenario caps the effect at the navigation figure (ratio 1.25 at 180 days) by reaching only part of the case load (`orchestration.programme_reach`, calibrated), and is the scenario to quote.

## Fairness: completion and time to closure by language and channel (literature anchored)

### Language

| Language | Baseline completion | Baseline median days to closure | Orchestrated completion | Orchestrated median days to closure |
|---|---|---|---|---|
| ar | 52.1% (51.3% to 52.8%) | 70.9 (70.3 to 71.5) | 65.0% (64.2% to 65.7%) | 64.4 (63.9 to 64.9) |
| en | 60.4% (59.5% to 61.2%) | 69.1 (68.4 to 69.7) | 69.4% (68.7% to 70.1%) | 64.2 (63.7 to 64.6) |
| other | 53.2% (52.3% to 54.2%) | 72.1 (71.1 to 73.2) | 65.4% (64.4% to 66.4%) | 64.4 (63.9 to 65.0) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.8% to 9.9%) | 5.5% (4.5% to 6.6%) | -3.4pp (-4.1pp to -2.6pp) |
| Median days to closure, max gap between groups | 4.2 (3.3 to 5.2) | 1.9 (1.4 to 2.4) | -2.4 (-3.3 to -1.4) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure | Orchestrated completion | Orchestrated median days to closure |
|---|---|---|---|---|
| letter | 55.4% (53.9% to 56.8%) | 70.8 (69.6 to 72.0) | 66.2% (64.8% to 67.5%) | 67.8 (66.8 to 68.8) |
| phone | 54.7% (53.6% to 55.7%) | 70.9 (70.0 to 71.9) | 66.9% (65.7% to 68.2%) | 63.7 (62.8 to 64.7) |
| sms | 55.4% (54.6% to 56.1%) | 69.9 (69.3 to 70.5) | 66.7% (66.1% to 67.4%) | 63.9 (63.6 to 64.2) |
| whatsapp | 55.1% (54.2% to 56.0%) | 70.3 (69.5 to 71.1) | 66.6% (65.7% to 67.4%) | 63.5 (62.9 to 64.0) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.1% (4.2% to 6.0%) | 4.7% (3.7% to 5.9%) | -0.4pp (-1.7pp to +1.0pp) |
| Median days to closure, max gap between groups | 4.4 (3.7 to 5.2) | 5.5 (4.4 to 6.4) | +1.1 (-0.4 to +2.4) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Fairness: completion and time to closure by language and channel (upper range)

### Language

| Language | Baseline completion | Baseline median days to closure | Orchestrated completion | Orchestrated median days to closure |
|---|---|---|---|---|
| ar | 52.1% (51.3% to 52.8%) | 70.9 (70.3 to 71.5) | 79.2% (78.7% to 79.7%) | 60.1 (59.7 to 60.6) |
| en | 60.4% (59.5% to 61.2%) | 69.1 (68.4 to 69.7) | 79.8% (79.0% to 80.6%) | 60.1 (59.7 to 60.6) |
| other | 53.2% (52.3% to 54.2%) | 72.1 (71.1 to 73.2) | 79.6% (78.8% to 80.4%) | 59.5 (59.0 to 60.0) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.8% to 9.9%) | 3.1% (2.4% to 3.6%) | -5.8pp (-7.1pp to -4.6pp) |
| Median days to closure, max gap between groups | 4.2 (3.3 to 5.2) | 1.8 (1.5 to 2.1) | -2.4 (-3.5 to -1.4) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure | Orchestrated completion | Orchestrated median days to closure |
|---|---|---|---|---|
| letter | 55.4% (53.9% to 56.8%) | 70.8 (69.6 to 72.0) | 78.6% (77.7% to 79.6%) | 64.4 (63.7 to 65.1) |
| phone | 54.7% (53.6% to 55.7%) | 70.9 (70.0 to 71.9) | 80.0% (79.0% to 81.1%) | 58.3 (57.7 to 58.9) |
| sms | 55.4% (54.6% to 56.1%) | 69.9 (69.3 to 70.5) | 79.5% (79.0% to 80.2%) | 59.9 (59.4 to 60.3) |
| whatsapp | 55.1% (54.2% to 56.0%) | 70.3 (69.5 to 71.1) | 79.5% (78.9% to 80.2%) | 58.8 (58.4 to 59.1) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.1% (4.2% to 6.0%) | 4.2% (3.3% to 5.1%) | -0.9pp (-2.0pp to +0.3pp) |
| Median days to closure, max gap between groups | 4.4 (3.7 to 5.2) | 6.6 (5.7 to 7.4) | +2.2 (+1.1 to +3.1) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Messy-case handling (literature anchored)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 41.1% (39.9% to 42.3%) | 21.5% (20.3% to 22.7%) | 42.8% (41.4% to 44.4%) | 68.8% (68.2% to 69.3%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 43.9% (41.5% to 46.4%) | 14.4% (12.7% to 16.0%) | 35.8% (33.4% to 38.3%) | 67.6% (67.0% to 68.1%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 48.8% (46.5% to 51.1%) | 54.0% (51.9% to 56.1%) | 64.8% (63.2% to 66.5%) | 66.8% (66.2% to 67.3%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 51.0% (49.2% to 53.0%) | 58.2% (56.2% to 60.3%) | 69.1% (67.0% to 71.4%) | 66.5% (66.0% to 67.1%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 57.8% (52.7% to 62.6%) | 66.0% (62.2% to 70.0%) | 64.0% (60.0% to 67.7%) | 66.7% (66.1% to 67.2%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute. In the literature-anchored scenario, cases outside the programme's reach are handled as today, so the orchestrated handled rates are below 100%.

## Messy-case handling (upper range)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 90.4% (89.2% to 91.6%) | 21.5% (20.3% to 22.7%) | 73.4% (71.7% to 75.2%) | 80.0% (79.6% to 80.4%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 14.4% (12.7% to 16.0%) | 63.2% (60.2% to 66.4%) | 79.9% (79.5% to 80.3%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 54.0% (51.9% to 56.1%) | 78.3% (77.0% to 79.8%) | 79.6% (79.2% to 80.0%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 58.2% (56.2% to 60.3%) | 80.9% (79.0% to 82.6%) | 79.4% (79.0% to 79.8%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 66.0% (62.2% to 70.0%) | 62.7% (58.4% to 66.8%) | 79.8% (79.4% to 80.1%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute.

## Parameters

Parameter files: `simulator/params.yaml`. Status counts: 88 assumed, 16 calibrated, 13 sourced, 15 sourced-adjacent. `sourced` and `sourced-adjacent` values are from public documents (details and caveats in `docs/evidence.md`); `calibrated` values were tuned to the published baseline curve; `assumed` values have no public source. This table is the upper-range scenario; the literature-anchored scenario changes only the keys listed below it.

| Parameter | Value | Status | Source |
|---|---|---|---|
| `acknowledgement_delay_days` | 2 | assumed | No public measurement of staff time-to-acknowledge a positive result. Policy anchors only: UAE National Guideline cl. 12.3 (result to participant < 15 working days), DoH IFHAS V10 s.4.9.2.1 (results documented within 10 days of initial visit). |
| `appointment.booking_lead_median_days` | 24 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 14, no public source |
| `appointment.booking_lead_sigma` | 0.6 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.5, no public source |
| `appointment.max_attempts` | 3 | assumed | assumed, no public source |
| `appointment.rebook_lead_median_days` | 7 | assumed | assumed, no public source |
| `appointment.reminder_days_before` | [7, 1] | assumed | assumed, no public source |
| `appointment.responsiveness_noshow_shift` | 0.5 | assumed | assumed, no public source |
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
| `delay_harm_threshold_days` | 270 | sourced-adjacent | Systematic review, Clin Gastroenterol Hepatol 2021 (PMID 33010414): colonoscopy 'should not be delayed beyond 9 months' after positive FIT. https://doi.org/10.1016/j.cgh.2020.09.048 . Corley JAMA 2017: first significant excess at 10-12 months, any CRC OR 1.48 (1.05-2.08), advanced stage OR 1.97 (1.14-3.42); >12 months OR 2.25 and 3.22. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `dna_rate` | 0.119 | sourced-adjacent | Lam et al. 2021 standard-care arm, colonoscopy non-attendance 11.9%. https://doi.org/10.1111/jgh.15218 |
| `eligible_age_max` | 75 | sourced | UAE National Guideline s.3.4 and DoH IFHAS V10 Appendix (40-75). Ages 76-85 decided individually. https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf |
| `eligible_age_min` | 40 | sourced | UAE National Guideline s.3.4 (ages 40-75); DoH IFHAS V10 Appendix (FIT, 40-75 years, annually); ADPHC 24 Sep 2019 (40 and older). https://www.doh.gov.ae/-/media/EE758C3B77314BC394F2479AEF803AA2.ashx |
| `fit_interval_months` | 12 | sourced | UAE National Guideline Standard 2 (FIT every year); DoH IFHAS V10; ADPHC 2019. The earlier 2-year interval (HAAD 2016) was superseded in 2019. |
| `fit_positivity_rate` | 0.07 | sourced-adjacent | Ding et al., Cancers 2022;14(4):1073, pooled positivity 7.28% across 93 studies, https://pubmed.ncbi.nlm.nih.gov/35205821/ ; AltaMed FQHC 7.0%, https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ |
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
| `sms_reminder_effect` | 0.25 | sourced-adjacent | Relative reduction in colonoscopy non-attendance, (11.9 - 8.9) / 11.9, Lam et al. 2021. https://doi.org/10.1111/jgh.15218 |
| `staff.delay_median_days.accept_ownership` | 2 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 1.0, no public source |
| `staff.delay_median_days.book_confirmatory` | 6 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 4.0, no public source |
| `staff.delay_median_days.clinician_review` | 4.0 | assumed | assumed, no public source |
| `staff.delay_median_days.close` | 2.0 | assumed | assumed, no public source |
| `staff.delay_median_days.contact_patient` | 5 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 3.0, no public source |
| `staff.delay_sigma` | 0.8 | assumed | assumed, no public source |
| `staff.forget_prob` | 0.015 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.04, no public source |
| `time_to_colonoscopy_iqr_days` | [52, 131] | sourced-adjacent | Sea Mar FQHC, IQR 52-131 d (same source as the median). https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
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
| `orchestration.programme_reach` | 1.0 | 0.5 | calibrated | Calibrated so the orchestrated/baseline completion ratio at 180 days equals navigation_effect = 1.27 (pooled RR 1.27, 95% CI 1.10-1.46, 8 navigation studies, moderate certainty, J Gen Intern Med 2026, PMID 42618855, https://doi.org/10.1007/s11606-026-10706-x; 12-month RCT: 55.1% vs 42.1%, Annals 2025, https://pmc.ncbi.nlm.nih.gov/articles/PMC13007779/). Procedure and fitted ratios: docs/results/results.md. |
| `patient_response.human_dropout_residual_multiplier` | 0.6 | 0.25 | assumed | Capped at patient_response.dropout_residual_multiplier (0.25): a human call does not re-engage a disengaged patient better than a digital re-contact. |
| `sms_reminder_effect` | 0.25 | 0.0 | sourced-adjacent | Lower bound of the sensitivity range in docs/evidence.md section 4: Mahmud et al., JAMA Netw Open 2021 (753 patients, nine text messages, no effect on attendance with good prep, 53.1% vs 54.4%, P=.73). https://doi.org/10.1001/jamanetworkopen.2020.34553 |
