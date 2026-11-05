"""The blank-frame guard (tools/video/blankcheck.py): the span logic is pure, the ffmpeg path is checked with generated clips."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import blankcheck  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_luma_std_of_flat_and_busy_frames():
    assert blankcheck.luma_std(bytes([255]) * 100) == 0
    assert blankcheck.luma_std(bytes([0, 255]) * 50) == pytest.approx(127.5)


def test_a_run_of_flat_samples_of_half_a_second_or_more_is_flagged():
    # 10 fps: 5 flat samples = 0.5 s
    stds = [40.0] * 10 + [0.0] * 5 + [40.0] * 10
    assert blankcheck.find_blank_spans(stds, 10) == [(1.0, 1.5)]


def test_a_shorter_flash_is_not_flagged():
    assert blankcheck.find_blank_spans([40.0] * 10 + [0.0] * 4 + [40.0] * 10, 10) == []


def test_a_blank_run_at_the_start_or_end_is_flagged():
    assert blankcheck.find_blank_spans([0.0] * 6 + [30.0] * 4, 10) == [(0.0, 0.6)]
    assert blankcheck.find_blank_spans([30.0] * 4 + [1.0] * 8, 10) == [(0.4, 1.2)]


def test_a_whole_blank_video_and_threshold():
    assert blankcheck.find_blank_spans([0.0] * 140, 10) == [(0.0, 14.0)]
    assert blankcheck.find_blank_spans([2.9] * 10, 10, threshold=3.0) == [(0.0, 1.0)]
    assert blankcheck.find_blank_spans([3.1] * 10, 10, threshold=3.0) == []


def _clip(path: Path, graph: str, seconds: int = 3) -> str:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", graph, "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


@needs_ffmpeg
def test_cli_fails_on_a_white_clip_and_passes_a_real_looking_one(tmp_path, capsys):
    white = _clip(tmp_path / "white.mp4", "color=c=white:s=320x200:r=25")
    busy = _clip(tmp_path / "busy.mp4", "testsrc=s=320x200:r=25")
    assert blankcheck.main([busy]) == 0
    assert blankcheck.main([white]) == 1
    assert "white.mp4" in capsys.readouterr().err
    assert blankcheck.main([busy, white]) == 1  # one bad file fails the whole run


@needs_ffmpeg
def test_a_clip_that_goes_blank_in_the_middle_is_located(tmp_path):
    busy = _clip(tmp_path / "a.mp4", "testsrc=s=320x200:r=25", 2)
    white = _clip(tmp_path / "b.mp4", "color=c=white:s=320x200:r=25", 1)
    listing = tmp_path / "l.txt"
    listing.write_text(f"file '{busy}'\nfile '{white}'\nfile '{busy}'\n")
    joined = tmp_path / "joined.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)], check=True)
    ((start, end),) = blankcheck.check([str(joined)])[str(joined)]
    assert start == pytest.approx(2.0, abs=0.15) and end == pytest.approx(3.0, abs=0.15)
