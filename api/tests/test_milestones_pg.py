"""A18, ADR-028 against Postgres (`make test-db`): the milestone breach and its ladder through the real store,
the sharded sweep, concurrent sweeps and replay. Sweeps are restricted to the test's own cases (`only=`)."""

import os
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event, replay_model
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, run_sweep, sweep_event_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday
PCO = "primary_care_owner:dr_k"
REFERRAL = "referral_requested"


def wd(n, hours=0):
    return UAE.add_working_days(AT, n) + timedelta(hours=hours)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=12)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


def new_case(store, steps=("acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge")):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
    for i, kind in enumerate(steps, start=1):
        actor = "patient:p" if kind == "patient_acknowledge" else "lab:l" if kind == "acknowledge" else PCO
        store.append(Event(cid, kind, AT + timedelta(days=i), actor, {}), PATHWAY)
    return cid


def milestone_events(store, cid, type_):
    return [e for e in store.events_for(cid) if e.type == type_ and e.payload.get("milestone") == REFERRAL]


def test_a_referral_on_day_14_is_no_breach_and_on_day_16_is_one_breach_and_one_reminder(store):
    early, late = new_case(store), new_case(store)
    store.append(Event(early, "handoff_requested", wd(14, 2), PCO, {"to_provider": "endo-1"}), PATHWAY)
    for now in (wd(14, 3), wd(16, 1), wd(30)):
        run_sweep(store, PATHWAY, UAE, now, only=[early])
    assert milestone_events(store, early, "mark_overdue") == [] and milestone_events(store, early, "escalate") == []
    assert store.read_model(early, PATHWAY).milestones[REFERRAL].met_at == wd(14, 2)

    for _ in range(3):
        run_sweep(store, PATHWAY, UAE, wd(16, 1), only=[late])
    [breach] = milestone_events(store, late, "mark_overdue")
    [reminder] = milestone_events(store, late, "escalate")
    created = store.events_for(late)[0]
    assert breach.event_id == sweep_event_id(late, created.event_id, f"milestone:{REFERRAL}:mark_overdue")
    assert breach.actor == reminder.actor == SCHEDULER_ACTOR and "state" not in breach.payload
    assert reminder.payload["rung"] == "reminder"
    store.append(Event(late, "handoff_requested", wd(16, 2), PCO, {"to_provider": "endo-1"}), PATHWAY)
    ms = store.read_model(late, PATHWAY).milestones[REFERRAL]
    assert not ms.overdue and ms.met_at == wd(16, 2) and ms.escalation_level == 0


def test_the_stored_model_equals_a_fresh_replay_with_milestone_events(store):
    cid = new_case(store)
    run_sweep(store, PATHWAY, UAE, wd(40), only=[cid])
    events = store.events_for(cid)
    assert any(e.payload.get("milestone") for e in events)
    assert store.read_model(cid, PATHWAY) == replay_model(events, PATHWAY)
    assert store.current_state(cid) == replay_model(events, PATHWAY).state


def test_concurrent_sweeps_write_each_milestone_event_once(store):
    cid = new_case(store)
    barrier = threading.Barrier(8)
    errors = []

    def worker():
        barrier.wait()
        try:
            run_sweep(store, PATHWAY, UAE, wd(40), only=[cid])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(milestone_events(store, cid, "mark_overdue")) == 1
    assert [e.payload["rung"] for e in milestone_events(store, cid, "escalate")] == ["reminder", "supervisor", "coordinator"]
    seqs = [e.seq for e in store.events_for(cid)]
    assert seqs == list(range(1, len(seqs) + 1))  # no gaps, no duplicates


def test_the_sharded_sweep_writes_the_same_milestone_log_as_the_single_process_sweep():
    """Two scratch databases with the same cases (a sweep of the shared test database would touch other tests' cases)."""
    from app.sweep import run_sweep_parallel
    from scratchdb import scratch_database, seed_open_cases

    def rows(pool):
        with pool.connection() as conn:
            return conn.execute(
                "SELECT event_id, case_id, seq, type, payload FROM events WHERE actor = %s ORDER BY case_id, seq",
                (SCHEDULER_ACTOR,),
            ).fetchall()

    with scratch_database("a18single") as url1, scratch_database("a18shard") as url2:
        pool1, pool2 = create_pool(url1, max_size=4), create_pool(url2, max_size=4)
        try:
            for pool in (pool1, pool2):
                seed_open_cases(PostgresEventStore(pool), PATHWAY, 40, salt="a18")
            single = run_sweep(PostgresEventStore(pool1), PATHWAY, UAE, wd(40))
            sharded = run_sweep_parallel(url2, PATHWAY, UAE, wd(40), workers=3)
            assert single.errors == sharded.errors == [] and single.rejected == sharded.rejected == 0
            assert single.mark_overdue == sharded.mark_overdue == 80  # each case: the state breach and the referral milestone
            first, second = rows(pool1), rows(pool2)
            assert first == second
            milestone_rows = [r for r in first if r[4].get("milestone")]
            assert len(milestone_rows) == 40 * 4  # per case: one breach and three rungs
            again = run_sweep_parallel(url2, PATHWAY, UAE, wd(40), workers=3)
            assert again.mark_overdue == again.escalate == 0 and rows(pool2) == second
        finally:
            pool1.close()
            pool2.close()
