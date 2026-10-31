"""M7-1 (engineering review F6): the simulator runs the API's sweep, and this test proves it event for event.

Since M7-1 the simulator has no ladder of its own: for every case the orchestrated process reaches it folds the machine
form of its events into the API's read model with the API's `step`, and checks the case with `app.sweep.plan_case` at the
instants `app.sweep.next_due_at` names (`sim.machine`). This test takes each case's machine log and replays it into a real
`InMemoryEventStore`: every event a person or service made is appended through the store (the real state machine, the
strict payload schema), and at each instant at which the simulator checked the case the real `app.sweep.run_sweep` runs on
the API's calendar (`app.calendar.default_calendar()`, Asia/Dubai). The store's log must then EQUAL the simulator's machine
log: same events, same order, same ids, same times, same payloads (breaches, rungs, coalescing, bounces).

No case is left out. The sample runs the default messy-case rates and then three times them, and the test asserts it
contains every messy case and every path the sweep acts on: a provider's rejection, a bounce of an unanswered request into
`referral_rejected` and its re-route back to `owner_accepted`, a consent withdrawal (restored and not), a missed
appointment rebooked through `appointment_cancelled`, a reopen, an informed refusal, a provider switch, a duplicate result,
a conflicting ownership claim, a wrong contact, and coalesced rungs. Finally the simulator's own log (what the metrics read)
must show each breach and each notified rung of the machine log once, at the same instant.

The timer-miss rate is 0 here: a missed obligation is the one thing the API cannot do (the simulator masks it out of the
planner's view; `test_a_missed_timer_masks_only_its_obligation` covers it).
"""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest

from app import payloads
from app.calendar import default_calendar
from app.events import InMemoryEventStore
from app.state_machine import genesis, initial_model, step
from app.sweep import SCHEDULER_ACTOR, run_sweep
from simulator.sim.calendar import WorkingCalendar
from simulator.sim.engine import ORCHESTRATED, Engine
from simulator.sim.runner import make_patients

SEED = 7
CASES = 300
DAYS = 365
RUNG_EVENTS = ("escalated", "reminder_sent")


def _run(ctx, messy: float, miss: float = 0.0, cases: int = CASES, seed: int = SEED):
    mp = dataclasses.replace(ctx.mp, timer_miss_prob=miss, programme_reach=1.0, messy_multiplier=messy)
    eng = Engine(ORCHESTRATED, mp, ctx.pathway, ctx.cal, DAYS, seed)
    return eng.run(make_patients(ctx, cases, seed))


@pytest.fixture(scope="module", params=[1.0, 3.0], ids=["messy-x1", "messy-x3"])
def run(request, ctx):
    return _run(ctx, request.param)


def replay(mc, pathway, calendar):
    """The machine log through a real store, with the real sweep at the simulator's check instants. Returns the store's log."""
    first = mc.log[0]
    store = InMemoryEventStore()
    store.create_case(first.case_id, pathway.name, first, pathway.initial_state)
    made = [(i, e) for i, e in enumerate(mc.log) if i > 0 and e.actor != SCHEDULER_ACTOR]
    k = 0
    for now, upto in mc.checks:
        while k < len(made) and made[k][0] < upto:
            store.append(made[k][1], pathway)
            k += 1
        run_sweep(store, pathway, calendar, now)
    for _, e in made[k:]:
        store.append(e, pathway)
    return store.events_for(first.case_id)


def _key(e):
    return (e.type, str(e.event_id), e.actor, e.at, e.payload)


def test_the_simulator_runs_the_apis_sweep_event_for_event(run, ctx):
    pathway, calendar = ctx.pathway, default_calendar()
    compared, failures = 0, []
    for case in run.cases:
        mc = case.mach
        assert mc is not None, f"case {case.p.idx}: reached but has no machine model"
        got = replay(mc, pathway, calendar)
        compared += 1
        if [_key(e) for e in got] != [_key(e) for e in mc.log]:
            diff = next(i for i, (a, b) in enumerate(zip([*got, None], [*mc.log, None])) if a is None or b is None
                        or _key(a) != _key(b))
            failures.append((case.p.idx, diff, got[diff:diff + 2], mc.log[diff:diff + 2]))
    assert not failures, f"{len(failures)} of {compared} cases differ from the real sweep: {failures[:2]}"
    assert compared == len(run.cases)


def test_every_machine_event_passes_the_apis_strict_payload_schema(run):
    bad = []
    for case in run.cases:
        for e in case.mach.log[1:]:
            try:
                payloads.validate(e.type, e.payload)
            except payloads.PayloadSchemaError as exc:
                bad.append((case.p.idx, e.type, str(exc)))
    assert not bad, bad[:5]


def test_the_simulators_log_shows_each_breach_and_rung_of_the_sweep_once(run):
    """What the metrics read (the simulator's `overdue`, `escalated`, `reminder_sent`) is what the sweep planned."""
    for case in run.cases:
        mlog = [e for e in case.mach.log if e.actor == SCHEDULER_ACTOR]
        marks = [e.at for e in mlog if e.type == "mark_overdue"]
        notified = [e for e in mlog if e.type == "escalate" and "attached_to" not in e.payload]
        riders = sum(len(e.payload.get("also_for", ())) for e in mlog if e.type == "escalate") + sum(
            1 for e in mlog if e.type == "escalate" and "attached_to" in e.payload)
        sim_marks = [e.at for e in case.log if e.type == "overdue" and "reason" not in e.payload]
        sim_rungs = [e for e in case.log if e.type in RUNG_EVENTS]
        assert sim_marks == marks, case.p.idx
        assert [e.at for e in sim_rungs] == [e.at for e in notified], case.p.idx
        assert [e.payload["rung"] for e in sim_rungs] == [e.payload["level"] - 1 for e in notified], case.p.idx
        assert sum(len(e.payload.get("also_for", ())) for e in sim_rungs) == riders, case.p.idx


def test_the_sample_covers_every_messy_case_and_every_path_the_sweep_acts_on(run, ctx):
    seen = Counter()
    for case in run.cases:
        types = {e.type for e in case.log}
        mtypes = [(e.type, e.actor, e.payload) for e in case.mach.log]
        states = set()
        for tag in case.msy.tags():
            seen["messy:" + tag] += 1
        for t in ("consent_updated", "appointment_missed", "reopened", "provider_switched", "duplicate_result_merged",
                  "conflicting_ack_rejected", "lost_contact", "record_informed_refusal", "watchdog_flagged"):
            seen[t] += t in types
        seen["provider rejection"] += any(t == "handoff_rejected" and a != SCHEDULER_ACTOR for t, a, _ in mtypes)
        seen["bounce"] += any(t == "handoff_rejected" and a == SCHEDULER_ACTOR for t, a, _ in mtypes)
        seen["appointment_cancelled"] += any(t == "appointment_cancelled" for t, _, _ in mtypes)
        seen["consent restored"] += sum(1 for t, _, p in mtypes if t == "consent_updated" and p.get("withdrawn") is False) > 0
        seen["consent never restored"] += case.mach.frozen
        seen["coalesced"] += any(t == "escalate" and ("also_for" in p or "attached_to" in p) for t, _, p in mtypes)
        seen["milestone breach"] += any(t == "mark_overdue" and "milestone" in p for t, _, p in mtypes)
        m = None  # the states the machine passed through
        for e in case.mach.log:
            m = genesis(initial_model(ctx.pathway), e, ctx.pathway) if m is None else step(m, e, ctx.pathway)
            if states and m.state == "owner_accepted" and "referral_rejected" in states:
                seen["re-routed to owner_accepted"] += 1
            states.add(m.state)
    need = ["messy:" + t for t in ("wrong_contact", "provider_switch", "duplicate_result", "conflicting_ack",
                                   "consent_after_booking")] + [
        "consent_updated", "appointment_missed", "reopened", "provider_switched", "duplicate_result_merged",
        "conflicting_ack_rejected", "lost_contact", "provider rejection", "bounce", "appointment_cancelled",
        "consent restored", "coalesced", "milestone breach", "re-routed to owner_accepted"]
    missing = [k for k in need if not seen[k]]
    assert not missing, (missing, dict(seen))


def test_the_comparison_would_catch_a_utc_calendar(ctx):
    """Not vacuous: the same replay on UTC working days (the simulator's calendar before M7-1) does not match."""
    res = _run(ctx, 1.0, cases=120)
    utc = WorkingCalendar(ctx.cal.start, ctx.cal.weekend, ctx.cal.holidays, "UTC").api
    differ = sum(1 for c in res.cases if [_key(e) for e in replay(c.mach, ctx.pathway, utc)] != [_key(e) for e in c.mach.log])
    assert differ > 0


def test_a_missed_timer_masks_only_its_obligation(ctx):
    """With the miss rate at 1 every SLA clock and milestone is missed: the sweep plans no breach and no rung, and still
    bounces an unanswered referral (the bounce timer is not a missable one, as before M7-1)."""
    res = _run(ctx, 1.0, miss=1.0, cases=120)
    sched = [e for c in res.cases for e in c.mach.log if e.actor == SCHEDULER_ACTOR]
    assert not [e for e in sched if e.type in ("mark_overdue", "escalate")]
    assert any(e.type == "handoff_rejected" for e in sched)
    assert res.counters["timers_missed"] > 0


# ---- the second pathway (cervical screening) on the same engine ------------------------------------------------------
#
# Conformance is a property of the engine, not of a pack: the simulator folds a case with the API's `step`, checks it with
# the API's `plan_case` at the instants `next_due_at` names, and this test replays the log through the real store and the
# real sweep. Run on the cervical pack (its own milestones and SLAs), the two must still agree event for event. This is a
# check that the pack is data the sweep honours. It is NOT an evaluation: the cohort, rates and behaviours are the bowel
# programme's, no result of this run is reported anywhere, and no impact number exists for the second pathway.

CERVICAL_CASES = 120


@pytest.fixture(scope="module")
def cervical_run():
    from pathlib import Path

    from simulator.sim.runner import load_context

    ctx = load_context(pathway_path=Path(__file__).resolve().parents[2] / "config/pathways/cervical_hpv.yaml", warn=False)
    assert ctx.pathway.name == "cervical_hpv"
    return ctx, _run(ctx, 1.0, cases=CERVICAL_CASES)


def test_the_simulator_runs_the_apis_sweep_event_for_event_on_the_second_pathway(cervical_run):
    ctx, result = cervical_run
    calendar = default_calendar()
    failures = []
    for case in result.cases:
        got = replay(case.mach, ctx.pathway, calendar)
        if [_key(e) for e in got] != [_key(e) for e in case.mach.log]:
            failures.append(case.p.idx)
    assert not failures, f"{len(failures)} of {len(result.cases)} cervical cases differ from the real sweep: {failures[:5]}"
    assert len(result.cases) == CERVICAL_CASES


def test_the_second_pathways_breaches_come_from_its_own_pack(cervical_run):
    """The cervical pack's milestone clocks are the ones the sweep ran: the log carries `mark_overdue` for the cervical
    milestones by name, and every machine event passes the API's strict payload schema."""
    ctx, result = cervical_run
    milestones = set()
    for case in result.cases:
        for e in case.mach.log[1:]:
            payloads.validate(e.type, e.payload)
            if e.type == "mark_overdue" and e.payload.get("milestone"):
                milestones.add(e.payload["milestone"])
    assert milestones == set(ctx.pathway.milestones), milestones  # both cervical milestones were breached and named
