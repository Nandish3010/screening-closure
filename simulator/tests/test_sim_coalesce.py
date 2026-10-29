"""A19, ADR-031 in the simulator: concurrent ladders coalesce per role per working day.

Since M7-1 the coalescing is the API's own (`app.sweep.plan_case`, tested in api/tests); the simulator logs what the
planner decided: a rung that rides on another ladder's escalation is an `also_for` entry of that escalation's logged event,
and a rung `attached_to` an escalation of the same working day is recorded on that escalation's logged event. These tests
check the invariant on whole simulated logs; tests/test_sweep_conformance.py checks the planner's events one by one.
"""

import math


from simulator.sim.engine import BASELINE, ORCHESTRATED, Engine
from simulator.sim.runner import run_arm

SEED = 20261017
RUNG_EVENTS = ("escalated", "reminder_sent")


def rungs(c):
    return [e for e in c.log if e.type in RUNG_EVENTS]


def test_the_engine_has_no_ladder_of_its_own():
    """F6: one escalation ladder. The simulator's timers, ladder, coalescing and bounce were removed (M7-1)."""
    for gone in ("arm_sla", "arm_milestone", "start_ladder", "on_rung", "on_sla_due", "on_ms_due", "on_h_timeout",
                 "handoff_bounce"):
        assert not hasattr(Engine, gone), gone
    import simulator.sim.engine as engine

    assert engine.plan_case.__module__ == "app.sweep" and engine.next_due_at.__module__ == "app.sweep"


def test_a_run_that_coalesces_is_still_deterministic(ctx):
    res = run_arm(ctx, ORCHESTRATED, 150, 250, SEED)
    assert res.event_hash == run_arm(ctx, ORCHESTRATED, 150, 250, SEED).event_hash
    assert res.counters["escalations_coalesced"] > 0


def test_every_coalesced_rung_is_recorded_once_on_an_earlier_event(ctx):
    res = run_arm(ctx, ORCHESTRATED, 400, 300, SEED)
    entries = sum(len(e.payload.get("also_for", ())) for c in res.cases for e in rungs(c))
    assert entries == res.counters["escalations_coalesced"] > 0
    for c in res.cases:
        for e in rungs(c):
            for x in e.payload.get("also_for", ()):
                assert set(x) in ({"step"}, {"milestone"})


def test_a_role_is_not_told_twice_on_one_working_day_for_one_step(ctx):
    """The point of A19: while the case stays in a step, at most one rung event per notify role and working day."""
    res = run_arm(ctx, ORCHESTRATED, 400, 300, SEED)
    offenders = []
    for c in res.cases:
        seen = {}
        for e in rungs(c):
            t = ctx.cal.t_of(e.at)  # the day index is the API's (Asia/Dubai) calendar day
            key = (e.payload["notify"], math.floor(t), e.payload["step"])
            if key in seen:
                offenders.append((c.p.idx, key))
            seen[key] = e
    assert offenders == []


def test_the_baseline_has_no_ladders_to_coalesce(ctx):
    res = run_arm(ctx, BASELINE, 200, 250, SEED)
    assert res.counters["escalations_coalesced"] == 0 and not any(rungs(c) for c in res.cases)
