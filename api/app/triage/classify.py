"""Classify an inbound patient reply (D13): intent, confidence, and whether a person must look at it.

    classify(text, language_hint) -> {"intent", "confidence", "model_id", "needs_human"}

The model is a small multilingual instruct model (Qwen2.5-1.5B, 4-bit) fine-tuned with LoRA on SYNTHETIC replies
(tools/triage). It runs on this machine, offline. `confidence` is the model's own probability for its intent among
the nine intents, softened by the temperature that tools/triage/evaluate.py fitted on held-out data; it is a
calibrated score, not a guarantee (docs/governance/reply-triage-card.md).

`needs_human` is true when ANY of these holds, and these are the only ways to be false:

* the reply says STOP (the deterministic gate in optout.py, which runs before the model and never asks it),
* the model could not be loaded or failed (fail safe: with no model, every reply goes to a person),
* the intent is `question`, `other` or `opt_out` (never handled without a person, whatever the confidence),
* the confidence is below the threshold chosen for >= 0.95 precision on the auto-handled intents,
* the model puts too little probability on any intent label at all (the reply is off its distribution),
* the text is empty, or longer than the model reads (it is cut for the model, but a person reads all of it),
* the reply is Arabizi and the confidence is below the Arabizi threshold, or it is Gulf-dialect Arabic, whatever the
  confidence (dialect.py: the groups the M7-5 fairness audit found the model reads worst, detected at run time).

The model is loaded lazily on the first reply. Where this interpreter has mlx it is loaded in process; the API's own
virtualenv does not, so it is started in a worker process using the interpreter of tools/triage/.venv. If neither
works, or TRIAGE_ENABLED=0, the classifier is "unavailable" and answers as above.

D15: when TRIAGE_REMOTE_URL is set (compose: http://host.docker.internal:8765) the model is asked over HTTP instead, at
tools/triage/serve.py running on the host, because MLX cannot run inside the Linux API container. Any error, timeout
(2 s) or malformed answer from it is the fail-safe answer; it never falls back to the local worker (which would
start a second model load inside a request). With TRIAGE_REMOTE_URL unset, nothing changes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from . import dialect, optout
from .labels import ALWAYS_HUMAN, INTENTS, MAX_TEXT_CHARS

log = logging.getLogger("app.triage")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
CALIBRATION_PATH = HERE / "calibration.json"
UNAVAILABLE_MODEL_ID = "unavailable"
STARTUP_TIMEOUT_S = 120
SCORE_TIMEOUT_S = 30
REMOTE_TIMEOUT_S = 2.0


class Scorer(Protocol):
    def score(self, text: str) -> dict:
        """{"logits": [floats in INTENTS order], "mass": float}. May raise."""
        ...


class Calibration:
    """temperature, abstain threshold and the identity of the model, as written by tools/triage/evaluate.py."""

    def __init__(self, temperature: float, threshold: float, mass_floor: float, model_id: str) -> None:
        self.temperature = temperature
        self.threshold = threshold
        self.mass_floor = mass_floor
        self.model_id = model_id

    @classmethod
    def load(cls, path: Path = CALIBRATION_PATH) -> "Calibration":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(float(raw["temperature"]), float(raw["threshold"]), float(raw["mass_floor"]), str(raw["model_id"]))


def softmax(logits: list[float], temperature: float) -> list[float]:
    scaled = [v / temperature for v in logits]
    top = max(scaled)
    exps = [math.exp(v - top) for v in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def decide(scores: dict, cal: Calibration, text: str) -> dict:
    """Pure: the scorer's output and the calibration to the result dict. Tested without any model."""
    logits, mass = scores["logits"], scores.get("mass", 0.0)
    if (len(logits) != len(INTENTS) or not all(_finite(v) for v in logits)  # a malformed answer is not a distribution (RT-R-03)
            or not _finite(mass) or not 0.0 <= mass <= 1.0 + 1e-6):  # mass is a probability: NaN and > 1 compare "safe" below
        raise ValueError("the scorer's answer is not nine finite logits and a mass in [0, 1]")
    probs = softmax(logits, cal.temperature)
    best = max(range(len(INTENTS)), key=lambda i: probs[i])
    intent, confidence = INTENTS[best], probs[best]
    needs_human = (
        intent in ALWAYS_HUMAN
        or confidence < cal.threshold
        or scores.get("mass", 0.0) < cal.mass_floor
        or len(text) > MAX_TEXT_CHARS
    )
    return {"intent": intent, "confidence": round(confidence, 4), "model_id": cal.model_id, "needs_human": needs_human}


def unavailable() -> dict:
    return {"intent": "other", "confidence": 0.0, "model_id": UNAVAILABLE_MODEL_ID, "needs_human": True}


# ---------------------------------------------------------------------------------------- the backends


class WorkerScorer:
    """Scores in a child process running tools/triage/.venv's interpreter (the only one with mlx)."""

    def __init__(self, python: str, model_path: str, adapter_path: str | None) -> None:
        self._lock = threading.Lock()
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "api"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [python, "-m", "app.triage.worker", model_path, adapter_path or "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1,
        )
        first = self._readline(STARTUP_TIMEOUT_S)
        if not first or not json.loads(first).get("ready"):
            self.close()
            raise RuntimeError("the triage worker did not start")

    def _readline(self, timeout: float) -> str:
        result: list[str] = []
        reader = threading.Thread(target=lambda: result.append(self._proc.stdout.readline()), daemon=True)  # type: ignore[union-attr]
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            self._proc.kill()
            raise TimeoutError("the triage worker did not answer in time")
        return result[0] if result else ""

    def score(self, text: str) -> dict:
        with self._lock:
            self._proc.stdin.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
            line = self._readline(SCORE_TIMEOUT_S)
        answer = json.loads(line) if line else {"error": "worker exited"}
        if "error" in answer:
            raise RuntimeError(f"the triage worker failed: {answer['error']}")
        return answer

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()


def _build_default_scorer() -> Scorer:
    """The real model. Raises when it is not available; the caller turns that into the fail-safe answer."""
    model_path = os.environ.get("TRIAGE_MODEL_PATH", str(REPO_ROOT / "tools/triage/models/qwen2.5-1.5b-4bit"))
    adapter_path = os.environ.get("TRIAGE_ADAPTER_PATH", str(HERE / "adapters"))
    if not Path(model_path).is_dir():
        raise FileNotFoundError(f"base model not found at {model_path}")
    if not (Path(adapter_path) / "adapters.safetensors").is_file():
        raise FileNotFoundError(f"adapter not found at {adapter_path}")
    try:
        from .scorer import MlxScorer

        return MlxScorer(model_path, adapter_path)
    except ImportError:
        python = os.environ.get("TRIAGE_PYTHON", str(REPO_ROOT / "tools/triage/.venv/bin/python"))
        if not Path(python).is_file():
            raise FileNotFoundError(f"no mlx here and no worker interpreter at {python}") from None
        return WorkerScorer(python, model_path, adapter_path)


# ------------------------------------------------------------------------------------------- the entry point

_lock = threading.Lock()
_scorer: Scorer | None = None
_calibration: Calibration | None = None
_failed = False
_builder = _build_default_scorer


def set_backend(scorer: Scorer | None, calibration: Calibration | None = None) -> None:
    """Install a scorer (tests, tools). `None` makes the classifier unavailable, which is the fail-safe state."""
    global _scorer, _calibration, _failed
    with _lock:
        _scorer, _calibration, _failed = scorer, calibration, scorer is None


def reset() -> None:
    """Forget everything, so the next call loads the real model lazily again."""
    global _scorer, _calibration, _failed
    with _lock:
        _scorer, _calibration, _failed = None, None, False


def _ready() -> tuple[Scorer, Calibration] | None:
    global _scorer, _calibration, _failed
    with _lock:
        if _failed:
            return None
        if _scorer is None:
            if os.environ.get("TRIAGE_ENABLED", "1") == "0":
                _failed = True
                return None
            try:
                cal = Calibration.load()
                _scorer, _calibration = _builder(), cal
            except Exception as exc:  # noqa: BLE001 - any failure to load means: no model, everyone to a person
                log.warning("reply triage model unavailable (%s: %s); every reply goes to a person", type(exc).__name__, exc)
                _failed = True
                return None
        if _calibration is None:
            _calibration = Calibration.load()
        return _scorer, _calibration


def classify(text: str, language_hint: str | None = None) -> dict:
    """The contract above. Never raises. `language_hint` ('en' or 'ar') is accepted for the caller's benefit; the model
    reads the text itself (it is trained on Arabic, English, Arabizi and mixed script) and does not use the hint."""
    if isinstance(text, str) and text.strip() and not optout.is_opt_out(text):
        remote_url = os.environ.get("TRIAGE_REMOTE_URL", "").strip()
        if remote_url:
            return classify_remote(remote_url, text, language_hint)
    return classify_local(text, language_hint)


def classify_local(text: str, language_hint: str | None = None) -> dict:
    """The opt-out gate, then the model in this process or its worker. Never asks TRIAGE_REMOTE_URL (tools/triage/serve.py
    calls this one, so it can never call itself)."""
    if not isinstance(text, str) or not text.strip():
        return unavailable() | {"model_id": _model_id_or_unavailable()}
    if optout.is_opt_out(text):  # before any model: an opt-out never depends on one
        return {"intent": "opt_out", "confidence": 1.0, "model_id": optout.GATE_MODEL_ID, "needs_human": True}
    ready = _ready()
    if ready is None:
        return unavailable()
    scorer, cal = ready
    try:
        return dialect.apply_group_rule(decide(scorer.score(text[:MAX_TEXT_CHARS]), cal, text), text)
    except Exception:  # noqa: BLE001
        log.warning("reply triage failed on one reply; sent to a person", exc_info=True)
        return unavailable() | {"model_id": cal.model_id}


def classify_remote(base_url: str, text: str, language_hint: str | None, timeout: float = REMOTE_TIMEOUT_S) -> dict:
    """POST {base_url}/classify. The answer is checked field by field; anything else, or any error, is `unavailable()`."""
    try:
        body = json.dumps({"text": text[:MAX_TEXT_CHARS], "language_hint": language_hint}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base_url.rstrip("/") + "/classify", data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the operator's own URL
            if resp.status != 200:
                raise ValueError(f"status {resp.status}")
            answer = json.loads(resp.read(65536).decode("utf-8"))
        intent, conf, model_id, needs_human = answer["intent"], answer["confidence"], answer["model_id"], answer["needs_human"]
        if (intent not in INTENTS or not _finite(conf) or not 0.0 <= conf <= 1.0 or not isinstance(model_id, str)
                or not model_id or len(model_id) > 100 or not isinstance(needs_human, bool)):
            raise ValueError("malformed answer")
        # The server's needs_human is never trusted alone: the rules that do not depend on the model, and the local
        # confidence threshold (F8), are applied here. The answer has no `mass` score, so mass_floor cannot be applied.
        try:
            cal = _calibration if _calibration is not None else Calibration.load()
            below = conf < cal.threshold
        except Exception:  # noqa: BLE001 - no calibration to check the confidence against: a person reads it
            below = True
        needs_human = needs_human or intent in ALWAYS_HUMAN or len(text) > MAX_TEXT_CHARS or below
        return dialect.apply_group_rule(
            {"intent": intent, "confidence": round(float(conf), 4), "model_id": model_id, "needs_human": needs_human}, text)
    except Exception as exc:  # noqa: BLE001 - unreachable, slow, malformed: a person reads it
        log.warning("remote reply triage failed (%s); sent to a person", type(exc).__name__)
        return unavailable()


def _model_id_or_unavailable() -> str:
    return _calibration.model_id if _calibration is not None else UNAVAILABLE_MODEL_ID
