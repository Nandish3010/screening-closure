"""D8, D10 (T-D11, T-D9, T-D12): the risk model is trained from the simulator, reproducibly, validated on a
held-out split, and the committed artefacts are exactly what a fresh run writes.

Fast tests use a small cohort; `test_committed_artefacts_are_byte_identical_to_a_fresh_run` regenerates the
full 20,000-case run (about 10 s; marked slow) and compares every generated file with the committed one.
"""

import json

import pytest

from app.risk import features as F
from app.risk import train
from app.risk.model import MODEL_PATH, Model, load_model
from simulator.sim import export_training as X
from simulator.sim.rng import derive_seed
from simulator.sim.runner import make_patients, run_arm

SMALL = 1500


@pytest.fixture(scope="module")
def scenario():
    return X.load_scenario_context()


@pytest.fixture(scope="module")
def small_run(scenario):
    return X.train_and_report(scenario, SMALL, X.DEFAULT_SEED)


def test_t_d11_training_is_reproducible_from_the_seed(scenario, small_run):
    again = X.train_and_report(scenario, SMALL, X.DEFAULT_SEED)
    assert train.canonical_json(small_run["model_file"]) == train.canonical_json(again["model_file"])  # byte-identical model.json
    assert small_run["risk"]["meta"]["coefficient_sha256"] == again["risk"]["meta"]["coefficient_sha256"]
    assert small_run["risk"]["meta"]["data_sha256"] == again["risk"]["meta"]["data_sha256"]
    other = X.train_and_report(scenario, SMALL, X.DEFAULT_SEED + 1)
    assert other["risk"]["meta"]["coefficient_sha256"] != small_run["risk"]["meta"]["coefficient_sha256"]


def test_the_label_is_no_colonoscopy_by_day_180_and_features_see_only_the_past(scenario):
    seed = X.DEFAULT_SEED
    rows = {r.key: r for r in X.build_rows(scenario, 600, seed)}
    result = run_arm(scenario, "orchestrated", 600, X.FOLLOW_UP_DAYS, seed, make_patients(scenario, 600, seed))
    assert rows
    day = 86400.0
    for case in result.cases:
        t0 = case.log[0].at
        done = [(e.at - t0).total_seconds() / day for e in case.log if e.type == "complete_confirmatory"]
        row = rows.get(case.p.key)
        if row is None:
            assert done and done[0] <= X.SNAPSHOT_MAX_DAYS  # left out only because the colonoscopy was already done
            continue
        assert row.label == (0 if done else 1)
        assert not done or done[0] > row.snapshot_day  # at risk at the snapshot
        seen = [e for e in case.log if (e.at - t0).total_seconds() / day <= row.snapshot_day]
        s = F.summarise_events(seen)
        f = row.features
        assert (f.prior_escalations, f.prior_bounces, f.consent_withdrawn, f.provider_changed) == (
            s.prior_escalations, s.prior_bounces, s.consent_withdrawn, s.provider_changed)
        assert f.days_since_result == F.cap_days(row.snapshot_day)
        assert f.stage in F.STAGES
        assert (f.language, f.channel, f.contact_valid) == (case.p.language, case.p.channel, case.p.contact_valid)
        assert 0 <= row.snapshot_day <= X.SNAPSHOT_MAX_DAYS


def test_each_case_is_in_one_split_only_and_the_split_is_about_seventy_thirty(scenario):
    rows = X.build_rows(scenario, 3000, X.DEFAULT_SEED)
    assert len({r.key for r in rows}) == len(rows)
    share = sum(r.split == "train" for r in rows) / len(rows)
    assert abs(share - X.TRAIN_FRACTION) < 0.04
    assert {r.split for r in rows} == {"train", "heldout"}
    # a case's split depends on its key and the seed only, so adding cases never moves existing ones
    again = {r.key: r.split for r in X.build_rows(scenario, 1000, X.DEFAULT_SEED)}
    assert all(again[k] == next(r.split for r in rows if r.key == k) for k in again)
    assert X._split(1, "P000001") == X._split(1, "P000001") and derive_seed(1, "P000001", "risk-split") >= 0


def test_the_age_band_is_a_null_feature_by_construction(small_run):
    m = Model(small_run["model_file"])
    age = [c.coef for c in m.columns if c.feature == "age_band"]
    assert max(abs(c) for c in age) < 0.5  # tiny sample; the committed model is held to a stricter bound below
    rows = X.build_rows(X.load_scenario_context(), 6000, X.DEFAULT_SEED)
    by_band = {}
    for r in rows:
        by_band.setdefault(r.features.age_band, []).append(r.label)
    rates = [sum(v) / len(v) for v in by_band.values()]
    assert set(by_band) == set(F.AGE_BANDS) and max(rates) - min(rates) < 0.06


def test_the_fit_is_better_than_the_base_rate_even_on_a_small_cohort(small_run):
    h = small_run["risk"]["heldout"]
    assert h["auc"] > 0.75 and h["brier"] < h["brier_base_rate"]
    assert len(h["calibration"]) == 10 and sum(b["n"] for b in h["calibration"]) == h["n"]


# ---------------------------------------------------------------- the committed artefacts


def test_the_committed_model_is_consistent_with_the_spec_and_the_parameters(scenario):
    raw = json.loads(MODEL_PATH.read_text())
    m = load_model()  # raises if the columns differ from app.risk.features.COLUMNS
    assert [c.name for c in m.columns] == list(F.COLUMN_NAMES)
    t = raw["training"]
    for key in ("seed", "n_cases", "n_rows", "n_train", "n_heldout", "params_hash", "simulated_start_date",
                "data_sha256", "coefficient_sha256", "scenario", "l2", "optimizer"):
        assert t[key] not in (None, "")
    assert t["seed"] == X.DEFAULT_SEED and t["n_cases"] == X.DEFAULT_CASES and t["scenario"] == "literature_anchored"
    assert raw["synthetic_data"] is True and "worklist ordering only" in raw["intended_use"]
    # drift guard: if the simulator parameters change, the score must be retrained (make risk-report)
    assert t["params_hash"] == scenario.params.hash(), (
        "simulator parameters changed since the risk model was trained: "
        "run `PYTHONPATH=api:. .venv/bin/python -m simulator.sim.export_training`")
    assert len(MODEL_PATH.read_bytes()) < 20_000  # a few kilobytes of JSON
    assert max(abs(c.coef) for c in m.columns if c.feature == "age_band") < 0.1  # the null feature stays null


def test_t_d9_calibration_is_within_tolerance_and_beats_the_base_rate_on_held_out_data():
    risk = json.loads((X.RESULTS_DIR / "risk.json").read_text())
    h = risk["heldout"]
    assert risk["tolerance"]["ece_max"] == 0.05
    assert h["ece"] <= risk["tolerance"]["ece_max"] and risk["tolerance"]["met"] is True
    assert h["brier"] < h["brier_base_rate"] and h["auc"] > 0.5
    assert len(h["calibration"]) == 10
    raw = json.loads(MODEL_PATH.read_text())
    assert raw["validation"]["ece"] == h["ece"] and raw["validation"]["auc"] == h["auc"]
    for feature, n in (("language", 3), ("channel", 4), ("age_band", 4)):
        assert len(risk["groups"][feature]) == n
        assert sum(g["n"] for g in risk["groups"][feature]) == h["n"]


@pytest.mark.slow
def test_t_d12_committed_artefacts_are_byte_identical_to_a_fresh_run(scenario):
    result = X.train_and_report(scenario, X.DEFAULT_CASES, X.DEFAULT_SEED)
    files = X.render_files(result, X.CARD.read_text() if X.CARD.exists() else None)
    stale = [p.name for p, text in files.items() if not p.exists() or p.read_text() != text]
    assert not stale, f"committed files differ from a fresh run: {stale} (run python -m simulator.sim.export_training)"
