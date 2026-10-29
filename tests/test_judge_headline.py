"""The /judge page figures come from web/src/judge/headline.json, generated from docs/results/results.json."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_headline", ROOT / "tools" / "judge" / "make_headline.py")
mh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mh)

STALE = ["64.9", "65.6", "51.1", "52.3", "45.7", "20.8", "18.3", "33.4"]


def test_committed_headline_matches_a_fresh_generation():
    assert mh.OUT.read_text() == mh.generate(), "run `make judge-headline`"


def test_headline_values_are_the_literature_anchored_ones():
    h = mh.build(__import__("json").loads(mh.SRC.read_text()))
    assert h["completion_180d"]["ratio"]["value"] == "1.31"
    assert h["silent_loss"]["orchestrated_in_reach"]["value"] == "0.0%"
    # M7-1: the upper range is now 39.99%, about the 40% target, not over it; the judge page says it "comes close"
    assert h["labour_reduction"]["literature_all"]["mean"] < 0.4 and 0.39 <= h["labour_reduction"]["upper_all"]["mean"] < 0.41


def test_judge_page_and_tour_carry_no_stale_figure_literals():
    for rel in ("web/src/pages/JudgePage.tsx", "web/src/judge/JudgeTour.tsx"):
        text = (ROOT / rel).read_text()
        for lit in STALE:
            assert lit not in text, f"{rel} still contains {lit}"
    page = (ROOT / "web/src/pages/JudgePage.tsx").read_text()
    assert "headline.json" in page
    assert not re.search(r"\b1\.27\b", page.replace("anchored at 1.27", "")), "only the 1.27 anchor wording may remain"
