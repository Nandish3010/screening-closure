# Submission video and backup demo video (E16)

Both videos are produced by a script from the storyboard of [demo-script.md](demo-script.md), with no human recording: Playwright drives the real web app against the live stack, a text-to-speech voice reads the narration, and ffmpeg joins the shots. **All data shown is synthetic, and the narration is synthetic text-to-speech (the macOS `say` voice Samantha), not a person.** Say so wherever the video is posted.

| File | What | Where |
|---|---|---|
| `submission-3min.mp4` | Title card (3 s), the shots flagged `submission: true` (13 shots, the storyboard of the demo script), end card (4 s). Hard cap 180 s: `assemble.sh` fails above it. | `tools/video/out/` (gitignored) |
| `backup-demo.mp4` | The same, plus every other shot that could be recorded (the two reveal shots below, when their pages exist). | `tools/video/out/` (gitignored) |

Last run (18 Oct, current stack, `make video` equivalent): 13 shots recorded, 2 skipped (the reveal shots, their pages are not in this build). `submission-3min.mp4` 177.8 s, 11.8 MB, 1280x800, 25 fps, H.264 and AAC; `backup-demo.mp4` the same 177.8 s and 11.8 MB, because the optional shots were skipped. The recording itself takes about 5 minutes (two `make demo-reset` of about a minute each, the 60 to 80 s wait for the watchdog is cut from the video), narration and assembly about 40 s.

## Regenerate

```
make video-check     # the storyboard validates, targets add up to 180 s or less (no stack needed)
make video           # video-check, then record, narrate, assemble (needs the live stack, about 7 minutes)
```

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

Shots 1 to 13 are the 13 rows of the storyboard in the demo script, same order and captions. Their targets are trimmed by 11 s in total so the two cards fit inside 180 s (see the comment at the top of the YAML). Waiting is cut from the video (the watchdog wait in shot 5, the bounce wait in shot 8, the scheduler stop and the property test run), so the viewer sees the banner appear and clear without a minute of nothing.

Two reveal shots are `optional: true` and `submission: false`, so they appear in the backup video only. The recorder opens their route first and skips the shot, with the reason in `shots.json`, when the route redirects to the home page (the app sends unknown routes there) or answers 404, when the guard element never shows up, or when a step fails:

| Shot | Needs | Guard | Present in this build |
|---|---|---|---|
| 14 `/agency` page (A22) | the `cut-agency` branch, web and API | `agency-org` | no, skipped: the route redirects to `/` |
| 15 effort counter on a case page (A21) | the `cut-auto` branch | `effort-counter` | no, skipped: the case page has no counter |

After the branches merge, run `make video` again: the reveal shots then record, and the backup video grows by about 20 s. Whether either belongs in the 3-minute video is an editing decision: set `submission: true` on it in the YAML, and shorten another shot, because `make video-check` and `assemble.sh` hold the total to 180 s.

Re-record (run `make video`) after any of these changes:

- **The web app's text or layout** changes a label the storyboard clicks or waits for (the YAML names buttons by their text, for example "Approve draft", "Request hand-off", and panels by test id). A failed required shot stops the run and leaves a screenshot, `out/raw/shot-N-failure.png`.
- **The numbers**: shot 12 is rendered from the headline table of `docs/results/results.md` by row name, so a regenerated table flows in by itself, but the caption and the narration of shot 12 (and the 30.5% of shot 1, the 2,000 sequences of shots 5 and 11) are written in the YAML and must be changed by hand to match, with the same sources as in the demo script. Check them against `results.md` after every `make evaluate`.
- **The demo scenario or the snapshot** (a different named patient, or a second case in another state): the recorder finds the two cases by their state (`owner_accepted`, `confirmatory_completed`).
- **The demo script's storyboard**: keep the YAML in step with it (T-E15).

## Known quirks of the live recording

- **The closure shot reloads the page after the refused close.** The first sign-off with a reference that does not exist is refused with 422, but only after the engine has recorded the clinician review; the page does not refresh after a refusal, so a second click would post the review again and be refused ("clinician_review not allowed from clinician_reviewed"). Shot 10 therefore opens the case again before the real sign-off. The demo script's live beat 13 ("click Record colonoscopy result, then Sign off and close case") hits the same thing: reload the case page in between.
- **The second case shows red banners** ("Nobody is looking at this", an SLA breach) in shots 10 and 11, because the simulation clock was moved by 20 days earlier in the story and the scheduler ran on the second case too. That is what a live demo shows as well.
- **Static pages are short in the raw video.** Playwright writes a frame only when the screen changes, so the end of a shot that stays still is missing from the raw webm; `narrate.py` repeats the last frame to fill the shot.
- **Timings** in the dashboard banner (for example "has not reported since ...") are wall-clock times of the recording, not simulated time.

## Honesty

Narration is synthetic text-to-speech, and the video says so on its last card. Every number comes from `docs/results/results.md` (synthetic, literature-anchored scenario) or `docs/evidence.md`, the same ones the demo script quotes. The recording shows the real application on the real API, with the real scheduler stopped and restarted, but the data is synthetic and the story is the scripted one: a named synthetic patient, a prepared second case. The recording is edited (waits cut, short shots held longer or sped up), as the storyboard says. Ticket H4 (a human records the backup and submission videos) stays open until the owner decides whether these automated videos replace that or are the fallback.
