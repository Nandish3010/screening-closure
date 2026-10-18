#!/usr/bin/env python3
"""Load and validate tools/video/storyboard.yaml (E16).

    storyboard.py --check        validate and print the budget (this is `make video-check`)
    storyboard.py --json         print the storyboard as JSON (record.ts reads it this way; Node has no YAML parser)
    storyboard.py --tools        check that ffmpeg, ffprobe and macOS `say` (with the storyboard's voice) are installed
                                 (this is `make video-deps`; `make video` runs it before it touches the stack)

Validation: every shot has id, route, actions, caption, narration and a positive duration; ids are unique; every
action is a known verb; and the targets of the shots flagged `submission: true`, plus the title and end cards, add up
to at most the submission cap (180 s).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
STORYBOARD = HERE / "storyboard.yaml"

VERBS = {"goto", "role", "click", "select", "fill", "scroll", "wait_for", "hold", "caption", "cut", "shell",
         "terminal", "card", "advance_clock"}
ROLES = {"coordinator", "owner", "specialist", "clinician", "supervisor", "patient"}
REQUIRED = ("id", "route", "actions", "caption", "narration", "duration")


def load(path: Path = STORYBOARD) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate(sb: dict) -> list[str]:
    errors: list[str] = []
    for key in ("meta", "cards", "shots"):
        if key not in sb:
            errors.append(f"missing top-level key {key!r}")
    if errors:
        return errors
    seen: set = set()
    for shot in sb["shots"]:
        sid = shot.get("id")
        where = f"shot {sid}"
        for field in REQUIRED:
            if field not in shot or shot[field] in (None, "", []):
                errors.append(f"{where}: missing {field}")
        if sid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(sid)
        dur = shot.get("duration")
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0:
            errors.append(f"{where}: duration must be a positive number of seconds")
        cuts = 0
        for i, act in enumerate(shot.get("actions") or []):
            if not isinstance(act, dict) or len(act) != 1:
                errors.append(f"{where}: action {i + 1} must be a map with exactly one key")
                continue
            verb = next(iter(act))
            if verb not in VERBS:
                errors.append(f"{where}: action {i + 1}: unknown verb {verb!r}")
            if verb == "role" and act[verb] not in ROLES:
                errors.append(f"{where}: action {i + 1}: unknown role {act[verb]!r}")
            if verb == "cut":
                cuts += 1 if act[verb] == "start" else -1
                if cuts not in (0, 1):
                    errors.append(f"{where}: action {i + 1}: cut start/end markers must alternate")
        if cuts != 0:
            errors.append(f"{where}: cut start without a matching end")
    return errors


def budget(sb: dict) -> dict:
    submission = [s for s in sb["shots"] if s.get("submission")]
    shots = sum(float(s["duration"]) for s in submission)
    cards = float(sb["cards"]["title"]["seconds"]) + float(sb["cards"]["end"]["seconds"])
    return {
        "submission_shots": len(submission),
        "submission_shot_seconds": shots,
        "card_seconds": cards,
        "submission_total": shots + cards,
        "cap": float(sb["meta"].get("submission_cap_seconds", 180)),
        "backup_shots": len(sb["shots"]),
        "backup_total": sum(float(s["duration"]) for s in sb["shots"]) + cards,
    }


def missing_tools(sb: dict, which=shutil.which, run=subprocess.run) -> list[str]:
    """What is needed to narrate and assemble, and is not there. Empty when ffmpeg, ffprobe and `say` (and its voice) are present."""
    problems: list[str] = []
    for tool in ("ffmpeg", "ffprobe"):
        if not which(tool):
            problems.append(f"{tool} not found on PATH: install it with `brew install ffmpeg` (narrate.py and assemble.sh need it)")
    if not which("say"):
        problems.append("`say` not found: narration uses the macOS text-to-speech command, so `make video` runs on macOS only")
    else:
        voice = sb.get("meta", {}).get("voice")
        try:
            listing = run(["say", "-v", "?"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
        if voice and listing and not any(line.split()[:1] == [voice] for line in listing.splitlines()):
            problems.append(f"the macOS voice {voice!r} named in storyboard.yaml (meta.voice) is not installed: `say -v '?'` lists "
                            f"the installed voices (System Settings > Accessibility > Spoken Content > System Voice > Manage Voices)")
    return problems


def main(argv: list[str]) -> int:
    sb = load()
    if "--tools" in argv:
        problems = missing_tools(sb)
        if problems:
            print("video-deps: FAIL", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"video-deps: PASS: ffmpeg, ffprobe and say (voice {sb['meta'].get('voice')}) are available")
        return 0
    if "--json" in argv:
        json.dump(sb, sys.stdout)
        return 0
    errors = validate(sb)
    if not errors:
        b = budget(sb)
        if b["submission_total"] > b["cap"]:
            errors.append(f"submission targets plus cards add up to {b['submission_total']:.0f} s, over the {b['cap']:.0f} s cap")
    if errors:
        print("video-check: FAIL", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    b = budget(sb)
    print(f"video-check: PASS: {len(sb['shots'])} shots; submission {b['submission_shots']} shots, "
          f"{b['submission_shot_seconds']:.0f} s + {b['card_seconds']:.0f} s cards = {b['submission_total']:.0f} s "
          f"(cap {b['cap']:.0f} s); backup {b['backup_shots']} shots, {b['backup_total']:.0f} s from targets")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
