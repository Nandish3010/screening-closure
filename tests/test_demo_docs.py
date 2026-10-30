"""T-E19: the demo script, the teleprompter and the video storyboard describe the same demo.

* the teleprompter has a timestamp every 15 seconds from 0:00, click cues in square brackets, and fewer than 900 spoken words;
* the "Backup video storyboard" table of docs/submission/demo-script.md lists the same shots, in the same order and with the
  same titles and captions, as tools/video/storyboard.yaml;
* the numbers the script speaks are the ones in docs/results/results.json (so a regenerated evaluation cannot leave a stale
  figure in the words)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "video"))
import storyboard  # noqa: E402

SCRIPT = (ROOT / "docs/submission/demo-script.md").read_text(encoding="utf-8")
TELE = (ROOT / "docs/submission/teleprompter.md").read_text(encoding="utf-8")
RESULTS = json.loads((ROOT / "docs/results/results.json").read_text(encoding="utf-8"))
LA = RESULTS["scenarios"]["literature_anchored"]


def tele_blocks() -> list[tuple[int, str]]:
    blocks = []
    for m in re.finditer(r"^\*\*(\d+):(\d\d)\*\*\s*(.*?)(?=^\*\*\d+:\d\d\*\*|\Z)", TELE, re.S | re.M):
        blocks.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3).strip()))
    return blocks


def spoken(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", " ", text)


def test_the_teleprompter_has_a_timestamp_every_15_seconds_and_every_block_has_a_click_cue():
    blocks = tele_blocks()
    assert blocks and blocks[0][0] == 0
    assert [t for t, _ in blocks] == list(range(0, 15 * len(blocks), 15))
    for t, text in blocks:
        assert spoken(text).strip(), f"the block at {t} s has no spoken words"
    cued = [t for t, text in blocks if re.search(r"\[[^\]]+\]", text)]
    assert len(cued) >= len(blocks) - 6, "most blocks should carry a click cue"


def test_the_teleprompter_is_under_900_spoken_words_and_no_block_is_a_wall_of_text():
    blocks = tele_blocks()
    words = sum(len(re.findall(r"[\w'’.%-]+", spoken(text))) for _, text in blocks)
    assert words < 900, f"{words} spoken words"
    for t, text in blocks:
        n = len(re.findall(r"[\w'’.%-]+", spoken(text)))
        assert n <= 55, f"the block at {t} s has {n} words: too many for 15 seconds"


def test_the_demo_script_storyboard_table_is_the_yaml_storyboard():
    section = SCRIPT[SCRIPT.index("## Backup video storyboard"):SCRIPT.index("## If something fails on stage")]
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in section.splitlines()
            if re.match(r"^\|\s*\d+\s*\|", line)]
    sb = storyboard.load()
    submission = [s for s in sb["shots"] if s.get("submission")]
    assert [int(r[0]) for r in rows] == [s["id"] for s in submission] == list(range(1, len(submission) + 1))
    for row, shot in zip(rows, submission, strict=True):
        assert row[1] == shot["title"], (row[1], shot["title"])
        assert row[3].strip('"') == shot["caption"], f"shot {shot['id']}: caption differs between the script and the YAML"
    assert [s["id"] for s in sb["shots"] if not s.get("submission")] == [11]  # the backup-only extra named under the table


def test_the_spoken_figures_match_the_committed_results():
    arms = LA["arms"]
    closed_base, closed_orch = (arms[a]["closed_share"]["mean"] for a in ("baseline", "orchestrated"))
    assert round(closed_base * 100) == 53 and round(closed_orch * 100) == 68, (closed_base, closed_orch)
    assert round(arms["baseline"]["silent_loss_rate"]["mean"] * 100) == 45
    assert round(arms["orchestrated"]["silent_loss_rate"]["mean"] * 100) == 21
    assert arms["orchestrated"]["silent_loss_rate_in_reach"]["mean"] == 0
    for text in (TELE, SCRIPT, (ROOT / "tools/video/storyboard.yaml").read_text(encoding="utf-8")):
        assert "fifty-three" in text or "53%" in text
        assert "sixty-eight" in text or "68%" in text


def test_the_labour_figures_are_the_committed_ones_and_the_target_is_said_to_be_not_met_in_realistic_scenarios():
    eff = RESULTS["effort"]["scenarios"]
    programme = eff["literature_anchored"]["all"]["reduction"]["labour_reduction"]["mean"]
    in_reach = eff["literature_anchored"]["in_reach"]["reduction"]["labour_reduction"]["mean"]
    upper = eff["upper_range"]["all"]["reduction"]["labour_reduction"]["mean"]
    assert (round(in_reach * 100), round(programme * 100), round(upper * 100)) == (36, 20, 40)
    # not met in the realistic (literature-anchored) scenarios; only the upper bound, a mechanism ceiling, comes close (39.99%)
    assert in_reach < 0.40 and programme < 0.40
    assert 0.39 <= upper < 0.41  # the upper range is about the target (39.99%); the docs say it "comes close", not that it is met
    assert "thirty-six percent" in TELE and "twenty percent" in TELE and "not met" in TELE
    assert "36% fewer" in SCRIPT and "20% across the programme" in SCRIPT and "not met" in SCRIPT
    storyboard_text = (ROOT / "tools/video/storyboard.yaml").read_text(encoding="utf-8")
    assert "36%" in storyboard_text and "20%" in storyboard_text and "not met" in storyboard_text
    tech = (ROOT / "docs/submission/technical-documentation.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name, text in (("teleprompter", TELE), ("script", SCRIPT), ("storyboard", storyboard_text), ("README", readme),
                       ("technical documentation", tech)):
        assert "in every scenario" not in text.lower(), f"{name}: the target is no longer missed in every scenario"


def test_the_headline_is_the_180_day_ratio_and_the_docs_link_the_validation():
    """After B21 the simulator's timing is partly validated: two timing claims may be quoted (about 12% with a colonoscopy within
    30 days under today's unmanaged process, provisional; a managed-arm median of about 6 weeks among completers) and are written
    only in the README and the technical documentation. The headline is the 180-day completion ratio (about 1.31, inside the
    pooled navigation interval; calibrated to the anchor 1.27, not evidence) and the programme-level silent loss, with the in-reach 0% stated as true by construction. No spoken line makes a speed claim."""
    arms = LA["arms"]
    base180, orch180 = (arms[a]["completion_180d"]["mean"] for a in ("baseline", "orchestrated"))
    assert (round(base180 * 100, 1), round(orch180 * 100, 1)) == (51.0, 66.8)
    assert round(orch180 / base180, 2) == 1.31
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tech = (ROOT / "docs/submission/technical-documentation.md").read_text(encoding="utf-8")
    assert "1.31" in readme and "docs/results/validation.md" in readme and "51.0% to 66.8%" in readme
    assert "45.3% to 20.6%" in readme and "by construction" in readme and "about 12%" in readme and "about 6 weeks" in readme
    assert "1.31" in tech and "(../results/validation.md)" in tech and "about 12%" in tech and "about 6 weeks" in tech
    assert "failed for timing" not in readme and "failed for timing" not in tech
    for text in (TELE, SCRIPT, (ROOT / "tools/video/storyboard.yaml").read_text(encoding="utf-8")):
        assert "1.31" in text or "one point three one" in text
    assert "one point three one" in TELE


def test_no_spoken_line_makes_a_speed_claim():
    storyboard_text = (ROOT / "tools/video/storyboard.yaml").read_text(encoding="utf-8")
    narration = " ".join(re.findall(r'^\s*narration:\s*"(.*)"\s*$', storyboard_text, re.M))
    quoted = " ".join(re.findall(r"^> (.*)$", SCRIPT, re.M))
    banned = re.compile(r"days? (sooner|faster|earlier|saved)|sooner|faster|quicker|median time|time to (safe )?closure (falls|drops)"
                        r"|within 30 days|30-day completion|days saved", re.I)
    for name, text in (("teleprompter", spoken(TELE)), ("storyboard narration", narration), ("script quotes", quoted)):
        for m in banned.finditer(text):
            context = text[max(0, m.start() - 60): m.end() + 40]
            # the script quotes may say that we make no such claim ("no claim about days saved")
            assert "no claim" in context or "claim none" in context, f"{name}: speed claim {m.group(0)!r} in ...{context}..."
    assert "no claim about days saved" in SCRIPT and "claim none" in TELE
