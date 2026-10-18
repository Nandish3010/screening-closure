"""Tickets B15 and B16: censoring-aware closure times (Kaplan-Meier median, restricted mean) and the in-reach rates."""

from datetime import date

import pytest

from simulator.sim.calendar import WorkingCalendar
from simulator.sim.metrics import CaseRecord, compute_metrics, km_curve, km_median, rmst

FOLLOW_UP = 200.0


def rec(idx, close=None, complete=None, *, language="ar", channel="sms", in_reach=True, lost=False, visible=False,
        breach=False, follow_up=FOLLOW_UP) -> CaseRecord:
    return CaseRecord(
        idx=idx, language=language, channel=channel, messy=(), follow_up_days=follow_up, t0=0.0, t_owner=None,
        t_contact=None, t_complete=complete, closes=(close,) if close is not None else (), reopens=(), escalations=(),
        actions=(), last_progress=None, lost_contact=lost, visible=visible, consent_breach=breach, handled=(),
        in_reach=in_reach,
    )


def metrics(records, min_group_size=1):
    return compute_metrics(records, cal=WorkingCalendar(date(2026, 1, 5)), ltfu_idle_days=30, escalation_window_wd=5,
                           min_group_size=min_group_size)


def arm(closes, n=10):
    """n cases, the first len(closes) of them closed at the given days, the rest never."""
    return [rec(i, close=closes[i] if i < len(closes) else None) for i in range(n)]


def test_km_curve_with_censoring_and_ties():
    # 5 cases: events at 10 and 10, censored at 20, event at 30, censored at 40
    steps = km_curve([(10, True), (10, True), (20, False), (30, True), (40, False)])
    assert steps == [(10, pytest.approx(0.6)), (30, pytest.approx(0.6 * (1 - 1 / 2)))]
    assert km_median(steps) == 30
    assert rmst(steps, 40) == pytest.approx(10 * 1 + 20 * 0.6 + 10 * 0.3)
    assert km_median(km_curve([(5, True), (200, False), (200, False)])) is None  # never drops to 0.5


def test_rmst_without_events_is_tau():
    assert rmst([], 120.0) == 120.0
    assert rmst(km_curve([(120, False)] * 4), 120.0) == 120.0


def test_superset_of_closures_at_later_times_conditional_median_worse_km_not():
    # Arm A closes 6 of 10 cases; arm B closes the same six at the same times PLUS four more, late.
    a = metrics(arm([5, 10, 15, 20, 25, 30]))
    b = metrics(arm([5, 10, 15, 20, 25, 30, 100, 110, 120, 130]))
    assert a["time_to_closure_median"] == 17.5 and b["time_to_closure_median"] == 27.5
    assert b["time_to_closure_median"] > a["time_to_closure_median"]  # the conditional median says B is worse
    assert a["time_to_closure_km_median"] == 25 and b["time_to_closure_km_median"] == 25  # KM: not worse
    assert b["time_to_closure_rmst"] < a["time_to_closure_rmst"]  # and B spends fewer days unclosed
    assert a["closed_share"] < b["closed_share"]


def test_km_median_defined_where_the_conditional_median_is_misleading():
    # Arm A closes 4 of 10 (KM median never reached); arm B closes the same four plus three late ones.
    a = metrics(arm([5, 10, 15, 20]))
    b = metrics(arm([5, 10, 15, 20, 100, 110, 120]))
    assert b["time_to_closure_median"] > a["time_to_closure_median"]  # 20 vs 12.5: B "slower" among closed cases
    assert a["time_to_closure_km_median"] is None  # under half closed: no median within follow-up
    assert b["time_to_closure_km_median"] == 100  # B reaches one
    assert b["time_to_closure_rmst"] < a["time_to_closure_rmst"]


def test_colonoscopy_km_uses_completions():
    recs = [rec(i, complete=c) for i, c in enumerate([10, 20, 30, 40, None, None])]
    m = metrics(recs)
    assert m["time_to_colonoscopy_km_median"] == 30  # S: 5/6, 4/6, 3/6 -> first <= 0.5 at 30
    assert m["time_to_colonoscopy_rmst"] == pytest.approx((10 + 20 + 30 + 40 + 2 * FOLLOW_UP) / 6)


def test_fairness_groups_get_censoring_aware_closure_and_gaps():
    # Group ar closes only 1 of 4 cases (early); group en closes all 4, late. Among closed cases ar looks much faster
    # (10 vs 120 days), but ar spends more of the follow-up unclosed: the restricted mean reverses the ranking.
    recs = [rec(0, 10, language="ar"), rec(1, None, language="ar"), rec(2, None, language="ar"), rec(3, None, language="ar"),
            rec(4, 100, language="en"), rec(5, 110, language="en"), rec(6, 130, language="en"), rec(7, 140, language="en")]
    m = metrics(recs)
    assert m["fairness.language.ar.closure_median"] == 10 and m["fairness.language.en.closure_median"] == 120
    assert m["fairness.language.closure_median_gap_max_days"] == 110
    assert m["fairness.language.ar.closure_km_median"] is None  # fewer than half closed
    assert m["fairness.language.en.closure_km_median"] == 110
    assert m["fairness.language.closure_km_median_gap_max_days"] is None  # only one group has a median
    assert m["fairness.language.ar.closure_rmst"] == pytest.approx((10 + 3 * FOLLOW_UP) / 4)
    assert m["fairness.language.en.closure_rmst"] == pytest.approx((100 + 110 + 130 + 140) / 4)
    assert m["fairness.language.ar.closure_rmst"] > m["fairness.language.en.closure_rmst"]
    assert m["fairness.language.closure_rmst_gap_max_days"] == pytest.approx((10 + 600) / 4 - 120)


def test_in_reach_rates_use_the_in_reach_denominator():
    recs = [
        rec(0, in_reach=True, lost=True, visible=True),                 # reached, lost, surfaced
        rec(1, complete=10, in_reach=True),                             # reached, completed: counts in the denominator only
        rec(2, in_reach=False, lost=True, visible=False, breach=True),  # outside reach: lost silently, contacted after withdrawal
        rec(3, in_reach=False, lost=True, visible=False),               # outside reach: lost silently
    ]
    m = metrics(recs)
    assert m["in_reach_share"] == 0.5
    assert m["silent_loss_rate"] == 0.5 and m["silent_loss_rate_in_reach"] == 0.0
    assert m["consent_breach_rate"] == 0.25 and m["consent_breach_rate_in_reach"] == 0.0
    recs[0] = rec(0, in_reach=True, lost=True, visible=False)
    assert metrics(recs)["silent_loss_rate_in_reach"] == 0.5


def test_no_case_in_reach_gives_none():
    m = metrics([rec(0, in_reach=False), rec(1, in_reach=False)])
    assert m["in_reach_share"] == 0.0 and m["silent_loss_rate_in_reach"] is None


# ---- committed artefacts (ticket B15) ----------------------------------------------------------------------------

def _results():
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parents[2] / "docs" / "results"
    return json.loads((d / "results.json").read_text()), (d / "results.md").read_text(), (d / "metrics.md").read_text()


def test_committed_results_report_programme_level_and_in_reach_and_the_renamed_metric():
    res, md, metrics_md = _results()
    for sc in res["scenarios"].values():
        for a in sc["arms"].values():
            assert "escalation_precision" not in a and "escalation_acted_on_rate" in a
            assert {"silent_loss_rate", "silent_loss_rate_in_reach", "consent_breach_rate", "consent_breach_rate_in_reach"} <= set(a)
        assert sc["arms"]["orchestrated"]["silent_loss_rate_in_reach"]["mean"] == 0.0
        assert sc["arms"]["orchestrated"]["consent_breach_rate_in_reach"]["mean"] == 0.0
    lit = res["scenarios"]["literature_anchored"]["arms"]["orchestrated"]
    assert lit["silent_loss_rate"]["mean"] > 0.1 and 0.45 < lit["in_reach_share"]["mean"] < 0.55
    assert "Escalation acted-on rate (within 5 working days)" in md and "Escalation precision" not in md
    assert "programme level (all cases)" in md and "in reach (cases the programme reached)" in md
    assert "follow from the `programme_reach` assumption (0.5 in the literature-anchored scenario)" in md
    assert "deprecated_aliases" in metrics_md and "`escalation_precision`" in metrics_md.split("## 10.")[1]
    assert "### `escalation_precision`" not in metrics_md and "### `escalation_acted_on_rate`" in metrics_md


def test_censoring_aware_columns_are_in_the_committed_results():
    res, md, _ = _results()
    for sc in res["scenarios"].values():
        for a in sc["arms"].values():
            for k in ("time_to_closure_km_median", "time_to_closure_rmst", "time_to_colonoscopy_rmst",
                      "fairness.language.ar.closure_rmst", "fairness.channel.closure_rmst_gap_max_days"):
                assert k in a
    assert "among closed cases only" in md and "Kaplan-Meier median" in md
