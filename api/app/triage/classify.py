"""Classify an inbound patient reply (D13): intent, confidence, and whether a person must look at it.

    classify(text, language_hint) -> {"intent", "confidence", "model_id", "needs_human"}

The model is a small multilingual instruct model (Qwen2.5-1.5B, 4-bit) fine-tuned with LoRA on SYNTHETIC replies
(tools/triage). It runs on this machine, offline. `confidence` is the model's own probability for its intent among
the nine intents, softened by the temperature that tools/triage/evaluate.py fitted on held-out data; it is a
calibrated score, not a guarantee (docs/governance/reply-triage-card.md).

`needs_human` is true when ANY of these holds, and these are the only ways to be false:

* the model could not be loaded or failed (fail safe: with no model, every reply goes to a person),
* the intent is `question`, `other` or `opt_out` (never handled without a person, whatever the confidence),
* the confidence is below the threshold chosen for >= 0.95 precision on the auto-handled intents,
* the model puts too little probability on any intent label at all (the reply is off its distribution),
* the text is empty, or longer than the model reads (it is cut for the model, but a person reads all of it).

The model is loaded lazily on the first reply. Where this interpreter has mlx it is loaded in process; the API's own
virtualenv does not, so it is started in a worker process using the interpreter of tools/triage/.venv. If neither
works, or TRIAGE_ENABLED=0, the classifier is "unavailable" and answers as above.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
from pathlib import Path
from typing import Protocol

from .labels import ALWAYS_HUMAN, INTENTS, MAX_TEXT_CHARS

log = logging.getLogger("app.triage")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
CALIBRATION_PATH = HERE / "calibration.json"
UNAVAILABLE_MODEL_ID = "unavailable"
STARTUP_TIMEOUT_S = 120
SCORE_TIMEOUT_S = 30


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


def decide(scores: dict, cal: Calibration, text: str) -> dict:
    """Pure: the scorer's output and the calibration to the result dict. Tested without any model."""
    probs = softmax(scores["logits"], cal.temperature)
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
    if not isinstance(text, str) or not text.strip():
        return unavailable() | {"model_id": _model_id_or_unavailable()}
    ready = _ready()
    if ready is None:
        return unavailable()
    scorer, cal = ready
    try:
        return decide(scorer.score(text[:MAX_TEXT_CHARS]), cal, text)
    except Exception:  # noqa: BLE001
        log.warning("reply triage failed on one reply; sent to a person", exc_info=True)
        return unavailable() | {"model_id": cal.model_id}


def _model_id_or_unavailable() -> str:
    return _calibration.model_id if _calibration is not None else UNAVAILABLE_MODEL_ID
