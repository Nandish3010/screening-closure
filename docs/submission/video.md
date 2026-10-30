# Submission video and backup demo video (E16)

Both videos are produced by a script from the storyboard of [demo-script.md](demo-script.md), with no human recording: Playwright drives the real web app against the live stack, a text-to-speech voice reads the narration, and ffmpeg joins the shots. **All data shown is synthetic, and the narration is synthetic text-to-speech (the macOS `say` voice Samantha), not a person.** Say so wherever the video is posted.

| File | What | Where |
|---|---|---|
| `submission-3min.mp4` | Title card (3 s), the shots flagged `submission: true` (10 shots since the M7 presentation fixes, 14 before; the storyboard of the demo script), end card (4 s). Hard cap 180 s: `assemble.sh` fails above it. | `tools/video/out/` (gitignored) |
| `backup-demo.mp4` | The same, plus every other shot that could be recorded (the two reveal shots below, when their pages exist). | `tools/video/out/` (gitignored) |

**M7 presentation fixes, not yet recorded (30 Oct 2026):** the storyboard is cut from 14 shots to 10 (167 s of targets, 174 s with the cards) and the shot numbers below the recorded runs refer to the earlier 14-shot cut. The triage cameo (now shot 5) shows a Gulf-Arabic reply going to a person, because `api/app/triage/dialect.py` forces every Gulf reply to a person (the earlier cut narrated "no person is needed", which the code no longer does); `tests/test_storyboard_triage.py` keeps the quoted replies and the words in step with the code. Two numbers shots are new (the honest range, labour by role) and the ask names the regulatory pull and the shadow-run cost. The end card still carries two placeholders, the repository link and the team name (`cards.end` in the YAML). The video must be rebuilt (`make video`) and its length measured: the 14-shot cut measured just under the 180 s cap.

**E20b changes, not yet recorded:** the Human effort card is no longer a shot (it is not in the spoken demo; the effort counter stays as backup shot 15); shot 10 is the triage cameo and says that no appointment is booked and the FHIR Appointment is a stub; shot 11 is new, the patient's own page at `/patient/<case>` switched to Arabic with the language button (an action that exists in `PatientPage`); shot 8 starts the agency run with `make agency-run` in a terminal and shows the inbox as the specialist, so it needs no supervisor switch; shots 3 and 5 say that messages are mock-only; shots 12 and 14 carry the calibrated-ratio wording, the programme-level silent loss first, and the buyer hypothesis. The targets add up to 170 s of shots (177 s with the cards); the per-shot lengths below are from the earlier cut and the real length has to be measured again (H4 stays open).

**Since that run the storyboard changed (E19): the spoken figures follow the regenerated results (B21), shot 11 reads the reply with the live triage model (D15; run `make triage-serve` first) and shots 11, 13 and 14 are 2 s longer, so the targets now add up to 168 s of shots (175 s with the cards, 5 s under the cap, so check the real length). The measurements below are from the earlier cut and have not been repeated: run `make video` again and replace them (H4 stays open).**

**Re-run for E19 (25 Oct 2026, M6 main with live triage via `make triage-serve`):** `make up`, `make demo-reset`, `make video` recorded 15 of 15 shots, none skipped (shot 11 read the Gulf-Arabic reply as `confirm` on the live model); after shortening the narration of shots 2, 12, 13 and 14, `submission-3min.mp4` is 177.8 s (cap 180 s) and `backup-demo.mp4` 187.8 s. Final shot lengths: 1 8.0, 2 9.4, 3 12.3, 4 12.0, 5 15.0, 6 5.0, 7 12.0, 8 15.0, 9 13.0, 10 10.1, 11 16.0, 12 20.0, 13 12.0, 14 11.2, 15 10.0 s. The paragraph below is the earlier proof run, kept for its method.

**Status: the proof run is done (18 Oct 2026, on the merged code with A24, A25 and A26 and the final demo script).** `make demo-reset`, `make video-deps`, `make video` ran against the real stack and recorded **15 of 15 shots, none skipped**, then narrated and assembled both files; the recorder left the scheduler running and the demo reset (state hash `a0a07a71...` equal to the snapshot's). Measured:

| File | Duration | Size | Format |
|---|---|---|---|
| `tools/video/out/submission-3min.mp4` | 169.95 s (2:49.95; cap 180 s) | 13,009,985 bytes (12.4 MiB) | H.264, 1280x800, 25 fps, AAC |
| `tools/video/out/backup-demo.mp4` | 179.95 s (2:59.95) | 13,325,911 bytes (12.7 MiB) | the same |

The 14 submission shots add up to 162.9 s of narrated video plus the 3 s title card and the 4 s end card; the backup adds shot 15 (10 s). The whole `make video` (record, narrate, assemble) took 4 min 31 s on a development MacBook, and the real watchdog wait (57.5 s of raw video in shot 5) is cut to 16.6 s. Per shot (target, narration, final): shot 1 8 s, 6.4 s, 8.0 s; shot 2 7, 8.6, 9.4; shot 3 12, 11.5, 12.3; shot 4 12, 7.6, 12.0; shot 5 15, 14.0, 15.0 (sped up 1.11x); shot 6 5, 2.3, 5.0; shot 7 12, 8.5, 12.0; shot 8 15, 13.3, 15.0; shot 9 13, 10.2, 13.0; shot 10 10, 9.3, 10.1; shot 11 14, 10.5, 14.0; shot 12 16, 17.2, 18.0; shot 13 10, 7.9, 10.0; shot 14 9, 8.3, 9.1; shot 15 10, 6.8, 10.0. Five shots run longer than their target because their narration does (2, 3, 10, 12, 14); none needed more than a 1.22x speed-up. Frames of the final file were checked at the key moments (the amber "DEMO PLACEHOLDER" banner on the case page and the dashboard, the agency run and its one-item inbox, "Case closed (signed)" and the Reopen panel, the Human effort card, the results table and the labour slide). The two mp4 files are **not** in the repository (gitignored); ticket H4 (a person records the final videos) stays open, and the narration is the macOS `say` voice, not a person.

What the first runs found, all fixed before this one: each shot is its own browser context, so a shot that clicks a link must open the page that holds it first (shot 9); a role switch on the dashboard asked for its metrics with the previous role's token and showed a 403 and "No effort data yet" (the web now waits for the login first, with a test); the agency inbox showed a closure pack as a wall of JSON (now folded); a still card held for 15 seconds is almost invisible in a Playwright recording, so the numbers card and the labour slide are two shots, not one.

## Judge tour (C13), backup note

The header **Judge tour** button opens a 7-step, read-only overlay (problem, timeline, closure guard, agency inbox, effort counter, numbers, summary) and `/judge` is the one-page summary. It is not a shot of its own: the submission cut is at its length limit (170 s of shots plus 7 s of cards of the 180 s cap). Shot 14 names it in its narration and on its card as the hand-off. To film it for the backup video only, add a shot with `submission: false`, `optional: true` and `route: /judge`, plus a click on the button and a few holds, and re-run `make video-check`.

## Regenerate

Run it again after any change to the web app, the demo script or the snapshot, with the stack up on the merged code:

```
make triage-serve   # in its own terminal: shot 5 reads the Gulf-Arabic reply live (D15)
make video-check     # the storyboard validates, targets add up to 180 s or less (no stack, no ffmpeg needed)
make video-deps      # ffmpeg, ffprobe and macOS `say` (and the voice) are installed; says how to fix it if not
make up              # the stack must be running ...
make demo-reset      # ... and reset to the demo snapshot: the recording starts from that state
make video           # video-check, video-deps, then record, narrate, assemble (about 5 minutes)
```

`make video` stops before touching the stack when `ffmpeg`, `ffprobe` or `say` is missing (`brew install ffmpeg`; `say` exists on macOS only). It resets the demo again at the end. Check the result with `ffprobe tools/video/out/submission-3min.mp4` (at most 180 s), watch both files once, then replace the measured-result paragraph above with the new numbers.

`make video` is three steps, which can be run separately:

| Step | Command | What it does |
|---|---|---|
| Record | `node tools/video/record.ts` | Waits for the stack (3 tries, 2 minutes apart, if it is being rebuilt), runs `make demo-reset` and `make demo-scenario UNTIL=owner_accepted`, builds the web app with the http adapter into `tools/video/out/web-build` and serves it on port 4175, renders the two cards, then plays each shot in its own browser context with Playwright `recordVideo` (1280x800), a caption bar and the "Synthetic data" strip injected into every page. Writes `tools/video/out/raw/shot-N.webm` and `tools/video/out/shots.json` (status, real duration, cut windows). **Always**, even after a failure, it starts the scheduler again, stops the preview server and runs `make demo-reset`. Options: `--only 3,4` (a subset; the shots depend on each other, so use it to look at one shot after the ones before it), `--no-build`, `--no-reset`. |
| Narrate | `.venv/bin/python tools/video/narrate.py` | `say -v Samantha -r 185` for each shot to AIFF, ffmpeg to AAC; final length of a shot = max(narration + 0.8 s, the storyboard target); the recorded video, minus its cut windows, is padded with its last frame when shorter and sped up when longer (the speed-up is printed per shot; above 6x it warns). Writes `out/final/shot-N.mp4` and `out/timeline.json`. |
| Assemble | `tools/video/assemble.sh` | Cards to video, then an ffmpeg concat (no re-encode) of the cards and the shots into the two mp4 files, and the 180 s check. |

## What the recording does to the stack

It needs the stack up and the demo snapshot taken (`make up`, and `tools/demo/snapshot.sh status` says a snapshot exists). It runs the catch for real: `docker compose stop scheduler` in shot 3, the watchdog banner (60 to 80 s later), `docker compose start scheduler` in shot 5. Do not run it while someone else is using the stack, and not at the same time as `make test-e2e-live` (same port 4175). It leaves the demo reset (`make demo-reset`, state hash equal to the snapshot's) and the scheduler running. If the run is killed hard (not by an error), run `docker compose start scheduler` and `make demo-reset` yourself.

## Files

| File | What |
|---|---|
| `tools/video/storyboard.yaml` | The shot list: per shot the route, the actions (Playwright steps by accessible name or test id, the verbs are listed at the top of the file), the on-screen caption, the narration, the target duration, `submission`, `optional`. The only file to edit to change the video. |
| `tools/video/storyboard.py` | Loads and validates the YAML (`make video-check`); also prints it as JSON for `record.ts`, because Node has no YAML parser. |
| `tools/video/record.ts` | The recorder (Node 22.18 or later runs the TypeScript directly; Playwright is taken from `web/node_modules`). |
| `tools/video/narrate.py`, `tools/video/assemble.sh` | Narration and shot videos; the two mp4 files. |
| `tools/video/tests/test_storyboard.py` | Tests of the validator (part of `make test-tools`). |

Requirements: `ffmpeg` and `ffprobe` (`brew install ffmpeg`), macOS `say` with the voice named in `meta.voice` (`say -v '?'` lists them; Daniel, en_GB, also works), the `.venv` (PyYAML), Node 22.18 or later, the web app's `node_modules` (`make web-install`) and a Chromium for Playwright (the web app's own `pw-chromium.ts` finds one). Playwright's video recorder also needs its own ffmpeg build in `~/Library/Caches/ms-playwright/ffmpeg-<revision>`: `npx playwright install ffmpeg` (a small download), or point `ffmpeg-<revision>/ffmpeg-mac` at the Homebrew ffmpeg, which is what the first run on this machine did.

## Shots, and what to re-record after merges

Shots 1 to 10 are the 10 rows of the storyboard in the demo script, same order, titles and captions (`tests/test_demo_docs.py` checks it): the problem and what this is (1), **the catch** (shots 2 and 3: an owned case with the amber demo-approval banner, the scheduler stopped, the patient contacted and the clock jump; the watchdog, the restart and the escalation accepted), **the reveal** (shots 4 to 6: a bad reference refused on the prepared second case, the agency run (started from a terminal) and its one-item inbox, signed and closed, all in shot 4; the Gulf-Arabic reply routed to a person in shot 5; the patient's own screen in Arabic, right to left, in shot 6), the numbers (7 headline, 8 the honest range, 9 labour by role) and the ask (10). Their targets add up to 167 s so the two cards fit inside 174 s of the 180 s cap (see the comment at the top of the YAML). Waiting is cut from the video (the watchdog wait in shot 5, the scheduler stop and the property test run), so the viewer sees the banner appear and clear without a minute of nothing.

Shot 11, the effort counter on a case page (shot 15 of the earlier cut), is `optional: true` and `submission: false`, so it is in the backup video only. The recorder opens its route first and skips an optional shot, with the reason in `shots.json`, when the route redirects to the home page (the app sends unknown routes there) or answers 404, when the guard element never shows up, or when a step fails. It recorded in the proof run (15 of 15, none skipped). Whether it belongs in the 3-minute video is an editing decision: set `submission: true` on it in the YAML and shorten another shot, because `make video-check` and `assemble.sh` hold the total to 180 s.

Re-record (run `make video`) after any of these changes:

- **The web app's text or layout** changes a label the storyboard clicks or waits for (the YAML names buttons by their text, for example "Approve draft", "Request hand-off", and panels by test id). A failed required shot stops the run and leaves a screenshot, `out/raw/shot-N-failure.png`.
- **The numbers**: shot 7 is rendered from the headline table of `docs/results/results.md` by row name, so a regenerated table flows in by itself, but the captions and the narration of shots 7 to 9 (the range slide and the labour-by-role slide: 36% in reach, 20% programme-level, target not met in realistic scenarios), and the 30.5% of shot 1 and the 2,000 sequences of shot 3, are written in the YAML and must be changed by hand to match, with the same sources as in the demo script. `tests/test_demo_docs.py` fails if the spoken figures no longer match `docs/results/results.json`; check the rest against `results.md` after every `make evaluate`.
- **The demo scenario or the snapshot** (a different named patient, or a second case in another state): the recorder finds the two cases by their state (`owner_accepted`, `confirmatory_completed`). The named patient's owner is Dr. Huda Rahman (the web's **clinician** role), not Dr. Layla Haddad; the second case's evidence must be complete (A24), which the snapshot taken after A24 holds.
- **The demo script's storyboard**: keep the YAML in step with it (T-E15).

## Known quirks of the live recording

- **The closure is split over two shots, with a reload between.** Shot 7 types a reference that does not exist: the engine records the clinician review and then refuses the close (422), and the page does not refresh after a refusal. Shot 9 therefore opens the agency page again and follows the inbox's "Open case" link, which loads the case fresh (at "Clinician reviewed"), so the real sign-off posts only the close. In the live demo, reload the case page between the refusal and the real sign-off, or open it from the inbox as shot 9 does.
- **Every shot is its own browser context.** The role (a localStorage value) is carried from one shot to the next, but the page is not: a shot that clicks something must first open the page that holds it.
- **The second case shows red banners** ("Nobody is looking at this", an SLA breach) from shot 7 on, because the simulation clock was moved by 15 days earlier in the story and the scheduler ran on the second case too. That is what a live demo shows as well.
- **The Human effort card says "Target met"** (about 70 to 76%) on the demo's 22 cases. The minutes are assumed and the sample is a handful of seeded cases; the narration says so, and the labour slide that follows gives the simulation's figure, which is below the 40% target.
- **The triage cameo needs the live model (D15).** Run `make triage-serve` on the host (port 8765) in its own terminal before `make video`, and check `curl -s http://127.0.0.1:8765/health`. The recorder waits for the amber "A person must look at this reply" banner (a Gulf-Arabic reply always goes to a person, whatever the model reads) and for "model: qwen" in the reply result (the live model answered); shot 5 is a required shot, so without the server it fails loudly, with a screenshot, instead of recording the fail-safe by accident. The fail-safe itself (the reply read as "other", 0% sure, model "unavailable", sent to a person) is what you see if the server is down: it is correct behaviour but it is not what the narration says. `make demo-reset` prints a note when the server is not running. The opt-out path (STOP, answered by the keyword gate before any model) is not filmed.
- **Static pages are short in the raw video.** Playwright writes a frame only when the screen changes, so the end of a shot that stays still is missing from the raw webm and a still card held for a long time is almost invisible in it; `narrate.py` repeats the last frame to fill the shot. That is why each card is its own shot.
- **Timings** in the dashboard banner (for example "has not reported since ...") are wall-clock times of the recording, not simulated time.

## Honesty

Narration is synthetic text-to-speech, and the video says so on its last card. Every number comes from `docs/results/results.md` (synthetic, literature-anchored scenario) or `docs/evidence.md`, the same ones the demo script quotes. The recording shows the real application on the real API, with the real scheduler stopped and restarted, but the data is synthetic and the story is the scripted one: a named synthetic patient, a prepared second case. The recording is edited (waits cut, short shots held longer or sped up), as the storyboard says. Ticket H4 (a human records the backup and submission videos) stays open until the owner decides whether these automated videos replace that or are the fallback.
