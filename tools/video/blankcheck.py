#!/usr/bin/env python3
"""Blank-frame guard for the video pipeline (needs ffmpeg for files; the span logic is pure Python).

    tools/video/blankcheck.py FILE.mp4 [FILE.mp4 ...] [--fps 10] [--min-seconds 0.5] [--std 3.0]

Samples each video at --fps, computes the luma standard deviation of every sample (a 64x40 grey downscale) and fails (exit 1)
when a run of near-uniform frames (luma std below --std) lasts --min-seconds or longer. A plain white or plain black frame has
std ~0; every real shot or card has text, a caption bar or a border, so its std is far above the threshold. There are no
deliberate solid-colour transitions in this video, so nothing is exempt.

Why it exists: v7 shipped with shot 9 (a card page) rendered plain white for ~11 s, and nothing noticed.
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys

W, H = 64, 40


def luma_std(frame: bytes) -> float:
    """Population standard deviation of 8-bit grey pixels."""
    n = len(frame)
    if n == 0:
        return 0.0
    mean = sum(frame) / n
    return math.sqrt(sum((p - mean) ** 2 for p in frame) / n)


def find_blank_spans(stds: list[float], fps: float, min_seconds: float = 0.5, threshold: float = 3.0) -> list[tuple[float, float]]:
    """(start, end) seconds of every run of samples with std < threshold that lasts at least min_seconds.
    A sample at index i stands for the interval [i/fps, (i+1)/fps)."""
    spans: list[tuple[float, float]] = []
    start: int | None = None
    for i, s in enumerate(list(stds) + [float("inf")]):  # the sentinel closes a run that reaches the end
        if s < threshold:
            if start is None:
                start = i
        elif start is not None:
            a, b = start / fps, i / fps
            if b - a >= min_seconds - 1e-9:
                spans.append((round(a, 3), round(b, 3)))
            start = None
    return spans


def frame_stds(path: str, fps: float) -> list[float]:
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"blankcheck: ffmpeg failed on {path}: {r.stderr.decode(errors='replace')[-500:]}")
    data, size = r.stdout, W * H
    return [luma_std(data[i:i + size]) for i in range(0, len(data) - size + 1, size)]


def check(paths: list[str], fps: float = 10.0, min_seconds: float = 0.5, threshold: float = 3.0) -> dict[str, list[tuple[float, float]]]:
    return {p: find_blank_spans(frame_stds(p, fps), fps, min_seconds, threshold) for p in paths}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--min-seconds", type=float, default=0.5)
    ap.add_argument("--std", type=float, default=3.0)
    a = ap.parse_args(argv)
    bad = 0
    for path, spans in check(a.files, a.fps, a.min_seconds, a.std).items():
        if spans:
            bad += 1
            print(f"blankcheck: FAIL {path}: " + ", ".join(f"{s:.1f}-{e:.1f} s" for s, e in spans) + " blank or near-uniform", file=sys.stderr)
        else:
            print(f"blankcheck: ok   {path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
