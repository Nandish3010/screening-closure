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

## 5 November 2026 re-score

Three AI reviewers scored `origin/main` at a335633 (5 November 2026) against the same six criteria of [PLAN.md](../../project/PLAN.md) section 1. **These are AI reviews, not human judges.** They were run separately: the judge-panel reviewer (a Department of Health senior leader sitting with industry judges) read the documents, the web source and the video frames but did not run the stack; the engineering reviewer ran `make check`, the model checker and probes against the code; the third reviewer read the criteria and the repository with no other brief and ran nothing. Each formed scores before reading this file. The reviews are kept with the team's working notes and are not committed to the repository.

A fourth reviewer, Codex, could not score. Its run ended with "You've hit your usage limit" from OpenAI and the quota returns on 25 November 2026, after the 8 November 2026 submission. So this round has three scores where the 25 October round had three different ones (including a Codex score of 5.5), and **the two means are not like for like**: the earlier mean was pulled down by a harsher third reviewer.

### Scores

| # | Criterion | Judge view | Engineering view | Outsider (criteria only) | Mean of three |
|---|---|---:|---:|---:|---:|
| 1 | Health impact and clinical relevance | 5.5 | 6.0 | 6.0 | 5.8 |
| 2 | Technical depth and architecture | 8.5 | 8.0 | 8.5 | 8.3 |
| 3 | Working solution and demonstration quality | 6.5 | 7.0 | 6.5 | 6.7 |
| 4 | Responsible AI, safety and data governance | 8.0 | 8.0 | 8.5 | 8.2 |
| 5 | Scalability and deployment readiness | 6.5 | 7.0 | 6.5 | 6.7 |
| 6 | Commercial viability and path to pilot | 4.5 | 4.5 | 5.0 | 4.7 |
| | Unweighted mean | 6.6 | 6.75 | 6.83 | 6.72 |

The per-criterion means are computed from the three files (for example criterion 1 is (5.5 + 6.0 + 6.0) / 3). The last row is each reviewer's own mean, and 6.72 is the mean of all eighteen scores. The spread between reviewers is small (6.6 to 6.83); the spread between criteria is large (4.7 to 8.3).

### Top gaps the reviewers agreed on, and where each stands

| Gap | Judge | Eng | Outsider | Status |
|---|:-:|:-:|:-:|---|
| No human review of anything (clinician, Arabic) and no real-world data; the reviews on file are AI-simulated | yes | yes | yes | Cannot be closed with software. Stays labelled placeholder or AI-simulated everywhere. |
| No buyer or letter of intent; the licence is not covered by avoided cost; wage-weighted staff cost per case rises; the 40% labour target is missed | yes | yes | yes | Cannot be closed without a buyer. **This docs pass** leads the commercial front page with what is sourced and labels the licence as an assumption ([commercial/README.md](../commercial/README.md)). |
| The hosted demo is not live, and the local path is long (12-minute seed, Docker, venv, Node, an Apple-only model server) | yes | yes | yes | **Hosted deploy on 8 November 2026**, and a **self-healing deploy** (engineering F2) in an open branch. This docs pass states the platform needs and honest timings. |
| API memory growth with an unknown cause | yes | yes | yes | Mitigated by an automatic API restart; cause still unknown. |
| Stale or overclaimed text: CI badge and "CI off", video "Not recorded", "building autonomously until 18 October", the M6 changelog in the README, "60 seconds", 16 versus 17 stages, the missing-checker line | yes | yes | yes | **Fixed in this docs pass.** |
| The brief names hypertension, diabetes and obesity; the demo and the headline are bowel; the chronic thresholds are placeholders | yes | | in part | A **demo chronic seed** and **Malaffi and Sahatna mocks** are in open branches; the thresholds stay placeholders until a clinician signs. |
| The simulator is calibrated, not validated; most held-out timing checks fail | in part | in part | yes | Already stated everywhere; no fix intended. |
| The video: one blank results card (judge); video status stale in the docs (outsider) | yes | | yes | A **re-render fixing the blank card** is in progress on another branch; the stale status text is **fixed in this docs pass**. |
| Engine safety: a single physician can close as "declined" with a fabricated counselling reference (F1), the opt-out gate misses "don't" phrasings (F3), consent writes carry no provenance (F4), case-page reads are not need-to-know scoped (F5), `APP_ENV` defaults to dev (F6) | | yes | | **Engine-safety F1 and F3 to F6** in an open branch. Until it merges the documents say where the declined path is weaker (technical documentation section 4.4). |

### Reviewer claims checked against the repository

- Confirmed and fixed here: the CI badge and "CI off" wording; "Not recorded" and the `say` voice in the docs; "Draft" status labels; "building autonomously until 18 October"; "As of 25 Oct"; the "What changed in M6" changelog (moved to [project/CHANGELOG.md](../../project/CHANGELOG.md)); the Quickstart "60 seconds" and "Judge path in 5 minutes" timings; the hosted sign-in described as if it existed; 16 versus 17 check stages (the fast tier has 17 and `check-full` has 23); the docs-numbers checker listed as missing (B10 is built); evidence resolution described as inside the state machine (it is in the API route); "Arabic-first"; the 31 deaths versus 42 advanced cancers units (a bridge sentence was added); PLAN.md criteria 3 and 4 promising a live kill and an on-screen model draft that the recorded video does not show.
- Wrong or out of date: the outsider's statements that CI "is dead (billing)" and that the video "is synthetic macOS `say` TTS" described the repository's stale text, not the facts (Actions has run on every pull request since 31 October; the video uses a local neural voice). The judge's reading that the 91.1% Gulf figure belongs to the hosted CPU build is not right: the on-premises model's own fairness audit gives the same 91.1% (45 auto-handled replies, interval 79.3% to 96.5%), so the narration is accurate; the teleprompter now names the model and the 95% target. The outsider's note that the README and PLAN disagree on the registration and feature-freeze dates was not found: both say 10 November. Test counts quoted in the evaluation summary are static text, as the outsider said, and now say so.
- Not fixed here: the engineering finding F8 (scale runs attributed to commits with uncommitted paths, `docs/results/scale.md`) needs a re-run on a clean tree and is outside this docs pass.

### Since the 25 October round

CI: the entries above that read "CI is not running" and "CI stays off until the billing is resolved or the repository goes public" are history. The repository went public on 31 October 2026 and GitHub Actions has run on every pull request since; a merge needs a green local `make check-full` and a green CI run.
