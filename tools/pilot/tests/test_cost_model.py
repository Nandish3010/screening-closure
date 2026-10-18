"""tools/pilot/cost_model.py: status grades, wage ratios and the wage-weighted staff-time section.

Run: `.venv/bin/pytest tools/pilot/tests -q -p no:cacheprovider`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "tools" / "pilot" / "cost_model.py"

spec = importlib.util.spec_from_file_location("pilot_cost_model", MODULE)
cm = importlib.util.module_from_spec(spec)
sys.modules["pilot_cost_model"] = cm
spec.loader.exec_module(cm)

INPUTS = ROOT / "tools" / "pilot" / "inputs.yaml"
RESULTS = ROOT / "docs" / "results" / "results.json"


def test_sourced_adjacent_is_an_allowed_status():
    assert "sourced-adjacent" in cm.ALLOWED_STATUS
    assert {"assumed", "sourced"} <= set(cm.ALLOWED_STATUS)


def test_unknown_status_is_refused(tmp_path):
    text = INPUTS.read_text(encoding="utf-8").replace("status: sourced-adjacent", "status: guessed", 1)
    bad = tmp_path / "inputs.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="status 'guessed'"):
        cm.load_inputs(bad)


def test_wage_ratios_are_read_with_ranges():
    _, spec_ = cm.load_inputs(INPUTS)
    assert spec_["wage_ratio_nurse"]["value"] == 1.5
    assert spec_["wage_ratio_clinician"]["value"] == 4.0
    assert spec_["wage_ratio_specialist"]["value"] == 10.0
    for name in cm.WAGE_RATIO_INPUTS:
        assert spec_[name]["status"] == "sourced-adjacent"
        assert spec_[name]["low"] < spec_[name]["value"] < spec_[name]["high"]
    assert (spec_["wage_ratio_clinician"]["low"], spec_["wage_ratio_clinician"]["high"]) == (3.0, 6.0)
    assert (spec_["wage_ratio_specialist"]["low"], spec_["wage_ratio_specialist"]["high"]) == (8.0, 15.0)
    assert (spec_["wage_ratio_nurse"]["low"], spec_["wage_ratio_nurse"]["high"]) == (1.2, 2.2)


def test_missing_wage_ratio_is_refused(tmp_path):
    lines = INPUTS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("  wage_ratio_specialist:"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "")
    bad = tmp_path / "inputs.yaml"
    bad.write_text("\n".join(lines[:start] + lines[end:]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="wage_ratio_specialist"):
        cm.load_inputs(bad)


def test_weighted_minutes_formula():
    minutes = {"coordinator": 40.0, "owner_clinician": 5.0, "specialist": 6.0}
    assert cm.weighted_minutes(minutes, 4.0, 10.0) == pytest.approx(40 + 4 * 5 + 10 * 6)
    assert cm.weighted_minutes(minutes, 1.0, 1.0) == pytest.approx(sum(minutes.values()))


def test_effort_minutes_match_results_json():
    rows = {label: m for label, m, _ in cm.load_effort_minutes(RESULTS)}
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    arm = data["effort"]["scenarios"]["literature_anchored"]["all"]["arms"]["orchestrated"]
    got = rows["literature anchored, programme level"]
    assert got["coordinator"] == arm["minutes_per_case.coordinator"]["mean"]
    assert got["owner_clinician"] == arm["minutes_per_case.owner_clinician"]["mean"]
    assert got["specialist"] == arm["minutes_per_case.specialist"]["mean"]
    # the by-role minutes add up to the all-roles figure (no role is missing from the weighted sum)
    assert sum(got.values()) == pytest.approx(arm["minutes_per_case"]["mean"], abs=1e-3)


def test_output_has_wage_weighted_section_and_is_deterministic(capsys):
    assert cm.main([]) == 0
    first = capsys.readouterr().out
    assert cm.main([]) == 0
    second = capsys.readouterr().out
    assert first == second
    assert "WAGE-WEIGHTED STAFF TIME" in first
    assert "central (4/10)" in first and "low end (3/8)" in first and "high end (6/15)" in first
    for name in cm.WAGE_RATIO_INPUTS:
        assert name in first  # listed in the INPUTS table with its status
    assert "sourced-adjacent" in first
