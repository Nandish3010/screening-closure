"""The hosted demo is live (deploy/gcp records its URL); no front-door document may still say it is not.

A small list of banned stale phrases, matched case-insensitively against README.md, docs/submission/*.md and project/PLAN.md.
Add a phrase here when a new "not yet" claim is overtaken by events."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BANNED = (
    "not live yet",
    "it is not live today",
    "deploys on 8 november",
    "deploying 8 november",
    "deploys 8 november",
    "goes up on 8 november",
    "not yet deployed",
    "hosted demo not yet exercised",
)


def _files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs/submission").glob("*.md")), ROOT / "project/PLAN.md"]


def test_no_front_door_document_says_the_hosted_demo_is_not_live():
    hits = []
    for f in _files():
        if not f.exists():
            continue
        text = re.sub(r"\s+", " ", f.read_text(encoding="utf-8")).lower()
        hits += [f"{f.relative_to(ROOT)}: {p!r}" for p in BANNED if p in text]
    assert not hits, "stale 'not live' wording while the hosted demo is live:\n" + "\n".join(hits)
