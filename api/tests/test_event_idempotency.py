"""SEC-2 fix 5: an event_id is an idempotency key for ONE event, not a licence to overwrite history.

A repeated event_id with the same content is a no-op (a retry). With a different type, actor, payload or
time it is a 409, not a silent success that returns the state and writes nothing; and two events of one batch
may not share an id. Memory and Postgres."""

import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from api_helpers import actor_of, make_client
from app import evidence
from app.calendar import WorkingCalendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, EventContentConflict, EventIdConflict, InMemoryEventStore, event_digest
from app.pathway import load_pathway
from app.routers import cases
from app.sweep import SCHEDULER_ACTOR, plan_case, run_sweep
from test_evidence import reviewed_case, state
from test_handoff import REQUESTED, at, make_store_case
from test_state_machine import OWNER

PATHWAY = load_pathway(app_settings.pathway_path)
HAS_DB = bool(os.environ.get("DATABASE_URL"))
client = make_client()


@pytest.fixture(params=["memory", pytest.param("postgres", marks=[pytest.mark.integration, pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")])])
def store(request, monkeypatch):
    if request.param == "memory":
        s = InMemoryEventStore()
        monkeypatch.setattr(cases, "store", s)
        yield s
    else:
        from app.db import create_pool, run_migrations
        from app.store_pg import PostgresEventStore

        pool = create_pool(os.environ["DATABASE_URL"], max_size=4)
        run_migrations(pool)
        s = PostgresEventStore(pool)
        monkeypatch.setattr(cases, "store", s)
        yield s
        pool.close()
    clock.reset()


@pytest.fixture(autouse=True)
def _verification_off():
    saved = evidence.verifier
    evidence.verifier = evidence.AcceptAllVerifier()
    yield
    evidence.verifier = saved


def new_case(store, patient_ref="Patient/idem"):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, at(0), "system:intake", {"patient_ref": patient_ref}),
                      PATHWAY.initial_state)
    return cid


# ---- over HTTP: the repro ------------------------------------------------------------------------------------


def test_reposting_a_close_id_with_a_reopen_body_is_409_and_changes_nothing(store):
    cid, spec = reviewed_case("Dr Idem")
    key = str(uuid4())
    closing = {"type": "close", "event_id": key,
               "payload": {"evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Idem")}}
    first = client.post(f"/cases/{cid}/events", json=closing, headers=spec)
    assert first.status_code == 201 and first.json()["state"] == "closed"
    n = len(store.events_for(UUID(cid)))
    # a genuine retry of the same request: the same answer, nothing written
    again = client.post(f"/cases/{cid}/events", json=closing, headers=spec)
    assert again.status_code == 201 and again.json()["state"] == "closed"
    assert len(store.events_for(UUID(cid))) == n
    # the same key on a different event: refused (it used to answer 201 and show the case as closed)
    reopen = client.post(f"/cases/{cid}/events", json={"type": "reopen", "event_id": key, "payload": {"reason": "new_finding"}}, headers=spec)
    assert reopen.status_code == 409 and "different event" in reopen.json()["detail"], reopen.text
    assert len(store.events_for(UUID(cid))) == n and state(cid) == "closed"


def test_a_different_payload_with_the_same_key_is_409(store):
    cid, spec = reviewed_case("Dr Idem2")
    key = str(uuid4())
    body = {"type": "close", "event_id": key, "payload": {"evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Idem2")}}
    assert client.post(f"/cases/{cid}/events", json=body, headers=spec).status_code == 201
    other = {**body, "payload": {**body["payload"], "evidence_ref": "DiagnosticReport/r2"}}
    r = client.post(f"/cases/{cid}/events", json=other, headers=spec)
    assert r.status_code == 409 and "different event" in r.json()["detail"]


def test_a_different_time_with_the_same_key_is_409(store):
    cid, spec = reviewed_case("Dr Idem3")
    key = str(uuid4())
    last = store.events_for(UUID(cid))[-1].at
    body = {"type": "close", "event_id": key, "at": last.isoformat(),
            "payload": {"evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Idem3")}}
    assert client.post(f"/cases/{cid}/events", json=body, headers=spec).status_code == 201
    r = client.post(f"/cases/{cid}/events", json={**body, "at": (last - timedelta(minutes=1)).isoformat()}, headers=spec)
    assert r.status_code == 409
    assert client.post(f"/cases/{cid}/events", json=body, headers=spec).status_code == 201  # the exact retry is fine


def test_a_key_used_on_another_case_is_still_the_old_409(store):
    a, spec_a = reviewed_case("Dr Idem4")
    b, spec_b = reviewed_case("Dr Idem5")
    key = str(uuid4())
    body = {"type": "close", "event_id": key, "payload": {"evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Idem4")}}
    assert client.post(f"/cases/{a}/events", json=body, headers=spec_a).status_code == 201
    other = {**body, "payload": {**body["payload"], "signed_by": actor_of("specialist_owner", "Dr Idem5")}}
    r = client.post(f"/cases/{b}/events", json=other, headers=spec_b)
    assert r.status_code == 409 and "another case" in r.json()["detail"]


# ---- the store -----------------------------------------------------------------------------------------------


def test_every_part_of_the_content_is_compared(store):
    cid = new_case(store)
    first = Event(cid, "acknowledge", at(1), "lab:l", {}, uuid4())
    store.append(first, PATHWAY)
    assert store.append(first, PATHWAY) == "acknowledged"  # identical
    assert store.append(Event(cid, "acknowledge", at(1), "lab:l", {}, first.event_id), PATHWAY) == "acknowledged"  # equal, not the same object
    variants = [
        Event(cid, "callback_requested", at(1), "lab:l", {}, first.event_id),  # type
        Event(cid, "acknowledge", at(2), "lab:l", {}, first.event_id),  # time
        Event(cid, "acknowledge", at(1), "lab:other", {}, first.event_id),  # actor
        Event(cid, "acknowledge", at(1), "lab:l", {"note": "x"}, first.event_id),  # payload
    ]
    for v in variants:
        with pytest.raises(EventContentConflict):
            store.append(v, PATHWAY)
        with pytest.raises(EventIdConflict):  # it is also an EventIdConflict: a 409 for every caller
            store.append(v, PATHWAY)
    assert [e.type for e in store.events_for(cid)] == ["case_created", "acknowledge"]


def test_the_same_instant_in_another_time_zone_is_the_same_event(store):
    from datetime import timezone

    cid = new_case(store)
    first = Event(cid, "acknowledge", at(1), "lab:l", {}, uuid4())
    store.append(first, PATHWAY)
    plus4 = first.at.astimezone(timezone(timedelta(hours=4)))
    assert store.append(Event(cid, "acknowledge", plus4, "lab:l", {}, first.event_id), PATHWAY) == "acknowledged"


def test_duplicate_ids_inside_one_batch_are_refused_and_nothing_is_written(store):
    if not hasattr(store, "append_many"):
        pytest.skip("the in-memory store has no batch append")
    cid = new_case(store)
    key = uuid4()
    batch = [Event(cid, "acknowledge", at(1), "lab:l", {}, key), Event(cid, "accept_ownership", at(2), OWNER, {}, key)]
    with pytest.raises(EventIdConflict, match="twice in one batch"):
        store.append_many(batch, PATHWAY)
    assert [e.type for e in store.events_for(cid)] == ["case_created"]
    # a batch of distinct ids is fine, and a retry of it is a no-op
    ok = [Event(cid, "acknowledge", at(1), "lab:l", {}), Event(cid, "accept_ownership", at(2), OWNER, {})]
    assert store.append_many(ok, PATHWAY) == "owner_accepted"
    assert store.append_many(ok, PATHWAY) == "owner_accepted"
    assert len(store.events_for(cid)) == 3
    changed = [ok[0], Event(cid, "accept_ownership", at(2), OWNER, {"provider": "other"}, ok[1].event_id)]
    with pytest.raises(EventContentConflict):
        store.append_many(changed, PATHWAY)


def test_an_automated_actors_time_is_not_part_of_its_identity(store):
    """The sweep's ids come from the cause; `at` is when it noticed. Two detectors an instant apart agree."""
    cid = new_case(store)
    for i, t in enumerate([("acknowledge", "lab:l"), ("accept_ownership", OWNER), ("contact_patient", OWNER)], 1):
        store.append(Event(cid, t[0], at(i), t[1], {}), PATHWAY)
    model = store.read_model(cid, PATHWAY)
    planned = plan_case(cid, model, PATHWAY, WorkingCalendar(), at(60))
    first = planned[0]
    store.append(first, PATHWAY)
    later = Event(cid, first.type, at(61), first.actor, first.payload, first.event_id)
    assert store.append(later, PATHWAY)  # same cause, later detection: the same event
    assert event_digest(first.type, first.at, first.actor, first.payload) == event_digest(later.type, later.at, later.actor, later.payload)
    different = Event(cid, first.type, at(61), first.actor, {**first.payload, "due_at": "2026-02-01T00:00:00+00:00"}, first.event_id)
    with pytest.raises(EventContentConflict):
        store.append(different, PATHWAY)


def test_a_sweep_that_meets_a_conflicting_id_reports_a_race_not_an_error():
    store, cid = make_store_case(*REQUESTED)
    (bounce,) = plan_case(cid, store.read_model(cid, PATHWAY), PATHWAY, WorkingCalendar(), at(30))
    # something else already wrote this id as a different event on this case
    store._event_ids[bounce.event_id] = (cid, "someone-elses-digest")
    res = run_sweep(store, PATHWAY, WorkingCalendar(), at(30))
    assert res.rejected == 1 and res.errors == [] and res.handoff_bounced == 0


# ---- E20a F9: a retry without `at` (what the web client sends) ----------------------------------------------------


def test_a_retry_without_at_after_time_has_passed_is_the_same_event(store):
    """web/src/api/httpAdapter.ts posts {type, payload, event_id} and no `at`, so the route stamps the clock's time. Under
    CLOCK_MODE=wall that time moves between a request that timed out and its retry; the retry must still be the same
    event (201, the stored result, nothing written), not a 409 for a 'different time'."""
    cid, spec = reviewed_case("Dr Idem F9")
    key = str(uuid4())
    body = {"type": "close", "event_id": key,
            "payload": {"evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Idem F9")}}
    first = client.post(f"/cases/{cid}/events", json=body, headers=spec)
    assert first.status_code == 201 and first.json()["state"] == "closed"
    n, stored_at = len(store.events_for(UUID(cid))), store.events_for(UUID(cid))[-1].at
    clock.advance(hours=1)  # the network timeout and the retry: real time has moved on
    again = client.post(f"/cases/{cid}/events", json=body, headers=spec)
    assert again.status_code == 201, again.text
    assert again.json()["state"] == "closed"
    assert len(store.events_for(UUID(cid))) == n and store.events_for(UUID(cid))[-1].at == stored_at


def test_a_retry_without_at_with_another_body_is_still_409(store):
    """Leaving `at` out keeps the stored event's time, not its content: a different payload under the same key is refused."""
    cid, spec = reviewed_case("Dr Idem F9b")
    key = str(uuid4())
    body = {"type": "close", "event_id": key,
            "payload": {"evidence_ref": "DiagnosticReport/r1", "signed_by": actor_of("specialist_owner", "Dr Idem F9b")}}
    assert client.post(f"/cases/{cid}/events", json=body, headers=spec).status_code == 201
    clock.advance(hours=1)
    other = {**body, "payload": {**body["payload"], "evidence_ref": "DiagnosticReport/r2"}}
    r = client.post(f"/cases/{cid}/events", json=other, headers=spec)
    assert r.status_code == 409 and "different event" in r.json()["detail"]
