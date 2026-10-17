"""Metric arithmetic on the hand-built five-case scenario (expected values worked out by hand in scenario.py)."""

import pytest

from simulator.sim.invariants import check_case_log
from simulator.sim.metrics import is_open_at, percentile
from simulator.sim.scenario import EXPECTED, scenario_logs, scenario_metrics, scenario_records


def test_scenario_logs_are_valid_pathway_replays(ctx):
    for log in scenario_logs():
        assert check_case_log(log, ctx.pathway) == []


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_scenario_metric(key):
    got = scenario_metrics()[key]
    want = EXPECTED[key]
    if want is None:
        assert got is None
    else:
        assert got == pytest.approx(want, abs=1e-9)


def test_percentile_interpolation():
    assert percentile([], 0.5) is None
    assert percentile([7], 0.9) == 7
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([10, 20, 30, 40, 50], 0.9) == pytest.approx(46.0)


def test_reopened_case_counts_as_open_again():
    c2 = scenario_records()[1]
    assert not is_open_at(c2, 60) and not is_open_at(c2, 90)
    assert is_open_at(c2, 95) and is_open_at(c2, 119) and not is_open_at(c2, 120)


def test_min_group_size_excludes_small_groups_from_gap():
    # every scenario group has 1-2 cases, so with the default threshold of 30 there is no gap to report
    assert scenario_metrics(min_group_size=30)["fairness.language.completion_gap_max"] is None
    assert scenario_metrics(min_group_size=1)["fairness.language.completion_gap_max"] == 1.0
