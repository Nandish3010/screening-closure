#!/bin/sh
# E16: concatenate the per-shot videos into the two deliverables (needs ffmpeg and ffprobe).
#
#   tools/video/assemble.sh
#
# Reads tools/video/out/timeline.json (written by narrate.py) and the title and end cards (written by record.ts), and writes
#   out/submission-3min.mp4   title card + the shots flagged `submission: true` + end card, hard cap 180 s (fails above it)
#   out/backup-demo.mp4       title card + every recorded shot + end card
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${VIDEO_OUT:-$HERE/out}
ROOT=$(cd "$HERE/../.." && pwd)
PY=python3
[ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
CAP=180
cd "$OUT"

[ -f timeline.json ] || { echo "assemble: out/timeline.json missing: run narrate.py first" >&2; exit 1; }
[ -f cards/title.png ] && [ -f cards/end.png ] || { echo "assemble: out/cards/*.png missing: run record.ts first" >&2; exit 1; }

# The cards: a still, with silent audio, in the same encoding as the shots so the concat needs no re-encode.
secs() { "$PY" -c "import sys; sys.path.insert(0, '$HERE'); import storyboard; print(storyboard.load()['cards']['$1']['seconds'])"; }
mkdir -p final
for name in title end; do
  s=$(secs "$name")
  ffmpeg -y -loglevel error -loop 1 -t "$s" -framerate 25 -i "cards/$name.png" -f lavfi -t "$s" -i anullsrc=r=48000:cl=mono \
    -vf "scale=1280:800,format=yuv420p" -c:v libx264 -preset fast -crf 20 -r 25 -c:a aac -b:a 128k -ar 48000 -ac 1 -shortest \
    -movflags +faststart "final/card-$name.mp4"
done

list() { # $1 = all|submission
  echo "file 'final/card-title.mp4'"
  "$PY" - "$1" <<'PYEOF'
import json, sys
for t in json.load(open("timeline.json"))["shots"]:
    if sys.argv[1] == "all" or t["submission"]:
        print(f"file '{t['file']}'")
PYEOF
  echo "file 'final/card-end.mp4'"
}

for kind in submission backup; do
  if [ "$kind" = submission ]; then name=submission-3min; sel=submission; else name=backup-demo; sel=all; fi
  list "$sel" > "$name.txt"
  ffmpeg -y -loglevel error -f concat -safe 0 -i "$name.txt" -c copy -movflags +faststart "$name.mp4"
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$name.mp4")
  size=$(wc -c < "$name.mp4" | tr -d ' ')
  echo "assemble: $name.mp4 $d s, $size bytes"
  if [ "$kind" = submission ]; then
    "$PY" -c "import sys; d=float('$d'); sys.exit(0 if d <= $CAP else 1)" || { echo "assemble: FAIL: $name.mp4 is $d s, over the $CAP s cap: shorten narration or targets" >&2; exit 1; }
  fi
done
