"""D15: the remote classifier (a fake HTTP server stands in for tools/triage/serve.py) and the opt-out gate."""

import json
import urllib.error
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.triage import classify as triage
from app.triage import optout


class Fake:
    """A local HTTP server answering POST /classify with `reply` (a dict, raw bytes, or a callable), after `delay` seconds."""

    def __init__(self, reply, status=200, delay=0.0):
        outer = self
        outer.reply, outer.status, outer.delay, outer.bodies = reply, status, delay, []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                time.sleep(outer.delay)
                data = outer.reply if isinstance(outer.reply, bytes) else json.dumps(outer.reply).encode()
                try:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


GOOD = {"intent": "reschedule", "confidence": 0.93, "model_id": "m-1", "needs_human": False}
UNAVAILABLE = {"intent": "other", "confidence": 0.0, "model_id": "unavailable", "needs_human": True}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    triage.reset()
    monkeypatch.delenv("TRIAGE_REMOTE_URL", raising=False)
    yield
    triage.reset()


@pytest.fixture
def fake():
    made = []

    def make(*a, **k):
        f = Fake(*a, **k)
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()


def test_remote_answer_is_used_and_the_text_and_hint_are_sent(fake, monkeypatch):
    f = fake(GOOD)
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    assert triage.classify("ممكن نأجل الموعد؟", "ar") == GOOD
    assert f.bodies == [{"text": "ممكن نأجل الموعد؟", "language_hint": "ar"}]


def test_remote_is_not_asked_when_unset_and_local_path_is_the_fail_safe_default(fake):
    triage.set_backend(None)
    assert triage.classify("I will come tomorrow", "en") == UNAVAILABLE


@pytest.mark.parametrize("intent", ["question", "other", "opt_out"])
def test_remote_cannot_clear_needs_human_on_an_always_human_intent(fake, monkeypatch, intent):
    f = fake({**GOOD, "intent": intent, "needs_human": False})
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    assert triage.classify("hello there")["needs_human"] is True


def test_a_too_long_reply_always_needs_a_human_even_if_the_remote_says_otherwise(fake, monkeypatch):
    f = fake(GOOD)
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    assert triage.classify("ok " * 400)["needs_human"] is True
    assert len(f.bodies[0]["text"]) <= 600


def test_timeout_is_the_fail_safe_and_is_bounded(fake, monkeypatch):
    f = fake(GOOD, delay=3.0)
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    t0 = time.monotonic()
    assert triage.classify("see you then") == UNAVAILABLE
    assert time.monotonic() - t0 < 2.8


def test_unreachable_server_is_the_fail_safe(monkeypatch):
    f = Fake(GOOD)
    url = f.url
    f.close()
    monkeypatch.setenv("TRIAGE_REMOTE_URL", url)
    assert triage.classify("see you then") == UNAVAILABLE


@pytest.mark.parametrize("reply,status", [
    (b"not json", 200), (b"[]", 200), (GOOD, 500), ({**GOOD, "intent": "banana"}, 200),
    ({**GOOD, "confidence": 1.5}, 200), ({**GOOD, "confidence": float("nan")}, 200), ({**GOOD, "confidence": True}, 200),
    ({**GOOD, "needs_human": "no"}, 200), ({**GOOD, "model_id": ""}, 200),
    ({k: v for k, v in GOOD.items() if k != "model_id"}, 200),
])
def test_malformed_or_failing_remote_answers_are_the_fail_safe(fake, monkeypatch, reply, status):
    f = fake(reply, status=status)
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    assert triage.classify("see you then") == UNAVAILABLE


def test_empty_text_never_reaches_the_remote(fake, monkeypatch):
    f = fake(GOOD)
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    assert triage.classify("   ")["needs_human"] is True and f.bodies == []


# ---------------------------------------------------------------------------------------------- the gate

STOPS = [
    "STOP", "stop!", "Please stop messaging me", "unsubscribe", "I want to opt out", "do not contact me again",
    "Remove me from the list", "no more messages please", "I withdraw my consent",
    "وقف الرسائل", "توقفوا عن مراسلتي", "لا تراسلوني", "لا تتصلوا فيني", "ما أبي رسائل", "ما ابي رسايل", "الغاء الاشتراك",
    "سحبت موافقتي", "اشطب رقمي",
    "wa2af", "Please wa2af el rasayel", "la tkalmoni", "ma abgha rasayel", "mabi rasail", "7ezf raqmi",
]
NOT_STOPS = [
    "ان شاء الله بجي يوم الأحد", "شنو يعني هالنتيجة؟", "I will come on Sunday", "stopping by tomorrow", "please don't stop the reminders",
    "وقفت السيارة", "ممكن نأجل الموعد", "ok thanks", "bukra inshallah", "wrong number",
]


@pytest.mark.parametrize("text", STOPS)
def test_gate_catches_stop_phrases_in_three_scripts(text):
    assert optout.is_opt_out(text)


@pytest.mark.parametrize("text", NOT_STOPS)
def test_gate_leaves_ordinary_replies_to_the_model(text):
    assert not optout.is_opt_out(text)


@pytest.mark.parametrize("text", ["STOP", "وقف الرسائل", "wa2af"])
def test_gate_runs_before_the_model_and_the_remote_and_needs_no_model(fake, monkeypatch, text):
    f = fake(GOOD)
    monkeypatch.setenv("TRIAGE_REMOTE_URL", f.url)
    r = triage.classify(text)
    assert r == {"intent": "opt_out", "confidence": 1.0, "model_id": optout.GATE_MODEL_ID, "needs_human": True}
    assert f.bodies == []
    monkeypatch.delenv("TRIAGE_REMOTE_URL")
    triage.set_backend(None)  # no model at all
    assert triage.classify(text)["intent"] == "opt_out"


def test_a_gate_result_is_a_valid_logged_event_payload():
    from app import payloads
    r = triage.classify("STOP")
    payloads.validate("reply_classified", {**r, "text_ref": "4b0c1d52-0000-4000-8000-000000000001"})


# ------------------------------------------------------------------ tools/triage/serve.py, with a fake scorer


def test_serve_py_speaks_the_classify_contract_over_http(monkeypatch):
    import importlib.util
    import urllib.request
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "tools/triage/serve.py"
    spec = importlib.util.spec_from_file_location("triage_serve", path)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)

    class Scorer:
        def score(self, text):
            return {"logits": [8.0 if i == "reschedule" else 0.0 for i in triage.INTENTS], "mass": 0.99}

    triage.set_backend(Scorer(), triage.Calibration(1.0, 0.8, 0.5, "fake-model-1"))
    serve._state.update(model_id="fake-model-1", warm=True)
    srv = serve.Server(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        health = json.load(urllib.request.urlopen(base + "/health", timeout=2))
        assert health == {"status": "ok", "model_id": "fake-model-1", "warm": True}
        monkeypatch.setenv("TRIAGE_REMOTE_URL", base)
        r = triage.classify("ممكن نغير الموعد", "ar")  # through the real client and the real server
        assert r["intent"] == "reschedule" and r["model_id"] == "fake-model-1" and r["needs_human"] is False
        bad = urllib.request.Request(base + "/classify", data=b'{"text": 5}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad, timeout=2)
        assert err.value.code == 422
    finally:
        srv.shutdown()
        srv.server_close()
