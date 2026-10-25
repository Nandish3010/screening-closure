# Metric definitions

What every number in [`results.json`](results.json) and [`results.md`](results.md) means: numerator, denominator, clock, censoring. The
code is `simulator/sim/metrics.py` (`build_record` turns one case's event log into a record, `compute_metrics` turns records into
metrics); the hand-checked arithmetic is in `simulator/sim/scenario.py` and `simulator/tests/test_sim_metrics.py`. A test
(`simulator/tests/test_metrics_doc.py`) fails if a metric key in `results.json` has no heading in this file, if a heading names a key
that does not exist, or if the worked example in section 9 stops matching the code.

> **Capacity caveat: escalations cost nothing in this model.** Cases are independent. There are no shared endoscopy slots, no staff
> workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however many others are open.
> The orchestrated arm raises **about 144 escalations per 100 cases** in the upper-range scenario (see `escalations_per_100_cases`
> below), roughly two for every case. Every completion and time gain reported is therefore a gain **before capacity constraints**, and
> would shrink if the people who must work those escalations, or the colonoscopy slots they ask for, are not there. The finite-capacity
> model of ticket B19 (section 12 below; the section "Under finite capacity" of `results.md`) charges for both a shared booking calendar
> and a daily staff-minutes budget and shows how much of each gain survives; everything else in this file is still the unconstrained
> model. What remains future work: per-endoscopist rosters, priority classes, overbooking, and a measured rather than assumed supply of
> slots and staff. Use `escalations_per_100_cases` to size a pilot, and the capacity section for what the gains become when capacity binds.

## 1. Conventions that apply to every metric

**Unit of analysis.** One case = one positive FIT result. `n_cases` per replication (2,000 in `make evaluate`). Both arms and both
scenarios see the same patients.

**Clock zero.** Every time is measured in days since the case's own result arrived, which is the timestamp of its `case_created` event
(`records` store "relative days").

**Calendar days versus working days.** All *reported* durations (time to ownership, contact, colonoscopy, closure, and the 30/60/90 and
90/180/360 day horizons) are **calendar days**. Working days (Monday to Friday, no holidays configured; the
`working_week` and `calendar.holidays` parameters; a Friday counts as a full working day. Friday is a half day in government, which `params.yaml` records as `working_week.friday_half_day: true`, but neither the simulator's nor the API's calendar reads it, so the calendars do not shorten Friday: see [evidence.md](../evidence.md) section 8d) enter in three places only, all inside the model rather than in the reported numbers:
(a) the orchestrated arm's SLA timers and escalation ladder are armed in working days, (b) staff actions land on working days, (c) the
escalation acted-on window is five **working** days (`metrics.escalation_precision_window_working_days`, a parameter key that keeps its original name, see section 10).

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
| Escalation acted-on window | an `escalated` event | five working days later | any action in the window |

**Milestones (A18, ADR-028).** Besides its per-step SLAs, the pathway (`milestones` in `config/pathways/colorectal_fit.yaml`) carries two
clocks that span states and are measured against a national standard. The simulator reads them from the same file as the SLAs.

| Milestone | Clock starts | Met by | Allowance | Source of the figure |
|---|---|---|---|---|
| `referral_requested` | `case_created`, taken as the result release | the first `handoff_requested` (a request, not an acceptance; any handoff counts, including a transfer between primary care owners) | 15 working days | DoH IFHAS standard V10, July 2025: referral to secondary care within 15 working days of releasing results ([evidence.md](../evidence.md) section 8, read first-hand) |
| `confirmatory_completed` | the FIRST referral request (`handoff_requested`); it never restarts, so a later request or acceptance (a bounce, a re-route) changes nothing (A25); if no referral was requested, the case entering `confirmatory_booked` (ADR-020) | `complete_confirmatory` | 31 working days from the first referral request | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed. 2022, Appendix 2 (colonoscopy within 31 working days of referral; [evidence.md](../evidence.md) section 8b). **Not** an IFHAS figure. The guideline is internally inconsistent (15 working days in clause 12.5.2, 31 in Appendix 2, 31 days in Appendix 3). |

The colonoscopy clock starts at the first referral request and never restarts (A25, ADR-028 amended), as the source says: "within 31 working days of referral". Until A25 it started at the latest acceptance and restarted on every later one, which let a case bounced between providers wait without limit while no milestone breached (the AI-simulated clinician review, gap 2). The days between the request and its acceptance now count against the 31; they are also bounded by the 3-working-day handoff response timer (`handoff_response_working_days: 3`). The latest acceptance is kept as `referral_accepted_at` in the read model for reporting only. Any `handoff_requested` counts, including a transfer between primary care owners (the same reading as the referral milestone; the clinician review asks H1 to settle it).

A milestone is breached when its met-by event has not happened by the end of the allowance (the same strict "after" rule as the SLAs, on the
working-day calendar). The breach is recorded once (the API's `mark_overdue` names the milestone instead of a state; the simulator's
`overdue` carries `milestone`) and opens the escalation ladder for the case, in parallel with any ladder already running for the current
step. Meeting the milestone late clears the breach and the ladder; a milestone that is met stays met. The orchestrated arm raises both; the
baseline has no timers, so it never records a breach. The 31-working-day clock is armed once, when the case logs its first `handoff_requested` (A25), and is not bound to the step the case is in, so a
breach opens the ladder for whatever step the case is at (a case still waiting for a provider to accept, or re-routed, breaches at
`book_confirmatory`; a booked one at `complete_confirmatory`). The two figures are **per-case limits**; the standards are programme-level shares (above 90% acceptable for the
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
the rest follow baseline rules, so ownership, escalation and visibility metrics are computed over all cases, reached or not (the
programme-level figures). `silent_loss_rate` and `consent_breach_rate` are also reported over the cases in reach only (section 6), because
their programme-level values follow from the `programme_reach` assumption.

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
**Among completers only.** Median, 25th and 75th percentile, over cases that completed the confirmatory colonoscopy within follow-up, of the days from
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
Cases with at least one `close` event that is not a declined closure (below) within follow-up / all cases. A case closed and later reopened still counts as closed.

### `declined_closed_share`
Cases with a `close` event whose payload says `outcome: declined` (an informed refusal, A33, ADR-042: the owner counselled the patient, recorded the refusal, and a physician who did not counsel them signed) / all cases. **Reported apart: such a case is not in `closed_share`, not in the closure-time medians, not in `open_*` (it stays "open" there, as it does in the baseline arm, where nobody records the refusal), and never counts as a completed colonoscopy.** **The path is off in the committed results** (`orchestration.refusal_path_share` is 0, so this row is 0.0% and refusers are flagged lost, as before): switching it on moves the labour results the submission quotes, so that is the results' owner's decision. `make evaluate-refusal` runs both scenarios with it on (`simulator/params.refusal.yaml`) and writes to a scratch directory. With it on, 6.5% of cases (literature anchored) and 12.0% (upper range) close as declined; the programme-level labour reduction goes from 20.1% to 25.2% and 40.8% to 50.4%, and the reduction among the cases the programme reaches from 36.7% to 46.1% (that is, above the 40% target, which the committed results say is missed), because a recorded refusal stops the escalation ladder that used to keep climbing for a patient who had decided (about 84 and 142 escalations per 100 cases instead of 85 and 144). `closed_share`, the headline lost-to-follow-up rate and the colonoscopy numbers do not move. Only the orchestrated arm has the path, so the baseline's value is 0. Counselling happens five working days after the process notices the refusal (an assumption, not a fitted parameter), and the sign-off three working days later. The headline `lost_to_follow_up_rate` deliberately still counts these patients (no colonoscopy, no progress): exempting them in the orchestrated arm alone would credit the orchestration for relabelling the same patients, because the baseline has no refusal path and calls them lost. The API's live `GET /metrics/closure` takes the other view, because it is a work list: a recorded refusal is a decision, so it is reported as `declined_closures` / `declined_pending_signoff` and is not "lost to follow-up".

### `time_to_closure_median`, `time_to_closure_p90`
**Among closed cases only.** Median and 90th percentile, over cases that closed, of the days from `case_created` to the **first** `close`. Conditional on closing:
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

### `time_to_closure_km_median`, `time_to_closure_rmst`
**Censoring-aware** time to closure (ticket B16). The conditional `time_to_closure_median` above is computed over closed cases only, so an
arm that also closes its slower cases looks slower on it, and a group that closes only its fastest cases looks fast. These two use every
case. Each case contributes one observation: the day of its first `close` if that is within follow-up (an event), otherwise the end of
follow-up `follow_up_days` (censored: still unclosed when observation stopped). The Kaplan-Meier curve S(t) is the product over event
days of (1 - closures that day / cases still event-free and under observation); at a tied day closures are counted before censorings.
- `time_to_closure_km_median`: the earliest day at which S(t) is at or below 0.5, i.e. by which half of all cases have closed. `null` when
  fewer than half the cases have closed by the end of follow-up (the median is not reached). Read `null` as "later than the follow-up
  window", not as missing. In the replication summary it is the mean over the replications in which it was reached (`n_reps` says how many).
- `time_to_closure_rmst`: restricted mean survival time at tau = `follow_up_days`, the area under S(t) from day 0 to tau. It is the average
  number of days a case spends **not yet closed** within the window (equal to the mean of min(day of first close, tau) over all cases when
  every case has the same follow-up). Always defined. Smaller is better; tau = 360 in `make evaluate`, so a case that never closes counts as 360.
  Unlike a median it needs no 50% crossing, and it rewards closing more cases as well as closing them sooner.
- The conditional median and these two answer different questions and can disagree. If arm B closes everything arm A closes at the same
  times and also closes some slow cases, B's conditional median is higher (worse), B's Kaplan-Meier median is no higher, and B's restricted mean
  is lower. A test (`simulator/tests/test_sim_km.py`) builds exactly that case. No confidence-interval or log-rank treatment beyond the
  replication bootstrap; reopened cases use the first close, as everywhere else.

### `time_to_colonoscopy_km_median`, `time_to_colonoscopy_rmst`
The same two statistics for the first `complete_confirmatory`: a case without a colonoscopy within follow-up is censored at `follow_up_days`.
Companion to the completers-only `time_to_colonoscopy_median`. The complement of S(t) at 90, 180 and 360 days is `completion_90d`, `completion_180d`
and `completion_360d`.

## 5. Escalations

### `escalations_per_100_cases`
100 x the number of `escalated` events / `n_cases`. An `escalated` event is emitted when a rung of the pathway's escalation ladder
whose action is `escalate` (supervisor rung, 3 working days after an SLA breach; coordinator rung, 7 working days) fires for a case that is
still waiting. The first rung (owner reminder) emits `reminder_sent`, which is **not** counted. One case can breach several times (each
missed SLA, each missed colonoscopy appointment and each missed national timeline, see the milestones in section 1, opens a ladder, and
the ladders run side by side), so more than one escalation per case is possible; the metric is a
count of events per 100 cases, not the share of cases escalated. **Concurrent ladders coalesce (A19, ADR-031):** when a rung of one ladder
fires for a role (`notify`) that another ladder of the same case already went to on the same working day, while the case is still in the
same step, the simulator emits no second event and takes no second action (no outreach, no staff reaction). The earlier event records the
other ladder in its payload (`also_for`), and the run counter `escalations_coalesced` counts these, so a role is never counted twice for one case on one day. The rule applies to every
rung, so it also removes second `reminder_sent` events (which are not counted here anyway). In the API the same rule is a single `escalate`
with `also_for`, or a later `escalate` with `attached_to`, which tells nobody and is not counted as an escalation. The baseline has no ladder, so 0. **Escalations cost nothing in the
model, see the capacity caveat at the top: about 144 escalations per 100 cases in the upper-range scenario.**

### `escalation_acted_on_rate`
Escalation acted-on rate (within 5 working days): escalations followed by an action within five working days / all escalations
(`escalated` events as above; the denominator counts events, not cases). Renamed from `escalation_precision` (see section 10): the pilot plan
uses "precision" for a different, human-judged definition (pilot plan K7: the share of escalations that a coordinator reviewer, on a sample, judges to have been needed), and
this simulator quantity is not that.

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

### `silent_loss_rate_in_reach`
Lost cases never surfaced to a human, among **cases within the programme's reach** / cases within the programme's reach (ticket B15). A case is in
reach if its fixed random draw for `orchestration.programme_reach` is below that parameter (the draw is the same in both arms, so the baseline arm is
scored on the same patients: "the cases the programme would have reached"). In the orchestrated arm these cases get the orchestrated process, so
every lost or unreachable one is flagged to a human and this rate is 0% by construction (a non-zero value would be a defect). The
programme-level `silent_loss_rate` counts all cases, including those outside the reach, which follow today's process; it is above zero in the
orchestrated arm **only because of the `programme_reach` assumption** (0.55 in the literature-anchored scenario, 1.0 in the upper-range one) and
should not be read as a measured property of the orchestration. `null` if no case is in reach.

### `consent_breach_rate`
Cases with at least one `message_sent` event **strictly after** the first `consent_updated` with status `withdrawn` / all cases.
`message_sent` includes appointment reminders and every outreach kind. Zero in the orchestrated arm for the cases that get the orchestrated process, where the consent ledger blocks
outreach (a blocked message is `message_blocked`, which does not count); the baseline has no ledger. Uses event time, so a message in the
same instant as the withdrawal is not a breach. Outside the programme's reach the case follows today's process and can breach, so this
programme-level rate is above zero in the literature-anchored orchestrated arm; see `consent_breach_rate_in_reach`.

### `consent_breach_rate_in_reach`
The same, among cases within the programme's reach only (denominator: cases in reach; baseline arm scored on the same patients, as for
`silent_loss_rate_in_reach`). 0% by construction in the orchestrated arm. Like the silent-loss pair, the gap between this and the programme-level
rate follows from the `programme_reach` assumption.

### `in_reach_share`
Cases within the programme's reach / all cases: the denominator of the two in-reach rates, about `orchestration.programme_reach` up to sampling
noise (0.55 in the literature-anchored scenario, 1.0 in the upper-range one). Identical in both arms. It is the one baseline-arm quantity that differs between the two scenarios: the baseline process and its event logs are identical, but the in-reach rates score it on the subset of the same patients that the scenario's `programme_reach` would reach.

## 7. Fairness

Groups are the patient's **language** (`ar`, `en`, `other`) and **preferred contact channel** (`sms`, `whatsapp`, `phone`, `letter`) as
drawn for the synthetic patient. They apply to both arms: in the baseline the preferred channel is not used, but the patient is still
in that group. Each group has three statistics, computed per replication.

### `fairness.<dim>.<group>.n`
Cases in the group (`<dim>` is `language` or `channel`).

### `fairness.<dim>.<group>.completion_final`
Cases in the group with a colonoscopy within follow-up / cases in the group (the `completion_final` definition restricted to the group).

### `fairness.<dim>.<group>.closure_median`
**Among closed cases only.** Median days to first `close` among the group's closed cases (`time_to_closure_median` restricted to the group);
`null` if the group has no closed case.

### `fairness.<dim>.<group>.closure_km_median`, `fairness.<dim>.<group>.closure_rmst`
The censoring-aware versions (ticket B16, definitions under `time_to_closure_km_median`) restricted to the group: Kaplan-Meier median (`null`
when fewer than half the group closed within follow-up) and restricted mean days unclosed within follow-up. A group that closes only its
fastest cases has a low conditional median but a high restricted mean; the gap statistics below should be read on the censoring-aware columns.

### `fairness.language.completion_gap_max`, `fairness.channel.completion_gap_max`
The **max-gap statistic** for completion: among groups with at least `metrics.min_group_size` (30) cases, the largest
`completion_final` minus the smallest, as a share (0.05 = 5 percentage points). `null` with fewer than two eligible groups. It is a
range, not a variance, and **not a significance test**: with small groups it is positive by chance alone. It is computed within each
replication and then averaged, so the reported mean gap is at least as large as the gap between the reported group means. The
`difference` row is the paired change in the gap (orchestrated minus baseline); a negative value means the arm narrowed the gap.

### `fairness.language.closure_median_gap_max_days`, `fairness.channel.closure_median_gap_max_days`
The same statistic for `closure_median`: largest minus smallest group median days to closure **among closed cases only**, among groups with at least
`metrics.min_group_size` cases and a defined median. In days. Conditional on closing, so it is biased by censoring and can move in the "wrong"
direction when a change lets slower cases close.

### `fairness.language.closure_km_median_gap_max_days`, `fairness.channel.closure_km_median_gap_max_days`
The same statistic on the Kaplan-Meier medians (groups whose median is not reached are left out; `null` with fewer than two such groups).

### `fairness.language.closure_rmst_gap_max_days`, `fairness.channel.closure_rmst_gap_max_days`
The same statistic on the restricted mean days unclosed: always defined for every eligible group, so this is the censoring-aware gap to quote.

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
- `time_to_closure_km_median` / `time_to_closure_rmst`: C4 never closes, so it is censored at 180. S(t) drops to 0.8 at 25, 0.6 at 50, 0.4 at 85,
  0.2 at 110. The first S at or below 0.5 is at day **85**. The area under S from 0 to 180 is 25 x 1 + 25 x 0.8 + 35 x 0.6 + 25 x 0.4 + 70 x 0.2 =
  **90** (the mean of 25, 50, 85, 110 and 180). The conditional median of the closed cases is 67.5, lower, because C4 is not in its sample.
  `time_to_colonoscopy_km_median`: completions at 20, 40, 60, 100 give S = 0.8, 0.6, 0.4, so **60**; `time_to_colonoscopy_rmst` = (20 + 40 + 60 + 100 + 180) / 5 = **80**.
- `time_to_closure_median`: first closes at 25, 50, 85, 110 (C4 never closed, so it is not in the sample); (50 + 85) / 2 = **67.5**.
  C2's second close at d120 is ignored. `closed_share` = 4 / 5 = **0.8**.
- `open_60d`: C1 (closed d25) and C2 (closed d50, reopened only at d95) are closed; C3, C4, C5 are open: 3 / 5 = **0.6**. `open_90d`: C3
  (d85) is now closed too, C2's reopen at d95 is later: 2 / 5 = **0.4**. Reopen at work: C2 is open again on day 100 (closed d50,
  reopened d95, closed again d120), so it counts as open there although it counts once in `closed_share`.
- `completion_90d`: colonoscopies at 20, 40, 60, 100; three are within 90; 3 / 5 = **0.6**. `completion_180d` = 4 / 5 = **0.8**.
  `completion_360d` is `null` (follow-up 180).
- `reopen_rate`: 1 reopened (C2) of 4 ever closed = **0.25**.
- `escalations_per_100_cases`: 2 escalations over 5 cases = **40**. `escalation_acted_on_rate`: C2's escalation on d10 (Thursday) has a window to
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
| `time_to_closure_km_median` | 85.0 |
| `time_to_closure_rmst` | 90.0 |
| `time_to_colonoscopy_km_median` | 60.0 |
| `time_to_colonoscopy_rmst` | 80.0 |
| `closed_share` | 0.8 |
| `open_60d` | 0.6 |
| `open_90d` | 0.4 |
| `completion_90d` | 0.6 |
| `completion_180d` | 0.8 |
| `reopen_rate` | 0.25 |
| `escalations_per_100_cases` | 40.0 |
| `escalation_acted_on_rate` | 0.5 |
| `lost_to_follow_up_rate` | 0.2 |
| `silent_loss_rate` | 0.0 |
| `fairness.language.completion_gap_max` | 1.0 |
| `fairness.language.closure_median_gap_max_days` | 25.0 |

## 10. Deprecated aliases

Names that changed. Nothing reads the old names any more; they are listed so older notes and screenshots can be mapped to the current ones. The
quantity itself is unchanged by the rename (every value in `results.json` is identical); only the bootstrap interval around a renamed metric moves in the
third decimal place, because the bootstrap stream is seeded from the metric name.

| `deprecated_aliases` (old name) | Current name | Why |
|---|---|---|
| `escalation_precision` | `escalation_acted_on_rate` | The pilot plan uses "precision" for a human-judged definition (pilot plan K7: the share of escalations a coordinator reviewer judges to have been needed). This simulator quantity is the share of escalations followed by any pathway action within 5 working days, which is not that. Label: "Escalation acted-on rate (within 5 working days)". |
| `metrics.escalation_precision_window_working_days` | (not renamed) | The parameter that sets the five-working-day window keeps its key, with a note in `simulator/params.yaml`: renaming it would change the parameter hash that the risk model (`api/app/risk/model.json`) records and force a retrain outside this ticket. |

## 11. Human effort (ticket B17)

The quantities in the "Human effort" section of [`results.md`](results.md) and under the `effort` key of [`results.json`](results.json). The code is
`simulator/sim/effort.py`; the minutes and where they come from are in [evidence.md](../evidence.md) section 11. They are computed from the event
logs after the run, so they change no outcome in sections 2 to 8. They sit under `effort`, not under `arms`, so the test that matches section
headings to metric keys does not cover them; `simulator/tests/test_effort.py` checks that every effort key below is defined here.

**Unit and process.** One case = one positive FIT result, as everywhere. A case follows one of two *processes* whatever its arm: the **manual** process (today's: every
step done by a person) or the **orchestrated** process (the engine acts, a person decides). The baseline arm is all manual; in the orchestrated arm the cases
within `orchestration.programme_reach` are orchestrated and the rest are manual, exactly as in the engine. The `effort.scenarios.<scenario>.all` block is the programme level (every case);
`effort.scenarios.<scenario>.in_reach` counts only the cases within the reach (in the baseline column, the same patients under today's process).

**Human action.** An event, or a piece of chasing, that costs a person minutes. Each event type maps to a human action, an automated step or nothing
for each process (`effort.model.event_map`). Minutes per human action, with a low and a high end, a role (coordinator, owner clinician, specialist), a grade
(sourced, sourced-adjacent, assumed) and a basis are `effort.model.actions`. The human steps of the orchestrated process are: accept an escalation (supervisor or coordinator, also a watchdog
flag), the phone call that follows it, approve a drafted message, sign the closure, handle an exhausted reroute list or a lost case, plus what automation cannot do (record that
a patient called back, review a consent withdrawal, decide on a transfer, rebook, record a corrected contact, review a reopened case, and the specialist's accept or reject of a referral). Automated
steps (`effort.model.automated_steps`) cost no human minutes and are only counted.

**Chasing.** In the manual process a coordinator chases when something is overdue and no event records it: one attempt for each full `interval_days` (14) that a patient has not
answered an outreach, a referral has not been answered, or a colonoscopy has not been recorded since the appointment date, up to `max_attempts` (3) per waiting episode. The simulator gives the baseline no
outcome for this work (its response and delay parameters already describe today's chasing), so it is an effort only. Variants `none` and `intensive` (5 attempts every 7 days) are in `effort.model.chase`.

### Human minutes per case: minutes_per_case

Total human minutes of all human actions (events plus chasing) divided by the cases of the arm. Per role: minutes_per_case.coordinator, minutes_per_case.owner_clinician, minutes_per_case.specialist
(`minutes_per_case.<role>`). By grade: minutes_per_case.status.sourced, minutes_per_case.status.sourced-adjacent, minutes_per_case.status.assumed
(`minutes_per_case.status.<grade>`): the minutes that rest on figures of that grade, so they sum to `minutes_per_case`. Reported as a mean over replications with a bootstrap 95% CI.

### Human minutes per closed case: minutes_per_closed_case

Total human minutes divided by the cases that have at least one `close` event in the follow-up (the same cases as `closed_share`). A case that never closes still costs minutes, so an arm that
closes fewer cases pays more per closed case.

### Human actions per case: human_actions_per_case, automated_actions_per_case

The number of human actions (events and chasing attempts) and of automated steps per case. An escalation that the engine emits and a person accepts counts as one automated step and one human action.

### Automation share: automation_share

Automated steps divided by all steps (automated steps plus human actions) of the arm. It is 0 for the baseline by construction. It counts steps, not minutes; the orchestrated arm takes
many more steps than the baseline because it does work (reminders, escalation, bounce) that today's process never does.

### Labour reduction: labour_reduction, labour_reduction_per_closed_case, human_actions_reduction

For each replication, 1 - orchestrated / baseline of `minutes_per_case`, of `minutes_per_closed_case` and of `human_actions_per_case` (same patients, same random numbers in both arms), then the
mean and percentile bootstrap 95% CI over replications. Per role: labour_reduction.coordinator, labour_reduction.owner_clinician, labour_reduction.specialist (`labour_reduction.<role>`);
negative where the orchestrated arm needs more of that role's time (the specialist reviews more referrals because fewer are silently lost). The 40% target is met in a scenario when the lower end of the CI of `labour_reduction` is above 40%.

### Robustness and the minutes sweep

`effort.scenarios.<scenario>.robustness` recomputes `labour_reduction` (point estimate over replications) with the baseline's chasing not costed, with a harder chase, with the specialist left out, with a person still
recording every booking in the orchestrated arm, without the draft-approval charge, with every minutes figure at its low or its high end, and in the least favourable corner (baseline-only actions at their low
end, orchestrated-only actions at their high end). `effort.scenarios.<scenario>.tornado` moves each minutes figure alone to its low and high end. Both are exact (counts are stored, minutes applied afterwards), not simulated again.

### Not in the count

Patients' time, the endoscopy unit's procedure and reception work, training, management and IT time, queueing and shift patterns (no capacity model, see the capacity caveat), and the work
that today's process never does (listed in `effort.model.never_done_in_baseline`).

## 12. Finite capacity (ticket B19)

These metrics exist only in `capacity.json` (written by `make evaluate-capacity`), never in `results.json`, so the unconstrained results keep
their bytes. They are computed per run from the shared booking calendar and the escalation queue (`simulator/sim/capacity.py`) and aggregated
like every other metric (mean over replications, percentile bootstrap 95% CI, differences paired by replication). Every outcome metric of
section 1 to 10 (completion, time to colonoscopy, open share and so on) keeps its definition; under capacity it is simply computed on the
event logs of a run in which appointments take the earliest free slot and escalations wait for a person.

**The window.** Utilisation metrics are measured over the working days from day 30 to day 150 of the run (`capacity.utilisation_window_start_day`,
`capacity.utilisation_window_end_day`): results arrive over the first 30 days and the median booking lead is 24 days, so before day 30 hardly
anyone can hold a booking, and by day 150 about 90% of the baseline arm's appointments have happened.

**Slots.** Supply is `sessions_per_week_per_1000_cases` x `slots_per_session` x `scale` x cases / 1000 slots a week, spread evenly over the working days
and `providers` providers (integer per provider per day; the running total is floored). A booking takes the earliest working day on or after the lead-time
date with a free slot at the case's provider and keeps the time of day. A no-show keeps its slot; with probability `dna_backfill_prob` the unit refills it
from outside the cohort. A patient who withdraws consent and declines care before the day cancels and frees the slot.

| Key | Definition |
|---|---|
| `slots_offered` | Slots the calendar offers on the working days of the window, summed over providers. |
| `slots_booked` | Bookings that hold a slot on a day of the window (attended, no-show, refilled no-show or still to come), not cancellations. |
| `slot_utilisation` | `slots_booked` / `slots_offered`. At most 1 because a day never holds more bookings than slots. |
| `slot_used_share` | (attended + refilled no-shows) on a day of the window / `slots_offered`: the slots that did something. At most `slot_utilisation`. |
| `bookings_per_100_cases` | Appointments booked (and not cancelled) over the whole follow-up, per 100 cases. Rebookings count. |
| `cancellations_per_100_cases` | Slots given back by a patient who declined care before the day, per 100 cases. |
| `dna_backfilled_share` | Refilled no-shows / all no-shows (a share of the draw `dna_backfill_prob`). |
| `request_to_slot_wait_median`, `request_to_slot_wait_p90` | Days from the moment a booking is requested (the specialist accepts the referral, or a rebooking is made) to the appointment day, over all bookings. Includes the lead time the unconstrained model already had. |
| `capacity_delay_mean`, `capacity_delay_p90` | Days between the lead-time date the unconstrained model would have used and the slot actually given: the wait that exists only because the calendar was full. 0 when the first choice was free. |
| `booked_late_share` | Share of bookings given a slot one day or more after their first choice. |

**Escalation handling.** The supervisor (rung 1 of the escalation ladder) and the programme coordinator (rung 2, and the watchdog flags) have a daily minutes
budget, `capacity.staff.*_minutes_per_day_per_1000_cases` x `capacity.staff.scale` x cases / 1000. An escalation (a ladder rung whose action is `escalate`) or a watchdog flag is a
job of `accept_minutes` plus, when the case is waiting for the patient, `contact_minutes`. Jobs are served first come first served; a job that does not fit in the rest of the
day's budget starts at the beginning of the next working day. An escalation that waits is raised, called and acted on when it is served; if the case has moved on by then it is
stale: nothing happens, but the minutes were spent. Owner reminders (rung 0) are not charged.

| Key | Definition |
|---|---|
| `esc_jobs_per_100_cases` | Escalations and watchdog flags that needed a person, per 100 cases (rungs that coalesced into another are not counted twice). |
| `esc_delay_mean`, `esc_delay_p90` | Days from the moment a job is requested to the moment it is served, over jobs served before the end of follow-up. 0 when the person was free. |
| `esc_delayed_share` | Share of those jobs that waited half a day or more. |
| `esc_stale_share` | Share of those jobs whose case had moved on (a new step or abandoned) by the time they were served. |
| `esc_queue_mean`, `esc_queue_max` | Jobs requested but not yet started at the end of a working day of the window: mean and worst day, both roles together. |
| `esc_supervisor_utilisation`, `esc_coordinator_utilisation` | Minutes of job time falling on working days of the window / (daily budget x working days of the window), per role. Above 1 is impossible; below the demand when work is still queued at the end of the window. |

**Sensitivity design.** Capacity: `capacity.scale` at 0.70, 0.85, 1.00 and 1.15 with staffing at base. Staffing: `capacity.staff.scale` at 0.50, 0.75, 1.00, 1.50, 2.00 and 3.00 with slots at
base (orchestrated arm only; the baseline raises no escalations, a test pins that its event log does not depend on staffing). Decomposition: the orchestrated arm with only the slots limited
or only the staff limited (the other limit multiplied by 1,000). All sensitivity differences are orchestrated minus baseline under the same slot supply.

**Not in the count.** Priority classes, overbooking, per-endoscopist rosters, and any demand on the calendar other than this cohort and the refill of no-shows.
