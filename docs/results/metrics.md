# Metric definitions

What every number in [`results.json`](results.json) and [`results.md`](results.md) means: numerator, denominator, clock, censoring. The
code is `simulator/sim/metrics.py` (`build_record` turns one case's event log into a record, `compute_metrics` turns records into
metrics); the hand-checked arithmetic is in `simulator/sim/scenario.py` and `simulator/tests/test_sim_metrics.py`. A test
(`simulator/tests/test_metrics_doc.py`) fails if a metric key in `results.json` has no heading in this file, if a heading names a key
that does not exist, or if the worked example in section 9 stops matching the code.

> **Capacity caveat: escalations cost nothing in this model.** Cases are independent. There are no shared endoscopy slots, no staff
> workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however many others are open.
> The orchestrated arm raises **about 212 escalations per 100 cases** in the upper-range scenario (see `escalations_per_100_cases`
> below), roughly two for every case. Every completion and time gain reported is therefore a gain **before capacity constraints**, and
> would shrink if the people who must work those escalations, or the colonoscopy slots they ask for, are not there. A capacity model
> (staff time per escalation, slot supply, queueing) is future work. Until it exists, use `escalations_per_100_cases` to size a pilot,
> not the completion gains.

## 1. Conventions that apply to every metric

**Unit of analysis.** One case = one positive FIT result. `n_cases` per replication (2,000 in `make evaluate`). Both arms and both
scenarios see the same patients.

**Clock zero.** Every time is measured in days since the case's own result arrived, which is the timestamp of its `case_created` event
(`records` store "relative days").

**Calendar days versus working days.** All *reported* durations (time to ownership, contact, colonoscopy, closure, and the 30/60/90 and
90/180/360 day horizons) are **calendar days**. Working days (Monday to Friday, no holidays configured, Friday not shortened; the
`working_week` and `calendar.holidays` parameters) enter in three places only, all inside the model rather than in the reported numbers:
(a) the orchestrated arm's SLA timers and escalation ladder are armed in working days, (b) staff actions land on working days, (c) the
escalation-precision window is five **working** days (`metrics.escalation_precision_window_working_days`).

**Follow-up and censoring.** Each case is followed for `follow_up_days` after its own arrival (360 in `make evaluate`); events after that
are not simulated. All cases have the same follow-up, so there is no staggered censoring. A metric that needs a longer horizon than the
follow-up is `null` (`completion_360d` with 200 days of follow-up, `delay_harm_share` with under 270). No metric adjusts for censoring:
a time-to-event is reported only for cases that reached the event within follow-up (so it is conditional and biased toward fast cases),
and a case that is still moving at the end of follow-up is neither completed nor lost (see `lost_to_follow_up_rate`). There is no
Kaplan-Meier estimate and no competing-risks treatment.

**Which events start and stop the clocks.**

| Quantity | Clock starts | Clock stops | First or any occurrence |
|---|---|---|---|
| Time to ownership | `case_created` | `accept_ownership` that names an owner | first |
| Time to first contact | `case_created` | `contact_patient` (the staff action that sends the first outreach; not the patient's reply) | first |
| Time to colonoscopy | `case_created` | `complete_confirmatory` | first |
| Time to closure | `case_created` | `close` (needs `evidence_ref`, `signed_by`, a human actor) | first (a later re-close does not count) |
| Open at day D | `case_created` | a `close` at or before D that has not been undone by a `reopened` at or before D | state at D |
| Escalation precision window | an `escalated` event | five working days later | any action in the window |

**Milestones (A18, ADR-028).** Besides its per-step SLAs, the pathway (`milestones` in `config/pathways/colorectal_fit.yaml`) carries two
clocks that span states and are measured against a national standard. The simulator reads them from the same file as the SLAs.

| Milestone | Clock starts | Met by | Allowance | Source of the figure |
|---|---|---|---|---|
| `referral_requested` | `case_created`, taken as the result release | the first `handoff_requested` (a request; acceptance is not required, and a transfer between owners counts) | 15 working days | DoH IFHAS standard V10, July 2025: referral to secondary care within 15 working days of releasing results ([evidence.md](../evidence.md) section 8) |
| `confirmatory_completed` | the latest `handoff_accepted`; if no referral was accepted, the case entering `confirmatory_booked` | `complete_confirmatory` | 31 working days | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed. 2022, Appendix 2 (colonoscopy within 31 working days of referral). **Not** an IFHAS figure. |

A milestone is breached when its met-by event has not happened by the end of the allowance (the same strict "after" rule as the SLAs, on the
working-day calendar). The breach is recorded once (the API's `mark_overdue` names the milestone instead of a state; the simulator's
`overdue` carries `milestone`) and opens the escalation ladder for the case, in parallel with any ladder already running for the current
step. Meeting the milestone late clears the breach and the ladder; a milestone that is met stays met. The orchestrated arm raises both; the
baseline has no timers, so it never records a breach. The 31-working-day clock is armed when the case enters the step that awaits the
colonoscopy, which the simulator reaches at the same instant as the referral acceptance, so its behaviour is the same as the per-step
colonoscopy SLA it replaced. The two figures are **per-case limits**; the standards are programme-level shares (above 90% acceptable for the
31 days), and no results metric reports the share of cases within either limit. The pre-referral step SLAs (2, 1, 3 and 5 working days) add
up to 11, inside the 15.

**Aggregation.** Each metric is computed per replication (one cohort of 2,000 cases per arm per scenario), then summarised over the 20
replications as `{mean, ci_low, ci_high, n_reps, of_reps}`: the mean of the per-replication values, a percentile bootstrap 95% interval
over replications (1,000 resamples, seeded), and `n_reps`, the number of replications in which the metric was defined (`of_reps` is the
total). Medians and percentiles are therefore **means of per-replication medians**, not the median of pooled cases. `difference` is
orchestrated minus baseline, paired by replication. Shares are fractions in `results.json` (0 to 1) and percentages in `results.md`;
durations are days; percentiles use linear interpolation at position q x (n - 1).

**`null`.** A metric is `null` when it has no data by design (the baseline has no named owner and no escalations), when the horizon is
longer than the follow-up, or when its denominator is empty in a replication (replications with an undefined value are left out of the mean).

**Scenarios.** The definitions are identical in the `upper_range` and `literature_anchored` scenarios and in both arms. In the
literature-anchored scenario only part of the orchestrated arm's cases get the orchestrated process (`orchestration.programme_reach`);
the rest follow baseline rules, so ownership, escalation and visibility metrics are computed over all cases, reached or not.

**Reopening.** A closed case can be reopened once by a late finding (`reopened`, `reopen.after_closure_prob`, about 30 days after closing,
the same in both arms). A reopen moves the case back to `owner_accepted` and it runs the later steps again. It affects the metrics as
follows: a reopened case is **open again** from the reopen onward (`open_30d`, `open_60d`, `open_90d`); it still counts as **closed** in
`closed_share` and in the `reopen_rate` denominator; its **time to closure is the first close**, not the re-close; its **colonoscopy
time and completion are those of the first `complete_confirmatory`**; and for `lost_to_follow_up_rate` its progress after the reopen
counts as progress. A reopen that would fall after the end of follow-up is not observed, so late closures are under-counted in
`reopen_rate`.

## 2. Counts and ownership

### `n_cases`
Number of cases in the replication. Denominator for every share below unless stated otherwise.

### `owned_share`
Cases with a named owner / all cases. A named owner is an `accept_ownership` event whose payload has an `owner`. The baseline's
`accept_ownership` is implicit (`owner: null`), so it has none by design and its value is 0.

### `time_to_ownership_median`, `time_to_ownership_p90`
Median and 90th percentile, over cases that have a named owner, of the days from `case_created` to the first owner-naming
`accept_ownership`. Calendar days. `null` for the baseline. Conditional on being owned within follow-up.

## 3. Contact and colonoscopy

### `time_to_first_contact_median`, `time_to_first_contact_p90`
Median and 90th percentile, over cases that had a first outreach, of the days from `case_created` to the first `contact_patient` event.
This is when staff or the system **initiated** contact in the chosen channel and language, not when the patient received or answered
it. In the baseline it is the single letter or SMS.

### `time_to_colonoscopy_median`, `time_to_colonoscopy_p25`, `time_to_colonoscopy_p75`
Median, 25th and 75th percentile, over cases that completed the confirmatory colonoscopy within follow-up, of the days from
`case_created` to the first `complete_confirmatory`. Completers only, so a slow arm that loses its slow cases looks fast.

### `completion_90d`, `completion_180d`, `completion_360d`
Cases with a first `complete_confirmatory` at or before day D / **all** cases (not only those still active). Lost, refused and
withdrawn cases stay in the denominator. `null` if the follow-up is shorter than D. These are the figures compared with the published
completion curves (Mohl 2023 baseline, Corley 2017 managed) and with the navigation risk ratio.

### `completion_final`
Cases with any `complete_confirmatory` within follow-up / all cases. Same as `completion_360d` when the follow-up is 360 days;
defined for any follow-up length.

### `delay_harm_share`
Cases with **no** colonoscopy by day 270 (never within follow-up, or completed after day 270) / all cases. 270 days is
`delay_harm_threshold_days` (a risk-flag threshold from the literature, not a clinical claim). `null` if the follow-up is shorter than 270 days.

## 4. Closure, open cases and reopening

### `closed_share`
Cases with at least one `close` event within follow-up / all cases. A case closed and later reopened still counts as closed.

### `time_to_closure_median`, `time_to_closure_p90`
Median and 90th percentile, over cases that closed, of the days from `case_created` to the **first** `close`. Conditional on closing:
read it with `closed_share` (the baseline closes about half its cases, the orchestrated arm more, so the two medians describe
different populations). "Safe closure" means the closure rule held: evidence reference, signer and a human actor are checked on
every case log by the invariants (`simulator/sim/invariants.py`).

### `open_30d`, `open_60d`, `open_90d`
Cases **not currently closed** on day D / all cases. A case is closed on day D if a `close` happened at or before D and no `reopened`
for that close happened at or before D. Cases that never close, were closed after D, or were reopened by D are open. `null` if the
follow-up is shorter than D. Note that "open" includes cases that are lost or have been abandoned: it is not "being worked".

### `reopen_rate`
Cases with at least one `reopened` event / cases with at least one `close`. `null` if nothing closed. Equal in both arms by
construction (a late finding arrives regardless of arm); a difference is noise. Reopens after the end of follow-up are not seen.

## 5. Escalations

### `escalations_per_100_cases`
100 x the number of `escalated` events / `n_cases`. An `escalated` event is emitted when a rung of the pathway's escalation ladder
whose action is `escalate` (supervisor rung, 3 working days after an SLA breach; coordinator rung, 7 working days) fires for a case that is
still waiting. The first rung (owner reminder) emits `reminder_sent`, which is **not** counted. One case can breach several times (each
missed SLA, each missed colonoscopy appointment and each missed national timeline, see the milestones in section 1, opens a ladder, and
the ladders run side by side), so more than one escalation per case is possible; the metric is a
count of events per 100 cases, not the share of cases escalated. The baseline has no ladder, so 0. **Escalations cost nothing in the
model, see the capacity caveat at the top: about 212 escalations per 100 cases in the upper-range scenario.**

### `escalation_precision`
Escalations followed by an action within five working days / all escalations (`escalated` events as above; the denominator counts
events, not cases).

- **Window.** For an escalation at relative day e, the window is (e, e'] where e' is e plus five **working** days on the simulation calendar
  (Monday to Friday), keeping the time of day. An escalation on a Thursday has its window end the following Thursday.
- **Action.** Any of: the pathway events `acknowledge`, `accept_ownership`, `contact_patient`, `patient_acknowledge`,
  `book_confirmatory`, `complete_confirmatory`, `clinician_review`, `close`, or `handoff_accepted`, `contact_recovered`,
  `appointment_rebooked`, `consent_review_completed`. Anyone can perform it, **including the patient** (a patient
  acknowledgement counts). `reminder_sent`, `message_sent`, `overdue`, `watchdog_flagged` and the escalation itself do not.
- **What it is not.** It is not causal: the action may have happened without the escalation. It is not censoring-adjusted: an escalation
  near the end of follow-up is scored on whatever part of its window falls inside it, which biases the metric down slightly.
  It does not measure whether the right person was escalated to.
- `null` when there were no escalations (the baseline).

### `automation_abstention_rate`
Share of AI-drafted outreach where the automation abstained and a human wrote the message. **Always `null` for now:** every message is a
template (`draft_source: template`) and the hook (`metrics.AbstentionHook`) waits for the AI cut. Not an estimate of anything.

## 6. Loss and visibility

### `lost_to_follow_up_rate`
Lost cases / all cases. A case is lost to follow-up if it has **no `complete_confirmatory` within follow-up** and either (a) it carries
a `lost_contact` event, or (b) it has had **no pathway progress for 30 days or more** at the end of follow-up
(`metrics.ltfu_idle_days`; progress = the last of the eight pathway events `acknowledge` to `close`, and 0 if there was none, so
follow-up days minus last progress is at least 30). Cases that completed are never lost, even if they never close. A case whose last
progress was in the final 29 days is still "in progress" and is not counted. A `lost_contact` event is a flag, not a verdict: it can be
raised for a patient who later completes (then the case is not lost).

### `silent_loss_rate`
Lost cases that were **never surfaced to a human** / all cases (not / lost cases, so it can never exceed `lost_to_follow_up_rate`).
"Surfaced" means the case log contains at least one of `overdue`, `escalated`, `watchdog_flagged` or `lost_contact` **at any time**
(not necessarily after the case was lost). These are the events by which the system puts a case on a person's list: an SLA breach, an
escalation, an idle-case flag, or an explicit lost-contact flag. The model does not check that a person read the list; it only checks
that the case was put on it. The baseline raises no `overdue`, `escalated` or `watchdog_flagged`, but it does record `lost_contact` when
referral attempts are exhausted, and those cases count as surfaced. In the literature-anchored scenario,
orchestrated-arm cases outside the programme's reach behave like baseline cases, so silent loss is not zero there.

### `consent_breach_rate`
Cases with at least one `message_sent` event **strictly after** the first `consent_updated` with status `withdrawn` / all cases.
`message_sent` includes appointment reminders and every outreach kind. Zero in the orchestrated arm where the consent ledger blocks
outreach (a blocked message is `message_blocked`, which does not count); the baseline has no ledger. Uses event time, so a message in the
same instant as the withdrawal is not a breach.

## 7. Fairness

Groups are the patient's **language** (`ar`, `en`, `other`) and **preferred contact channel** (`sms`, `whatsapp`, `phone`, `letter`) as
drawn for the synthetic patient. They apply to both arms: in the baseline the preferred channel is not used, but the patient is still
in that group. Each group has three statistics, computed per replication.

### `fairness.<dim>.<group>.n`
Cases in the group (`<dim>` is `language` or `channel`).

### `fairness.<dim>.<group>.completion_final`
Cases in the group with a colonoscopy within follow-up / cases in the group (the `completion_final` definition restricted to the group).

### `fairness.<dim>.<group>.closure_median`
Median days to first `close` among the group's closed cases (`time_to_closure_median` restricted to the group); `null` if the group has
no closed case.

### `fairness.language.completion_gap_max`, `fairness.channel.completion_gap_max`
The **max-gap statistic** for completion: among groups with at least `metrics.min_group_size` (30) cases, the largest
`completion_final` minus the smallest, as a share (0.05 = 5 percentage points). `null` with fewer than two eligible groups. It is a
range, not a variance, and **not a significance test**: with small groups it is positive by chance alone. It is computed within each
replication and then averaged, so the reported mean gap is at least as large as the gap between the reported group means. The
`difference` row is the paired change in the gap (orchestrated minus baseline); a negative value means the arm narrowed the gap.

### `fairness.language.closure_median_gap_max_days`, `fairness.channel.closure_median_gap_max_days`
The same statistic for `closure_median`: largest minus smallest group median days to closure, among groups with at least
`metrics.min_group_size` cases and a defined median. In days. Conditional on closing, so it can move in the "wrong" direction when a
change lets slower cases close.

## 8. Messy cases

Five hazards are injected identically into both arms. A case is **affected** by a tag only if the hazard actually occurred in that arm:
`wrong_contact` is a static patient attribute (`wrong_contact_rate`); `provider_switch` counts only if the patient switched before the
colonoscopy was booked (a `provider_switched` event with `applies: true`); `duplicate_result` if a duplicate-result event was logged;
`conflicting_ack` if a second ownership claim was logged (rejected by the state machine in the orchestrated arm, unresolved in the
baseline); `consent_after_booking` if consent was withdrawn with source `after_booking`. `<tag>` below is one of those five.

### `messy.<tag>.n`
Cases affected by the tag in the replication.

### `messy.<tag>.handled_rate`
Affected cases that carry the **specific handling event** / affected cases: `contact_recovered` (wrong contact), `handoff_accepted`
with `kind: transfer` (provider switch), `duplicate_result_merged` (duplicate), `conflicting_ack_rejected` (conflicting claim),
`consent_review_completed` (consent withdrawn). "Handled" means the system logged the handling, not that the outcome was good; the
baseline has no such events, so 0. `null` if no case was affected.

### `messy.<tag>.completion_final`, `messy.<tag>.completion_final_unaffected`
`completion_final` among affected cases, and among cases **not** affected by the tag (the comparison group).

### `messy.<tag>.closed_share`, `messy.<tag>.closure_median`
`closed_share` and `time_to_closure_median` among affected cases.

## 9. Worked example

The five hand-built cases in `simulator/sim/scenario.py` (day 0 is Monday 2026-01-05, every case starts on day 0, follow-up 180 days)
feed the same `build_record` and `compute_metrics` as the engine's logs. Fairness uses `min_group_size = 1` here because the cohort
is five cases.

| Case | Language / channel | Owner | First contact | Colonoscopy | First close | Other |
|---|---|---|---|---|---|---|
| C1 | ar / sms | d1 | d3 | d20 | d25 | consent withdrawn after booking, review at d13 |
| C2 | en / letter | d2 | d5 | d40 | d50 | escalation d10; provider switch handled d8; reopened d95, closed again d120 |
| C3 | ar / phone | d1 | d2 | d60 | d85 | escalation d15; duplicate result merged |
| C4 | other / whatsapp | d4 | d10 | never | never | wrong contact; `lost_contact` flag d60 |
| C5 | en / sms | d1 | d4 | d100 | d110 | conflicting ownership claim rejected |

How the numbers come out:

- `time_to_ownership_median`: owners at 1, 2, 1, 4, 1; sorted 1, 1, 1, 2, 4; the middle value is **1**. `time_to_ownership_p90`: position
  0.9 x 4 = 3.6, so 2 + 0.6 x (4 - 2) = **3.2**.
- `time_to_closure_median`: first closes at 25, 50, 85, 110 (C4 never closed, so it is not in the sample); (50 + 85) / 2 = **67.5**.
  C2's second close at d120 is ignored. `closed_share` = 4 / 5 = **0.8**.
- `open_60d`: C1 (closed d25) and C2 (closed d50, reopened only at d95) are closed; C3, C4, C5 are open: 3 / 5 = **0.6**. `open_90d`: C3
  (d85) is now closed too, C2's reopen at d95 is later: 2 / 5 = **0.4**. Reopen at work: C2 is open again on day 100 (closed d50,
  reopened d95, closed again d120), so it counts as open there although it counts once in `closed_share`.
- `completion_90d`: colonoscopies at 20, 40, 60, 100; three are within 90; 3 / 5 = **0.6**. `completion_180d` = 4 / 5 = **0.8**.
  `completion_360d` is `null` (follow-up 180).
- `reopen_rate`: 1 reopened (C2) of 4 ever closed = **0.25**.
- `escalations_per_100_cases`: 2 escalations over 5 cases = **40**. `escalation_precision`: C2's escalation on d10 (Thursday) has a window to
  five working days later, Thursday d17; the patient acknowledges on d12, inside it, so it counts. C3's escalation on d15 (Tuesday)
  has a window to d22; its next action is d30, outside it. 1 of 2 = **0.5**.
- `lost_to_follow_up_rate`: only C4 has no colonoscopy and a `lost_contact` flag: 1 / 5 = **0.2**. `silent_loss_rate`: C4's own
  `lost_contact` event surfaced it, so **0.0**.
- `fairness.language.completion_gap_max`: ar 1.0, en 1.0, other 0.0, so **1.0**. `fairness.language.closure_median_gap_max_days`: ar closes
  at 25 and 85 (median 55), en at 50 and 110 (median 80), other has no closed case and is left out; 80 - 55 = **25**.

Machine-checked values for this example (the test compares each row with `scenario_metrics(min_group_size=1)`):

| Metric | Value |
|---|---|
| `time_to_ownership_median` | 1.0 |
| `time_to_ownership_p90` | 3.2 |
| `time_to_closure_median` | 67.5 |
| `closed_share` | 0.8 |
| `open_60d` | 0.6 |
| `open_90d` | 0.4 |
| `completion_90d` | 0.6 |
| `completion_180d` | 0.8 |
| `reopen_rate` | 0.25 |
| `escalations_per_100_cases` | 40.0 |
| `escalation_precision` | 0.5 |
| `lost_to_follow_up_rate` | 0.2 |
| `silent_loss_rate` | 0.0 |
| `fairness.language.completion_gap_max` | 1.0 |
| `fairness.language.closure_median_gap_max_days` | 25.0 |
