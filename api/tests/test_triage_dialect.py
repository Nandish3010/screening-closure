"""M7-1 (after the M7-5 fairness audit): Arabizi and Gulf-dialect replies are detected at run time, and the model's
auto-handling is limited for them. No model here: a fake scorer stands in."""

import json
from pathlib import Path

import pytest

from app.triage import classify as triage
from app.triage import dialect
from app.triage.labels import INTENTS

CAL = triage.Calibration(temperature=1.0, threshold=0.553, mass_floor=0.5, model_id="fake-model-1")
DATASET = Path(__file__).resolve().parents[2] / "tools/triage/dataset"


class Fake:
    def __init__(self, intent="confirm", p=0.99):
        import math
        # logits that give `intent` probability p among nine intents at temperature 1
        rest = (1 - p) / (len(INTENTS) - 1)
        self.logits = [math.log(p) if i == intent else math.log(rest) for i in INTENTS]

    def score(self, text):
        return {"logits": self.logits, "mass": 0.99}


@pytest.fixture(autouse=True)
def _reset():
    triage.reset()
    yield
    triage.reset()


@pytest.mark.parametrize("text, group", [
    ("ma a2dar ajee bukra", "arabizi"),
    ("momken tersiloha bil englizi? ma afham 3arabi", "arabizi"),
    ("shino hatha? mub fahem shi", "arabizi"),
    ("khallast el fahs embarih", "arabizi"),
    ("ما ابي اي رسايل ولا اتصالات بعد", "ar_gulf"),
    ("سويت الفحص هذا من كم يوم", "ar_gulf"),
    ("ما اقدر اجي الحين", "ar_gulf"),
    ("هلا والله you have the wrong person، مب أنا", "ar_gulf"),
    ("I confirm my appointment on the 25th, see you at 4pm", None),
    ("Salam, I confirm my visit on Sunday. Thanks a lot", None),
    ("hello doctor i would like another date", None),
    ("هل يمكن تغيير الموعد إلى بعد نهاية الأسبوع؟", None),
    ("لن أتمكن من الحضور يوم الأربعاء", None),
    ("", None),
])
def test_the_group_is_detected(text, group):
    assert dialect.detect(text) == group


def test_a_confident_arabizi_reply_below_the_group_threshold_goes_to_a_person():
    triage.set_backend(Fake("confirm", 0.90), CAL)  # above the global 0.553, below the Arabizi 0.918
    assert triage.classify("ok ma3 el salama, akeed ajee", None)["needs_human"] is True
    assert triage.classify("ok I will come, thanks", None)["needs_human"] is False  # English: the global rule


def test_an_arabizi_reply_above_the_group_threshold_is_auto_handled():
    triage.set_backend(Fake("confirm", 0.95), CAL)
    assert triage.classify("ok ma3 el salama, akeed ajee", None)["needs_human"] is False


def test_every_gulf_reply_goes_to_a_person_however_sure_the_model_is():
    triage.set_backend(Fake("confirm", 0.9999), CAL)
    result = triage.classify("ماشي، وصلتني الرسالة. سويت الحجز الحين", None)
    assert result["needs_human"] is True and set(result) == {"intent", "confidence", "model_id", "needs_human"}


def test_the_rule_never_clears_needs_human():
    triage.set_backend(Fake("question", 0.9999), CAL)  # always-human intent
    assert triage.classify("shino hatha?", None)["needs_human"] is True


def test_the_remote_path_applies_the_rule_too(monkeypatch):
    answer = {"intent": "confirm", "confidence": 0.99, "model_id": "remote", "needs_human": False}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return json.dumps(answer).encode()

    monkeypatch.setattr(triage.urllib.request, "urlopen", lambda req, timeout: Resp())
    triage.set_backend(object(), CAL)
    assert triage.classify_remote("http://x", "ما ابي اي رسايل", None)["needs_human"] is True
    assert triage.classify_remote("http://x", "I confirm, thank you", None)["needs_human"] is False


def test_the_detector_rarely_flags_english_or_standard_arabic_and_finds_most_of_its_groups():
    """On the committed (synthetic) test split: no English or MSA reply flagged; most Arabizi and Gulf replies found."""
    rows = [json.loads(line) for line in (DATASET / "test.jsonl").open(encoding="utf-8")]
    by = lambda v: [r for r in rows if r["variant"] == v]  # noqa: E731
    assert not [r["text"] for r in by("en") + by("ar_msa") if dialect.detect(r["text"])]
    assert sum(dialect.detect(r["text"]) == "arabizi" for r in by("arabizi")) >= 0.9 * len(by("arabizi"))
    assert sum(dialect.detect(r["text"]) == "ar_gulf" for r in by("ar_gulf")) >= 0.6 * len(by("ar_gulf"))
