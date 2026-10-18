"""SEC-2 fix 2: event time has a lower bound for user tokens (checked under the case lock), and the
scheduler's / watchdog's deterministic ids derive from the event that started the clock or request,
never from a timestamp (ADR-027 amended)."""

import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api_helpers import actor_of, make_client
from app import evidence
from app.calendar import WorkingCalendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import step
from app.sweep import plan_case, run_sweep, sweep_event_id
from test_evidence import reviewed_case, state
from test_handoff import REQUESTED, at, bounces, make_store_case
from test_state_machine import OWNER, TO_CONTACTED, drive, make_event

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
HAS_DB = bool(os.environ.get("DATABASE_URL"))
client = make_client()


@pytest.fixture(params=["memory", pytest.param("postgres", marks=[pytest.mark.integration, pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")])])
def backend(request, monkeypatch):
    if request.param == "memory":
        monkeypatch.setattr(cases, "store", InMemoryEventStore())
        yield "memory"
    else:
        from app.db import create_pool, run_migrations
        from app.store_pg import PostgresEventStore

        pool = create_pool(os.environ["DATABASE_URL"], max_size=4)
        run_migrations(pool)
        monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
        yield "postgres"
        pool.close()
    clock.reset()


@pytest.fixture(autouse=True)
def _verification_off():
    saved = evidence.verifier
    evidence.verifier = evidence.AcceptAllVerifier()
    yield
    evidence.verifier = saved


# ---- a user's backdated event is refused ---------------------------------------------------------------------


def test_a_backdated_close_is_refused_422_and_nothing_is_written(backend):
    cid, spec = reviewed_case("Dr Backdate Close")
    last = cases.store.events_for(UUID(cid))[-1]
    n = len(cases.store.events_for(UUID(cid)))
    signer = actor_of("specialist_owner", "Dr Backdate Close")
    body = {"evidence_ref": "DiagnosticReport/r1", "signed_by": signer}
    behind = (last.at - timedelta(seconds=1)).isoformat()
    r = client.post(f"/cases/{cid}/events", json={"type": "close", "payload": body, "at": behind}, headers=spec)
    assert r.status_code == 422 and "backdated" in r.json()["detail"], r.text
    assert len(cases.store.events_for(UUID(cid))) == n and state(cid) == "clinician_reviewed"
    # the same instant as the latest event is not backdated; the boundary is inclusive
    r = client.post(f"/cases/{cid}/events", json={"type": "close", "payload": body, "at": last.at.isoformat()}, headers=spec)
    assert r.status_code == 201 and r.json()["state"] == "closed"


def test_a_naive_timestamp_is_read_as_utc_and_judged_the_same_way(backend):
    cid, spec = reviewed_case("Dr Naive")
    last = cases.store.events_for(UUID(cid))[-1]
    naive = (last.at - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    r = client.post(f"/cases/{cid}/events", json={"type": "close", "payload": {
        "evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Naive")}, "at": naive}, headers=spec)
    assert r.status_code == 422 and "backdated" in r.json()["detail"]


def test_the_bound_is_read_under_the_lock_not_from_an_earlier_read(backend):
    """The guard runs on the model replayed inside the append, so an event that landed after the caller's
    own read is seen. (The route reads nothing before it: the guard is the only judge.)"""
    store = cases.store
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, at(0), "system:intake", {}), PATHWAY.initial_state)
    seen = []
    store.append(Event(cid, "acknowledge", at(2), "lab:l", {}), PATHWAY, guard=lambda m: seen.append(m.last_event_at))
    assert seen == [at(0)]
    seen.clear()
    first = Event(cid, "accept_ownership", at(3), OWNER, {})
    store.append(first, PATHWAY, guard=lambda m: seen.append(m.last_event_at))
    assert seen == [at(2)]
    store.append(first, PATHWAY, guard=lambda m: seen.append("retry"))  # an idempotent retry is not re-judged
    assert seen == [at(2)]

    def refuse(model):
        raise PermissionError("no")

    with pytest.raises(PermissionError):
        store.append(Event(cid, "contact_patient", at(4), OWNER, {}), PATHWAY, guard=refuse)
    assert [e.type for e in store.events_for(cid)] == ["case_created", "acknowledge", "accept_ownership"]


def test_a_service_actor_may_still_post_late():
    """ADR-027 stays reachable for the actors that legitimately post late: the sweep reads `now` first."""
    store, cid = make_store_case(("acknowledge", "lab:l", {}), ("callback_requested", "coordinator:c", {}))
    late = Event(cid, "watchdog_flagged", at(0, 0), "system:watchdog", {"reason": "idle", "idle_days": 7})
    store.append(late, PATHWAY)
    assert store.read_model(cid, PATHWAY).backdated_events == 1


# ---- the ids -------------------------------------------------------------------------------------------------


def test_the_double_handoff_bounce_repro_gives_two_distinct_bounces():
    """Request at T, bounce, request at T again, bounce again. The ids used to be uuid5(case, requested_at):
    the second bounce shared the first one's id and was swallowed as a repeat, so the second request never
    bounced and the case waited on a handoff nobody would answer."""
    store, cid = make_store_case(*REQUESTED)
    first_request = [e for e in store.events_for(cid) if e.type == "handoff_requested"][0]
    assert run_sweep(store, PATHWAY, UAE, at(3, 6)).handoff_bounced == 1
    assert store.read_model(cid, PATHWAY).pending_handoff is None
    again = Event(cid, "handoff_requested", first_request.at, OWNER, {"to_provider": "endo-1"})  # at T again
    store.append(again, PATHWAY)
    assert store.read_model(cid, PATHWAY).pending_handoff["requested_at"] == first_request.at.isoformat()
    res = run_sweep(store, PATHWAY, UAE, at(10))
    assert res.handoff_bounced == 1 and res.errors == []
    two = bounces(store, cid)
    assert len(two) == 2 and two[0].event_id != two[1].event_id
    assert store.read_model(cid, PATHWAY).pending_handoff is None
    # and each is still idempotent: sweeping again adds nothing
    assert run_sweep(store, PATHWAY, UAE, at(11)).handoff_bounced == 0
    assert len(bounces(store, cid)) == 2


def test_a_bounce_id_comes_from_the_request_event():
    store, cid = make_store_case(*REQUESTED)
    request = [e for e in store.events_for(cid) if e.type == "handoff_requested"][0]
    model = store.read_model(cid, PATHWAY)
    assert model.pending_handoff["requested_event_id"] == str(request.event_id)
    (bounce,) = plan_case(cid, model, PATHWAY, UAE, at(30))
    assert bounce.event_id == sweep_event_id(cid, request.event_id, "bounce")


def _overdue_id(cid, model, now):
    planned = [e for e in plan_case(cid, model, PATHWAY, UAE, now) if e.type == "mark_overdue"]
    assert len(planned) == 1
    again = [e for e in plan_case(cid, model, PATHWAY, UAE, now + timedelta(hours=1)) if e.type == "mark_overdue"]
    assert again[0].event_id == planned[0].event_id  # stable for one clock, whenever it is planned
    return planned[0].event_id


def test_reentering_a_state_at_the_same_instant_is_a_new_breach_with_a_new_id():
    cid = uuid4()
    model = drive(*TO_CONTACTED)
    ids = [_overdue_id(cid, model, at(400))]
    t = at(3)
    for _ in range(3):  # lost contact and found again, every time at the same instant the state was entered
        model = step(model, make_event("mark_lost_contact", OWNER, {"failed_attempts": 3}, t), PATHWAY)
        model = step(model, make_event("contact_patient", OWNER, {}, t), PATHWAY)
        assert model.state == "patient_contacted"
        ids.append(_overdue_id(cid, model, at(400)))
    assert len(set(ids)) == 4


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=5))
def test_property_each_visit_to_a_state_yields_a_fresh_mark_overdue_id(gaps):
    cid = uuid4()
    model = drive(*TO_CONTACTED)
    ids = [_overdue_id(cid, model, at(900))]
    t = at(3)
    for gap in gaps:
        t = t + timedelta(days=gap)  # a gap of 0 re-enters at exactly the instant of the previous entry
        model = step(model, make_event("mark_lost_contact", OWNER, {"failed_attempts": 3}, t), PATHWAY)
        model = step(model, make_event("contact_patient", OWNER, {}, t), PATHWAY)
        ids.append(_overdue_id(cid, model, at(900)))
    assert len(set(ids)) == len(ids)


def test_rungs_take_the_clock_start_too_and_a_consent_excursion_keeps_the_clock():
    cid = uuid4()
    model = drive(*TO_CONTACTED)
    started = model.clock_started_event_id
    planned = plan_case(cid, model, PATHWAY, UAE, at(400))
    assert {e.event_id for e in planned} == {sweep_event_id(cid, started, k) for k in ("mark_overdue", "escalate:reminder", "escalate:supervisor", "escalate:coordinator")}
    # consent withdrawn and restored is an excursion: the same clock, so the same breach and the same ids
    model = step(model, make_event("consent_updated", "patient:p", {"withdrawn": True}, at(5)), PATHWAY)
    model = step(model, make_event("consent_updated", "patient:p", {"withdrawn": False}, at(6)), PATHWAY)
    assert model.state == "patient_contacted" and model.clock_started_event_id == started


def test_a_referral_acceptance_restarts_the_confirmatory_clock_with_its_own_event():
    model = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}))
    assert PATHWAY.state_clock_start("confirmatory_booked") == "handoff_accepted"
    model = step(model, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(10)), PATHWAY)
    accept = make_event("handoff_accepted", "specialist_owner:s", {}, at(11))
    model = step(model, accept, PATHWAY)
    model = step(model, make_event("book_confirmatory", "specialist_owner:s", {}, at(12)), PATHWAY)
    assert model.state == "confirmatory_booked" and model.clock_started_event_id == accept.event_id
    assert model.clock_started_at == accept.at
