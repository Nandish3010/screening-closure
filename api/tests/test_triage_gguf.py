"""The hosted demo's GGUF triage path (deploy/gcp): the llama-server scorer, the service contract, the calibration switch.

A fake llama-server stands in for llama.cpp (no model, no Docker). The real model is evaluated by tools/triage/evaluate_gguf.py."""

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.triage import classify as triage
from app.triage import dialect, gguf_server
from app.triage.labels import INTENTS
from app.triage.scorer import LlamaServerScorer

# the fake model: label i -> token id 100 + i; the logprobs it returns depend on the reply text
LOGPROBS = {"yes": {"confirm": -0.05, "reschedule": -4.0, "other": -5.0}, "mystery": {"other": -2.0, "question": -2.1}}


class Fake(BaseHTTPRequestHandler):
    seen_prompts: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            out = {"tokens": [100 + INTENTS.index(body["content"]), 5]}
        elif self.path == "/apply-template":
            out = {"prompt": "<|im_start|>user\n" + body["messages"][0]["content"] + "<|im_end|>\n<|im_start|>assistant\n"}
        elif self.path == "/completion":
            Fake.seen_prompts.append(body["prompt"])
            key = "yes" if "yes" in body["prompt"] else "mystery"
            tops = [{"id": 100 + INTENTS.index(k), "token": k, "logprob": v} for k, v in LOGPROBS[key].items()]
            tops.append({"id": 7, "token": "zzz", "logprob": -9.0})
            out = {"completion_probabilities": [{"id": 100, "token": "confirm", "logprob": -0.05, "top_logprobs": tops}]}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


@pytest.fixture
def llama():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_scorer_returns_the_nine_logprobs_in_label_order_with_a_floor(llama):
    sc = LlamaServerScorer(llama).score("yes please")
    assert len(sc["logits"]) == 9
    assert sc["logits"][INTENTS.index("confirm")] == pytest.approx(-0.05)
    assert sc["logits"][INTENTS.index("cannot_attend")] == LlamaServerScorer.FLOOR  # not in the returned log-probabilities
    assert sc["mass"] == pytest.approx(math.exp(-0.05) + math.exp(-4.0) + math.exp(-5.0))
    assert "Classify the patient reply intent: yes please" in Fake.seen_prompts[-1]  # the training prompt, via the model's template


def test_service_decision_uses_this_builds_calibration(llama, tmp_path, monkeypatch):
    cal_file = tmp_path / "cal.json"
    cal_file.write_text(json.dumps({"temperature": 1.0, "threshold": 0.5, "mass_floor": 0.5,
                                    "model_id": "qwen2.5-1.5b-q8_0-gguf+lora-test"}))
    monkeypatch.setenv(triage.CALIBRATION_ENV, str(cal_file))
    gguf_server.load(llama, wait_seconds=5)
    try:
        ok = triage.classify_local("yes I will come", "en")
        assert (ok["intent"], ok["needs_human"], ok["model_id"]) == ("confirm", False, "qwen2.5-1.5b-q8_0-gguf+lora-test")
        assert triage.classify_local("mystery", "en")["needs_human"] is True       # other/question are always a person
        assert triage.classify_local("stop messaging me", "en")["intent"] == "opt_out"  # the gate runs before the model
        assert gguf_server._state["warm"] is True
    finally:
        triage.reset()


def test_calibration_group_thresholds_replace_the_module_constants(tmp_path, monkeypatch):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"temperature": 1.0, "threshold": 0.5, "mass_floor": 0.5, "model_id": "m", "group_thresholds": {"arabizi": 0.99}}))
    monkeypatch.setenv(triage.CALIBRATION_ENV, str(f))
    cal = triage.Calibration.load()
    assert cal.group_thresholds == {"arabizi": 0.99}
    arabizi = "ok ma3 el salama, akeed ajee"
    res = {"intent": "confirm", "confidence": 0.95, "model_id": "m", "needs_human": False}
    assert dialect.apply_group_rule(res, arabizi)["needs_human"] is False                         # 0.95 clears the MLX 0.918
    assert dialect.apply_group_rule(res, arabizi, cal.group_thresholds)["needs_human"] is True    # not this build's 0.99
    assert dialect.group_reason({**res, "needs_human": True}, arabizi, cal.group_thresholds) == "arabizi"


def test_default_calibration_is_unchanged(monkeypatch):
    monkeypatch.delenv(triage.CALIBRATION_ENV, raising=False)
    cal = triage.Calibration.load()
    assert cal.group_thresholds is None and cal.model_id.startswith("qwen2.5-1.5b-4bit+lora-")
