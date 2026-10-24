"""A33 (ADR-042): the secondary "with the informed-refusal pathway" section of results.md, rendered from refusal.json."""

import json
from pathlib import Path

from simulator.sim import refusal_report as rr

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "docs/results"


def test_the_committed_section_matches_refusal_json_and_keeps_the_headline():
    summary = json.loads((RESULTS / "refusal.json").read_text())
    res = json.loads((RESULTS / "results.json").read_text())
    block = rr.section_block(summary, res)
    md = (RESULTS / "results.md").read_text()
    assert block in md, "results.md is stale: run make evaluate (or make evaluate-refusal)"
    assert "The 40% target is not claimed" in block and "The headline remains" in block
    # the headline is computed with the path off (params.refusal.yaml is not among its files); the scenario turns it on
    assert "simulator/params.refusal.yaml" not in res["meta"]["params_files"]
    assert summary["assumptions"]["refusal_path_share"] == 1.0


def test_the_assumptions_are_read_from_the_simulator_and_the_pathway():
    a = rr.assumptions()
    assert a["counselling_delay_working_days"] == 5 and a["signoff_sla_working_days"] == 3
    assert a["counselling_minutes"] == 15.0 and a["re_offer_months"] == 12 and a["re_offer_months_clinical_review"] == 3


def test_without_refusal_json_the_section_is_a_stub():
    assert "Not generated yet" in rr.section_block(None)
