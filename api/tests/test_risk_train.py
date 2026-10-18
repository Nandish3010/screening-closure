"""D8 (T-D11, T-D9 building blocks): the logistic regression, its metrics and the SVG, on small
hand-checkable data. The training run itself (simulator data) is in simulator/tests/test_risk_training.py."""

import math
import random
import xml.dom.minidom

import pytest

from app.risk import features as F
from app.risk import report, train
from app.risk.model import Model, sigmoid


# ---------------------------------------------------------------- metrics, by hand


def test_auc_perfect_inverted_and_tied():
    assert train.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert train.auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert train.auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5  # all ties count half
    # two positives (0.4, 0.8) against two negatives (0.4, 0.6): pairs 0.4>0.4 tie, 0.4<0.6, 0.8>0.4, 0.8>0.6
    assert train.auc([1, 0, 1, 0], [0.4, 0.4, 0.8, 0.6]) == pytest.approx((0.5 + 0 + 1 + 1) / 4)
    assert train.auc([1, 1], [0.2, 0.9]) is None  # one class only


def test_brier_by_hand():
    assert train.brier([1, 0], [0.8, 0.3]) == pytest.approx((0.04 + 0.09) / 2)
    assert train.brier([1, 1, 0, 0], [0.5] * 4) == 0.25


def test_calibration_bins_and_ece_by_hand():
    y = [1, 0, 1, 1, 0, 0]
    p = [0.05, 0.05, 0.55, 0.55, 0.95, 1.0]   # 1.0 belongs to the last bin
    bins = train.calibration_bins(y, p)
    assert [b["n"] for b in bins] == [2, 0, 0, 0, 0, 2, 0, 0, 0, 2]
    assert bins[1]["mean_predicted"] is None and bins[1]["abs_gap"] is None  # empty bins keep their row
    assert bins[0]["observed_rate"] == 0.5 and bins[0]["mean_predicted"] == 0.05
    assert bins[5]["observed_rate"] == 1.0 and bins[9]["observed_rate"] == 0.0
    # |0.5-0.05|*2 + |1-0.55|*2 + |0-0.975|*2, over 6
    assert train.ece_of(bins) == pytest.approx((0.9 + 0.9 + 1.95) / 6, abs=1e-6)


def test_a_perfectly_calibrated_sample_has_small_ece_and_the_noise_floor_is_deterministic():
    rng = random.Random(7)
    p = [rng.random() for _ in range(3000)]
    y = [1 if rng.random() < pi else 0 for pi in p]
    assert train.ece_of(train.calibration_bins(y, p)) < 0.05
    a, b = train.ece_noise_floor(p, 11, 30), train.ece_noise_floor(p, 11, 30)
    assert a == b and 0 < a["mean"] <= a["p95"] < 0.08
    assert train.ece_noise_floor(p, 12, 30) != a


# ---------------------------------------------------------------- the fit


def _synthetic(n, seed, w_days=1.0, w_consent=2.0, w_letter=-0.5, bias=-0.3):
    """Rows whose labels come from a known logistic law over three features."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        days = rng.uniform(0, 90)
        consent = rng.random() < 0.1
        channel = rng.choice(F.CHANNELS)
        f = F.CaseFeatures(stage="patient_contacted", days_since_result=days, consent_withdrawn=consent,
                           channel=channel, age_band="50-59", contact_valid=True)
        z = bias + w_days * (days - 45) / 26 + w_consent * consent + w_letter * (channel == "letter")
        rows.append((f, 1 if rng.random() < sigmoid(z) else 0))
    return rows


def test_fit_recovers_a_known_logistic_law():
    rows = _synthetic(6000, 1)
    raw, info = train.fit(rows, l2=1.0)
    m = Model(raw)
    coef = {c.name: c.coef for c in m.columns}
    assert coef["consent_withdrawn"] == pytest.approx(2.0, abs=0.45)
    assert coef["days_since_result"] == pytest.approx(1.0, abs=0.15)   # per SD of uniform(0, 90) = 26
    assert coef["channel=letter"] < coef["channel=sms"] - 0.2
    assert info["iterations"] < 15


def test_fit_is_deterministic_and_l2_shrinks():
    rows = _synthetic(1500, 2)
    a, _ = train.fit(rows, l2=1.0)
    b, _ = train.fit(rows, l2=1.0)
    assert train.canonical_json(a) == train.canonical_json(b)
    big, _ = train.fit(rows, l2=500.0)
    norm = lambda raw: math.sqrt(sum(c["coef"] ** 2 for c in raw["columns"]))  # noqa: E731
    assert norm(big) < norm(a)


def test_a_trained_model_beats_the_base_rate_on_held_out_data():
    rows = _synthetic(5000, 3)
    tr, te = rows[:3500], rows[3500:]
    raw, _ = train.fit(tr)
    base = sum(y for _f, y in tr) / len(tr)
    res = train.evaluate(Model(raw), te, base)
    assert res["auc"] > 0.7 and res["brier"] < res["brier_base_rate"] and res["brier_skill"] > 0
    assert res["ece"] < 0.06 and len(res["calibration"]) == 10 and sum(b["n"] for b in res["calibration"]) == len(te)


def test_group_metrics_report_every_level_even_an_empty_one():
    rows = _synthetic(800, 4)
    raw, _ = train.fit(rows)
    groups = train.group_metrics(Model(raw), rows, 0.4, "channel", ("sms", "whatsapp", "phone", "letter", "fax"))
    assert [g["group"] for g in groups] == ["sms", "whatsapp", "phone", "letter", "fax"]
    assert groups[-1]["n"] == 0 and groups[-1]["auc"] is None
    assert sum(g["n"] for g in groups) == len(rows)


# ---------------------------------------------------------------- model arithmetic


def _model():
    raw, _ = train.fit(_synthetic(1500, 5))
    return Model(raw)


def test_an_unknown_value_is_imputed_at_the_mean_and_contributes_nothing():
    m = _model()
    full = F.CaseFeatures(stage="patient_contacted", days_since_result=30.0, channel="sms",
                          age_band="50-59", contact_valid=True)
    blank = F.CaseFeatures(stage="patient_contacted", days_since_result=30.0)
    assert m.contributions(blank)["channel"] == pytest.approx(0.0, abs=1e-12)
    assert m.contributions(blank)["age_band"] == pytest.approx(0.0, abs=1e-12)
    odd = F.CaseFeatures(stage="patient_contacted", days_since_result=30.0, channel="fax")  # a level never seen
    assert m.contributions(odd)["channel"] == pytest.approx(0.0, abs=1e-12)
    assert 0.0 < m.predict(full) < 1.0 and 0.0 < m.predict(blank) < 1.0


def test_contributions_sum_to_the_logit_relative_to_the_mean_case():
    m = _model()
    f = F.CaseFeatures(stage="acknowledged", days_since_result=70.0, prior_escalations=2, consent_withdrawn=True,
                       channel="letter", age_band="60-69", contact_valid=False)
    mean_logit = m.intercept + sum(c.coef * c.mean_enc for c in m.columns)
    assert sum(m.contributions(f).values()) == pytest.approx(m.logit_of(m.encode(f)) - mean_logit)


def test_group_metrics_can_group_by_an_audit_label_the_model_never_sees_and_flags_a_gap():
    rows = _synthetic(900, 8)
    raw, _ = train.fit(rows)
    m = Model(raw)
    audit = ["ar" if i % 3 else "en" for i in range(len(rows))]          # a label with no connection to the features
    groups = train.group_metrics(m, rows, 0.4, "language", ("ar", "en", "other"), audit)
    assert [g["group"] for g in groups] == ["ar", "en", "other"] and groups[-1]["n"] == 0 and groups[-1]["gap"] is None
    assert groups[0]["n"] + groups[1]["n"] == len(rows)
    for g in groups[:2]:
        assert g["gap"] == pytest.approx(g["mean_predicted"] - g["prevalence"], abs=1e-7)
        assert g["gap_flag"] == (abs(g["gap"]) > train.GAP_TOLERANCE) and g["gap_margin_95"] > 0
    # a model that under-predicts one group by construction is flagged, the other is not
    lo = [(f, 1) for f, _y in rows[:200]]
    flagged = train.group_metrics(m, lo + rows[200:400], 0.4, "language", ("x", "y"), ["x"] * 200 + ["y"] * 200)
    assert flagged[0]["gap_flag"] is True and flagged[0]["gap"] < -train.GAP_TOLERANCE
    with pytest.raises(ValueError):
        train.group_metrics(m, rows, 0.4, "language", ("ar",), ["ar"])


def test_the_model_refuses_a_language_column():
    """D12: language must not be a model input, whatever model.json says."""
    raw, _ = train.fit(_synthetic(300, 9))
    raw["columns"] = [{**raw["columns"][0], "feature": "language", "level": "ar"}] + raw["columns"]
    with pytest.raises(ValueError, match="excluded feature"):
        Model(raw)


def test_the_model_refuses_columns_that_do_not_match_the_feature_spec():
    raw, _ = train.fit(_synthetic(300, 6))
    raw["columns"] = raw["columns"][:-1]
    with pytest.raises(ValueError, match="retrain"):
        Model(raw)


def test_sigmoid_is_stable_at_the_extremes():
    assert sigmoid(-800) == 0.0 and sigmoid(800) == 1.0 and sigmoid(0) == 0.5


# ---------------------------------------------------------------- the SVG


def test_the_reliability_svg_is_well_formed_and_deterministic():
    rows = _synthetic(2000, 7)
    raw, _ = train.fit(rows[:1400])
    heldout = train.evaluate(Model(raw), rows[1400:], 0.4)
    a, b = report.reliability_svg(heldout), report.reliability_svg(heldout)
    assert a == b
    doc = xml.dom.minidom.parseString(a)
    assert doc.documentElement.tagName == "svg"
    assert a.count("<circle") == sum(1 for x in heldout["calibration"] if x["n"])
    assert "no matplotlib" not in a and "<script" not in a


def test_splice_replaces_only_generated_blocks_and_is_idempotent():
    card = "head\n<!-- risk:a:begin -->\nold\n<!-- risk:a:end -->\ntail <!-- risk:b:begin -->\nz\n<!-- risk:b:end -->"
    out = report.splice(card, {"a": "NEW", "b": "B"})
    assert out == "head\n<!-- risk:a:begin -->\nNEW\n<!-- risk:a:end -->\ntail <!-- risk:b:begin -->\nB\n<!-- risk:b:end -->"
    assert report.splice(out, {"a": "NEW", "b": "B"}) == out
    with pytest.raises(KeyError):
        report.splice(card, {"a": "x"})  # block b produced by nobody
    with pytest.raises(KeyError):
        report.splice(card, {"a": "x", "b": "y", "c": "z"})  # card lacks block c
