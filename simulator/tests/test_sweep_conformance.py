"""E20a F6: the simulator's SLA timers and escalation ladder against the API's real sweep, over the same timeline.

The simulator does not run `app.sweep`: it has its own timers and ladder (`sim.engine`: `arm_sla`, `arm_milestone`,
`start_ladder`, `on_rung`). This test checks that the two agree. It runs a sample of orchestrated cases, takes each case's
person-made events (the pathway steps and the referral request and acceptance), writes them in order into a real
`InMemoryEventStore` through the real state machine, and runs the real `app.sweep.run_sweep` on the API's calendar every
day at 23:45 UTC and one second before every person-made event. Then it compares what the two emitted:

* breaches: the simulator's `overdue` against the sweep's `mark_overdue` (the state's SLA, by its awaited event, or a
  milestone, by name), by due date;
* rungs: each ladder notification (the simulator's rung, also when coalesced into another; the sweep's `escalate`, with
  its `also_for` entries), by ladder, rung and due date.

They must pair one to one, each pair at most ONE working day apart. Most agree to the day; the rest differ because the
simulator counts working days on UTC days and the API on Asia/Dubai days (config/calendar.yaml), so an event between
20:00 and 24:00 UTC can start its clock a working day apart (a known difference, not fixed: it would move the
simulator's whole time base).

The timer-miss rate is set to 0 (the simulator's model of a scheduler that silently fails to arm a timer has no
counterpart in the sweep) and the programme reach to 1. Cases outside the comparable subset are counted by reason, and the
reasons are a fixed list, so a new kind of divergence fails the test:

* events the simulator models as overlays that have no person-made counterpart in this replay (lost contact flagged by
  the ladder, consent changes, missed appointments, reopenings, duplicate results, provider switches, conflicting claims,
  rejected referrals);
* `bounce`: a referral request the sweep bounced. The sweep's bounce moves the case to `referral_rejected` (its own
  2-working-day re-route SLA, and re-routing returns it to `owner_accepted`); the simulator keeps the case in its booking
  step and re-requests. A structural difference of the referral model, known and not fixed here.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.calendar import default_calendar
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.state_machine import InvalidTransition
from app.sweep import SCHEDULER_ACTOR, run_sweep
from simulator.sim.actors import machine_actor
from simulator.sim.engine import ORCHESTRATED, Engine
from simulator.sim.runner import make_patients

SEED = 7
CASES = 240
DAYS = 365
REFERRAL = ("handoff_requested", "handoff_accepted")
# simulator overlays that are bookkeeping the sweep does not need (it emits or ignores them)
NEUTRAL = frozenset({"message_sent", "reminder_sent", "escalated", "overdue", "watchdog_flagged", "message_blocked",
                     "delivery_failed"})
# overlays that stand for a path this replay does not translate: the case is left out, counted by its reason
UNTRANSLATED = frozenset({
    "lost_contact", "contact_recovered", "consent_updated", "consent_review_completed", "appointment_missed",
    "appointment_rebooked", "reopened", "duplicate_result_received", "duplicate_result_merged", "provider_switched",
    "conflicting_ack_rejected", "conflicting_ack_unresolved", "handoff_rejected",
})
SWEEP_TIME = time(23, 45, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def run(ctx):
    mp = dataclasses.replace(ctx.mp, timer_miss_prob=0.0, programme_reach=1.0)
    eng = Engine(ORCHESTRATED, mp, ctx.pathway, ctx.cal, DAYS, SEED)
    rungs: dict[int, list] = {}
    effects = eng._rung_effects

    def spy(c, t, i, source, day, pend, waited):  # the ladder a rung belongs to is not in its payload: record it
        rungs.setdefault(c.p.idx, []).append((source, i, t))
        return effects(c, t, i, source, day, pend, waited)

    eng._rung_effects = spy
    return eng, eng.run(make_patients(ctx, CASES, SEED)), rungs


def _replay(case, pathway, calendar):
    """The case's person-made events through the real store and the real sweep. Returns the store, or None on a bounce."""
    store, cid = InMemoryEventStore(), case.p.case_id
    store.create_case(cid, pathway.name, Event(cid, CASE_CREATED, case.log[0].at, "system:intake", {"patient_ref": "Patient/sim"}),
                      pathway.initial_state)
    steps = {t.event for t in pathway.transitions}
    human = [e for e in case.log[1:] if e.type in steps or e.type in REFERRAL]
    first, last = case.log[0].at.date(), case.log[-1].at.date()
    days = [datetime.combine(first + timedelta(days=d), SWEEP_TIME) for d in range((last - first).days + 30)]
    ticks = sorted({*days, *(e.at - timedelta(seconds=1) for e in human)})
    i = 0
    for now in ticks:
        while i < len(human) and human[i].at <= now:
            e = human[i]
            i += 1
            payload = ({"to_provider": e.payload["to"]} if e.type == "handoff_requested"
                       else {} if e.type == "handoff_accepted" else dict(e.payload))
            try:
                store.append(Event(cid, e.type, e.at, machine_actor(e.actor), payload, e.event_id), pathway)
            except InvalidTransition:
                if any(x.type == "handoff_rejected" and x.actor == SCHEDULER_ACTOR for x in store.events_for(cid)):
                    return None  # the sweep bounced the referral: see `bounce` in the module docstring
                raise
        if store.read_model(cid, pathway).state == pathway.terminal_state:
            break
        run_sweep(store, pathway, calendar, now)
    return store


def _real(store, cid):
    marks, rungs = [], []
    for e in store.events_for(cid):
        if e.actor != SCHEDULER_ACTOR:
            continue
        if e.type == "mark_overdue":
            marks.append((e.payload.get("milestone") or e.payload["sla_event"], e.payload["due_at"]))
        elif e.type == "escalate":
            for x in (e.payload, *e.payload.get("also_for", ())):
                rungs.append((x.get("milestone") or "state", x["level"] - 1, x["rung_due_at"]))
    return marks, rungs


def _sim(case, recorded, start):
    marks = [(e.payload.get("milestone") or e.payload["step"], e.at.isoformat()) for e in case.log
             if e.type == "overdue" and "reason" not in e.payload]
    rungs = [(src, i, (start + timedelta(days=t)).isoformat()) for src, i, t in recorded]
    for e in case.log:  # a rung coalesced into another ladder's (A19) is recorded on that event's `also_for`
        if e.type in ("escalated", "reminder_sent"):
            rungs.extend((x.get("milestone", "state"), e.payload["rung"], e.at.isoformat()) for x in e.payload.get("also_for", ()))
    return marks, rungs


def _pair(real, sim, cal):
    """Pair each real item with a simulated one of the same key at most one working day away. Returns the unpaired."""
    left = list(sim)
    unpaired, exact = [], 0
    for item in sorted(real):
        day = date.fromisoformat(item[-1][:10])
        match = next((s for s in sorted(left) if s[:-1] == item[:-1]
                      and abs(cal.working_days_between(min(day, d := date.fromisoformat(s[-1][:10])), max(day, d))) <= 1), None)
        if match is None:
            unpaired.append(("sweep only", item))
        else:
            left.remove(match)
            exact += match[-1][:10] == item[-1][:10]
    return unpaired + [("simulator only", s) for s in left], exact


def test_the_simulated_ladder_matches_the_real_sweep(run, ctx):
    eng, result, recorded = run
    pathway, calendar = ctx.pathway, default_calendar()
    left_out, compared, items, exact, failures = Counter(), 0, 0, 0, []
    for case in result.cases:
        overlays = {e.type for e in case.log} & UNTRANSLATED
        if overlays:
            left_out["untranslated: " + ",".join(sorted(overlays))] += 1
            continue
        unknown = {e.type for e in case.log} - NEUTRAL - {CASE_CREATED, *REFERRAL} - {t.event for t in pathway.transitions}
        assert not unknown, f"case {case.p.idx}: event types this test does not classify: {unknown}"
        store = _replay(case, pathway, calendar)
        if store is None:
            left_out["bounce"] += 1
            continue
        compared += 1
        (rm, rr), (sm, sr) = _real(store, case.p.case_id), _sim(case, recorded.get(case.p.idx, []), eng.start_dt)
        bad_m, ex_m = _pair(rm, sm, ctx.cal)
        bad_r, ex_r = _pair(rr, sr, ctx.cal)
        items += len(rm) + len(rr)
        exact += ex_m + ex_r
        if bad_m or bad_r:
            failures.append((case.p.idx, bad_m, bad_r))
    assert not failures, f"{len(failures)} of {compared} cases differ by more than a working day: {failures[:3]}"
    # the comparison is not vacuous: a good share of the sample, with breaches and rungs on both sides, mostly to the day
    assert compared >= CASES // 3, (compared, dict(left_out))
    assert items >= compared and exact >= 0.9 * items, (items, exact)
