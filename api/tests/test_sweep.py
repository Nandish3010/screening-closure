"""T-A16 and T-A17 (unit parts) for the scheduler sweep, plus the sweep and clock routes."""

import ast
import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api_helpers import login_headers, make_client
from app import clock as clock_module
from app.calendar import WorkingCalendar
from app.clock import SimClock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import InvalidTransition, step
from app.sweep import (
    SCHEDULER_ACTOR,
    SCHEDULER_EVENT_TYPES,
    due_at_for,
    milestone_due_at,
    plan_case,
    run_sweep,
    sweep_event_id,
)
from test_state_machine import TO_CONTACTED, drive, event_sequences, make_event, start_model

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # a Monday
CASE = uuid4()


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


# ---- due dates -------------------------------------------------------------------------------


def test_initial_state_is_due_from_case_creation():
    # result_received awaits `acknowledge` (2 working days) from case creation (Monday 5th)
    assert due_at_for(start_model(), PATHWAY, UAE) == at(2)


def test_due_at_uses_the_entering_event_time_and_the_state_sla():
    # acknowledge on day 3 (Thursday 8th): accept_ownership is due 1 working day later
    m = drive(("acknowledge", "lab:l", {}), model=start_model(), start_day=2)
    assert m.state == "acknowledged" and m.clock_started_at == at(3)
    assert due_at_for(m, PATHWAY, UAE) == at(4)  # Friday 9th, same time of day
    # confirmatory_booked has no per-state SLA: its clock is the 31 working day colonoscopy milestone (A18)
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}), ("book_confirmatory", "primary_care_owner:k", {}))
    assert m.state == "confirmatory_booked" and due_at_for(m, PATHWAY, UAE) is None
    ms = dataclasses.replace(m.milestones["confirmatory_completed"], started_at=at(0))
    m = dataclasses.replace(m, milestones={**m.milestones, "confirmatory_completed": ms})
    assert milestone_due_at(m, "confirmatory_completed", PATHWAY, UAE) == at(0) + timedelta(weeks=6, days=1)


def test_a_friday_start_skips_the_weekend():
    friday = datetime(2026, 1, 9, 8, 0, tzinfo=UTC)
    model = dataclasses.replace(start_model(), clock_started_at=friday)
    assert due_at_for(model, PATHWAY, UAE) == datetime(2026, 1, 13, 8, 0, tzinfo=UTC)  # Tuesday


@pytest.mark.parametrize("state", ["consent_withdrawn", "closed"])
def test_states_without_an_sla_have_no_due_date(state):
    model = dataclasses.replace(start_model(), state=state)
    assert due_at_for(model, PATHWAY, UAE) is None
    assert plan_case(CASE, model, PATHWAY, UAE, at(500)) == []


@pytest.mark.parametrize("state", ["lost_contact", "reopened"])
def test_side_states_inherit_the_sla_of_the_event_that_leaves_them(state):
    model = dataclasses.replace(start_model(), state=state, clock_started_at=AT)
    assert due_at_for(model, PATHWAY, UAE) == (
        UAE.add_working_days(AT, 3 if state == "lost_contact" else 5)  # contact_patient 3 / clinician_review 5
    )


# ---- planning -------------------------------------------------------------------------------


def test_nothing_is_planned_until_the_due_time_has_passed():
    m = start_model()
    due = due_at_for(m, PATHWAY, UAE)
    assert plan_case(CASE, m, PATHWAY, UAE, due - timedelta(hours=1)) == []
    assert plan_case(CASE, m, PATHWAY, UAE, due) == []  # exactly at the due time is not late
    planned = plan_case(CASE, m, PATHWAY, UAE, due + timedelta(minutes=1))
    # the breach itself, then the breach+0 reminder rung (A4); the later rungs are not due yet
    assert [e.type for e in planned] == ["mark_overdue", "escalate"]
    ev = planned[0]
    assert ev.actor == SCHEDULER_ACTOR and ev.at == due + timedelta(minutes=1)
    assert ev.payload["state"] == "result_received"
    assert ev.payload["due_at"] == due.isoformat()
    assert ev.payload["sla_event"] == "acknowledge" and ev.payload["sla_working_days"] == 2
    for e in planned:  # the state machine accepts what the planner plans, in order
        m = step(m, e, PATHWAY)
    assert m.overdue and m.escalation_level == 1


def test_an_overdue_case_is_not_planned_again():
    m = start_model()
    due = due_at_for(m, PATHWAY, UAE)
    now = due + timedelta(days=1)
    m2 = m
    for ev in plan_case(CASE, m, PATHWAY, UAE, now):
        m2 = step(m2, ev, PATHWAY)
    assert m2.overdue and m2.escalation_level == 1
    assert plan_case(CASE, m2, PATHWAY, UAE, now) == []
    # much later only the remaining rungs are planned; never another mark_overdue
    later = plan_case(CASE, m2, PATHWAY, UAE, now + timedelta(days=30))
    state_events = [e for e in later if "milestone" not in e.payload]
    assert [e.payload["rung"] for e in state_events] == ["supervisor", "coordinator"]
    # the referral milestone (15 working days) has been breached by then: one breach and its three rungs (A18)
    assert [(e.type, e.payload.get("rung")) for e in later if "milestone" in e.payload] == [
        ("mark_overdue", None), ("escalate", "reminder"), ("escalate", "supervisor"), ("escalate", "coordinator")]


def test_event_ids_are_deterministic_and_distinct_per_breach():
    due = at(2)
    a = sweep_event_id(CASE, "result_received", due, "mark_overdue")
    assert a == sweep_event_id(CASE, "result_received", due, "mark_overdue")
    assert a != sweep_event_id(CASE, "acknowledged", due, "mark_overdue")
    assert a != sweep_event_id(CASE, "result_received", due + timedelta(days=1), "mark_overdue")
    assert a != sweep_event_id(uuid4(), "result_received", due, "mark_overdue")
    assert a != sweep_event_id(CASE, "result_received", due, "escalate:reminder")
    # the same instant expressed in another time zone gives the same id
    other = due.astimezone(timezone_plus_4())
    assert a == sweep_event_id(CASE, "result_received", other, "mark_overdue")


def timezone_plus_4():
    from datetime import timezone

    return timezone(timedelta(hours=4))


# ---- run_sweep on the in-memory store ----------------------------------------------------------


def make_store(*cases_spec):
    store = InMemoryEventStore()
    ids = []
    for events in cases_spec:
        cid = uuid4()
        store.create_case(
            cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state
        )
        for ev in events:
            store.append(dataclasses.replace(ev, case_id=cid), PATHWAY)
        ids.append(cid)
    return store, ids


def count(store, cid, type_):
    """Events of a type on the state's SLA (milestone events, A18, are counted by `count_ms`)."""
    return sum(1 for e in store.events_for(cid) if e.type == type_ and "milestone" not in e.payload)


def count_ms(store, cid, type_):
    return sum(1 for e in store.events_for(cid) if e.type == type_ and "milestone" in e.payload)


def test_sweep_marks_a_breach_once_however_often_it_runs():
    store, (cid,) = make_store([])
    assert run_sweep(store, PATHWAY, UAE, at(1)).mark_overdue == 0
    for _ in range(5):
        run_sweep(store, PATHWAY, UAE, at(3))
    assert count(store, cid, "mark_overdue") == 1
    model = store.read_model(cid, PATHWAY)
    assert model.overdue and model.breach_due_at == at(2)
    assert store.current_state(cid) == "result_received"  # the scheduler never moves a case


def test_a_later_transition_gets_its_own_breach():
    store, (cid,) = make_store([])
    run_sweep(store, PATHWAY, UAE, at(3))
    store.append(Event(cid, "acknowledge", at(3), "lab:l"), PATHWAY)
    assert not store.read_model(cid, PATHWAY).overdue  # progress resolved the first breach
    run_sweep(store, PATHWAY, UAE, at(4))  # not yet due: accept_ownership due Fri 9th 08:00Z (day 4)
    assert count(store, cid, "mark_overdue") == 1
    run_sweep(store, PATHWAY, UAE, at(8))
    run_sweep(store, PATHWAY, UAE, at(9))
    assert count(store, cid, "mark_overdue") == 2
    first, second = [e for e in store.events_for(cid) if e.type == "mark_overdue"]
    assert first.event_id != second.event_id
    assert (first.payload["state"], second.payload["state"]) == ("result_received", "acknowledged")


def test_sweep_skips_closed_cases_and_honours_only():
    closed_steps = [
        make_event(t.event, "primary_care_owner:k", {"evidence_ref": "x", "signed_by": "y"}, at(i + 1))
        for i, t in enumerate(PATHWAY.main_transitions)
    ]
    store, (done, open1, open2) = make_store(closed_steps, [], [])
    assert store.current_state(done) == "closed"
    result = run_sweep(store, PATHWAY, UAE, at(300), only=[open1])
    assert result.cases_evaluated == 1
    assert count(store, open1, "mark_overdue") == 1 and count(store, open2, "mark_overdue") == 0
    run_sweep(store, PATHWAY, UAE, at(300))
    assert count(store, open2, "mark_overdue") == 1
    assert count(store, done, "mark_overdue") == 0 and store.current_state(done) == "closed"


def test_sweep_survives_a_concurrent_writer():
    """The planned event is refused (not crashed on) if the case moved after we read it."""
    store, (cid,) = make_store([])
    stale = store.read_model(cid, PATHWAY)
    store.append(Event(cid, "acknowledge", at(1), "lab:l"), PATHWAY)
    ev = plan_case(cid, stale, PATHWAY, UAE, at(3))[0]
    assert ev.type == "mark_overdue"
    with pytest.raises(InvalidTransition):
        store.append(ev, PATHWAY)  # names result_received but the case moved on
    assert count(store, cid, "mark_overdue") == 0


# ---- T-A16: the scheduler never closes ---------------------------------------------------------


@settings(
    max_examples=300, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(event_sequences(), st.integers(0, 500), st.integers(0, 3))
def test_scheduler_emits_only_overdue_and_escalate_never_close(events, jump_days, sweeps):
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
    for ev in events:
        try:
            store.append(dataclasses.replace(ev, case_id=cid), PATHWAY)
        except InvalidTransition:
            pass
    before = store.events_for(cid)
    state_before = store.current_state(cid)
    now = at(40 + jump_days)
    for k in range(sweeps + 1):
        run_sweep(store, PATHWAY, UAE, now + timedelta(days=k))
    after = store.events_for(cid)
    new = after[len(before):]
    assert all(e.actor == SCHEDULER_ACTOR for e in new)
    assert {e.type for e in new} <= SCHEDULER_EVENT_TYPES
    assert "close" not in {e.type for e in new}
    if any(e.type == "handoff_rejected" for e in new):
        # the only state change the scheduler can cause: a bounced referral needs a re-route
        assert store.current_state(cid) in (state_before, "referral_rejected")
        assert all(e.payload.get("reason") == "timeout" and e.payload.get("bounced") for e in new if e.type == "handoff_rejected")
    else:
        assert store.current_state(cid) == state_before  # otherwise the scheduler never changes the state
    if state_before != "closed":
        assert store.current_state(cid) != "closed"


# ---- T-A17: the sim clock ------------------------------------------------------------------------


def test_advancing_the_clock_creates_the_breach_without_wall_time():
    clk = SimClock(AT)
    store, (cid,) = make_store([])
    run_sweep(store, PATHWAY, UAE, clk.now())
    assert count(store, cid, "mark_overdue") == 0
    clk.advance(days=1)
    run_sweep(store, PATHWAY, UAE, clk.now())
    assert count(store, cid, "mark_overdue") == 0  # still before Wednesday 08:00Z
    clk.advance(days=1, hours=1)
    run_sweep(store, PATHWAY, UAE, clk.now())  # Wednesday 09:00Z: past the due time
    assert count(store, cid, "mark_overdue") == 1
    assert clk.now() == AT + timedelta(days=2, hours=1)


def test_the_clock_never_moves_backwards_except_by_explicit_reset():
    clk = SimClock(AT)
    clk.advance(days=2)
    for kwargs in ({"days": -1}, {"hours": -0.5}):
        with pytest.raises(ValueError):
            clk.advance(**kwargs)
    assert clk.now() == AT + timedelta(days=2)
    assert clk.reset() == AT


class FakeClockStore:
    def __init__(self):
        self.saved = None
        self.writes = []

    def load(self):
        return self.saved

    def save(self, start, now):
        self.saved = now
        self.writes.append((start, now))


def test_a_bound_clock_persists_every_move_and_resumes_after_a_restart():
    store = FakeClockStore()
    clk = SimClock(AT, store)
    assert store.saved == AT  # first bind records the start
    clk.advance(days=3)
    assert store.saved == AT + timedelta(days=3)
    restarted = SimClock(AT, store)  # "API restart": new process, same store
    assert restarted.now() == AT + timedelta(days=3)
    restarted.reset()
    assert store.saved == AT and SimClock(AT, store).now() == AT


def test_no_wall_clock_reads_in_the_api_or_scheduler_code():
    """The sweep and everything it uses must take time from the sim clock only."""
    root = Path(__file__).resolve().parents[2]
    files = [*(root / "api" / "app").rglob("*.py"), root / "scheduler" / "main.py"]
    assert len(files) > 10
    banned_attrs = {
        ("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"), ("date", "today"),
        ("time", "time"), ("time", "monotonic"), ("time", "perf_counter"), ("time", "time_ns"),
    }
    offenders = []
    for path in files:
        source = path.read_text()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                if (name, node.func.attr) in banned_attrs:
                    if "wall-clock-ok" not in lines[node.lineno - 1]:
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, f"wall-clock reads (mark liveness-only ones 'wall-clock-ok'): {offenders}"


def test_the_only_allowed_wall_clock_reads_are_the_scheduler_heartbeat():
    text = (Path(__file__).resolve().parents[2] / "scheduler" / "main.py").read_text()
    marked = [ln for ln in text.splitlines() if "wall-clock-ok" in ln]
    assert len(marked) == 2 and all("heartbeat" in ln for ln in marked)


# ---- routes ----------------------------------------------------------------------------------------

client = make_client()
SUP = login_headers(client, "supervisor", "sam")
COORD = login_headers(client, "coordinator", "bea")
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}


@pytest.fixture(autouse=True)
def fresh_state():
    saved_store = cases.store
    cases.store = InMemoryEventStore()
    clock_module.clock.reset()
    yield
    clock_module.clock.reset()
    cases.store = saved_store


def test_clock_routes_are_supervisor_only():
    for headers, want in [({}, 401), (COORD, 403), (login_headers(client, "lab"), 403), (SCHED, 403), (SUP, 200)]:
        assert client.post("/clock/advance", json={"days": 1}, headers=headers).status_code == want
    assert client.post("/clock/reset", headers=COORD).status_code == 403
    assert client.post("/clock/reset", headers=SUP).status_code == 200


def test_clock_advance_and_reset_over_http():
    start = client.get("/health").json()["sim_time"]
    out = client.post("/clock/advance", json={"days": 2, "hours": 3}, headers=SUP).json()
    assert out["sim_time"] == "2026-01-07T11:00:00+00:00"
    assert client.get("/health").json()["sim_time"] == out["sim_time"]
    assert client.post("/clock/advance", json={"hours": 1}, headers=SUP).json()["sim_time"].startswith("2026-01-07T12:00")
    assert client.post("/clock/reset", headers=SUP).json()["sim_time"] == start


@pytest.mark.parametrize("body", [{}, {"days": 0}, {"days": -1}, {"hours": -2}, {"days": "x"}, {"weeks": 1}])
def test_clock_advance_rejects_backwards_and_empty_requests(body):
    before = client.get("/health").json()["sim_time"]
    assert client.post("/clock/advance", json=body, headers=SUP).status_code == 422
    assert client.get("/health").json()["sim_time"] == before


def test_sweep_route_marks_overdue_once_as_system_scheduler():
    case_id = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=COORD).json()["case_id"]
    assert client.post("/scheduler/sweep", headers=SCHED).json()["mark_overdue"] == 0
    client.post("/clock/advance", json={"days": 3}, headers=SUP)  # Thursday: acknowledge was due Wednesday
    first = client.post("/scheduler/sweep", headers=SCHED).json()
    assert first["mark_overdue"] == 1 and first["cases_evaluated"] == 1 and first["errors"] == []
    again = client.post("/scheduler/sweep", headers=SCHED).json()
    assert again["mark_overdue"] == 0
    case = client.get(f"/cases/{case_id}", headers=COORD).json()
    overdue = [e for e in case["events"] if e["type"] == "mark_overdue"]
    assert len(overdue) == 1 and overdue[0]["actor"] == "system:scheduler"
    assert case["read_model"]["overdue"] is True and case["state"] == "result_received"
    assert case["read_model"]["breach_due_at"] == "2026-01-07T08:00:00+00:00"


def test_only_the_scheduler_service_may_run_a_sweep():
    for headers, want in [({}, 401), (SUP, 403), (COORD, 403)]:
        assert client.post("/scheduler/sweep", headers=headers).status_code == want
    assert client.post("/scheduler/sweep", headers={"Authorization": "Bearer wrong"}).status_code == 401
