"""The hosted demo's reply-triage service: the existing triage server contract (tools/triage/serve.py) in front of a llama.cpp server.

    python -m app.triage.gguf_server --llama-url http://triage-llama:8081 --port 8765

    GET  /health    -> {"status": "ok" | "loading", "model_id": ..., "warm": bool}
    POST /classify  -> {"text", "language_hint"}  =>  {"intent", "confidence", "model_id", "needs_human"}

Same answers as `make triage-serve`: it calls `classify_local`, so the opt-out gate runs first, then the model's nine-token decision with
the calibration of THIS build (TRIAGE_CALIBRATION_PATH, calibration_gguf.json), then the dialect group rule. The model is the same
fine-tuned Qwen2.5-1.5B + LoRA, merged and converted to GGUF and run on the CPU by llama.cpp on our own server: no third-party AI. It
is reachable only on the private compose network, keeps no copy of any text and logs none. The API reaches it through TRIAGE_REMOTE_URL.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.triage import classify as triage
from app.triage.scorer import LlamaServerScorer

MAX_BODY = 8192
_lock = threading.Lock()
_state = {"model_id": triage.UNAVAILABLE_MODEL_ID, "warm": False}


def load(llama_url: str, wait_seconds: float = 300.0) -> None:
    """Wait for llama-server, install the scorer with this build's calibration, score one reply so the first request is fast."""
    deadline = time.monotonic() + wait_seconds  # wall-clock-ok: startup wait for llama-server, not case logic
    while True:
        try:
            scorer = LlamaServerScorer(llama_url)
            break
        except Exception:  # noqa: BLE001 - llama-server still loading the model
            if time.monotonic() > deadline:  # wall-clock-ok: startup wait
                raise
            time.sleep(2)
    cal = triage.Calibration.load()
    triage.set_backend(scorer, cal)
    triage.classify_local("warm up", "en")
    _state.update(model_id=cal.model_id, warm=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "triage-gguf"

    def log_message(self, fmt, *args):  # noqa: D401 - no request line
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
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
            text, hint = body["text"], body.get("language_hint")
            if not isinstance(text, str) or hint not in (None, "en", "ar"):
                return self._send(422, {"error": "text must be a string; language_hint en, ar or null"})
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "bad request"})
        with _lock:  # one forward pass at a time on the CPU
            result = triage.classify_local(text, hint)
        self._send(200, result)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--llama-url", default="http://triage-llama:8081")
    ap.add_argument("--host", default="0.0.0.0")  # noqa: S104 - inside the private compose network only
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    threading.Thread(target=lambda: load(args.llama_url), daemon=True).start()  # /health says "loading" until warm
    print(f"triage-gguf: listening on {args.host}:{args.port}, model server {args.llama_url}", flush=True)
    Server((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
