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


# --- commercial extension (E17) ------------------------------------------------------------------------------

def test_default_output_has_no_commercial_sections(capsys):
    assert cm.main([]) == 0
    out = capsys.readouterr().out
    assert "COMMERCIAL CASE EXTENSION" not in out
    assert "advanced_stage_risk_on_time_derived" not in out  # the INPUTS table lists the legacy sections only


def test_commercial_inputs_are_graded_and_in_known_sections():
    _, spec_ = cm.load_inputs(INPUTS)
    for name, s in spec_.items():
        if s["section"] in cm.COMMERCIAL_SECTIONS:
            assert s["status"] in cm.ALLOWED_STATUS
            assert s["basis"]
    # nothing in the extension may be graded sourced except the fixed peg
    sourced = [n for n, s in spec_.items() if s["section"] in cm.COMMERCIAL_SECTIONS and s["status"] == "sourced"]
    assert sourced == ["usd_aed_peg"]


def test_market_positives_arithmetic():
    assert cm.market_positives(1_000_000, 0.2, 0.07) == pytest.approx(14_000)
    _, spec_ = cm.load_inputs(INPUTS)
    rows = {r["case"]: r for r in cm.market_table(spec_)}
    assert rows["central"]["ad_fits"] == pytest.approx(1_264_000 * 0.20)
    assert rows["central"]["ad_positives"] == pytest.approx(1_264_000 * 0.20 * 0.07)
    assert rows["low"]["ad_positives"] < rows["central"]["ad_positives"] < rows["high"]["ad_positives"]
    assert rows["central"]["uae_positives"] > rows["central"]["ad_positives"]


def test_stage_cost_gap_uses_the_peg_and_each_papers_rate():
    _, spec_ = cm.load_inputs(INPUTS)
    assert cm.stage_cost_gap_aed(spec_, "saudi") == pytest.approx((71374 - 45317) * 3.6725)
    assert cm.stage_cost_gap_aed(spec_, "malaysia") == pytest.approx((27972 - 13485.9) / 3.10 * 3.6725)
    with pytest.raises(ValueError):
        cm.stage_cost_gap_aed(spec_, "elsewhere")


def test_outcome_value_matches_hand_calculation():
    _, spec_ = cm.load_inputs(INPUTS)
    shares = cm.load_delay_shares(RESULTS)
    got = cm.outcome_value(1000, shares, "literature_anchored", spec_, "10-12 month ORs", "saudi")
    moved = (shares["baseline"] - shares["literature_anchored"]) * 1000
    p0 = 0.0098
    odds = p0 / (1 - p0) * 1.97
    excess = odds / (1 + odds) - p0
    assert got["moved"] == pytest.approx(moved)
    assert got["late_stage_avoided"] == pytest.approx(moved * excess)
    assert got["cost_avoided"] == pytest.approx(moved * excess * (71374 - 45317) * 3.6725 * 0.6)
    assert got["cost_per_case"] == pytest.approx(got["cost_avoided"] / 1000)


def test_outcome_value_orders_scenarios_and_inputs():
    _, spec_ = cm.load_inputs(INPUTS)
    shares = cm.load_delay_shares(RESULTS)
    lit = cm.outcome_value(1000, shares, "literature_anchored", spec_, "10-12 month ORs", "saudi")
    upper = cm.outcome_value(1000, shares, "upper_range", spec_, "10-12 month ORs", "saudi")
    late = cm.outcome_value(1000, shares, "literature_anchored", spec_, ">12 month ORs", "saudi")
    low_gap = cm.outcome_value(1000, shares, "literature_anchored", spec_, "10-12 month ORs", "malaysia")
    assert upper["cost_avoided"] > lit["cost_avoided"]
    assert late["cost_avoided"] > lit["cost_avoided"]
    assert low_gap["cost_avoided"] < lit["cost_avoided"]
    zero = cm.outcome_value(1000, shares, "literature_anchored", spec_, "10-12 month ORs", "saudi", pair_factor=0.0)
    assert zero["cost_avoided"] == 0.0


def test_capacity_fte_hand_calculation_and_signs():
    _, spec_ = cm.load_inputs(INPUTS)
    rows = cm.load_effort_minutes(RESULTS)
    fte = cm.capacity_fte(10000, rows, spec_)
    by_label = {label: m for label, m, _ in rows}
    base, lit = by_label["baseline, all cases"], by_label["literature anchored, programme level"]
    got = fte["literature anchored, programme level"]
    assert got["coordinator"] == pytest.approx(10000 * (base["coordinator"] - lit["coordinator"]) / 60 / 1700)
    assert got["coordinator"] > 0           # coordinator time is freed
    assert got["owner_clinician"] < 0       # owner-clinician time rises
    assert got["specialist"] < 0            # specialist time rises
    assert got["all_roles"] == pytest.approx(got["coordinator"] + got["owner_clinician"] + got["specialist"])
    assert got["all_roles"] < got["coordinator"]
    assert "baseline, all cases" not in fte


def test_commercial_output_is_deterministic_and_complete(capsys):
    assert cm.main(["--commercial"]) == 0
    first = capsys.readouterr().out
    assert cm.main(["--commercial"]) == 0
    assert first == capsys.readouterr().out
    for heading in ("COMMERCIAL INPUTS", "MARKET:", "OUTCOME VALUE:", "CAPACITY VALUE:", "PRICE BENCHMARKS"):
        assert heading in first
    assert "capacity, not cash" in first


def test_commercial_input_without_status_is_refused(tmp_path):
    text = INPUTS.read_text(encoding="utf-8")
    bad_text = text.replace("  usd_aed_peg:\n    value: 3.6725\n    unit: AED per USD\n    status: sourced\n",
                            "  usd_aed_peg:\n    value: 3.6725\n    unit: AED per USD\n", 1)
    assert bad_text != text
    bad = tmp_path / "inputs.yaml"
    bad.write_text(bad_text, encoding="utf-8")
    with pytest.raises(ValueError, match="usd_aed_peg"):
        cm.load_inputs(bad)
