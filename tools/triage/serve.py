"""Host-side reply-triage service (D15): the fine-tuned MLX model behind a tiny HTTP server.

MLX cannot run inside the Linux API container, so the compose API asks this process (TRIAGE_REMOTE_URL=
http://host.docker.internal:8765). Run it with the interpreter of tools/triage/.venv:

    make triage-serve        # or: tools/triage/.venv/bin/python tools/triage/serve.py

It loads the model once, scores a warm-up reply before it starts listening, and answers

    GET  /health    -> {"status": "ok", "model_id": ..., "warm": true}
    POST /classify  -> {"text": "...", "language_hint": "ar"|"en"|null}  =>  {"intent", "confidence", "model_id", "needs_human"}

with exactly the contract of app.triage.classify.classify (classify_local: the same code answers, including the deterministic opt-out
gate). Binds 127.0.0.1 only, serves one request at a time, keeps no copy of any text and logs none.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))

from app.triage import classify as triage  # noqa: E402

MAX_BODY = 8192
_lock = threading.Lock()
_state = {"model_id": triage.UNAVAILABLE_MODEL_ID, "warm": False}


def load_model() -> None:
    """Load the real model in this process (this interpreter has mlx) and score one reply so the first request is fast."""
    from app.triage.scorer import MlxScorer  # noqa: PLC0415

    model_path = os.environ.get("TRIAGE_MODEL_PATH", str(REPO / "tools/triage/models/qwen2.5-1.5b-4bit"))
    adapter_path = os.environ.get("TRIAGE_ADAPTER_PATH", str(REPO / "api/app/triage/adapters"))
    cal = triage.Calibration.load()
    triage.set_backend(MlxScorer(model_path, adapter_path), cal)
    triage.classify_local("warm up", "en")
    _state.update(model_id=cal.model_id, warm=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "triage-serve"

    def log_message(self, fmt, *args):  # noqa: D401 - no request line: it carries no text, but keep the console quiet
        pass

    def _send(self, code: int, obj: dict, headers: dict | None = None) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok" if _state["warm"] else "loading", **_state})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/classify":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self._send(413, {"error": "body size"})
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            text = body["text"]
            hint = body.get("language_hint")
            if not isinstance(text, str) or hint not in (None, "en", "ar"):
                return self._send(422, {"error": "text must be a string; language_hint en, ar or null"})
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "bad request"})
        started = time.perf_counter()
        with _lock:  # one forward pass at a time on the GPU
            result = triage.classify_local(text, hint)
        self._send(200, result, {"X-Latency-Ms": f"{(time.perf_counter() - started) * 1000:.1f}"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    t0 = time.perf_counter()
    load_model()
    print(f"triage-serve: model {_state['model_id']} loaded and warm in {time.perf_counter() - t0:.1f}s; "
          f"listening on http://{args.host}:{args.port}", flush=True)
    Server((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
