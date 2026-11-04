"""Kokoro-82M text to speech for the voiceover: kokoro_tts.py <text.txt> <out.wav>  (24 kHz mono).

Runs in a separate Python that has `kokoro` installed (KOKORO_PYTHON, see narrate.py), offline (HF_HUB_OFFLINE=1): the model
is read from the local Hugging Face cache and nothing is downloaded. The voice and speed come from KOKORO_VOICE (default
bf_emma) and KOKORO_SPEED (default 1.0). Each sentence is synthesised on its own, the model's own lead and tail silence is
trimmed and a short breath is put between sentences; <out.wav>.json lists where every sentence starts and ends (seconds).
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from kokoro import KPipeline  # noqa: E402

SR = 24000
GAP = 0.14  # seconds of breath between sentences

text = open(sys.argv[1], encoding="utf-8").read().strip()
out = sys.argv[2]
voice = os.environ.get("KOKORO_VOICE", "bf_emma")
speed = float(os.environ.get("KOKORO_SPEED", "1.0"))
pipeline = KPipeline(lang_code=voice[0], repo_id="hexgrad/Kokoro-82M")  # 'a' = US English, 'b' = UK English
parts, spans, t = [], [], 0.0
gap = np.zeros(int(GAP * SR), dtype=np.float32)
for _, _, chunk in pipeline(text, voice=voice, speed=speed, split_pattern=r"(?<=[.!?])\s+"):
    chunk = np.asarray(chunk, dtype=np.float32)
    loud = np.flatnonzero(np.abs(chunk) > 0.01)
    if len(loud):
        chunk = chunk[max(0, loud[0] - int(0.02 * SR)) : loud[-1] + int(0.06 * SR)]
    if parts:
        parts.append(gap)
        t += GAP
    d = len(chunk) / SR
    spans.append({"a": round(t, 3), "b": round(t + d, 3)})
    parts.append(chunk)
    t += d
sf.write(out, np.concatenate(parts), SR)
with open(out + ".json", "w") as f:
    json.dump(spans, f)
