# Human handover checklist

Written 18 Oct 2026. The build is done; what remains needs a person. Items 1 to 9 map to tracker rows H1 to H6 ([TRACKER.md](TRACKER.md)); rows below are in deadline order. Registration closes and features freeze 10 Nov; submit 18 Nov; due 20 Nov. Tick a box when done, then ask Claude to set the H rows to `done`.

## Checklist

| # | Do by | What | Why it matters to the judges | Time | Done |
|---|---|---|---|---|---|
| 1 | 20 Oct (hard 10 Nov; form open by 7 Nov) | Confirm registration, download the requirements, diff against the bundle | Registration is required to be judged; a missed requirement can disqualify | 1 h | [ ] |
| 2 | Send 25 Oct; review held by 7 Nov | Clinician reviewer (H1) | Clinical credibility, responsible AI, the "reviewed by" section of the documentation | 30 min call, 1 h to apply | [ ] |
| 3 | Send 25 Oct; review held by 7 Nov | Arabic reviewer (H2) | Arabic-first patient experience, one of the six moats | 30 min, 1 h to apply | [ ] |
| 4 | Ask by 1 Nov; letter by 13 Nov | Letter of intent for a shadow-mode pilot | Commercial viability and pilot readiness, our weakest criterion (judge review scored it 5/10) | 20 min to ask | [ ] |
| 7 | 11 Nov (day after freeze) | Second-machine fresh-clone run (H5) | The plan promises one-command reproducibility; this proves it off your machine | 1 to 2 h | [ ] |
| 5 | 13 Nov | Ten timed rehearsals (H3) | The live catch is the demo's whole value | 10 x 8 min plus setup | [ ] |
| 6 | 14 Nov | Submission video and backup video (H4) | Judges may watch the video before reading anything | 3 to 4 h | [ ] |
| 8 | Decide 14 Nov; public 17 Nov | Repository visibility and name check | A private link is a 404 for judges | 1 h | [ ] |
| 9 | 18 Nov | Final submission (H6) | The deliverable | 1 h | [ ] |
| 10 | Any time | If something breaks | | | [ ] |

## 1. Registration and requirements

1. On the Hack2skill microsite, confirm registration for Challenge 05; if missing, register today.
2. Read the terms once, including the 6-month right of first refusal.
3. Download the full submission requirements and check each against the table in [docs/submission/README.md](../docs/submission/README.md). List gaps for Claude.

## 2. Clinician reviewer

Send to a GP, gastroenterologist or screening lead. Print [docs/review/clinician-pack.md](../docs/review/clinician-pack.md) to PDF from a rendered view (so the chart shows) and attach it.

> **Subject:** 30 minutes of clinical review for a screening follow-up prototype
>
> Dear Dr [Name],
>
> I am building a prototype for the Future Health Digital Innovation Challenge on one problem: a positive FIT result that nobody follows up. Each result gets a named owner, time limits, escalation to a person, and closure only with evidence and a clinician's signature. Nothing is diagnosed, and all data is synthetic.
>
> Could you give me 30 minutes, before 7 November, to review the pathway? The attached pack holds ten questions: time limits (two), patient wording, the 15 and 31 working-day clocks, closure evidence, informed refusal, lost contact, opt-out consent, escalation, and missing exits. Short answers are enough, and "cannot say" is fine. I will credit you in the documentation if you agree, and send you the changes made afterwards.
>
> Thank you, [Name]

Record the session in `docs/governance/clinician-review.md` (T-E13). Apply:

| Answer concerns | Change |
|---|---|
| Q1, Q2, Q4, Q7, Q9: SLA days, clock starts, milestones, lost-contact count, ladder offsets | `config/pathways/colorectal_fit.yaml`, then `make evaluate` (numbers move) |
| Q3, Q8: patient wording, forbidden phrases, consent default | `api/app/ai/templates/en.yaml`, `web/src/i18n/strings.ts`, `api/app/ai/forbidden_phrases.yaml` |
| Q5, Q6, Q10: evidence, informed refusal, missing exits | New rows in `docs/assumptions-limitations.md` (next free L number); a new state is a post-freeze feature, so ask Claude first |
| Any | Revise ratings in `docs/governance/hazard-log.md`; summarise in technical documentation section 14 |

Finish with `make review-packs && make check`.

## 3. Arabic reviewer

Attach [docs/review/arabic-pack.md](../docs/review/arabic-pack.md) (PDF).

> **Subject:** Native Arabic review of patient messages, 30 minutes
>
> Dear [Name],
>
> I am building a prototype for the Future Health Digital Innovation Challenge that makes sure a positive screening result is followed up. Patients see short messages and a page in Arabic and English. The Arabic is a draft that no native speaker has checked, and all data is synthetic.
>
> Could you spend 30 minutes, before 7 November, reading the attached pack? It shows each Arabic text beside its English, with context. Please mark anything unnatural, too hard for about grade 6, or wrong in right-to-left layout, and suggest better wording. I will credit you if you agree, and share changes.
>
> Thank you, [Name]

Apply edits in `web/src/i18n/strings.ts` (patient page) and `api/app/ai/templates/ar.yaml` (messages); demo drafts are in `web/src/api/fixtures.ts`; Arabic forbidden phrases in `api/app/ai/forbidden_phrases.yaml`. Then:

```
make review-packs
make test
make web-test
```

Record in `docs/governance/arabic-review.md` (every string approved or changed; T-E14). Then ask Claude to drop the "unreviewed draft" labels.

## 4. Letter of intent

Ask one provider or programme contact (primary-care or endoscopy lead, or screening programme manager) to sign this on letterhead:

> [Organisation] has read the description of Screening Closure, a prototype using synthetic data that gives each positive FIT result a named owner, time limits, escalation and clinician-signed closure. We are interested in discussing a 90-day pilot in shadow mode, where the system observes our results feed and flags overdue cases without sending messages or assigning work. This letter expresses interest only. It creates no obligation. Data terms would follow, and any pilot depends on our governance, data-sharing and regulatory approvals.

Get permission to publish the organisation's name. Save the PDF as `docs/submission/letter-of-intent.pdf`. Update `docs/pilot-plan.md` (the status paragraph says nothing was discussed with a provider; also section 10), technical documentation section 11, and `docs/submission/README.md`.

## 5. Rehearsals

Per [demo-script.md](../docs/submission/demo-script.md): start each run from "Before going on stage"; ten runs at or under 6:30 each; over-time runs are logged but do not count; cut in the script's order.

```
make up
make demo-kill-scheduler RUNS=3     # once; alert expected 58 to 80 s, restarts the scheduler itself
make demo-reset                     # before every run, about 25 s
make demo-scenario UNTIL=owner_accepted
```

Log here, not in `docs/demo/rehearsals.md`.

| # | Date | Total (m:ss) | Over 6:30? | Beat that slipped / note |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
| 8 | | | | |
| 9 | | | | |
| 10 | | | | |

## 6. Videos

Use the 13-shot storyboard in demo-script.md: 1080p, burned-in captions, SIMULATED TIME badge legible, "Synthetic data" strip on every shot. Record one raw capture (Shift-Command-5), then edit two files.

| File | Content | Where |
|---|---|---|
| `submission-3min.mp4` | 20 s problem, 2 min catch-and-close, 30 s metrics, 10 s pilot ask; voice-over | Upload (unlisted link); paste the link into the placeholder row of `docs/submission/README.md` |
| `backup-demo.mp4` | Same shots, captions only, no voice-over | Demo laptop and a USB stick; not committed (large) |

## 7. Fresh-clone run

Push first: the script tests committed state only. On the second machine (Docker, git, Python 3, GitHub access while the repo is private):

```
git clone https://github.com/nandish-30/screening-closure.git && cd screening-closure
sh tools/check/fresh_clone.sh
```

It prints a timed report and the H5 checklist. The first seed takes about 12 minutes; images need a network once. Pass: every command succeeds and `make evaluate` is under two minutes (T-F13). Save the report as `docs/results/fresh-clone.md`.

| Step | Time | OK |
|---|---|---|
| venv and dependencies | | |
| `make up` | | |
| `make demo-seed` | | |
| `make evaluate` | | |
| `make check` | | |

## 8. Repository visibility

1. Keep `project/` (it shows process); skim it once.
2. `make test-security` has no competitor check. The two names are in `/Users/NS/gitex/fhdic-challenge05-plan.md`, deliberately not written here. Run `git grep -n -i -E 'NAME1|NAME2'` and `git log --all -i -E --grep='NAME1|NAME2'`; both must be empty.
3. Run `make test-security`; skim `git log -p` for keys.
4. On 17 Nov: `gh repo edit nandish-30/screening-closure --visibility public --accept-visibility-change-consequences`; open the URL in a private window.

## 9. Final submission

Re-run `make evaluate`, `make evaluate-check` and `make check`; commit; fill the two placeholders (video, repo) in `docs/submission/README.md`.

| Bundle / likely form field | Source |
|---|---|
| Team, challenge 05, project name and one-liner | README.md first lines |
| Repository link | public URL from item 8 |
| Video link | item 6 |
| Documentation | `make docs-pdf` does not exist. To decide: markdown link or PDF (Claude can render [technical-documentation.md](../docs/submission/technical-documentation.md) with the make-pdf skill) |
| Evidence, load, licence | `docs/results/`, `LICENSE` (MIT) |
| Reviewer packs, letter | `docs/review/`, `docs/submission/` |

## 10. If something breaks

| Symptom | Do |
|---|---|
| Demo state is wrong | `make demo-reset` |
| Snapshot missing or stack corrupt | `make reset && make demo-seed && make demo-stage && make demo-snapshot` (seed about 12 min) |
| Second case missing | `make demo-stage` |
| Anything unclear | `make check` (add `CHECK_KEEP_GOING=1` to see every failure) |

Never run `make test-phi-logs` on the demo stack. Claude can resume from the repo: say "read project/TRACKER.md and resume"; the saved memory note points there.
