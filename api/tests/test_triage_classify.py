"""D13: the classifier contract. No model is loaded here: a fake scorer stands in, and the real loader is made to fail."""

import json
import math
from pathlib import Path

import pytest

from app import payloads
from app.triage import classify as triage
from app.triage.labels import ALWAYS_HUMAN, AUTO_HANDLED, INTENTS, build_prompt

CAL = triage.Calibration(temperature=1.0, threshold=0.8, mass_floor=0.5, model_id="fake-model-1")


def logits_for(intent: str, margin: float = 8.0) -> list[float]:
    return [margin if i == intent else 0.0 for i in INTENTS]


class Fake:
    def __init__(self, intent="confirm", margin=8.0, mass=0.99, boom=False):
        self.intent, self.margin, self.mass, self.boom, self.seen = intent, margin, mass, boom, []

    def score(self, text):
        self.seen.append(text)
        if self.boom:
            raise RuntimeError("the model fell over")
        return {"logits": logits_for(self.intent, self.margin), "mass": self.mass}


@pytest.fixture(autouse=True)
def _reset():
    triage.reset()
    yield
    triage.reset()


def keys(result):
    return set(result) == {"intent", "confidence", "model_id", "needs_human"}


def test_contract_shape_and_a_confident_auto_handled_intent():
    triage.set_backend(Fake("reschedule"), CAL)
    result = triage.classify("can we move it to Monday?", "en")
    assert keys(result) and result["intent"] == "reschedule" and result["model_id"] == "fake-model-1"
    assert 0.99 < result["confidence"] <= 1.0 and result["needs_human"] is False


@pytest.mark.parametrize("intent", sorted(ALWAYS_HUMAN))
def test_question_other_and_opt_out_always_need_a_human_however_sure_the_model_is(intent):
    triage.set_backend(Fake(intent, margin=30.0), CAL)
    result = triage.classify("anything", None)
    assert result["intent"] == intent and result["confidence"] > 0.999 and result["needs_human"] is True


def test_the_always_human_set_is_exactly_the_documented_one():
    assert ALWAYS_HUMAN == {"question", "other", "opt_out"}
    assert AUTO_HANDLED == set(INTENTS) - ALWAYS_HUMAN


def test_low_confidence_needs_a_human_and_the_threshold_is_the_boundary():
    triage.set_backend(Fake("confirm", margin=1.0), CAL)  # p = e / (e + 8) = 0.254
    low = triage.classify("ok", "en")
    assert low["intent"] == "confirm" and low["confidence"] < CAL.threshold and low["needs_human"] is True
    # exactly at the threshold the reply is NOT sent to a human (>= threshold is confident)
    margin = math.log(CAL.threshold * 8 / (1 - CAL.threshold))
    triage.set_backend(Fake("confirm", margin=margin + 1e-6), CAL)
    assert triage.classify("ok")["needs_human"] is False


def test_a_model_that_puts_its_probability_elsewhere_goes_to_a_human():
    triage.set_backend(Fake("confirm", mass=0.1), CAL)
    assert triage.classify("zzz")["needs_human"] is True


def test_calibration_temperature_softens_confidence():
    hot = triage.Calibration(4.0, 0.8, 0.5, "fake")
    triage.set_backend(Fake("confirm", margin=4.0), hot)
    assert triage.classify("ok")["needs_human"] is True  # p = e^1 / (e^1 + 8) = 0.25 after T=4


def test_fail_safe_when_there_is_no_model():
    triage.set_backend(None)
    result = triage.classify("I will come tomorrow", "en")
    assert keys(result) and result == {"intent": "other", "confidence": 0.0, "model_id": "unavailable", "needs_human": True}


def test_fail_safe_when_the_real_loader_cannot_find_the_model(monkeypatch):
    def missing():
        raise FileNotFoundError("base model not found")

    monkeypatch.setattr(triage, "_builder", missing)
    monkeypatch.delenv("TRIAGE_ENABLED", raising=False)
    result = triage.classify("tamam bukra")
    assert result["needs_human"] is True and result["model_id"] == "unavailable"
    assert triage.classify("again")["needs_human"] is True  # and it does not retry the load on every reply


def test_disabled_by_environment_is_the_same_fail_safe(monkeypatch):
    monkeypatch.setenv("TRIAGE_ENABLED", "0")
    monkeypatch.setattr(triage, "_builder", lambda: pytest.fail("must not load the model"))
    assert triage.classify("ok")["needs_human"] is True


def test_a_scorer_that_raises_never_raises_out_of_classify():
    triage.set_backend(Fake(boom=True), CAL)
    result = triage.classify("hello")
    assert result["needs_human"] is True and result["intent"] == "other" and result["model_id"] == "fake-model-1"


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_text_needs_a_human_and_is_not_scored(text):
    fake = Fake("confirm")
    triage.set_backend(fake, CAL)
    assert triage.classify(text)["needs_human"] is True and fake.seen == []


def test_a_very_long_reply_is_cut_for_the_model_but_needs_a_human():
    fake = Fake("confirm")
    triage.set_backend(fake, CAL)
    result = triage.classify("ok " * 1000)
    assert result["needs_human"] is True and len(fake.seen[0]) <= 600


def test_the_result_is_a_valid_reply_classified_payload():
    triage.set_backend(Fake("wrong_number"), CAL)
    result = triage.classify("wrong number")
    payloads.validate("reply_classified", {**result, "text_ref": "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11"})
    triage.set_backend(None)
    payloads.validate("reply_classified", {**triage.classify("x"), "text_ref": "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11"})


def test_prompt_format_is_the_training_format():
    assert build_prompt("hello") == "Classify the patient reply intent: hello"


def test_labels_agree_with_the_payload_schema_and_the_dataset_generator():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/triage"))
    import templates

    assert tuple(INTENTS) == tuple(payloads.REPLY_INTENTS) == tuple(templates.INTENTS)


def test_committed_calibration_is_well_formed_and_matches_the_report():
    cal_path = Path(triage.CALIBRATION_PATH)
    if not cal_path.exists():
        pytest.skip("no trained model yet")
    raw = json.loads(cal_path.read_text())
    assert 0.2 <= raw["temperature"] <= 6.0 and 0.3 <= raw["threshold"] <= 1.0 and 0 < raw["mass_floor"] < 1
    assert raw["labels"] == list(INTENTS) and set(raw["always_human"]) == ALWAYS_HUMAN
    assert raw["synthetic_data_only"] is True
    loaded = triage.Calibration.load()
    assert loaded.model_id == raw["model_id"]
    payloads.validate("reply_classified", {"intent": "confirm", "confidence": 0.9, "model_id": loaded.model_id,
                                           "needs_human": False, "text_ref": "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11"})
    report = json.loads((Path(__file__).resolve().parents[2] / "docs/results/triage/eval.json").read_text())
    assert report["threshold"] == raw["threshold"] and report["temperature"] == raw["temperature"]


def test_committed_evaluation_meets_the_precision_target_it_claims():
    report_path = Path(__file__).resolve().parents[2] / "docs/results/triage/eval.json"
    if not report_path.exists():
        pytest.skip("no evaluation yet")
    report = json.loads(report_path.read_text())
    assert report["auto_precision"] >= 0.95 and report["opt_out_leaked"] == 0
    assert report["n"]["test"] >= 500 and report["train"]["train_seconds"] < 30 * 60  # trained on this machine in under 30 minutes
    assert report["adapter_bytes"] < 50 * 1024 * 1024


# ---- the real model, through the real loader (skipped where the base model or the worker interpreter is not installed)
_ROOT = Path(__file__).resolve().parents[2]
_HAVE_MODEL = (
    (_ROOT / "tools/triage/models/qwen2.5-1.5b-4bit").is_dir()
    and (_ROOT / "api/app/triage/adapters/adapters.safetensors").is_file()
    and (_ROOT / "tools/triage/.venv/bin/python").is_file()
)


@pytest.mark.skipif(not _HAVE_MODEL, reason="base model or tools/triage/.venv not installed")
def test_the_real_model_loads_lazily_in_a_worker_and_keeps_the_safety_rules(monkeypatch):
    monkeypatch.delenv("TRIAGE_ENABLED", raising=False)
    assert triage._scorer is None  # nothing is loaded until the first reply
    try:
        stop = triage.classify("Please do not contact me again", "en")
        assert stop["intent"] == "opt_out" and stop["needs_human"] is True
        assert stop["model_id"] == triage.Calibration.load().model_id
        arabic = triage.classify("أرجو عدم التواصل معي مرة أخرى", "ar")
        assert arabic["intent"] == "opt_out" and arabic["needs_human"] is True
        clear = triage.classify("wrong number", "en")
        assert clear["intent"] == "wrong_number" and clear["needs_human"] is False and 0.5 < clear["confidence"] <= 1.0
        assert triage.classify("where is the clinic?")["needs_human"] is True
    finally:
        scorer = triage._scorer
        if hasattr(scorer, "close"):
            scorer.close()
        triage.reset()
