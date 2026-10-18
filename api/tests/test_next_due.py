"""A20: `next_due_at`, the sweep index. A sweep that evaluates only the cases whose `next_due_at` has passed must plan
exactly what a sweep of every case plans (T-F8, no database needed; the Postgres half is test_next_due_pg.py).

* hand-computed examples on the UAE calendar (Mon 5 Jan 2026 is the case's creation);
* a property test over random logs (the state machine's own fuzzer) swept at random times, with random human events
  between sweeps: the index never hides work, and it is tight (it names the first instant at which the planner has
  something to plan), so the speed-up is not lost to a loose bound;
* the fingerprint changes with everything the due time depends on.
"""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.state_machine import InvalidTransition, replay_model, step
from app.sweep import NEVER, due_at_for, handoff_due_at, milestone_due_at, next_due_at, plan_case, timing_fingerprint
from test_state_machine import (
    CASE,
    OWNER,
    TO_CONTACTED,
    drive,
    event_sequences,
    make_event,
    plausible_event,
)

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # a Monday


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


def start_model():
    """The model a replay of the genesis event gives (the milestones that run from `case_created` are started)."""
    return replay_model([Event(CASE, CASE_CREATED, AT, "system:intake", {})], PATHWAY)


def run(events):
    """Apply the fuzzer's events to a fresh case, skipping the ones the machine refuses (as the API's 409 does)."""
    model, applied = start_model(), []
    for ev in events:
        if ev.type == CASE_CREATED:
            continue
        try:
            model = step(model, ev, PATHWAY)
        except InvalidTransition:
            continue
        applied.append(ev)
    return model, applied, None


def apply_plan(model, planned):
    """What a sweep does: append each planned event in order, stopping at the first the machine refuses."""
    for e in planned:
        try:
            model = step(model, e, PATHWAY)
        except InvalidTransition:
            break
    return model


# ---- worked examples --------------------------------------------------------------------------------


def test_a_new_case_is_next_due_when_its_first_sla_expires_or_its_referral_milestone_whichever_is_first():
    m = start_model()
    assert next_due_at(m, PATHWAY, UAE) == at(2) == due_at_for(m, PATHWAY, UAE)  # acknowledge: 2 working days


def test_after_the_breach_the_next_event_is_the_next_rung_not_the_breach():
    m = start_model()
    due = due_at_for(m, PATHWAY, UAE)
    m = apply_plan(m, plan_case(CASE, m, PATHWAY, UAE, due + timedelta(minutes=1)))
    assert m.overdue and m.escalation_level == 1
    rung = PATHWAY.rung_at(2)
    assert next_due_at(m, PATHWAY, UAE) == UAE.add_working_days(due, rung.after_breach_days)
    assert next_due_at(m, PATHWAY, UAE) > due


def test_an_accepted_ladder_and_an_exhausted_ladder_have_nothing_more_to_plan_for_the_state():
    m = start_model()
    due = due_at_for(m, PATHWAY, UAE)
    m = apply_plan(m, plan_case(CASE, m, PATHWAY, UAE, due + timedelta(days=60)))
    assert m.escalation_level == len(PATHWAY.escalation_ladder)
    # the state's ladder and the referral milestone's (breached long before) are both exhausted at +60 days
    assert m.milestones["referral_requested"].escalation_level == len(PATHWAY.escalation_ladder)
    assert plan_case(CASE, m, PATHWAY, UAE, due + timedelta(days=61)) == []
    assert next_due_at(m, PATHWAY, UAE) is None


def test_a_pending_handoff_is_due_when_the_response_window_closes():
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}), model=start_model())
    m = step(m, make_event("handoff_requested", OWNER, {"to_provider": "endoscopy-unit"}, at(8)), PATHWAY)
    due = handoff_due_at(m, PATHWAY, UAE)
    assert due is not None
    assert next_due_at(m, PATHWAY, UAE) <= due
    assert plan_case(CASE, m, PATHWAY, UAE, due - timedelta(minutes=1)) == []
    assert [e.type for e in plan_case(CASE, m, PATHWAY, UAE, due + timedelta(minutes=1))] == ["handoff_rejected"]


def test_consent_withdrawn_freezes_the_milestone_clocks_in_the_index_too():
    m = drive(*TO_CONTACTED, ("consent_updated", "patient:p", {"withdrawn": True}), model=start_model())
    assert m.state == "consent_withdrawn"
    assert plan_case(CASE, m, PATHWAY, UAE, at(500)) == []
    assert next_due_at(m, PATHWAY, UAE) is None  # nothing until consent returns; that append recomputes it


def test_the_referral_milestone_can_be_the_earliest_trigger():
    # owner_accepted has a 3-working-day SLA from the contact step; make the case sit in a state without an SLA
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}), ("book_confirmatory", OWNER, {}), model=start_model())
    nd = next_due_at(m, PATHWAY, UAE)
    candidates = [milestone_due_at(m, n, PATHWAY, UAE) for n in PATHWAY.milestones]
    assert nd is not None and nd <= min(c for c in candidates if c is not None)


def test_never_is_a_real_instant_every_driver_can_store():
    assert NEVER.tzinfo is not None and NEVER > at(36500)


def test_the_fingerprint_changes_with_the_calendar_and_the_pathway_and_not_with_noise():
    base = timing_fingerprint(PATHWAY, UAE)
    assert base == timing_fingerprint(PATHWAY, WorkingCalendar())
    assert base != timing_fingerprint(PATHWAY, dataclasses.replace(UAE, holidays=frozenset({AT.date()})))
    assert base != timing_fingerprint(PATHWAY, dataclasses.replace(UAE, weekend=frozenset({4, 5})))
    slower = dataclasses.replace(PATHWAY, slas_working_days={**PATHWAY.slas_working_days, "acknowledge": 3})
    assert base != timing_fingerprint(slower, UAE)
    ladder = PATHWAY.escalation_ladder
    shifted = dataclasses.replace(PATHWAY, escalation_ladder=(dataclasses.replace(ladder[0], after_breach_days=1), *ladder[1:]))
    assert base != timing_fingerprint(shifted, UAE)
    assert base != timing_fingerprint(dataclasses.replace(PATHWAY, handoff_response_working_days=5), UAE)
    renamed = dataclasses.replace(PATHWAY, description="another description", version="9.9")
    assert base == timing_fingerprint(renamed, UAE)  # not a timing input


# ---- the property: the index never hides work and is tight -------------------------------------------


def check_instant(model, now):
    """The invariant at one sweep time. Returns the planned events."""
    nd = next_due_at(model, PATHWAY, UAE)
    planned = plan_case(CASE, model, PATHWAY, UAE, now)
    if planned:
        assert nd is not None and nd <= now, f"planner has work at {now} but next_due_at is {nd}"
    return nd, planned


@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(event_sequences(), st.lists(st.integers(1, 24 * 120), min_size=1, max_size=6), st.data())
def test_the_indexed_sweep_plans_exactly_what_a_full_scan_plans(events, hours, data):
    model, _applied, _trace = run(events)
    full = indexed = model
    last = model.last_event_at or AT
    for h in sorted(hours):
        now = max(AT + timedelta(hours=h), last + timedelta(minutes=1))
        # full scan
        planned_full = plan_case(CASE, full, PATHWAY, UAE, now)
        # indexed sweep: look at the case only if the stored due time has passed (or nothing is stored: never here)
        nd = next_due_at(indexed, PATHWAY, UAE)
        planned_indexed = plan_case(CASE, indexed, PATHWAY, UAE, now) if nd is not None and nd <= now else []
        assert [e.event_id for e in planned_indexed] == [e.event_id for e in planned_full]
        assert [(e.type, e.payload) for e in planned_indexed] == [(e.type, e.payload) for e in planned_full]
        full, indexed = apply_plan(full, planned_full), apply_plan(indexed, planned_indexed)
        assert full == indexed
        if planned_full:
            last = now
        # tightness: the instant just after the stored due time is one at which the planner does have something
        nd = next_due_at(indexed, PATHWAY, UAE)
        if nd is not None and nd > now:
            assert plan_case(CASE, indexed, PATHWAY, UAE, nd - timedelta(seconds=1)) == []
            assert plan_case(CASE, indexed, PATHWAY, UAE, nd + timedelta(seconds=1)) != [], f"loose bound {nd}"
        # a human acts between sweeps: the index is recomputed from the new model, as the store does on every append
        if data.draw(st.booleans()):
            ev = data.draw(plausible_event(indexed, max(now, last) + timedelta(hours=1)))
            try:
                nxt = step(indexed, ev, PATHWAY)
            except InvalidTransition:
                continue
            full = indexed = nxt
            last = ev.at


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(event_sequences(), st.integers(0, 24 * 200))
def test_the_planner_has_nothing_to_plan_before_next_due_at(events, hours):
    model, _, _ = run(events)
    check_instant(model, AT + timedelta(hours=hours))
