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
    """Events of a type on the state's SLA and ladder; milestone events (A18) are in test_milestones_pg.py."""
    return [e for e in store.events_for(case_id) if e.type == type_ and "milestone" not in e.payload]


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
    run_sweep(store, PATHWAY, UAE, at(4), only=[case_id])  # accept_ownership (1 working day): due Fri 9th 08:00Z, not late
    assert len(events_of(store, case_id, "mark_overdue")) == 1
    run_sweep(store, PATHWAY, UAE, at(5), only=[case_id])  # Saturday: late
    run_sweep(store, PATHWAY, UAE, at(9), only=[case_id])
    overdue = events_of(store, case_id, "mark_overdue")
    assert len(overdue) == 2
    assert overdue[0].event_id != overdue[1].event_id
    assert [e.payload["state"] for e in overdue] == ["result_received", "acknowledged"]
    assert overdue[1].payload["due_at"] == at(4).isoformat()
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


# -- escalation ladder on Postgres (T-A18, T-A19) --------------------------------------------------------


def utc(m, d, hh=8, mm=0):
    return datetime(2026, m, d, hh, mm, tzinfo=UTC)


def rungs(store, case_id):
    return [e.payload["rung"] for e in events_of(store, case_id, "escalate")]


def test_ladder_fires_in_order_once_each_at_the_working_day_offsets(store, pool):
    case_id = new_case(store)  # acknowledge due Wed 7 Jan 08:00Z
    only = [case_id]
    for now, expected in [
        (utc(1, 7, 9), ["reminder"]),
        (utc(1, 12, 7, 59), ["reminder"]),
        (utc(1, 12, 8), ["reminder", "supervisor"]),
        (utc(1, 16, 7, 59), ["reminder", "supervisor"]),
        (utc(1, 16, 8), ["reminder", "supervisor", "coordinator"]),
    ]:
        run_sweep(store, PATHWAY, UAE, now, only=only)
        assert rungs(store, case_id) == expected, now
    timeline = [e.type for e in store.events_for(case_id)]
    assert timeline == ["case_created", "mark_overdue", "escalate", "escalate", "escalate"]
    assert [e.payload["target"] for e in events_of(store, case_id, "escalate")] == [
        "owner", "supervisor", "coordinator"
    ]
    assert [e.at for e in events_of(store, case_id, "escalate")] == [
        utc(1, 7, 9), utc(1, 12, 8), utc(1, 16, 8)
    ]


def test_concurrent_sweeps_after_a_long_outage_emit_each_rung_once(store):
    case_id = new_case(store)
    barrier = threading.Barrier(8)
    errors = []

    def worker():
        barrier.wait()
        try:
            run_sweep(store, PATHWAY, UAE, utc(3, 1), only=[case_id])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert rungs(store, case_id) == ["reminder", "supervisor", "coordinator"]
    assert len(events_of(store, case_id, "mark_overdue")) == 1
    # the state's breach and three rungs, and the referral milestone's breach (A18); the milestone's three rungs go
    # to the same roles in the same sweep, so they ride on the state's escalations (`also_for`, A19)
    assert [e.seq for e in store.events_for(case_id)] == list(range(1, 7))


def test_a_human_in_the_target_role_stops_the_ladder_and_nothing_else_can(store, pool):
    case_id = new_case(store)
    store.append(Event(case_id, "acknowledge", utc(1, 5, 9), "lab:l"), PATHWAY)
    store.append(Event(case_id, "accept_ownership", utc(1, 5, 10), "primary_care_owner:k"), PATHWAY)
    # owner_accepted awaits contact_patient: 3 working days from Mon 5th 10:00Z = Thu 8th 10:00Z
    run_sweep(store, PATHWAY, UAE, utc(1, 15, 11), only=[case_id])  # +3 working days after the breach (Tue 13th)
    assert rungs(store, case_id) == ["reminder", "supervisor"]
    model = store.read_model(case_id, PATHWAY)
    assert model.owner == "primary_care_owner:k"

    with pool.connection() as conn:
        before = conn.execute("SELECT count(*) FROM events WHERE case_id=%s", (case_id,)).fetchone()[0]
    from app.state_machine import InvalidTransition

    for actor in ("system:scheduler", "watchdog", "coordinator:co", "primary_care_owner:k"):
        with pytest.raises(InvalidTransition):
            store.append(Event(case_id, "escalation_accepted", utc(1, 15, 12), actor, {}), PATHWAY)
    with pool.connection() as conn:
        after = conn.execute("SELECT count(*) FROM events WHERE case_id=%s", (case_id,)).fetchone()[0]
    assert after == before  # refused acceptances leave no row

    store.append(Event(case_id, "escalation_accepted", utc(1, 15, 12), "supervisor:sam", {}), PATHWAY)
    run_sweep(store, PATHWAY, UAE, utc(4, 1), only=[case_id])
    assert rungs(store, case_id) == ["reminder", "supervisor"]  # the coordinator rung never fires
    after_model = store.read_model(case_id, PATHWAY)
    assert after_model.escalation_accepted and after_model.escalation_level == 2
    assert after_model.owner == "primary_care_owner:k" and after_model.state == "owner_accepted"
