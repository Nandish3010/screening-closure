# Evaluation results: baseline vs orchestrated

generated_at: 2026-10-18 08:32:14 UTC (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)

All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.

- Seed 20261017, 20 replications x 2,000 cases x 360 days of follow-up, 1000 bootstrap resamples
- Parameters hash `3d2d984bb4cb25aa` (upper range), `edb40020bea5b7b7` (literature anchored), pathway `colorectal_fit` v0.2 hash `610903ae1b24a5de`, git commit `3ff0b0212406`
- Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).

## Headline: baseline vs the two orchestrated scenarios

There are two orchestrated scenarios and one baseline. **Quote the literature-anchored scenario.** It holds the orchestrated arm to what published studies report: the completion effect equals the pooled patient-navigation risk ratio of 1.27, SMS reminders add nothing beyond the no-reminder attendance rate (the lower bound of the published range), and escalating to a supervisor or coordinator is no more effective than re-contacting the patient. In it, 66% of patients reach a confirmatory colonoscopy within 180 days against 52% at baseline (ratio 1.27). To hold the average effect to the published figure the scenario lets the orchestrated process reach only 55% of cases, so 20% of cases are still lost without a human being told (programme level; among the cases it reaches the figure is 0%); that is a device for calibrating the average effect, not a prediction of how many cases a real programme would miss. **The upper-range scenario shows the mechanism ceiling**: what ownership, timers, escalation and re-contact could deliver if every case got the full orchestrated process and every escalation worked as assumed. It reaches 80% within 180 days (ratio 1.53), far above anything published for navigation, so read it as an upper bound on the mechanism and not as a forecast. Both scenarios use the same synthetic patients, the same random numbers and the same baseline arm; the baseline was calibrated to published unmanaged completion, and the only orchestrated-arm fit is the reach parameter, set so the literature-anchored 180-day ratio equals the navigation risk ratio.

| Metric | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 58.7% (58.3% to 59.2%) | 74.5% (74.1% to 74.8%) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 66.3% (65.7% to 66.8%) | 79.6% (79.3% to 80.0%) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 67.9% (67.3% to 68.4%) | 79.9% (79.5% to 80.2%) |
| Completion ratio vs baseline at 180 days (navigation RR for comparison: 1.27) | 1.00 | 1.27 | 1.53 |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 46.0% (45.6% to 46.4%) | 29.6% (29.2% to 30.0%) |
| Cases closed (evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 67.0% (66.4% to 67.5%) | 79.8% (79.5% to 80.2%) |
| Time to safe closure, median days (among closed cases only) | 70.3 (70.0 to 70.6) | 63.8 (63.6 to 64.1) | 60.0 (59.8 to 60.4) |
| Time to safe closure, Kaplan-Meier median days (censoring-aware) | 187.6 (171.2 to 206.6) | 81.5 (80.9 to 82.1) | 66.1 (65.8 to 66.4) |
| Time to safe closure, restricted mean days (censoring-aware) | 213.5 (212.0 to 214.9) | 167.9 (166.5 to 169.3) | 124.0 (122.9 to 125.0) |
| Time to first patient contact, median days | 12.4 (12.2 to 12.6) | 10.9 (10.8 to 10.9) | 10.0 (10.0 to 10.1) |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 32.0% (31.5% to 32.6%) | 20.1% (19.8% to 20.5%) |
| Lost AND never surfaced to a human: programme level (all cases) | 44.5% (44.0% to 45.1%) | 20.2% (19.8% to 20.6%) | 0.0% (0.0% to 0.0%) |
| Lost AND never surfaced to a human: in reach (cases the programme reached) | 44.5% (44.0% to 45.2%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 32.7% (32.1% to 33.2%) | 20.2% (19.9% to 20.6%) |
| Outreach after consent withdrawn: programme level (all cases) | 0.4% (0.3% to 0.5%) | 0.2% (0.1% to 0.2%) | 0.0% (0.0% to 0.0%) |
| Outreach after consent withdrawn: in reach (cases the programme reached) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Escalations per 100 cases (cost nothing in the model, see capacity caveat) | 0.00 (0.00 to 0.00) | 119.42 (117.92 to 121.02) | 205.21 (203.79 to 206.71) |
| Escalation acted-on rate (within 5 working days) | n/a | 43.5% (42.9% to 44.0%) | 47.1% (46.7% to 47.5%) |

**Lost and never surfaced, and outreach after consent withdrawn: two denominators each.** The programme-level rows count every case. The in-reach rows count only the cases the programme reached (`orchestration.programme_reach`; in the baseline column, the same patients under today's process, so the two columns compare like with like). In the orchestrated arm both in-reach rows are 0% by construction: a lost or unreachable case in reach is flagged to a human and the consent ledger blocks outreach after a withdrawal. The programme-level figures follow from the `programme_reach` assumption (0.55 in the literature-anchored scenario): cases outside the reach are handled as today and can still be lost silently or contacted after a withdrawal, so those figures are not a measured property of the orchestration.

Metric definitions (numerator, denominator, clocks, censoring) are in [`metrics.md`](metrics.md).

## Capacity caveat: escalations cost nothing in this model

**Every gain above is a gain before capacity constraints.** Cases are independent in the simulator: there are no shared endoscopy slots, no staff workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however many others are open. The orchestrated arm raises about 205 escalations per 100 cases in the upper-range scenario (119 in the literature-anchored one), roughly one for every case. A real programme needs people and colonoscopy slots to work them, and the completion and time gains would shrink if they are not there. A capacity model (staff time per escalation, slot supply, queueing) is future work; until it exists, use `escalations_per_100_cases` to size a pilot rather than the completion gains. The staff minutes an escalation takes are counted in the Human effort section, but still without any queue or shared capacity. Definitions: [`metrics.md`](metrics.md).

## Summary

**Literature anchored.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 66% of patients to a confirmatory colonoscopy within 180 days, against 52% at baseline (+14.2 percentage points, 95% CI +13.8 to +14.5, which excludes zero). At 90 days 46% of orchestrated cases were still open against 62% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 64 days orchestrated against 70 at baseline; that conditional median only covers the 53% of baseline cases that closed at all (67% orchestrated), so an arm that closes more slow cases looks slower on it. Counting the cases that never closed as censored at the end of follow-up instead of dropping them, the Kaplan-Meier median is 82 days orchestrated against 188 at baseline and the restricted mean time to closure within 360 days is 168 against 213 days. 45% of baseline cases were lost without anyone being told, against 20% in the orchestrated arm at programme level; among the 55% of cases the programme reached the orchestrated figure is 0% against 44% for the same patients at baseline, and the programme-level remainder is cases outside the programme's reach, which are handled as today.

**Upper range.** Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 80% of patients to a confirmatory colonoscopy within 180 days, against 52% at baseline (+27.5 percentage points, 95% CI +27.0 to +28.0, which excludes zero). At 90 days 30% of orchestrated cases were still open against 62% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 60 days orchestrated against 70 at baseline; that conditional median only covers the 53% of baseline cases that closed at all (80% orchestrated), so an arm that closes more slow cases looks slower on it. Counting the cases that never closed as censored at the end of follow-up instead of dropping them, the Kaplan-Meier median is 66 days orchestrated against 188 at baseline and the restricted mean time to closure within 360 days is 124 against 213 days. 45% of baseline cases were lost without anyone being told, against 0% in the orchestrated arm at programme level, where lost or unreachable cases are flagged to a human and never closed automatically. These results come from a model, not a trial: 104 of 132 parameters are assumed or calibrated rather than measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml` or `simulator/params.conservative.yaml`.

## Arm comparison: literature anchored scenario (the one to quote)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 58.7% (58.3% to 59.2%) | +16.0pp (+15.7pp to +16.3pp) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 66.3% (65.7% to 66.8%) | +14.2pp (+13.8pp to +14.5pp) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 67.9% (67.3% to 68.4%) | +12.7pp (+12.3pp to +13.0pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.6% (99.5% to 99.7%) | -0.2pp (-0.2pp to -0.1pp) |
| Cases still open at 60 days | 82.0% (81.8% to 82.3%) | 71.6% (71.2% to 72.1%) | -10.4pp (-10.7pp to -10.1pp) |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 46.0% (45.6% to 46.4%) | -16.4pp (-16.7pp to -16.1pp) |
| Time to safe closure, median (among closed cases only) | 70.3 (70.0 to 70.6) | 63.8 (63.6 to 64.1) | -6.5 (-6.8 to -6.2) |
| Time to safe closure, p90 (among closed cases only) | 143.2 (140.4 to 146.2) | 108.6 (107.5 to 109.9) | -34.7 (-36.8 to -32.7) |
| Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up) | 187.6 (171.2 to 206.6) | 81.5 (80.9 to 82.1) | -106.0 (-125.1 to -91.1) |
| Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware) | 213.5 (212.0 to 214.9) | 167.9 (166.5 to 169.3) | -45.6 (-46.6 to -44.5) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 67.0% (66.4% to 67.5%) | +13.6pp (+13.2pp to +14.0pp) |
| Time to named owner, median | n/a | 5.0 (4.9 to 5.0) | n/a |
| Time to named owner, p90 | n/a | 8.7 (8.5 to 8.9) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 10.9 (10.8 to 10.9) | -1.5 (-1.6 to -1.4) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 20.1 (20.0 to 20.3) | -3.1 (-3.2 to -2.9) |
| Time to colonoscopy, median (among completers only) | 61.2 (60.8 to 61.6) | 55.6 (55.4 to 55.8) | -5.6 (-5.8 to -5.3) |
| Time to colonoscopy, Kaplan-Meier median (censoring-aware) | 143.6 (135.3 to 153.6) | 71.5 (71.0 to 72.0) | -72.1 (-82.1 to -63.8) |
| Time to colonoscopy, restricted mean (censoring-aware) | 203.8 (202.3 to 205.4) | 159.8 (158.3 to 161.2) | -44.0 (-45.0 to -43.1) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.0% (2.8% to 3.1%) | -0.0pp (-0.1pp to +0.1pp) |
| Escalation acted-on rate (within 5 working days) | n/a | 43.5% (42.9% to 44.0%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 119.42 (117.92 to 121.02) | +119.42 (+117.83 to +121.00) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 32.0% (31.5% to 32.6%) | -12.5pp (-12.9pp to -12.2pp) |
| Lost AND never surfaced to a human, programme level (all cases) | 44.5% (44.0% to 45.1%) | 20.2% (19.8% to 20.6%) | -24.3pp (-24.7pp to -23.9pp) |
| Lost AND never surfaced to a human, in reach (cases the programme reached) | 44.4% (43.9% to 45.1%) | 0.0% (0.0% to 0.0%) | -44.4pp (-45.1pp to -43.9pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 32.7% (32.1% to 33.2%) | -13.1pp (-13.5pp to -12.7pp) |
| Outreach sent after consent withdrawn, programme level (all cases) | 0.4% (0.3% to 0.5%) | 0.2% (0.1% to 0.2%) | -0.2pp (-0.3pp to -0.2pp) |
| Outreach sent after consent withdrawn, in reach (cases the programme reached) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |
| Cases within the programme's reach (denominator of the in-reach rows) | 54.7% (54.2% to 55.2%) | 54.7% (54.2% to 55.2%) | +0.0pp (+0.0pp to +0.0pp) |

## Arm comparison: upper range scenario (mechanism ceiling)

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 74.5% (74.1% to 74.8%) | +31.8pp (+31.4pp to +32.1pp) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 79.6% (79.3% to 80.0%) | +27.5pp (+27.0pp to +28.0pp) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 79.9% (79.5% to 80.2%) | +24.7pp (+24.1pp to +25.1pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.3% (99.3% to 99.4%) | -0.4pp (-0.5pp to -0.4pp) |
| Cases still open at 60 days | 82.0% (81.8% to 82.3%) | 60.1% (59.5% to 60.6%) | -22.0pp (-22.5pp to -21.5pp) |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 29.6% (29.2% to 30.0%) | -32.8pp (-33.3pp to -32.4pp) |
| Time to safe closure, median (among closed cases only) | 70.3 (70.0 to 70.6) | 60.0 (59.8 to 60.4) | -10.3 (-10.7 to -9.9) |
| Time to safe closure, p90 (among closed cases only) | 143.2 (140.4 to 146.2) | 91.0 (90.5 to 91.4) | -52.3 (-55.4 to -49.4) |
| Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up) | 187.6 (171.2 to 206.6) | 66.1 (65.8 to 66.4) | -121.5 (-140.6 to -106.5) |
| Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware) | 213.5 (212.0 to 214.9) | 124.0 (122.9 to 125.0) | -89.5 (-90.8 to -88.2) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 79.8% (79.5% to 80.2%) | +26.4pp (+25.9pp to +27.0pp) |
| Time to named owner, median | n/a | 5.0 (5.0 to 5.0) | n/a |
| Time to named owner, p90 | n/a | 8.6 (8.5 to 8.7) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 10.0 (10.0 to 10.1) | -2.4 (-2.5 to -2.2) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 15.4 (15.3 to 15.5) | -7.8 (-8.1 to -7.6) |
| Time to colonoscopy, median (among completers only) | 61.2 (60.8 to 61.6) | 51.8 (51.5 to 52.1) | -9.4 (-9.8 to -9.0) |
| Time to colonoscopy, Kaplan-Meier median (censoring-aware) | 143.6 (135.3 to 153.6) | 57.5 (57.1 to 57.9) | -86.1 (-96.0 to -77.5) |
| Time to colonoscopy, restricted mean (censoring-aware) | 203.8 (202.3 to 205.4) | 117.4 (116.3 to 118.4) | -86.4 (-87.6 to -85.0) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.1% (2.9% to 3.2%) | +0.1pp (-0.1pp to +0.2pp) |
| Escalation acted-on rate (within 5 working days) | n/a | 47.1% (46.7% to 47.5%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 205.21 (203.79 to 206.71) | +205.21 (+203.83 to +206.62) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 20.1% (19.8% to 20.5%) | -24.4pp (-24.9pp to -23.9pp) |
| Lost AND never surfaced to a human, programme level (all cases) | 44.5% (44.0% to 45.1%) | 0.0% (0.0% to 0.0%) | -44.5pp (-45.2pp to -44.0pp) |
| Lost AND never surfaced to a human, in reach (cases the programme reached) | 44.5% (44.0% to 45.2%) | 0.0% (0.0% to 0.0%) | -44.5pp (-45.2pp to -44.0pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 20.2% (19.9% to 20.6%) | -25.5pp (-26.0pp to -25.0pp) |
| Outreach sent after consent withdrawn, programme level (all cases) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |
| Outreach sent after consent withdrawn, in reach (cases the programme reached) | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |
| Cases within the programme's reach (denominator of the in-reach rows) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | +0.0pp (+0.0pp to +0.0pp) |

Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. `n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.

## Human effort

Human minutes per case in each arm and the reduction automation buys, from the event logs of the same replications as the rest of this report (95% CIs are percentile bootstrap over replications; the reduction is paired by replication). Definitions: [`metrics.md`](metrics.md) section 11. Sources and grades of every minutes figure: [`evidence.md`](../evidence.md) section 11.

| Measure | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Human minutes per case | 57.3 (57.1 to 57.5) | 45.5 (45.3 to 45.8) | 33.7 (33.4 to 34.1) |
| Human minutes per closed case | 107.4 (106.5 to 108.3) | 68.0 (67.3 to 68.7) | 42.3 (41.7 to 42.9) |
| Human actions per case | 9.72 (9.68 to 9.76) | 9.03 (8.96 to 9.10) | 7.88 (7.77 to 7.98) |
| Automation share (automated actions / all actions) | 0.0% (0.0% to 0.0%) | 59.1% (58.9% to 59.4%) | 74.9% (74.7% to 75.1%) |
| **Labour reduction**, minutes per case (1 - orchestrated / baseline) | - | 20.5% (20.0% to 21.0%) | 41.1% (40.3% to 41.9%) |
| Labour reduction, minutes per closed case | - | 36.6% (36.1% to 37.2%) | 60.6% (60.2% to 61.1%) |
| Reduction in human actions per case | - | 7.1% (6.1% to 7.9%) | 18.9% (17.6% to 20.1%) |
| Among the cases the programme reaches: human minutes per case | 57.3 (57.0 to 57.6) | 35.8 (35.4 to 36.2) | 33.7 (33.4 to 34.1) |
| Among the cases the programme reaches: **labour reduction** | - | 37.5% (36.5% to 38.5%) | 41.1% (40.4% to 41.9%) |

**What is counted.** Staff minutes per case in each arm, computed from the simulated event log (`simulator/sim/effort.py`; no outcome in this report depends on it). A person's time is counted only when a person acts. Today's process is manual at every step (look up and acknowledge the result, phone or write to the patient, send the referral, book, record that the patient called back, review the report, file the closure), and the model adds the chasing that leaves no event in the log (up to 3 attempts every 14 days when a patient, a referral or a colonoscopy is overdue). In the orchestrated process the engine does the following by machine and they cost no human minutes: acknowledge on ingest; owner auto-assignment; pre-approved template outreach and reminders; evidence detection; closure proposal; referral acceptance carries the appointment; reroute down a provider list; bounce; overdue; escalation emission; reply handling; consent ledger; delivery tracking; de-duplication; state machine. A person acts only to accept an escalation, approve a drafted message, sign the closure and handle an exhausted reroute list, plus the steps automation cannot do (recording a patient's call-back, a consent withdrawal, a transfer decision, a rebooking, a corrected contact, a reopened case, and the specialist's own accept or reject). In the literature-anchored scenario only 55% of orchestrated-arm cases receive the orchestrated process (`programme_reach`); the rest stay manual, so the programme-level reduction is diluted by construction and the in-reach figure is the one that describes the automation itself.

**Result.** In the literature-anchored scenario the programme-level reduction is 20.5%, 95% CI 20.0% to 21.0%: the 40% target is **not met**. Among the cases the programme reaches it is 37.5%, 95% CI 36.5% to 38.5% (target not met); per closed case it is 36.6%, 95% CI 36.1% to 37.2%. In the upper-range scenario the reduction is 41.1%, 95% CI 40.3% to 41.9% (target met; the lower end of the interval is 40.3%); per closed case it is 60.6%, 95% CI 60.2% to 61.1%.

**What drives the remaining minutes.** In the literature-anchored scenario the orchestrated arm needs 45.5 human minutes per case against 57.3 at baseline. The largest orchestrated contributors are specialist review of referrals (7.3 min per case), coordinator alternate-route contacts (4.4 min per case), hand-over to a clinician (4.2 min per case), chasing patients (3.9 min per case). Escalation handling alone (accepting, phoning, approving) is 10.8 minutes per case, because the orchestrated arm escalates far more than today's process ever does; the orchestrated arm also takes 13.1 automated actions per case that the baseline does not do at all, so the reduction is in people's time, not in the work done. 9.0 human actions per case remain, many of them short. The time also moves between roles: coordinator minutes change by -37% but owner-clinician minutes by +96% (accepting escalations, calling, approving and signing are clinician decisions) and specialist minutes by +20%; whether that is cheaper depends on wages, which are not in the model.

**How far to trust the minutes.** 66% of baseline minutes and 62% of orchestrated minutes rest on a published minutes figure from an adjacent setting (care coordinators and navigators in Australia and the United States); none was measured in the Abu Dhabi pathway, and none is graded `sourced` (a direct measurement of the same task in the same population). The rest (34% of baseline and 38% of orchestrated minutes) is assumed. Moving every figure to its low or its high end together barely changes the reduction, because it is a ratio, but the relative sizes matter: the table above shows how far individual figures move it, and the least favourable corner (baseline-only actions at their low end, orchestrated-only actions at their high end) gives -40%. Three modelling choices carry most of the weight: the baseline's chasing (without it the reduction is 13.1%, and baseline chasing was 10.3 minutes per case), whether a person still records every booking in the orchestrated arm (if so, 14.2%), and whether the human escalation outreach is a phone call or a drafted message that a person only approves (if only approved, 30.9%). All three are flagged for confirmation with the engine ticket (A21).

**Limits.** Human minutes are not capacity: there is no queue, no shift pattern and no shared workload, so a coordinator who accepts three escalations in an hour is not slowed down (see the capacity caveat). The orchestrated escalation volume is a simulator output (the SLA timers breach often), and a real programme could reduce it by batching or by tuning the ladder. The simulator has no model-drafted messages (every message is a pre-approved template), so the approval charge is a proxy placed on the human-contact messages. Time spent by patients, the endoscopy unit's procedure and reception work, and management or training time are outside the count.

### Human minutes per case by role

| Measure | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|---|
| Coordinator: minutes per case | 46.7 (46.5 to 46.9) | 29.4 (29.2 to 29.7) | 13.4 (13.1 to 13.7) |
| Owner clinician: minutes per case | 4.5 (4.4 to 4.5) | 8.8 (8.7 to 8.9) | 12.0 (11.9 to 12.1) |
| Specialist: minutes per case | 6.1 (6.0 to 6.2) | 7.3 (7.2 to 7.4) | 8.3 (8.3 to 8.4) |
| Coordinator: reduction | - | 37.0% (36.5% to 37.5%) | 71.3% (70.6% to 72.0%) |
| Owner clinician: reduction | - | -96.4% (-99.6% to -93.4%) | -168.1% (-173.0% to -163.1%) |
| Specialist: reduction | - | -19.5% (-20.2% to -18.9%) | -36.5% (-37.7% to -35.5%) |

### How robust is the reduction

| Variant (labour reduction, minutes per case, point estimate) | Orchestrated, literature anchored | Orchestrated, upper range |
|---|---|---|
| Headline (the table above) | +20.5% | +41.1% |
| Baseline chasing not costed (only logged events count in the baseline) | +13.1% | +28.3% |
| Baseline chases harder (up to 5 attempts, every 7 days) | +24.8% | +48.4% |
| Specialist minutes left out (coordinator and owner clinician only) | +25.3% | +50.4% |
| Orchestrated arm: a person still records every booking | +14.2% | +29.5% |
| No charge for approving drafts (a phone call needs no approval) | +22.7% | +44.7% |
| Escalation outreach is an approved drafted message, not a phone call (calls cost no minutes) | +30.9% | +58.2% |
| Every minutes figure at its low end | +19.9% | +40.0% |
| Every minutes figure at its high end | +20.2% | +40.7% |
| Least favourable corner: baseline-only actions at their low end, orchestrated-only at their high end | -40.4% | -62.7% |

The five minutes figures that move the literature anchored reduction most, one at a time (central 20.5%):

| Minutes figure (top 5 by swing) | Grade | Low / central / high (min) | Reduction at low | Reduction at high |
|---|---|---|---|---|
| Coordinator contacts the patient by the alternate route (`O_COORDINATOR_CALL`) | sourced-adjacent | 3.9 / 7.2 / 12.1 | 24.0% | 15.3% |
| Look up the positive result, review the chart, open a work item (`M_RESULT_LOOKUP`) | assumed | 3 / 5 / 14 | 19.3% | 25.2% |
| Chase a patient who has not answered (phone call, up to N attempts) (`M_CHASE_PATIENT`) | sourced-adjacent | 3.9 / 7.2 / 12.1 | 18.0% | 23.7% |
| Coordinator accepts an escalation or a watchdog flag (`O_ACCEPT_ESC_COORDINATOR`) | assumed | 1 / 2 / 4 | 22.3% | 17.0% |
| Send or fax the referral to the endoscopy service (`M_REFERRAL_SEND`) | sourced-adjacent | 6.2 / 12.4 / 18.6 | 17.8% | 22.9% |

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
| 90 days | 43.0% | 42.7% (42.3% to 43.1%) | -0.3pp | 74.2% | 58.7% (58.3% to 59.2%) | 74.5% (74.1% to 74.8%) |
| 180 days | 51.0% | 52.1% (51.5% to 52.6%) | +1.1pp | 80.6% | 66.3% (65.7% to 66.8%) | 79.6% (79.3% to 80.0%) |
| 360 days | 56.0% | 55.2% (54.6% to 55.8%) | -0.8pp | 83.2% | 67.9% (67.3% to 68.4%) | 79.9% (79.5% to 80.2%) |

The baseline lands within 1.1 percentage points of the published curve at every horizon shown.
Among baseline completers the simulated time to colonoscopy is median 61 days (IQR 47 to 85). The Sea Mar FQHC figure in the parameter file (83 days, IQR 52 to 131) describes a slower, lower-completion setting (22.9% at 90 days); Mohl's higher 90-day completion implies a faster completer median, so the simulator follows the Mohl completion curve and does not try to match both.

### Orchestrated-to-baseline completion ratio against the navigation risk ratio

| Follow-up | Literature anchored | Upper range | Pooled navigation RR (J Gen Intern Med 2026) |
|---|---|---|---|
| 90 days | 1.37 | 1.74 | 1.27 |
| 180 days | 1.27 | 1.53 | 1.27 |
| 360 days | 1.23 | 1.45 | 1.27 |

The upper-range ratio at 180 days is 1.53 against a pooled risk ratio of 1.27 for patient navigation in published trials. Orchestration here bundles ownership, timers, escalation and human outreach, and the ratio is larger than the navigation figure because the unmanaged baseline loses many cases to missed hand-offs that a managed programme prevents; it is the mechanism ceiling and is bounded above by the managed-programme ceiling. The literature-anchored scenario caps the effect at the navigation figure (ratio 1.27 at 180 days) by reaching only part of the case load (`orchestration.programme_reach`, calibrated), and is the scenario to quote.

## Fairness: completion and time to closure by language and channel (literature anchored)

### Language

| Language | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| ar | 52.1% (51.3% to 52.8%) | 70.9 (70.3 to 71.5) | 66.4% (65.6% to 67.2%) | 64.0 (63.4 to 64.5) |
| en | 60.4% (59.5% to 61.2%) | 69.1 (68.4 to 69.7) | 70.3% (69.5% to 71.0%) | 63.9 (63.5 to 64.4) |
| other | 53.2% (52.3% to 54.2%) | 72.1 (71.1 to 73.2) | 66.8% (65.8% to 67.9%) | 63.8 (63.3 to 64.2) |

Censoring-aware closure time by language (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Language | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| ar | 274.4 (240.1 to 304.7) | 223.5 (221.5 to 225.6) | 83.9 (82.7 to 85.0) | 172.7 (170.4 to 174.8) |
| en | 111.2 (107.9 to 114.6) | 196.8 (194.7 to 199.1) | 78.5 (77.6 to 79.3) | 160.0 (158.2 to 161.9) |
| other | 220.8 (201.3 to 240.3) | 220.3 (217.9 to 222.8) | 83.0 (81.2 to 84.8) | 171.3 (168.1 to 174.1) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.8% to 9.9%) | 4.9% (4.0% to 5.9%) | -3.9pp (-4.7pp to -3.2pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.2 (3.3 to 5.2) | 1.8 (1.3 to 2.3) | -2.5 (-3.6 to -1.4) |
| Kaplan-Meier median days to closure, max gap between groups | 160.6 (136.7 to 183.9) | 7.7 (6.5 to 9.0) | -153.7 (-175.9 to -131.1) |
| Restricted mean days to closure, max gap between groups | 28.1 (25.6 to 30.6) | 15.6 (13.4 to 18.1) | -12.5 (-14.9 to -10.3) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| letter | 55.4% (53.9% to 56.8%) | 70.8 (69.6 to 72.0) | 67.2% (66.0% to 68.5%) | 67.5 (66.5 to 68.5) |
| phone | 54.7% (53.6% to 55.7%) | 70.9 (70.0 to 71.9) | 68.2% (66.9% to 69.4%) | 63.2 (62.3 to 64.2) |
| sms | 55.4% (54.6% to 56.1%) | 69.9 (69.3 to 70.5) | 67.9% (67.3% to 68.6%) | 63.6 (63.3 to 64.0) |
| whatsapp | 55.1% (54.2% to 56.0%) | 70.3 (69.5 to 71.1) | 67.9% (67.0% to 68.7%) | 63.1 (62.6 to 63.5) |

Censoring-aware closure time by preferred contact channel (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Preferred contact channel | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| letter | 172.2 (148.1 to 201.2) | 212.8 (209.1 to 216.8) | 85.0 (83.4 to 86.6) | 171.8 (168.3 to 175.4) |
| phone | 186.8 (165.2 to 209.8) | 215.1 (212.3 to 218.1) | 80.1 (78.5 to 81.7) | 165.9 (162.3 to 169.6) |
| sms | 175.3 (160.3 to 192.0) | 212.8 (210.6 to 215.2) | 81.2 (80.2 to 82.3) | 167.6 (165.3 to 169.6) |
| whatsapp | 185.6 (168.7 to 204.3) | 214.0 (212.0 to 216.0) | 81.3 (80.2 to 82.4) | 167.6 (165.8 to 169.4) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.1% (4.2% to 6.0%) | 4.5% (3.5% to 5.8%) | -0.6pp (-1.8pp to +0.8pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.4 (3.7 to 5.2) | 5.4 (4.3 to 6.4) | +1.0 (-0.4 to +2.4) |
| Kaplan-Meier median days to closure, max gap between groups | 77.8 (62.3 to 95.7) | 7.7 (6.6 to 9.0) | -70.1 (-85.5 to -54.6) |
| Restricted mean days to closure, max gap between groups | 13.9 (11.1 to 16.5) | 13.3 (10.3 to 16.5) | -0.5 (-4.0 to +3.5) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Fairness: completion and time to closure by language and channel (upper range)

### Language

| Language | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| ar | 52.1% (51.3% to 52.8%) | 70.9 (70.3 to 71.5) | 79.5% (79.1% to 80.0%) | 60.2 (59.8 to 60.7) |
| en | 60.4% (59.5% to 61.2%) | 69.1 (68.4 to 69.7) | 80.2% (79.4% to 80.9%) | 60.2 (59.8 to 60.6) |
| other | 53.2% (52.3% to 54.2%) | 72.1 (71.1 to 73.2) | 80.0% (79.1% to 80.9%) | 59.5 (59.0 to 60.0) |

Censoring-aware closure time by language (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Language | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| ar | 274.4 (240.1 to 304.7) | 223.5 (221.5 to 225.6) | 66.4 (65.9 to 66.9) | 125.1 (123.7 to 126.4) |
| en | 111.2 (107.9 to 114.6) | 196.8 (194.7 to 199.1) | 66.1 (65.5 to 66.6) | 122.9 (121.0 to 125.2) |
| other | 220.8 (201.3 to 240.3) | 220.3 (217.9 to 222.8) | 65.4 (64.7 to 66.0) | 123.2 (120.4 to 125.8) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.8% to 9.9%) | 3.2% (2.5% to 3.7%) | -5.7pp (-7.0pp to -4.5pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.2 (3.3 to 5.2) | 1.8 (1.5 to 2.1) | -2.4 (-3.5 to -1.4) |
| Kaplan-Meier median days to closure, max gap between groups | 160.6 (136.7 to 183.9) | 2.5 (2.0 to 3.1) | -158.2 (-180.6 to -134.9) |
| Restricted mean days to closure, max gap between groups | 28.1 (25.6 to 30.6) | 9.2 (7.4 to 10.8) | -19.0 (-22.3 to -15.5) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure (among closed cases only) | Orchestrated completion | Orchestrated median days to closure (among closed cases only) |
|---|---|---|---|---|
| letter | 55.4% (53.9% to 56.8%) | 70.8 (69.6 to 72.0) | 78.9% (78.1% to 79.8%) | 64.5 (63.8 to 65.1) |
| phone | 54.7% (53.6% to 55.7%) | 70.9 (70.0 to 71.9) | 80.5% (79.5% to 81.6%) | 58.4 (57.8 to 58.9) |
| sms | 55.4% (54.6% to 56.1%) | 69.9 (69.3 to 70.5) | 79.9% (79.3% to 80.5%) | 59.9 (59.5 to 60.4) |
| whatsapp | 55.1% (54.2% to 56.0%) | 70.3 (69.5 to 71.1) | 80.0% (79.3% to 80.7%) | 58.9 (58.5 to 59.2) |

Censoring-aware closure time by preferred contact channel (cases not closed by the end of follow-up are censored there, not dropped; `n/a` = fewer than half the group closed).

| Preferred contact channel | Baseline Kaplan-Meier median days | Baseline restricted mean days | Orchestrated Kaplan-Meier median days | Orchestrated restricted mean days |
|---|---|---|---|---|
| letter | 172.2 (148.1 to 201.2) | 212.8 (209.1 to 216.8) | 71.2 (70.5 to 71.9) | 130.0 (127.6 to 132.5) |
| phone | 186.8 (165.2 to 209.8) | 215.1 (212.3 to 218.1) | 64.0 (63.2 to 64.7) | 120.6 (116.8 to 123.9) |
| sms | 175.3 (160.3 to 192.0) | 212.8 (210.6 to 215.2) | 66.0 (65.4 to 66.6) | 123.8 (121.9 to 125.5) |
| whatsapp | 185.6 (168.7 to 204.3) | 214.0 (212.0 to 216.0) | 65.0 (64.7 to 65.3) | 123.0 (120.9 to 124.9) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.1% (4.2% to 6.0%) | 4.2% (3.4% to 5.1%) | -0.9pp (-2.0pp to +0.4pp) |
| Median days to closure (among closed cases only), max gap between groups | 4.4 (3.7 to 5.2) | 6.5 (5.7 to 7.4) | +2.1 (+1.1 to +3.1) |
| Kaplan-Meier median days to closure, max gap between groups | 77.8 (62.3 to 95.7) | 7.6 (6.7 to 8.5) | -70.2 (-86.1 to -54.4) |
| Restricted mean days to closure, max gap between groups | 13.9 (11.1 to 16.5) | 14.4 (11.1 to 17.6) | +0.6 (-3.2 to +4.5) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Messy-case handling (literature anchored)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 44.5% (43.2% to 45.9%) | 21.5% (20.3% to 22.7%) | 44.3% (42.9% to 45.9%) | 70.0% (69.4% to 70.5%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 48.7% (46.5% to 50.8%) | 14.4% (12.7% to 16.0%) | 38.0% (35.7% to 40.3%) | 68.7% (68.2% to 69.3%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 53.8% (51.5% to 56.1%) | 54.0% (51.9% to 56.1%) | 66.2% (64.8% to 67.6%) | 68.0% (67.4% to 68.5%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 55.4% (53.8% to 57.2%) | 58.2% (56.2% to 60.3%) | 70.4% (68.5% to 72.4%) | 67.8% (67.2% to 68.3%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 63.7% (58.2% to 68.6%) | 66.0% (62.2% to 70.0%) | 64.4% (60.7% to 67.9%) | 67.9% (67.4% to 68.5%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute. In the literature-anchored scenario, cases outside the programme's reach are handled as today, so the orchestrated handled rates are below 100%.

## Messy-case handling (upper range)

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 91.0% (89.9% to 92.1%) | 21.5% (20.3% to 22.7%) | 73.9% (72.1% to 75.7%) | 80.4% (80.0% to 80.8%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 14.4% (12.7% to 16.0%) | 63.8% (60.8% to 66.9%) | 80.2% (79.9% to 80.6%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 54.0% (51.9% to 56.1%) | 78.8% (77.6% to 80.3%) | 79.9% (79.5% to 80.3%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 58.2% (56.2% to 60.3%) | 81.4% (79.7% to 83.0%) | 79.8% (79.4% to 80.2%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 66.0% (62.2% to 70.0%) | 62.9% (58.7% to 67.0%) | 80.1% (79.8% to 80.5%) |

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
| `orchestration.programme_reach` | 1.0 | 0.55 | calibrated | Calibrated so the orchestrated/baseline completion ratio at 180 days equals navigation_effect = 1.27 (pooled RR 1.27, 95% CI 1.10-1.46, 8 navigation studies, moderate certainty, J Gen Intern Med 2026, PMID 42618855, https://doi.org/10.1007/s11606-026-10706-x; 12-month RCT: 55.1% vs 42.1%, Annals 2025, https://pmc.ncbi.nlm.nih.gov/articles/PMC13007779/). Fit of 2026-10-18 (refit after A18 milestones and A19 coalescing): 0.55 gives a literature-anchored 180-day completion ratio of 1.272 (66.3% against 52.1%; 0.54 gives 1.268, 0.56 gives 1.276). Procedure and fitted ratios: docs/results/results.md. |
| `patient_response.human_dropout_residual_multiplier` | 0.6 | 0.25 | assumed | Capped at patient_response.dropout_residual_multiplier (0.25): a human call does not re-engage a disengaged patient better than a digital re-contact. |
| `sms_reminder_effect` | 0.25 | 0.0 | sourced-adjacent | Lower bound of the sensitivity range in docs/evidence.md section 4: Mahmud et al., JAMA Netw Open 2021 (753 patients, nine text messages, no effect on attendance with good prep, 53.1% vs 54.4%, P=.73). https://doi.org/10.1001/jamanetworkopen.2020.34553 |
