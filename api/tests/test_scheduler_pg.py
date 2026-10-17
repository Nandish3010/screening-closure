"""T-A15 and the persistence half of T-A17: the sweep and the sim clock against Postgres.

Run with `make test-db`. Every sweep here is restricted to the test's own cases (`only=`) so
the shared dev database's seeded cases are never touched.
"""

import os
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.calendar import WorkingCalendar
from app.clock import SimClock
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.store_pg import PostgresClockStore, PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, run_sweep

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=12)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


def new_case(store, created=AT):
    case_id = uuid4()
    store.create_case(
        case_id, PATHWAY.name, Event(case_id, CASE_CREATED, created, "system:intake"),
        PATHWAY.initial_state,
    )
    return case_id


def events_of(store, case_id, type_):
    return [e for e in store.events_for(case_id) if e.type == type_]


def test_overdue_is_emitted_exactly_once_per_breach_even_when_swept_concurrently(store):
    case_id = new_case(store)
    only = [case_id]
    assert run_sweep(store, PATHWAY, UAE, at(2), only=only).mark_overdue == 0  # exactly due
    assert events_of(store, case_id, "mark_overdue") == []

    now = at(2, hours=1)
    for _ in range(5):  # sequential repeats
        run_sweep(store, PATHWAY, UAE, now, only=only)
    assert len(events_of(store, case_id, "mark_overdue")) == 1

    # a fresh breach, swept by many threads at once
    other = new_case(store)
    barrier = threading.Barrier(8)
    errors = []

    def worker():
        barrier.wait()
        try:
            for k in range(3):
                run_sweep(store, PATHWAY, UAE, now + timedelta(minutes=k), only=[other])
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    overdue = events_of(store, other, "mark_overdue")
    assert len(overdue) == 1
    assert overdue[0].actor == SCHEDULER_ACTOR
    assert [e.seq for e in store.events_for(other)] == list(range(1, len(store.events_for(other)) + 1))
    assert store.current_state(other) == "result_received"  # never moved by the scheduler


def test_a_second_breach_on_a_later_transition_produces_a_new_event(store):
    case_id = new_case(store)
    run_sweep(store, PATHWAY, UAE, at(3), only=[case_id])  # acknowledge due Wed 7th
    first = events_of(store, case_id, "mark_overdue")
    assert len(first) == 1
    store.append(Event(case_id, "acknowledge", at(3), "lab:l"), PATHWAY)  # Thursday 8th
    assert not store.read_model(case_id, PATHWAY).overdue
    run_sweep(store, PATHWAY, UAE, at(6), only=[case_id])  # Sunday: not due until Monday 12th
    assert len(events_of(store, case_id, "mark_overdue")) == 1
    run_sweep(store, PATHWAY, UAE, at(8), only=[case_id])  # Tuesday 13th
    run_sweep(store, PATHWAY, UAE, at(9), only=[case_id])
    overdue = events_of(store, case_id, "mark_overdue")
    assert len(overdue) == 2
    assert overdue[0].event_id != overdue[1].event_id
    assert [e.payload["state"] for e in overdue] == ["result_received", "acknowledged"]
    assert overdue[1].payload["due_at"] == at(7).isoformat()
    model = store.read_model(case_id, PATHWAY)
    assert model.state == "acknowledged" and model.overdue


def test_weekend_does_not_count_towards_the_sla(store):
    friday = datetime(2026, 1, 9, 8, 0, tzinfo=UTC)
    case_id = new_case(store, created=friday)  # 2 working days: due Tuesday 13th 08:00Z
    run_sweep(store, PATHWAY, UAE, datetime(2026, 1, 12, 20, 0, tzinfo=UTC), only=[case_id])
    assert events_of(store, case_id, "mark_overdue") == []  # Monday evening: not late
    run_sweep(store, PATHWAY, UAE, datetime(2026, 1, 13, 9, 0, tzinfo=UTC), only=[case_id])
    assert len(events_of(store, case_id, "mark_overdue")) == 1


def test_sweep_only_touches_the_requested_cases_and_skips_closed(store):
    a, b = new_case(store), new_case(store)
    closed = new_case(store)
    for i, t in enumerate(PATHWAY.main_transitions, start=1):
        store.append(
            Event(closed, t.event, at(i), "primary_care_owner:k",
                  {"evidence_ref": "x", "signed_by": "y"}),
            PATHWAY,
        )
    result = run_sweep(store, PATHWAY, UAE, at(400), only=[a, closed])
    assert result.cases_evaluated == 1  # closed cases are not evaluated
    assert len(events_of(store, a, "mark_overdue")) == 1
    assert events_of(store, b, "mark_overdue") == []
    assert events_of(store, closed, "mark_overdue") == []
    assert store.current_state(closed) == "closed"


def test_scheduler_events_never_include_close(store):
    case_id = new_case(store)
    for day in (3, 20, 80, 200):
        run_sweep(store, PATHWAY, UAE, at(day), only=[case_id])
    assert {e.type for e in store.events_for(case_id) if e.actor == SCHEDULER_ACTOR} <= {
        "mark_overdue", "escalate"
    }
    assert store.current_state(case_id) != "closed"


# -- sim clock persistence (T-A17) -------------------------------------------------------------------


def test_sim_clock_survives_an_api_restart_and_a_reset(pool):
    clock_id = f"test-{uuid4()}"
    persist = PostgresClockStore(pool, clock_id)
    try:
        first = SimClock(AT, persist)
        assert persist.load() == AT
        first.advance(days=3, hours=2)
        # "restart": a brand-new clock object bound to the same table row
        restarted = SimClock(AT, PostgresClockStore(pool, clock_id))
        assert restarted.now() == at(3, 2)
        with pytest.raises(ValueError):
            restarted.advance(days=-1)
        assert PostgresClockStore(pool, clock_id).load() == at(3, 2)  # a rejected move stores nothing
        restarted.reset()
        assert SimClock(AT, PostgresClockStore(pool, clock_id)).now() == AT
        with pool.connection() as conn:  # reset stored start == now
            row = conn.execute(
                "SELECT start_at, now_at FROM sim_clock WHERE clock_id = %s", (clock_id,)
            ).fetchone()
        assert row[0] == row[1] == AT
    finally:
        persist.delete()


def test_no_test_clock_rows_are_left_behind(pool):
    # the API's own clock uses clock_id "main" and these tests never write it
    with pool.connection() as conn:
        ids = {r[0] for r in conn.execute("SELECT clock_id FROM sim_clock").fetchall()}
    assert not any(i.startswith("test-") for i in ids)
