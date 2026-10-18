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
    assert [s["id"] for s in sb["shots"] if not s.get("submission")] == [15]  # the backup-only extra named under the table


def test_the_spoken_figures_match_the_committed_results():
    arms = LA["arms"]
    closed_base, closed_orch = (arms[a]["closed_share"]["mean"] for a in ("baseline", "orchestrated"))
    assert round(closed_base * 100) == 52 and round(closed_orch * 100) == 66, (closed_base, closed_orch)
    assert round(arms["baseline"]["silent_loss_rate"]["mean"] * 100) == 46
    assert round(arms["orchestrated"]["silent_loss_rate"]["mean"] * 100) == 21
    assert arms["orchestrated"]["silent_loss_rate_in_reach"]["mean"] == 0
    for text in (TELE, SCRIPT, (ROOT / "tools/video/storyboard.yaml").read_text(encoding="utf-8")):
        assert "fifty-two" in text or "52%" in text
        assert "sixty-six" in text or "66%" in text


def test_the_labour_figures_are_the_committed_ones_and_the_target_is_said_to_be_missed():
    eff = RESULTS["effort"]["scenarios"]
    programme = eff["literature_anchored"]["all"]["reduction"]["labour_reduction"]["mean"]
    in_reach = eff["literature_anchored"]["in_reach"]["reduction"]["labour_reduction"]["mean"]
    upper = eff["upper_range"]["all"]["reduction"]["labour_reduction"]["mean"]
    assert (round(in_reach * 100), round(programme * 100), round(upper * 100)) == (33, 18, 37)
    assert max(in_reach, programme, upper) < 0.40  # the target is not met in any scenario
    assert "thirty-three percent" in TELE and "eighteen percent" in TELE and "not met" in TELE
    assert "33% fewer" in SCRIPT and "18% across the programme" in SCRIPT and "not met" in SCRIPT
    storyboard_text = (ROOT / "tools/video/storyboard.yaml").read_text(encoding="utf-8")
    assert "33%" in storyboard_text and "18%" in storyboard_text and "not met" in storyboard_text
