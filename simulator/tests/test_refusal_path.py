"""A33 (ADR-042) in the simulator: the orchestrated arm's informed-refusal path, switched on by
`orchestration.refusal_path_share` (simulator/params.refusal.yaml). Off by default, so the committed headline results are the
ones they were; on, a refuser is counselled, the refusal is recorded and a different physician signs a declined closure, and the
declined closures are reported apart from completed colonoscopies."""

from pathlib import Path

import pytest

from simulator.sim.engine import BASELINE, ORCHESTRATED
from simulator.sim.invariants import check_case_log
from simulator.sim.runner import load_context, metrics_of, run_arm

OVERLAY = Path(__file__).resolve().parents[1] / "params.refusal.yaml"
DEFAULT = Path(__file__).resolve().parents[1] / "params.yaml"


@pytest.fixture(scope="module")
def on():
    return load_context([DEFAULT, OVERLAY], warn=False)


def _types(res):
    return [e for c in res.cases for e in c.log]


def test_off_by_default_the_refusers_are_lost_and_no_refusal_is_recorded(ctx):
    assert ctx.mp.refusal_path_share == 0.0
    res = run_arm(ctx, ORCHESTRATED, 600, 300, 20261017)
    assert not [e for e in _types(res) if e.type == "record_informed_refusal" or e.payload.get("outcome") == "declined"]
    assert metrics_of(ctx, res)["declined_closed_share"] == 0.0


def test_on_a_refuser_is_counselled_recorded_and_closed_as_declined_by_another_physician(on):
    res = run_arm(on, ORCHESTRATED, 600, 300, 20261017)
    refusals = [e for e in _types(res) if e.type == "record_informed_refusal"]
    declined = [e for e in _types(res) if e.type == "close" and e.payload.get("outcome") == "declined"]
    assert refusals and declined and len(declined) <= len(refusals)
    for c in res.cases:
        assert check_case_log(c.log, on.pathway) == []  # replayed through the real machine, declined close included
        recorded = [e for e in c.log if e.type == "record_informed_refusal"]
        closes = [e for e in c.log if e.type == "close" and e.payload.get("outcome") == "declined"]
        if closes:
            (r,), (x,) = recorded, closes
            assert x.payload["evidence_ref"] == r.payload["counselling_ref"]
            assert x.payload["signed_by"] != r.payload["counselled_by"] and x.actor.startswith("clinician:")
            assert not any(e.type == "complete_confirmatory" for e in c.log)  # never a colonoscopy
    m = metrics_of(on, res)
    assert 0 < m["declined_closed_share"] < 1
    # reported apart: a declined closure is not a closed loop in closed_share
    plain = metrics_of(on, run_arm(on, ORCHESTRATED, 600, 300, 20261017))
    assert plain["closed_share"] == m["closed_share"]


def test_the_baseline_arm_has_no_refusal_path_whatever_the_switch(on):
    res = run_arm(on, BASELINE, 400, 300, 20261017)
    assert not [e for e in _types(res) if e.type == "record_informed_refusal"]
