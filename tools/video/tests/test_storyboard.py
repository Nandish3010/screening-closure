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
    assert ids[:10] == list(range(1, 11))  # the 10 submission shots of docs/submission/demo-script.md, same order


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
    problems = storyboard.missing_tools(sb, which=none, env={})
    assert any("ffmpeg not found" in p and "brew install ffmpeg" in p for p in problems)
    assert any("ffprobe not found" in p for p in problems)
    assert any("`say` not found" in p and "macOS" in p for p in problems)


def test_everything_present_reports_nothing():
    sb = storyboard.load()

    class Done:
        stdout = f"{sb['meta']['say_voice']}   en_US    # Hello\n"

    assert storyboard.missing_tools(sb, which=lambda n: "/usr/bin/" + n, run=lambda *a, **k: Done(), env={}) == []


def test_an_uninstalled_voice_is_reported():
    sb = storyboard.load()

    class Done:
        stdout = "Daniel   en_GB    # Hello\n"

    sb["meta"]["say_voice"] = "Nobody"
    problems = storyboard.missing_tools(sb, which=lambda n: "/usr/bin/" + n, run=lambda *a, **k: Done(), env={})
    assert problems and "'Nobody'" in problems[0]


def test_a_spoken_card_counts_for_its_narration_in_the_budget():
    card = {"seconds": 3, "narration": " ".join(["word"] * 26)}
    assert storyboard.card_seconds(card) == 26 / storyboard.WORDS_PER_SECOND + 0.4
    assert storyboard.card_seconds({"seconds": 3}) == 3.0


def test_the_kokoro_cache_honours_hf_hub_cache_and_hf_home(tmp_path):
    assert storyboard.hf_kokoro_dir({"HF_HUB_CACHE": str(tmp_path)}) == tmp_path / "models--hexgrad--Kokoro-82M"
    assert storyboard.hf_kokoro_dir({"HF_HOME": str(tmp_path)}) == tmp_path / "hub" / "models--hexgrad--Kokoro-82M"


def test_kokoro_is_unusable_without_the_python_or_the_model_and_says_why(tmp_path):
    sb = {"meta": {"tts": "kokoro", "voice": "bf_emma"}}
    py, why = storyboard.kokoro_status(sb, env={})
    assert py is None and "KOKORO_PYTHON" in why
    py, why = storyboard.kokoro_status(sb, env={"KOKORO_PYTHON": "/usr/bin/python3", "HF_HUB_CACHE": str(tmp_path)})
    assert py is None and "nothing is downloaded" in why


def test_narrate_refuses_to_fall_back_to_say_unless_asked(tmp_path, monkeypatch):
    import narrate
    sb = {"meta": {"tts": "kokoro", "voice": "bf_emma", "rate": 185, "say_voice": "Samantha"}}
    monkeypatch.delenv("KOKORO_PYTHON", raising=False)
    monkeypatch.setattr(narrate, "ALLOW_SAY", False)
    import pytest
    with pytest.raises(SystemExit) as e:
        narrate.synth("hello", tmp_path / "x.wav", sb)
    assert "Kokoro is not usable" in str(e.value) and "--allow-say-fallback" in str(e.value)


def test_the_committed_end_card_estimate_is_close_to_its_measured_length():
    # measured with Kokoro bf_emma: the 17-word end card narration took 6.2 s, so the card is 6.6 s with the lead and tail
    sb = storyboard.load()
    assert abs(storyboard.card_seconds(sb["cards"]["end"]) - 6.6) < 0.3


def test_assemble_decides_a_spoken_card_from_the_current_storyboard_not_from_old_files():
    text = (Path(__file__).resolve().parents[1] / "assemble.sh").read_text(encoding="utf-8")
    assert "storyboard.load()['cards']" in text and ".get('narration')" in text
    assert "d.get('spoken'" not in text


def test_narrate_removes_stale_card_audio_before_deciding_whether_a_card_is_spoken():
    text = (Path(__file__).resolve().parents[1] / "narrate.py").read_text(encoding="utf-8")
    assert text.index('audio_dir.glob(f"card-{name}.*")') < text.index('if card.get("narration")')


def test_record_passes_the_agency_budget_through_the_environment_and_fails_when_the_restore_fails():
    text = (Path(__file__).resolve().parents[1] / "record.ts").read_text(encoding="utf-8")
    assert "AGENCY_RUN_BUDGET_SECONDS=${" not in text  # never interpolated into a shell command
    assert "env: { ...base, ...env }" in text and "agencyBudget(process.env.VIDEO_AGENCY_BUDGET)" in text
    assert "20 to 3600" in text
    assert "if (rb2.code !== 0 && !failure) failure = new Error(" in text


def test_the_recorders_page_overlay_is_valid_javascript():
    """The caption bar, the synthetic strip and the id clean-up are one injected script; a quote inside its template literal once
    broke it silently (shots recorded without captions). Extract it with node and check its syntax."""
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node is not installed")
    text = (Path(__file__).resolve().parents[1] / "record.ts").read_text(encoding="utf-8")
    fn = text[text.index("function overlayScript"):text.index("// ---- running a shot")]
    fn = fn.replace('function overlayScript(caption: string, pos = "bottom"): string', 'function overlayScript(caption, pos = "bottom")')
    with tempfile.TemporaryDirectory() as d:
        gen = Path(d) / "gen.mjs"
        out = Path(d) / "overlay.js"
        gen.write_text(fn + f"\nimport fs from 'fs'; fs.writeFileSync({str(out)!r}, overlayScript('a caption', 'top'));", encoding="utf-8")
        assert subprocess.run([node, str(gen)], capture_output=True, text=True).returncode == 0
        r = subprocess.run([node, "--check", str(out)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        js = out.read_text(encoding="utf-8")
        assert "__vcap_bar" in js and "H1" in js
