"""A19, ADR-031 in the simulator: concurrent ladders coalesce per role per working day.

The unit tests drive `Engine.on_rung` directly on a hand-made case (the handler is the unit under test), the run
tests check the invariant on whole simulated logs.
"""

import math
from datetime import datetime, timezone

import pytest

from simulator.sim.behaviour import draw_latents
from simulator.sim.engine import BASELINE, ORCHESTRATED, Case, make_engine
from simulator.sim.messy import draw_messy
from simulator.sim.runner import make_patients, run_arm

SEED = 20261017
REFERRAL = "referral_requested"
COLONOSCOPY = "confirmatory_completed"
RUNG_EVENTS = ("escalated", "reminder_sent")


def make_case(ctx, days=400):
    """An orchestrated engine and one case, in the first step (acknowledge), as `Engine.run` would build them."""
    eng = make_engine(ORCHESTRATED, ctx.mp, ctx.pathway, ctx.cal, days, SEED)
    p = make_patients(ctx, 1, SEED)[0]
    lat, msy = draw_latents(SEED, p.key), draw_messy(SEED, p, eng.mp)
    c = Case(p, lat, msy, p.arrival_t + days, ctx.pathway.initial_state, eng.beh.baseline_channel(lat), False, True)
    c.step = "acknowledge"
    c.token = 1
    return eng, c


def rungs(c):
    return [e for e in c.log if e.type in RUNG_EVENTS]


def test_two_ladders_to_the_same_role_on_the_same_day_are_one_event_that_records_both(ctx):
    eng, c = make_case(ctx)
    t = 40.25
    eng.on_rung(c, t, c.token, (1, "state"))  # supervisor rung of the step's ladder
    before = (len(c.log), len(eng.heap), c.resp_idx, c.reminders, eng.n_events)
    eng.on_rung(c, t + 0.5, c.token, (1, REFERRAL))  # the milestone's supervisor rung, later the same day
    [only] = rungs(c)
    assert only.type == "escalated" and only.payload["notify"] == "owner_supervisor"
    assert only.payload["also_for"] == [{"milestone": REFERRAL}]
    assert eng.counters["escalations_coalesced"] == 1
    # no side effects: no event, no outreach, no staff-reaction draw scheduled, nothing counted as an event
    assert (len(c.log), len(eng.heap), c.resp_idx, c.reminders, eng.n_events) == before


def test_a_state_rung_that_arrives_second_is_recorded_by_its_step(ctx):
    eng, c = make_case(ctx)
    eng.on_rung(c, 40.25, c.token, (2, COLONOSCOPY))
    eng.on_rung(c, 40.75, c.token, (2, "state"))
    [only] = rungs(c)
    assert only.payload["also_for"] == [{"step": "acknowledge"}]


def test_three_ladders_on_one_day_add_two_entries(ctx):
    eng, c = make_case(ctx)
    for src in ("state", REFERRAL, COLONOSCOPY):
        eng.on_rung(c, 40.5, c.token, (0, src))
    [only] = rungs(c)
    assert only.type == "reminder_sent" and only.payload["also_for"] == [{"milestone": REFERRAL}, {"milestone": COLONOSCOPY}]
    assert eng.counters["escalations_coalesced"] == 2


@pytest.mark.parametrize(
    "second",
    [
        pytest.param((1, 41.25, REFERRAL, False), id="next working day"),
        pytest.param((0, 40.5, REFERRAL, False), id="different role"),  # the owner's reminder, not the supervisor's
        pytest.param((1, 40.5, REFERRAL, True), id="step moved"),  # a new token: the obligation changed
    ],
)
def test_rungs_that_are_not_concurrent_are_all_emitted(ctx, second):
    eng, c = make_case(ctx)
    eng.on_rung(c, 40.25, c.token, (1, "state"))
    i, t, src, moved = second
    if moved:
        c.token += 1  # the case moved on to a new step, so its ladder was re-armed with the new token
    eng.on_rung(c, t, c.token, (i, src))
    assert len(rungs(c)) == 2 and eng.counters["escalations_coalesced"] == 0
    assert all("also_for" not in e.payload for e in rungs(c))


def test_the_same_ladder_is_never_coalesced_with_itself(ctx):
    eng, c = make_case(ctx)
    eng.on_rung(c, 40.25, c.token, (1, "state"))
    eng.on_rung(c, 40.75, c.token, (1, "state"))  # same source: not a second ladder
    assert len(rungs(c)) == 2 and eng.counters["escalations_coalesced"] == 0


def test_a_rung_of_a_stale_token_is_dropped_as_before(ctx):
    eng, c = make_case(ctx)
    eng.on_rung(c, 40.25, c.token - 1, (1, "state"))
    assert rungs(c) == []


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
    start = datetime(ctx.cal.start.year, ctx.cal.start.month, ctx.cal.start.day, tzinfo=timezone.utc)
    offenders = []
    for c in res.cases:
        seen = {}
        for e in rungs(c):
            t = (e.at - start).total_seconds() / 86400
            key = (e.payload["notify"], math.floor(t), e.payload["step"])
            if key in seen:
                offenders.append((c.p.idx, key))
            seen[key] = e
    assert offenders == []


def test_the_baseline_has_no_ladders_to_coalesce(ctx):
    res = run_arm(ctx, BASELINE, 200, 250, SEED)
    assert res.counters["escalations_coalesced"] == 0 and not any(rungs(c) for c in res.cases)
