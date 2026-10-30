# Independent re-score (E20)

Three reviewers scored `origin/main` at b20cace (25 Oct 2026) against the six judging criteria of [PLAN.md](../../project/PLAN.md) section 1. None of them read the others' reviews. The CEO reviewer took a judge-panel view, the engineering reviewer took a technical view and ran the test suites and the model check, and the third reviewer was a different model (Codex) with no brief beyond the criteria. These are AI reviews, not human judges.

## Scores

| # | Criterion | CEO / judge | Engineering | Codex | Mean |
|---|---|---:|---:|---:|---:|
| 1 | Health impact and clinical relevance | 6.5 | 6.5 | 4.0 | 5.7 |
| 2 | Technical depth and architecture | 9.0 | 8.5 | 7.0 | 8.2 |
| 3 | Working solution and demonstration quality | 7.5 | 7.5 | 7.0 | 7.3 |
| 4 | Responsible AI, safety and data governance | 8.5 | 8.5 | 6.0 | 7.7 |
| 5 | Scalability and deployment readiness | 7.0 | 7.0 | 6.0 | 6.7 |
| 6 | Commercial viability and path to pilot | 4.5 | 5.0 | 3.0 | 4.2 |
| | Unweighted mean | 7.2 | 7.2 | 5.5 | 6.6 |

## Top gaps the reviewers agreed on

1. **The completion ratio is an input.** All three caught it. `orchestration.programme_reach` (0.55) was fitted so the 180-day ratio matches the pooled navigation RR of 1.27, so the 1.31 is calibrated, not evidence. Only the Azulay comparison is independent. The held-out managed-programme completion levels are under-predicted by 13 to 16 points.
2. **Silent loss of 0% is true by construction** for the cases the programme reaches. The programme-level figure (45.3% to 20.6%) is the measured one.
3. **The talk opens with Abu Dhabi's 30.5% and then calls a US-calibrated 51% "today".** Nothing connected the opener to a result.
4. **The commercial story is too small:** about 3 coordinator FTE across the emirate, the fee not covered by avoided cost, no buyer and no LOI.
5. **Stale and contradictory text:** the capacity Q&A called a built model "future work", the wage ratios disagreed, "checked in CI" while Actions is off, "one AI use" (there are four), pathway v0.2 (it is v0.3), erasure "not built" (it is built).
6. **The closure engine accepted `close` from a non-physician actor** outside the HTTP layer (engineering F1). Closure evidence verification and a durable store failed open outside dev (F2, F3).
7. **CI is not running** (GitHub Actions unavailable: billing).
8. **No human clinician or native Arabic reviewer** has signed anything (H1, H2), and there is no buyer or LOI. These are human tasks.

## What was fixed (E20a, E20b)

| Gap | Fix | Where |
|---|---|---|
| 1 Ratio | Reworded everywhere: calibrated to published navigation trials; the model shows the mechanism and what drives it, not new evidence of effect. "We validated the ratio" removed. | README, technical documentation, demo script, teleprompter, storyboard, judge page and its generator, results.md and validation.md generators |
| 2 Silent loss | Both figures always shown, programme level first; the in-reach 0% is stated as true by construction | same files |
| 3 Abu Dhabi baseline | New sensitivity overlay `simulator/params.abudhabi.yaml` anchored to the 30.5% audit; `make evaluate-abudhabi`; results.md section "Sensitivity: an Abu Dhabi-shaped baseline"; one labelled line in the talk | [results.md](../results/results.md), [abudhabi.json](../results/abudhabi.json) |
| 4 Commercial | Programme licence across pathways (FIT, blood-based screening positives, breast and cervical recall), Abu Dhabi's public health programme as the hypothesised buyer; about 42 advanced cancers avoided a year (29 to 84; outcomes model, replacing the earlier 24 to 55 odds-ratio arithmetic); avoided cost alone still does not cover the fee | [business-case.md](../commercial/business-case.md) |
| 5 Stale text | Capacity Q&A uses the built B19 model; wage ratios consistent (4x and 10x); CI job exists but Actions is unavailable; four AI uses; pathway v0.3 with five side states; service roles listed; erasure as built; simulator's shared parts described exactly; red-team HL7 row matches the code; notifications mock-only and the FHIR Appointment a stub wherever the demo shows them; the Arabic moat marked pending review | README, technical documentation, pilot plan, deployment, red-team, PLAN |
| 6 Engine | Physician-role check inside the state machine for `close`, `clinician_review` and `reassign` targets (model-check mutation M8); outside dev the API refuses to start without evidence verification or `DATABASE_URL`; remote triage re-applies the local confidence threshold; an idempotent retry without `at` replays; an HL7 sender allow-list; a simulator/sweep conformance test (E20a, PR #19) | api/, simulator/tests |
| Demo | Agency headcount line and the effort card cut from the spoken script; fewer role switches; a 10-second Arabic patient screen added; spoken script re-timed to 6:18; 3-minute cut 177 s by its own estimate | demo script, teleprompter, storyboard |
| Docs | One-page architecture overview at the front of the technical documentation; a docs-numbers checker (B10) that fails when a number quoted in README or docs/submission has no source | technical documentation, `tools/check/docs_numbers.py` |

## Accepted, not fixed (at the time of the AI reviews)

- CI stays off until the billing is resolved or the repository goes public; merges rest on a full local `make check`.
- Human clinician review (H1), native Arabic review (H2), a buyer or LOI, and any real deployment or Malaffi connection need people, not code.
- Most timing rows still fail held-out validation; no speed claim is made.

## Since resolved ([PR #25](https://github.com/Nandish3010/screening-closure/pull/25), M7-1, merged 29 October 2026, 3a01dc6)

Three items the 25 October list above accepted as not fixed are now fixed. The scores and the list above stay as the record of what the AI reviewers saw on 25 October; they have not been re-scored.

- **Simulator escalation ladder (engineering F6): fixed.** The simulator no longer has an escalation ladder, SLA timers or referral bounce of its own. `simulator/sim/machine.py` folds each case's events into the API's read model and asks `app.sweep.next_due_at` and `app.sweep.plan_case` what is due. The conformance test (`simulator/tests/test_sweep_conformance.py`) replays every simulated case through the real event store and sweep and requires an identical event log, so the "within one working day" tolerance and the referral-bounce difference are gone.
- **UTC against Asia/Dubai calendar: fixed.** The simulator uses the API's calendar (`config/calendar.yaml`, Asia/Dubai) through `simulator/sim/calendar.py`.
- **Leader fence checked at request entry, not at the write (engineering F10): fixed.** The lease token is re-checked inside the write transaction (`SELECT ... FOR SHARE` on the lease row, `api/app/leader.py`); a deposed leader's write raises `StaleLeader` and the scheduler route answers 409.

Also new in the same change, not asked for by the reviewers: a dialect rule for reply triage (`api/app/triage/dialect.py`, hazard HZ-43, test T-D23: Arabizi replies are auto-handled only above their own validated threshold, Gulf-dialect replies always go to a person) and a fairness watch of the overdue share that now covers the `other` language group and contact quality (T-A82). Regenerating the results on the merged engine moved some quoted numbers; the outcomes figures are in [outcomes.md](../results/outcomes.md).
