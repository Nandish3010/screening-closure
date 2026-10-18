"""D13: the synthetic reply generator is deterministic and its splits cannot leak (tools/triage/generate_dataset.py)."""

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/triage"))

import generate_dataset as gd  # noqa: E402
from templates import INTENTS  # noqa: E402

SEED = gd.DEFAULT_SEED


@pytest.fixture(scope="module")
def rows():
    return gd.generate(SEED)


def test_same_seed_same_bytes_different_seed_different_data(rows):
    again = gd.generate(SEED)
    assert {s: gd._jsonl(r) for s, r in rows.items()} == {s: gd._jsonl(r) for s, r in again.items()}
    other = gd.generate(SEED + 1)
    assert gd._jsonl(other["train"]) != gd._jsonl(rows["train"])


def test_size_is_in_the_range_the_ticket_asks_for(rows):
    total = sum(len(r) for r in rows.values())
    assert 2000 <= total <= 4000
    assert len(rows["train"]) > len(rows["test"]) > 0 and len(rows["val"]) > 0


def test_split_is_by_template_and_no_text_is_shared(rows):
    gd.check_no_leakage(rows)  # raises AssertionError
    train_t = {r["template"] for r in rows["train"]}
    assert train_t.isdisjoint({r["template"] for r in rows["test"]})
    assert train_t.isdisjoint({r["template"] for r in rows["val"]})


def test_every_intent_and_language_is_present_in_every_split(rows):
    for split, items in rows.items():
        assert {r["label"] for r in items} == set(INTENTS), split
        assert {r["language"] for r in items} >= {"en", "ar", "arabizi", "mixed"}, split


def test_the_test_split_holds_templates_of_every_intent(rows):
    per_intent = {i: {r["template"] for r in rows["test"] if r["label"] == i} for i in INTENTS}
    assert all(per_intent[i] for i in INTENTS)


def test_arabic_text_is_arabic_and_arabizi_is_latin(rows):
    arabic = [r for r in rows["train"] if r["variant"] in ("ar_msa", "ar_gulf")]
    assert arabic and sum(1 for r in arabic if any("؀" <= c <= "ۿ" for c in r["text"])) / len(arabic) > 0.98
    latin = [r for r in rows["train"] if r["variant"] == "arabizi"]
    assert latin and sum(1 for r in latin if not any("؀" <= c <= "ۿ" for c in r["text"])) / len(latin) > 0.98


def test_no_example_looks_like_an_identifier(rows):
    import re

    for items in rows.values():
        for r in items:
            assert not re.search(r"\d{7,}", r["text"]), r["text"]  # no phone or ID numbers
            assert "@" not in r["text"]


def test_committed_dataset_matches_a_fresh_generation():
    out = ROOT / "tools/triage/dataset"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == SEED and manifest["synthetic"] is True
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, name
    fresh = gd.generate(SEED, manifest["per_intent"])
    for split, items in fresh.items():
        assert hashlib.sha256(gd._jsonl(items).encode()).hexdigest() == manifest["files"][f"{split}.jsonl"], split


def test_h2_review_sample_has_a_hundred_lines_and_covers_every_intent(rows):
    sample = gd.review_sample(rows, SEED)
    assert len(sample) == 100 and len({r["id"] for r in sample}) == 100
    assert {r["label"] for r in sample} == set(INTENTS)
    committed = ROOT / "docs/results/triage/h2_review_sample.csv"
    with committed.open(encoding="utf-8", newline="") as fh:
        lines = list(csv.DictReader(fh))
    assert len(lines) == 100
    assert [r["text"] for r in lines] == [r["text"] for r in sample]
    assert all(r["reviewer_label"] == "" for r in lines)  # blank: the reviewer fills it in
