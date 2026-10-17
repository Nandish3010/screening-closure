"""Integration tests for the Postgres event store. Run with `make test-db`."""

import dataclasses
import os
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from psycopg import errors

from app.config import settings as app_settings
from app.db import create_pool, migration_files, run_migrations
from app.events import (
    CASE_CREATED,
    CaseNotFound,
    Event,
    EventIdConflict,
    InMemoryEventStore,
    replay,
)
from app.pathway import load_pathway
from app.state_machine import InvalidTransition
from app.store_pg import PostgresEventStore
from test_state_machine import event_sequences, fold

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
SIGNED = {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}
HUMAN = "clinician:dr_a"


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=10)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


def new_case(store) -> UUID:
    case_id = uuid4()
    store.create_case(
        case_id,
        PATHWAY.name,
        Event(case_id, CASE_CREATED, AT, "system:intake", {"patient_ref": "Patient/1"}),
        PATHWAY.initial_state,
    )
    return case_id


def ev(case_id, type_, actor=HUMAN, payload=None, **kw):
    return Event(case_id, type_, AT, actor, SIGNED if payload is None else payload, **kw)


def cases_state(pool, case_id):
    with pool.connection() as conn:
        return conn.execute("SELECT current_state FROM cases WHERE id=%s", (case_id,)).fetchone()[0]


def count_events(pool, case_id):
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) FROM events WHERE case_id=%s", (case_id,)).fetchone()[0]


def test_happy_path_to_closed(store, pool):
    case_id = new_case(store)
    assert store.current_state(case_id) == PATHWAY.initial_state
    for t in PATHWAY.main_transitions:
        assert store.append(ev(case_id, t.event), PATHWAY) == t.to_state
    assert store.current_state(case_id) == "closed"
    assert cases_state(pool, case_id) == "closed"
    events = store.events_for(case_id)
    assert [e.seq for e in events] == list(range(1, len(PATHWAY.main_transitions) + 2))
    assert events[0].type == CASE_CREATED
    assert replay(events, PATHWAY) == "closed"
    assert [c.case_id for c in store.list_cases("closed")].count(case_id) == 1


def test_unknown_case(store):
    with pytest.raises(CaseNotFound):
        store.append(ev(uuid4(), PATHWAY.transitions[0].event), PATHWAY)
    assert store.current_state(uuid4()) is None
    assert store.events_for(uuid4()) == []


def test_invalid_transition_leaves_no_row_and_no_seq_gap(store, pool):
    case_id = new_case(store)
    first = PATHWAY.transitions[0]
    with pytest.raises(InvalidTransition):
        store.append(ev(case_id, "close"), PATHWAY)  # not allowed from the initial state
    with pytest.raises(InvalidTransition):
        store.append(ev(case_id, "bogus_event"), PATHWAY)
    assert count_events(pool, case_id) == 1
    assert store.current_state(case_id) == PATHWAY.initial_state
    store.append(ev(case_id, first.event), PATHWAY)
    assert [e.seq for e in store.events_for(case_id)] == [1, 2]


def test_same_event_id_inserts_once(store, pool):
    case_id = new_case(store)
    first = PATHWAY.transitions[0]
    event = ev(case_id, first.event)
    assert store.append(event, PATHWAY) == first.to_state
    # Retry: no longer valid from the new state, but idempotent replay must win.
    assert store.append(event, PATHWAY) == first.to_state
    assert store.append(dataclasses.replace(event), PATHWAY) == first.to_state
    assert count_events(pool, case_id) == 2
    assert [e.seq for e in store.events_for(case_id)] == [1, 2]


def test_event_id_reuse_on_other_case_is_a_conflict(store, pool):
    a, b = new_case(store), new_case(store)
    event = ev(a, PATHWAY.transitions[0].event)
    store.append(event, PATHWAY)
    with pytest.raises(EventIdConflict):
        store.append(dataclasses.replace(event, case_id=b), PATHWAY)
    assert count_events(pool, b) == 1


def test_automated_actor_cannot_close(store, pool):
    case_id = new_case(store)
    for t in PATHWAY.main_transitions[:-1]:
        store.append(ev(case_id, t.event), PATHWAY)
    before = store.current_state(case_id)
    with pytest.raises(InvalidTransition):
        store.append(ev(case_id, "close", actor="system:scheduler"), PATHWAY)
    assert store.current_state(case_id) == before != "closed"
    assert cases_state(pool, case_id) == before
    # and still closable by a human afterwards
    assert store.append(ev(case_id, "close"), PATHWAY) == "closed"


def test_events_are_append_only_in_the_database(store, pool):
    case_id = new_case(store)
    with pool.connection() as conn:
        with pytest.raises(errors.RaiseException):
            conn.execute("UPDATE events SET actor='x' WHERE case_id=%s", (case_id,))
    with pool.connection() as conn:
        with pytest.raises(errors.RaiseException):
            conn.execute("DELETE FROM events WHERE case_id=%s", (case_id,))
    with pool.connection() as conn:
        with pytest.raises(errors.RaiseException):
            conn.execute("TRUNCATE events")


def test_concurrent_appends_exactly_one_wins(store, pool):
    for _ in range(5):  # repeat: races are probabilistic
        case_id = new_case(store)
        first = PATHWAY.transitions[0]
        barrier = threading.Barrier(2)
        results: list = []

        def worker():
            event = ev(case_id, first.event)  # distinct event_id per thread
            barrier.wait()
            try:
                results.append(("ok", store.append(event, PATHWAY)))
            except InvalidTransition as exc:
                results.append(("invalid", exc))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(r[0] for r in results) == ["invalid", "ok"], results
        assert [e.seq for e in store.events_for(case_id)] == [1, 2]
        assert cases_state(pool, case_id) == first.to_state


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(event_sequences())
def test_postgres_replay_matches_in_memory_fold(store, pool, events):
    case_id = new_case(store)
    memory = InMemoryEventStore()
    memory.create_case(
        case_id, PATHWAY.name, Event(case_id, CASE_CREATED, AT, "system:intake"),
        PATHWAY.initial_state,
    )
    for e in events:
        e = dataclasses.replace(e, case_id=case_id, event_id=uuid4())
        pg_result = mem_result = None
        try:
            pg_result = store.append(e, PATHWAY)
        except InvalidTransition:
            pass
        try:
            mem_result = memory.append(e, PATHWAY)
        except InvalidTransition:
            pass
        assert pg_result == mem_result  # accepts and rejects the same events
        assert store.read_model(case_id, PATHWAY) == memory.read_model(case_id, PATHWAY)

    expected_state, applied = fold(events)
    pg_events = store.events_for(case_id)
    assert [e.seq for e in pg_events] == list(range(1, len(applied) + 2))
    assert [e.type for e in pg_events[1:]] == [e.type for e in applied]
    assert replay(pg_events, PATHWAY) == expected_state
    assert cases_state(pool, case_id) == expected_state == store.current_state(case_id)
    assert store.current_state(case_id) == memory.current_state(case_id)


def test_side_states_flags_and_read_model_persist_through_replay(store, pool):
    from datetime import timedelta

    case_id = new_case(store)

    def send(type_, actor, payload=None, day=0, hours=0):
        at = AT + timedelta(days=day, hours=hours)
        return store.append(Event(case_id, type_, at, actor, payload or {}), PATHWAY)

    send("acknowledge", "lab:l", day=1)
    send("accept_ownership", "primary_care_owner:k", day=2)
    send("contact_patient", "primary_care_owner:k", day=3)
    due = (AT + timedelta(days=10)).isoformat()
    send("mark_overdue", "system:scheduler", {"state": "patient_contacted", "due_at": due}, day=11)
    send("escalate", "system:scheduler", {"rung": "reminder", "state": "patient_contacted"}, day=11)
    assert send("consent_updated", "patient:p", {"withdrawn": True}, day=12) == "consent_withdrawn"
    assert cases_state(pool, case_id) == "consent_withdrawn"
    model = store.read_model(case_id, PATHWAY)
    assert model.consent_frozen and model.resume_state == "patient_contacted" and model.overdue
    assert send("consent_updated", "patient:p", {"withdrawn": False}, day=13) == "patient_contacted"
    model = store.read_model(case_id, PATHWAY)
    assert (model.state, model.overdue, model.escalation_level) == ("patient_contacted", True, 1)
    assert model.owner == "primary_care_owner:k"
    assert model.last_event_at == AT + timedelta(days=13)
    # an invalid annotation leaves no row
    before = count_events(pool, case_id)
    with pytest.raises(InvalidTransition):
        send("escalate", "system:scheduler", {"rung": "coordinator", "state": "patient_contacted"}, day=14)
    assert count_events(pool, case_id) == before
    # progress resolves the breach; a closed case can be reopened by a human with a reason
    for t in ("patient_acknowledge", "book_confirmatory", "complete_confirmatory", "clinician_review"):
        send(t, "primary_care_owner:k", day=15)
    assert not store.read_model(case_id, PATHWAY).overdue
    assert send("close", "primary_care_owner:k", SIGNED, day=16) == "closed"
    with pytest.raises(InvalidTransition):
        send("reopen", "system:scheduler", {"reason": "x"}, day=17)
    assert send("reopen", "primary_care_owner:k", {"reason": "amended report"}, day=17) == "reopened"
    assert cases_state(pool, case_id) == "reopened"
    assert replay(store.events_for(case_id), PATHWAY) == "reopened"


def test_migration_runner_is_idempotent(pool):
    run_migrations(pool)
    assert run_migrations(pool) == []  # second run applies nothing
    versions = [p.name for p in migration_files()]
    with pool.connection() as conn:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [r[0] for r in rows] == versions
    assert "001_events.sql" in versions
    assert len(rows) == len(set(versions))
