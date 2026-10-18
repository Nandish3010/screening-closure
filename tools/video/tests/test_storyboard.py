"""E16: the storyboard validator (`make video-check`). No stack, no ffmpeg, no browser."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storyboard  # noqa: E402


def test_committed_storyboard_is_valid_and_within_the_cap():
    sb = storyboard.load()
    assert storyboard.validate(sb) == []
    b = storyboard.budget(sb)
    assert b["submission_total"] <= b["cap"] == 180
    # The optional reveal shots are in the backup video only.
    optional = [s for s in sb["shots"] if s.get("optional")]
    assert optional and all(not s.get("submission") for s in optional)


def test_every_demo_script_storyboard_row_has_a_shot():
    ids = [s["id"] for s in storyboard.load()["shots"]]
    assert ids[:14] == list(range(1, 15))  # the 14 submission shots of docs/submission/demo-script.md, same order


def test_missing_fields_and_unknown_verbs_are_reported():
    sb = storyboard.load()
    bad = copy.deepcopy(sb)
    del bad["shots"][0]["narration"]
    bad["shots"][1]["actions"].append({"teleport": "now"})
    bad["shots"][2]["duration"] = 0
    errs = storyboard.validate(bad)
    assert any("shot 1: missing narration" in e for e in errs)
    assert any("unknown verb 'teleport'" in e for e in errs)
    assert any("shot 3: duration" in e for e in errs)


def test_unbalanced_cut_markers_are_reported():
    bad = copy.deepcopy(storyboard.load())
    bad["shots"][0]["actions"].append({"cut": "start"})
    assert any("without a matching end" in e for e in storyboard.validate(bad))


def test_over_budget_submission_is_caught(monkeypatch, capsys):
    sb = storyboard.load()
    sb["shots"][0]["duration"] = 60
    monkeypatch.setattr(storyboard, "load", lambda *a, **k: sb)
    assert storyboard.main(["--check"]) == 1
    assert "over the 180 s cap" in capsys.readouterr().err


def test_missing_ffmpeg_and_say_are_reported_with_the_fix(capsys):
    sb = storyboard.load()
    none = lambda name: None  # noqa: E731
    problems = storyboard.missing_tools(sb, which=none)
    assert any("ffmpeg not found" in p and "brew install ffmpeg" in p for p in problems)
    assert any("ffprobe not found" in p for p in problems)
    assert any("`say` not found" in p and "macOS" in p for p in problems)


def test_everything_present_reports_nothing():
    sb = storyboard.load()

    class Done:
        stdout = f"{sb['meta']['voice']}   en_US    # Hello\n"

    assert storyboard.missing_tools(sb, which=lambda n: "/usr/bin/" + n, run=lambda *a, **k: Done()) == []


def test_an_uninstalled_voice_is_reported():
    sb = storyboard.load()

    class Done:
        stdout = "Daniel   en_GB    # Hello\n"

    sb["meta"]["voice"] = "Nobody"
    problems = storyboard.missing_tools(sb, which=lambda n: "/usr/bin/" + n, run=lambda *a, **k: Done())
    assert problems and "'Nobody'" in problems[0]
