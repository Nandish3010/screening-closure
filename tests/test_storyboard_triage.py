"""M7 presentation fix (re-score finding F-A): every reply the video storyboard quotes for the triage cameo gets, in the code,
the outcome the words claim for it.

The storyboard says a Gulf-Arabic reply "always goes to a person" and that a clear English or standard-Arabic confirmation
can be handled without one; before this test the video typed a Gulf reply and narrated "no person is needed" after
api/app/triage/dialect.py had started forcing every Gulf reply to a person. The claims live in the shot itself
(`reply_claims`), so editing a reply or a sentence without the other fails here.

No model and no network: a stand-in scorer that is as sure as can be that every reply is a confirmation (the case where an
automatic rule is most tempting). The real model is not loaded, so for a `handled` claim this proves that no rule forces
a person; whether the live model is confident enough to handle it alone is the model's calibration, not tested here.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "video"))
import storyboard  # noqa: E402
from app.triage import classify as triage  # noqa: E402
from app.triage import dialect, optout  # noqa: E402
from app.triage.labels import INTENTS  # noqa: E402

SB = storyboard.load()
SCRIPT = (ROOT / "docs/submission/demo-script.md").read_text(encoding="utf-8")
TELE = (ROOT / "docs/submission/teleprompter.md").read_text(encoding="utf-8")
REPLY_LABEL = "Patient's reply"
CAL = triage.Calibration(temperature=1.0, threshold=0.553, mass_floor=0.5, model_id="stand-in")


class VerySure:
    """A scorer that puts 99.99% on `confirm` for any text."""

    def __init__(self) -> None:
        rest = 0.0001 / (len(INTENTS) - 1)
        self.logits = [math.log(0.9999) if i == "confirm" else math.log(rest) for i in INTENTS]

    def score(self, text: str) -> dict:
        return {"logits": self.logits, "mass": 0.99}


@pytest.fixture(autouse=True)
def _backend():
    triage.set_backend(VerySure(), CAL)
    yield
    triage.reset()


def claims() -> list[tuple[int, dict]]:
    return [(s["id"], c) for s in SB["shots"] for c in s.get("reply_claims", [])]


def typed_replies() -> list[tuple[int, str]]:
    out = []
    for s in SB["shots"]:
        for act in s["actions"]:
            fill = act.get("fill") if isinstance(act, dict) else None
            if fill and fill.get("label") == REPLY_LABEL:
                out.append((s["id"], fill["value"]))
    return out


def test_the_storyboard_makes_triage_claims_and_both_outcomes_are_covered():
    assert claims(), "the triage shot lost its reply_claims"
    assert {c["outcome"] for _, c in claims()} == {"person", "handled"}
    assert any(c["group"] == "ar_gulf" and c["outcome"] == "person" for _, c in claims())


def test_every_reply_typed_in_the_video_is_a_claim_that_is_shown():
    shown = {(sid, c["text"]) for sid, c in claims() if c.get("shown")}
    assert typed_replies() and set(typed_replies()) == shown, "a reply typed in the video has no claim, or a claim says shown but is not typed"


@pytest.mark.parametrize("shot_id, claim", claims(), ids=[f"shot{sid}-{c['outcome']}-{c['text'][:12]}" for sid, c in claims()])
def test_each_quoted_reply_gets_the_outcome_the_narration_claims(shot_id, claim):
    text, outcome = claim["text"], claim["outcome"]
    shot = next(s for s in SB["shots"] if s["id"] == shot_id)
    assert dialect.detect(text) == claim["group"], f"{text!r}: dialect group differs from the claim"
    result = triage.classify(text, None)
    if outcome == "person":
        assert result["needs_human"] is True, f"{text!r} is claimed to go to a person but the code handles it alone"
    else:
        assert claim["group"] is None and not optout.is_opt_out(text)
        assert result["needs_human"] is False, f"{text!r} is claimed to be handled without a person but the code sends it to one"
    # the words carry the claim: the phrase named in `says` is in the caption or the narration of the same shot
    said = (shot["caption"] + " " + shot["narration"]).lower()
    assert claim["says"].lower() in said, f"shot {shot_id}: the words no longer say {claim['says']!r}"


def test_a_shown_person_reply_is_never_narrated_as_needing_no_person():
    for sid, text in typed_replies():
        claim = next(c for i, c in claims() if i == sid and c["text"] == text)
        shot = next(s for s in SB["shots"] if s["id"] == sid)
        if claim["outcome"] == "person":
            words = (shot["caption"] + " " + shot["narration"]).lower()
            assert not re.search(r"no person (is )?needed|needs no person|without a person: a clear confirmation", words)
            assert "person" in shot["narration"].lower()


def test_the_demo_script_and_teleprompter_quote_the_same_shown_reply_and_say_it_goes_to_a_person():
    for sid, text in typed_replies():
        assert text in SCRIPT, "demo-script.md no longer quotes the reply the video types"
    for name, doc in (("teleprompter", TELE), ("demo script", SCRIPT)):
        assert "no person is needed" not in doc.lower(), f"{name} still narrates the Gulf reply as needing no person"
        assert re.search(r"gulf[^.]{0,80}(goes to|to) a person|always goes to a person", doc, re.I), f"{name} does not say a Gulf reply goes to a person"
