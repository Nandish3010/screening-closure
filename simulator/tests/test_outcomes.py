"""M7-3: the health-outcomes post-processor (simulator/sim/outcomes.py). Hand-computed arithmetic, the delay groups, the
cross-check against `evaluate`'s delay-harm share, the parameter file's provenance and determinism."""

import json
import math
import random
from pathlib import Path

import pytest

from simulator.sim import outcomes as oc
from simulator.sim.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"sourced", "sourced-adjacent", "assumed"}


@pytest.fixture(scope="module")
def cfg():
    return oc.load_outcome_params()


def test_delay_bins_edges():
    assert oc.delay_bin(None, 270, 360) == "none"
    assert oc.delay_bin(0.5, 270, 360) == "prompt"
    assert oc.delay_bin(270.0, 270, 360) == "prompt"
    assert oc.delay_bin(270.01, 270, 360) == "late"
    assert oc.delay_bin(360.0, 270, 360) == "late"
    assert oc.delay_bin(360.5, 270, 360) == "none"


def test_odds_shift_by_hand():
    # p = 0.0297, OR 2.25: odds 0.0297/0.9703 = 0.030609; x2.25 = 0.068870; p' = 0.068870/1.068870 = 0.064433
    assert oc.odds_shift(0.0297, 2.25) == pytest.approx(0.064433, abs=1e-6)
    assert oc.odds_shift(0.1, 1.0) == pytest.approx(0.1)


def test_central_risks_match_corley(cfg):
    r = oc.bin_risks(oc.central(cfg))
    # the prompt group is Corley's weighted 8-30 day to 7-9 month rates: 29.7 any and 7.68 advanced per 1,000
    assert 1000 * r["prompt"][0] == pytest.approx(29.7)
    assert 1000 * r["prompt"][1] == pytest.approx(7.669, abs=0.01)
    for b in oc.BINS:
        assert 0 < r[b][1] <= r[b][0] < 1
    assert r["prompt"][1] < r["late"][1] < r["none"][1]


def test_all_prompt_arm_by_hand(cfg):
    p = oc.central(cfg)
    a = oc.arm_outcomes({"prompt": 1.0, "late": 0.0, "none": 0.0}, p)
    adv = 29.7 * 0.2582
    early = 29.7 - adv
    s_adv = 0.65 * 0.6325 + 0.35 * 0.2031
    assert a["colonoscopies"] == pytest.approx(1000)
    assert a["cancers"] == pytest.approx(29.7)
    assert a["advanced"] == pytest.approx(adv)
    assert a["deaths_5y"] == pytest.approx(early * (1 - 0.7985) + adv * (1 - s_adv))
    assert a["aa_removed"] == pytest.approx(271.3)


def test_identical_arms_gain_nothing_and_cost_the_fee(cfg):
    p = oc.central(cfg)
    sh = {"prompt": 0.5, "late": 0.1, "none": 0.4}
    c = oc.compare(sh, sh, p)
    for k in ("extra_colonoscopies", "advanced_avoided", "deaths_avoided_5y", "qalys_disc"):
        assert c[k] == pytest.approx(0.0, abs=1e-12)
    assert c["net_cost_aed"] == pytest.approx(90_000)
    assert c["cost_per_qaly_aed"] is None


def test_moving_cases_to_prompt_is_a_benefit(cfg):
    p = oc.central(cfg)
    c = oc.compare({"prompt": 0.5, "late": 0.05, "none": 0.45}, {"prompt": 0.7, "late": 0.03, "none": 0.27}, p)
    assert c["extra_colonoscopies"] == pytest.approx(180.0)
    assert c["advanced_avoided"] > 0 and c["deaths_avoided_5y"] > 0 and c["qalys_disc"] > 0
    assert c["life_years_disc"] < c["life_years"]
    ly, ly_d = oc.life_years_per_death_avoided(p)
    assert c["qalys_disc"] == pytest.approx(c["deaths_avoided_5y"] * ly_d * 0.80)


def test_life_years_by_hand(cfg):
    p = oc.central(cfg)
    ly, ly_d = oc.life_years_per_death_avoided(p)
    assert ly == pytest.approx(24.79 - 2.0)
    d = math.log(1.03)
    assert ly_d == pytest.approx((math.exp(-2 * d) - math.exp(-24.79 * d)) / d)
    assert oc.life_expectancy(57.5, p["life_expectancy"]) == pytest.approx((29.49 + 24.79) / 2)


def test_draws_are_deterministic_and_inside_their_ranges(cfg):
    a = [oc.draw(cfg, random.Random(7)) for _ in range(1)][0]
    b = oc.draw(cfg, random.Random(7))
    assert a == b
    rng = random.Random(3)
    for _ in range(300):
        p = oc.draw(cfg, rng)
        for n in oc.PARAM_NAMES:
            spec = cfg[n]
            if spec["dist"] in ("uniform", "triangular"):
                assert min(spec["args"]) <= p[n] <= max(spec["args"])
            if spec["dist"] in ("beta", "beta_ci"):
                assert 0 < p[n] < 1
            if spec["dist"] == "lognormal_ci":
                assert p[n] > 0


def test_every_parameter_has_provenance(cfg):
    for n in (*oc.PARAM_NAMES, "life_expectancy_uae", "discount_rate", "fee_per_case_aed", "positives_per_year_abu_dhabi",
              "gdp_per_capita_usd", "usd_aed", "never_completer_rule"):
        spec = cfg[n]
        assert spec["status"] in STATUSES, n
        assert spec.get("source") or spec.get("basis"), n
        if spec["status"] != "assumed":
            assert str(spec.get("url", "")).startswith("http") or "business-case" in spec["source"], n
            if spec.get("url"):
                assert spec.get("accessed") == "2026-10-25", n
    ev = (ROOT / "docs" / "evidence.md").read_text()
    assert "## 14. Health outcomes" in ev
    for n in oc.PARAM_NAMES:
        assert f"`{n}`" in ev, f"{n} missing from docs/evidence.md section 14"


def test_bins_agree_with_evaluate_delay_harm_share(cfg):
    """The per-case extraction must agree with the simulator's own delay-harm metric (no colonoscopy by day 270)."""
    reps = oc.simulate(2, 150, 360, 11, 1, cfg, scenarios=(oc.HEADLINE, oc.UPPER))
    res = evaluate(2, 150, 360, 11, None, None, 50, 1, [str(oc.DEFAULT_CONSERVATIVE)])
    for name in (oc.HEADLINE, oc.UPPER):
        for arm in ("baseline", "orchestrated"):
            ours = sum((r["scenarios"][name][arm]["all"][2] + r["scenarios"][name][arm]["all"][3]) / r["scenarios"][name][arm]["all"][0]
                       for r in reps) / 2
            theirs = res["scenarios"][name]["arms"][arm]["delay_harm_share"]["mean"]
            assert ours == pytest.approx(theirs, abs=1e-6), (name, arm)


def test_build_is_deterministic_across_jobs(cfg, tmp_path):
    small = dict(cfg, psa={"draws": 50, "seed_label": "outcomes-psa"})
    a = oc.build(2, 120, 360, 5, 1, small)
    b = oc.build(2, 120, 360, 5, 2, small)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    paths = oc.write(a, tmp_path)
    md = paths[1].read_text()
    assert "## Equity: who gains" in md and "## Limitations, in plain words" in md
    assert "nationality" in md  # says plainly that no nationality data exist
