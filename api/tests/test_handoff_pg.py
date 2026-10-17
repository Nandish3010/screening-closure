"""T-A20, T-A21, T-A22 against Postgres (`make test-db`): ownership moves only on acceptance,
the reason taxonomy, and the scheduler's bounce of an unanswered referral."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.state_machine import InvalidTransition, PayloadError
from app.store_pg import PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, run_sweep

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday
A = "primary_care_owner:dr_a"
B = "specialist_owner:dr_b"


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=6)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


def referred_case(store, to_provider="endo-1"):
    """A case whose owner A has requested a handoff (Monday 5 Jan 13:00)."""
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {"patient_ref": "Patient/1"}),
                      PATHWAY.initial_state)
    steps = [("acknowledge", "lab:l", {}), ("accept_ownership", A, {}), ("contact_patient", A, {}),
             ("patient_acknowledge", "patient:p", {}), ("handoff_requested", A, {"to_provider": to_provider})]
    for i, (type_, actor, payload) in enumerate(steps, start=1):
        store.append(Event(cid, type_, at(0, i), actor, payload), PATHWAY)
    return cid


def post(store, cid, type_, actor, payload=None, when=at(1)):
    return store.append(Event(cid, type_, when, actor, payload or {}), PATHWAY)


def test_t_a20_ownership_changes_only_at_acceptance(store):
    cid = referred_case(store)
    m = store.read_model(cid, PATHWAY)
    assert m.owner == A and m.provider is None and m.pending_handoff["to_provider"] == "endo-1"
    post(store, cid, "handoff_accepted", B)
    m = store.read_model(cid, PATHWAY)
    assert m.owner == B and m.provider == "endo-1" and m.pending_handoff is None
    types = [(e.type, e.actor) for e in store.events_for(cid)[-2:]]
    assert types == [("handoff_requested", A), ("handoff_accepted", B)]
    assert store.current_state(cid) == "patient_acknowledged"


@pytest.mark.parametrize("reason", ["capacity", "wrong_service", "missing_info", "patient_declined"])
def test_t_a21_taxonomy_reasons_reject_to_referral_rejected(store, reason):
    cid = referred_case(store)
    assert post(store, cid, "handoff_rejected", B, {"reason": reason}) == "referral_rejected"
    m = store.read_model(cid, PATHWAY)
    assert m.owner == A and m.last_handoff["reason"] == reason
    # re-route: ask someone else, who accepts
    post(store, cid, "handoff_requested", A, {"to_provider": "endo-2"}, at(2))
    assert post(store, cid, "handoff_accepted", "specialist_owner:dr_c", None, at(3)) == "owner_accepted"
    assert store.read_model(cid, PATHWAY).owner == "specialist_owner:dr_c"


@pytest.mark.parametrize("payload", [{}, {"reason": "busy"}, {"reason": "timeout"}])
def test_t_a21_other_reasons_are_refused_and_write_nothing(store, pool, payload):
    cid = referred_case(store)
    n = len(store.events_for(cid))
    with pytest.raises(PayloadError):
        post(store, cid, "handoff_rejected", B, payload)
    assert len(store.events_for(cid)) == n and store.current_state(cid) == "patient_acknowledged"


def test_nothing_pending_means_nothing_to_answer(store):
    cid = referred_case(store)
    post(store, cid, "handoff_accepted", B)
    with pytest.raises(InvalidTransition, match="no pending handoff"):
        post(store, cid, "handoff_rejected", "specialist_owner:dr_c", {"reason": "capacity"})


def test_t_a22_unanswered_request_bounces_once_at_the_configured_time(store):
    cid = referred_case(store)  # requested Mon 13:00 -> due Thu 8 Jan 13:00 (3 working days)
    only = [cid]
    assert run_sweep(store, PATHWAY, UAE, at(3, 5), only=only).handoff_bounced == 0  # exactly due
    res = run_sweep(store, PATHWAY, UAE, at(3, 6), only=only)
    assert res.handoff_bounced == 1 and res.errors == []
    for k in range(1, 4):  # repeat sweeps (and a wider one) add nothing
        assert run_sweep(store, PATHWAY, UAE, at(3, 6 + k), only=only).handoff_bounced == 0
    bounces = [e for e in store.events_for(cid) if e.type == "handoff_rejected"]
    assert len(bounces) == 1
    b = bounces[0]
    assert b.actor == SCHEDULER_ACTOR and b.payload["reason"] == "timeout" and b.payload["bounced"] is True
    m = store.read_model(cid, PATHWAY)
    assert m.state == "referral_rejected" and m.owner == A  # back with the previous owner
    assert m.pending_handoff is None and m.last_handoff["bounced"] is True


def test_t_a22_the_bounce_escalates_when_the_reroute_is_late(store):
    cid = referred_case(store)
    only = [cid]
    run_sweep(store, PATHWAY, UAE, at(3, 6), only=only)  # bounce Thu 14:00; re-route due Mon 12 Jan 14:00
    assert not store.read_model(cid, PATHWAY).overdue
    res = run_sweep(store, PATHWAY, UAE, at(7, 7), only=only)
    assert (res.mark_overdue, res.escalate) == (1, 1)
    m = store.read_model(cid, PATHWAY)
    assert m.overdue and m.escalation_level == 1 and m.state == "referral_rejected"
    assert run_sweep(store, PATHWAY, UAE, at(7, 8), only=only).escalate == 0


def test_an_answered_request_is_never_bounced(store):
    cid = referred_case(store)
    post(store, cid, "handoff_accepted", B, None, at(2))
    res = run_sweep(store, PATHWAY, UAE, at(30), only=[cid])
    assert res.handoff_bounced == 0
    assert [e.type for e in store.events_for(cid) if e.type == "handoff_rejected"] == []
