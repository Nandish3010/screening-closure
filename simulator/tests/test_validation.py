"""Hand-checked arithmetic of the validation comparison (ticket B20) and the structure of the committed outputs."""
from __future__ import annotations

import json
import math

import pytest

from simulator.sim import validation as V
from simulator.sim.validation import TARGETS_PATH, DEFAULT_OUT


def test_percentile_linear_interpolation():
    v = [4.0, 1.0, 3.0, 2.0]
    assert V.percentile(v, 0.0) == 1.0
    assert V.percentile(v, 1.0) == 4.0
    assert V.percentile(v, 0.5) == 2.5                    # between 2 and 3
    assert V.percentile(v, 0.25) == pytest.approx(1.75)   # position 0.75 between 1 and 2
    assert V.percentile([], 0.5) is None
    assert V.percentile([7.0], 0.9) == 7.0


def test_interval_of_forty_values():
    vals = [float(i) for i in range(1, 41)]
    lo, hi = V.interval(vals)
    assert lo == pytest.approx(1 + 0.025 * 39)            # 1.975
    assert hi == pytest.approx(1 + 0.975 * 39)            # 39.025


def test_wilson_matches_hand_value():
    lo, hi = V.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)


def test_risk_ratio_katz_by_hand():
    # 70/100 vs 50/100: RR 1.4; se = sqrt(1/70 - 1/100 + 1/50 - 1/100) = sqrt(0.014286)= 0.11952
    rr, lo, hi = V.risk_ratio(70, 100, 50, 100)
    assert rr == pytest.approx(1.4)
    assert lo == pytest.approx(1.4 * math.exp(-1.959964 * 0.119523), rel=1e-4)
    assert hi == pytest.approx(1.4 * math.exp(1.959964 * 0.119523), rel=1e-4)
    assert 1.10 < lo < 1.12 and 1.76 < hi < 1.78


def test_tolerances_include_the_boundary_and_exclude_beyond():
    assert V.within_points(0.50, 0.45) and V.within_points(0.45, 0.50)
    assert not V.within_points(0.5101, 0.45)
    assert V.within_ratio(75.0, 100.0) and V.within_ratio(125.0, 100.0)
    assert not V.within_ratio(74.9, 100.0) and not V.within_ratio(125.1, 100.0)
    assert V.ratio_of(61.0, 184.0) == pytest.approx(0.33152, abs=1e-4)
    assert V.diff_points(0.541, 0.556) == pytest.approx(-0.015)


def test_covered_is_inclusive():
    assert V.covered(0.5, 0.5, 0.6) and V.covered(0.6, 0.5, 0.6) and not V.covered(0.61, 0.5, 0.6)


def test_compare_rows_on_synthetic_replications():
    reps = [{"completion": {"90": 0.40 + 0.01 * i, "180": 0.50}, "completer_share": {"90": None, "180": None},
             "median": 60.0 + i, "p25": 40.0, "p75": 90.0} for i in range(5)]       # completion@90 0.40..0.44 mean 0.42
    obs = {"completion": [{"day": 90, "k": 229, "n": 1000}, {"day": 180, "p": 0.60, "n": 1000}], "median": 80.0}
    rows = {r["metric"]: r for r in V._compare_rows(reps, obs, {"median"}, 1000)}
    r90 = rows["completion@90"]
    assert r90["sim_mean"] == pytest.approx(0.42) and r90["diff_points"] == pytest.approx(19.1)
    assert not r90["within_tolerance"] and not r90["covered_by_replication_interval"]
    assert r90["sim_inside_obs_interval"] is False
    r180 = rows["completion@180"]
    assert r180["diff_points"] == pytest.approx(-10.0) and not r180["within_tolerance"]
    med = rows["median"]
    assert med["sim_mean"] == pytest.approx(62.0) and med["ratio"] == pytest.approx(0.775) and med["within_tolerance"]
    assert med["in_sample"] is True and rows["completion@90"]["in_sample"] is False


def test_small_run_is_deterministic_across_job_counts():
    targets = {
        "cohort_targets": [{"id": "t", "title": "t", "arm": "baseline", "n": 100, "independence": "independent", "independence_note": "", "setting_note": "",
                            "source": {"url": "", "reference": "", "grade": "P", "licence": ""}, "primary_variant": "u",
                            "variants": [{"name": "u", "overrides": {}}, {"name": "m", "overrides": {"staff.forget_prob": 0.0}}],
                            "observed": {"completion": [{"day": 90, "p": 0.4, "n": 100}], "median": 60.0}}],
        "effect_trials": [{"id": "e", "title": "e", "intervention": "x", "day": 90, "treated": {"k": 60, "n": 100}, "control": {"k": 40, "n": 100},
                           "scenario": "literature_anchored", "independence": "independent", "independence_note": "", "source": {}}],
        "attempted_not_obtained": [],
    }
    import copy
    a = V.run_validation(copy.deepcopy(targets), reps=2, cases=60, days=120, jobs=1)
    b = V.run_validation(copy.deepcopy(targets), reps=2, cases=60, days=120, jobs=2)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["cohort_targets"][0]["variants"][0]["rows"][0]["metric"] == "completion@90"
    assert a["effect_trials"][0]["trial_rr"] == pytest.approx(1.5)


# ---- the committed outputs
def test_committed_validation_json_matches_the_committed_targets():
    res = json.loads((DEFAULT_OUT / "validation.json").read_text())
    assert res["design"]["targets_sha256"] == V._sha(TARGETS_PATH), "targets changed: run make validate"
    ids = {t["id"] for t in json.loads(TARGETS_PATH.read_text())["cohort_targets"]}
    assert {t["id"] for t in res["cohort_targets"]} == ids


def test_committed_verdicts_are_pinned_so_a_change_forces_a_look_at_the_reading():
    """The 'Reading' section of validation.md is prose. If a verdict flips, someone must reread it, so the verdicts are pinned here."""
    res = json.loads((DEFAULT_OUT / "validation.json").read_text())
    got = {t["id"]: t["verdict"] for t in res["cohort_targets"]}
    nw, w = "not within tolerance", "within tolerance"
    assert got == {"issaka-sf-safety-net": nw, "seamar-fqhc": nw, "kpnw-usual-care": nw, "azulay-control": w, "corley-kaiser": nw,
                   "phs-scotland": nw, "nhs-england-bcsp": nw, "aihw-nbcsp-2026-secondary": nw}
    assert {t["id"]: t["inside_trial_ci"] for t in res["effect_trials"]} == {"azulay-sms": True, "kpnw-navigation": True, "annals-navigation-2025": True}


def test_targets_file_is_consistent():
    d = json.loads(TARGETS_PATH.read_text())
    assert len([t for t in d["cohort_targets"] if t["independence"] == "independent"]) >= 3
    for t in d["cohort_targets"] + d["effect_trials"]:
        assert t["independence_note"] and t["source"]["url"].startswith("https://")
    for t in d["cohort_targets"]:
        for c in t["observed"]["completion"]:
            if "k" in c:
                assert 0 < c["k"] <= c["n"]
    for tr in d["effect_trials"]:
        assert tr["treated"]["k"] <= tr["treated"]["n"] and tr["control"]["k"] <= tr["control"]["n"]
