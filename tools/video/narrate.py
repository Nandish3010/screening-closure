#!/usr/bin/env python3
"""E16: narration audio per shot, and each shot's final video (E16, step 3).

For every recorded shot in out/shots.json:
  1. `say -v <voice> -r <rate> -o shot-N.aiff "<narration>"` (macOS text to speech, synthetic voice), then ffmpeg to AAC;
  2. final length = max(narration length + a short tail, the storyboard target);
  3. the recorded video, minus its cut windows (dead waiting), is padded with its last frame when shorter than the
     final length, and sped up when longer; the narration starts 0.3 s in; written as out/final/shot-N.mp4
     (1280x800, 25 fps, H.264 + AAC) so assemble.sh can concatenate without re-encoding.

Writes out/timeline.json (per shot: target, narration, final, speed-up, submission flag). Needs ffmpeg and `say`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storyboard  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("VIDEO_OUT", HERE / "out"))
LEAD = 0.3      # silence before the narration starts
TAIL = 0.5      # silence after it
HEAD_TRIM = 0.3  # the first frames of a Playwright recording are blank
MAX_SPEED = 6.0  # beyond this a shot is too fast to read: warn


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"narrate: {' '.join(cmd[:3])} failed:\n{r.stderr[-1500:]}")
    return r.stdout


def duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).strip())


def main() -> int:
    sb = storyboard.load()
    errs = storyboard.validate(sb)
    if errs:
        raise SystemExit("narrate: storyboard invalid: " + "; ".join(errs))
    shots = json.loads((OUT / "shots.json").read_text())["shots"]
    by_id = {s["id"]: s for s in sb["shots"]}
    voice, rate = sb["meta"]["voice"], str(sb["meta"]["rate"])
    audio_dir, final_dir = OUT / "audio", OUT / "final"
    audio_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    timeline = []
    for rec in shots:
        if rec["status"] != "recorded":
            print(f"shot {rec['id']:>2}: {rec['status']} ({rec.get('reason', '')}), no narration")
            continue
        sid = rec["id"]
        spec = by_id[sid]
        aiff, m4a = audio_dir / f"shot-{sid}.aiff", audio_dir / f"shot-{sid}.m4a"
        run(["say", "-v", voice, "-r", rate, "-o", str(aiff), "--", spec["narration"]])
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "48000", "-ac", "1", "-c:a", "aac", "-b:a", "128k", str(m4a)])
        narr = duration(m4a)
        final = max(narr + LEAD + TAIL, float(spec["duration"]))

        # keep-intervals of the raw video: everything except the head and the cut windows
        dur = rec["duration"]
        cuts = sorted([[0.0, HEAD_TRIM]] + [[min(a, dur), min(b, dur)] for a, b in rec.get("cuts", [])])
        keep, pos = [], 0.0
        for a, b in cuts:
            if a > pos:
                keep.append((pos, a))
            pos = max(pos, b)
        if pos < dur:
            keep.append((pos, dur))
        kept = sum(b - a for a, b in keep)
        parts = "".join(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[p{i}];" for i, (a, b) in enumerate(keep))
        chain = "".join(f"[p{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=0[c]"
        if kept > final:
            speed = kept / final
            tail = f";[c]setpts=PTS/{speed:.5f}[s]"
        else:
            speed = 1.0
            tail = f";[c]tpad=stop_mode=clone:stop_duration={final - kept:.3f}[s]"
        vf = f"{parts}{chain}{tail};[s]fps=25,scale={sb['meta']['width']}:{sb['meta']['height']},format=yuv420p[v]"
        af = f"[1:a]adelay={int(LEAD * 1000)}:all=1,apad=whole_dur={final:.3f}[a]"
        out = final_dir / f"shot-{sid}.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", rec["video"], "-i", str(m4a),
             "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{final:.3f}",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-r", "25", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
             "-movflags", "+faststart", str(out)])
        warn = f"  WARNING: sped up {speed:.1f}x" if speed > MAX_SPEED else ""
        over = " (narration longer than the target)" if narr + LEAD + TAIL > spec["duration"] else ""
        print(f"shot {sid:>2}: target {spec['duration']:>4.0f} s, narration {narr:5.1f} s, final {final:5.1f} s, "
              f"video {dur:5.1f} s raw / {kept:5.1f} s kept, speed {speed:.2f}x{over}{warn}")
        timeline.append({"id": sid, "title": spec["title"], "submission": bool(spec.get("submission")), "target": spec["duration"],
                         "narration_seconds": round(narr, 2), "final": round(final, 2), "speed": round(speed, 2),
                         "file": str(out)})

    (OUT / "timeline.json").write_text(json.dumps({"shots": timeline}, indent=2))
    sub = sum(t["final"] for t in timeline if t["submission"])
    cards = storyboard.budget(sb)["card_seconds"]
    print(f"narrate: {len(timeline)} shots; submission shots {sub:.1f} s + cards {cards:.0f} s = {sub + cards:.1f} s (cap {sb['meta']['submission_cap_seconds']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
