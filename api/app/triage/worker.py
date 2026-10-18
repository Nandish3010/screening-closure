"""A long-lived scoring process: `python -m app.triage.worker MODEL_PATH ADAPTER_PATH`.

Run with the interpreter of tools/triage/.venv (the only one with mlx). Reads one JSON object per line on stdin
({"text": "..."}), answers one JSON line on stdout ({"logits": [...], "mass": x} or {"error": "..."}), and prints
{"ready": true} once the model is loaded. It reads nothing but stdin and opens no network connection.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    from .scorer import MlxScorer

    model_path = argv[1]
    adapter_path = argv[2] if len(argv) > 2 and argv[2] != "-" else None
    scorer = MlxScorer(model_path, adapter_path)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            print(json.dumps(scorer.score(json.loads(line)["text"])), flush=True)
        except Exception as exc:  # noqa: BLE001 - report and keep serving
            print(json.dumps({"error": type(exc).__name__}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
