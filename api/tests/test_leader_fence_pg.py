"""F10 against Postgres (`make test-db`): the fence is checked in the write transaction itself, for `append`, `append_many`,
`append_batches`, the in-process sweep and the parallel sweep's worker processes. Each test has a scratch database, so
taking over the `scheduler` lease cannot disturb other tests."""

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.calendar import WorkingCalendar
from app.config import settings
from app.db import create_pool
from app.events import CASE_CREATED, Event
from app.leader import SCHEDULER_LEASE, Fence, PostgresLeaseStore, StaleLeader
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore
from app.sweep import run_sweep, run_sweep_parallel
from scratchdb import scratch_database, seed_open_cases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
LATE = UAE.add_working_days(AT, 40)


@pytest.fixture
def db():
    with scratch_database("f10fence") as url:
        pool = create_pool(url, max_size=8)
        try:
            yield url, pool, PostgresEventStore(pool), PostgresLeaseStore(pool)
        finally:
            pool.close()


def fence_of(lease):
    return Fence(lease.name, lease.holder, lease.token)


def depose(leases, pool):
    """Leader A acquires; its lease then expires in the database and B takes over (token 2). Returns A's old fence."""
    a = leases.acquire(SCHEDULER_LEASE, "sched-a", 30)
    with pool.connection() as conn:
        conn.execute("UPDATE leader_lease SET expires_at = now() - interval '1 second' WHERE name = %s", (SCHEDULER_LEASE,))
    b = leases.acquire(SCHEDULER_LEASE, "sched-b", 30)
    assert b.token == 2
    return fence_of(a)


def event_count(pool):
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) FROM events").fetchone()[0]


def ack(cid):
    return Event(cid, "acknowledge", AT + timedelta(days=1), "lab:l", {})


def test_append_and_append_many_refuse_a_deposed_fence_and_write_nothing(db):
    url, pool, store, leases = db
    ids = seed_open_cases(store, PATHWAY, 2, salt="f10-append")
    stale = depose(leases, pool)
    base = event_count(pool)
    with pytest.raises(StaleLeader):
        store.append(ack(ids[0]), PATHWAY, fence=stale)
    with pytest.raises(StaleLeader):
        store.append_many([ack(ids[1])], PATHWAY, fence=stale)
    assert event_count(pool) == base
    current = fence_of(leases.current(SCHEDULER_LEASE))
    store.append(ack(ids[0]), PATHWAY, fence=current)
    assert event_count(pool) == base + 1


def test_a_forged_token_and_an_expired_lease_are_refused(db):
    url, pool, store, leases = db
    ids = seed_open_cases(store, PATHWAY, 1, salt="f10-forged")
    a = leases.acquire(SCHEDULER_LEASE, "sched-a", 30)
    with pytest.raises(StaleLeader):
        store.append(ack(ids[0]), PATHWAY, fence=Fence(a.name, a.holder, a.token + 1))
    with pytest.raises(StaleLeader):
        store.append(ack(ids[0]), PATHWAY, fence=Fence(a.name, "sched-x", a.token))
    with pytest.raises(StaleLeader):
        store.append(ack(ids[0]), PATHWAY, fence=Fence("no-such-lease", a.holder, a.token))
    with pool.connection() as conn:
        conn.execute("UPDATE leader_lease SET expires_at = now() - interval '1 second' WHERE name = %s", (SCHEDULER_LEASE,))
    with pytest.raises(StaleLeader):  # unexpired-ness is part of the fence, not just holder and token
        store.append(ack(ids[0]), PATHWAY, fence=fence_of(a))


def test_append_batches_refuses_the_whole_batch_for_a_deposed_fence(db):
    url, pool, store, leases = db
    ids = seed_open_cases(store, PATHWAY, 3, salt="f10-batch")
    stale = depose(leases, pool)
    base = event_count(pool)
    with pytest.raises(StaleLeader):
        store.append_batches([[ack(c)] for c in ids], PATHWAY, fence=stale)
    assert event_count(pool) == base
    current = fence_of(leases.current(SCHEDULER_LEASE))
    outcomes = store.append_batches([[ack(c)] for c in ids], PATHWAY, fence=current)
    assert all(isinstance(o, str) for o in outcomes) and event_count(pool) == base + 3


def test_the_in_process_sweep_by_a_deposed_leader_writes_nothing_and_does_not_fall_back_to_per_case(db):
    url, pool, store, leases = db
    seed_open_cases(store, PATHWAY, 10, salt="f10-sweep")
    stale = depose(leases, pool)
    base = event_count(pool)
    calls = []
    real = store.append_many
    store.append_many = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    result = run_sweep(store, PATHWAY, UAE, LATE, fence=stale)
    assert result.fenced and any("stale leader" in e for e in result.errors)
    assert calls == [] and event_count(pool) == base  # the refusal was not retried case by case
    ok = run_sweep(store, PATHWAY, UAE, LATE, fence=fence_of(leases.current(SCHEDULER_LEASE)))
    assert not ok.fenced and ok.mark_overdue > 0 and event_count(pool) > base


def test_parallel_workers_deposed_between_planning_and_writing_write_nothing(db):
    url, pool, store, leases = db
    seed_open_cases(store, PATHWAY, 24, salt="f10-par")
    stale = depose(leases, pool)
    base = event_count(pool)
    result = run_sweep_parallel(url, PATHWAY, UAE, LATE, workers=3, fence=stale)
    assert result.fenced and result.mark_overdue == 0 and result.escalate == 0
    assert event_count(pool) == base
    ok = run_sweep_parallel(url, PATHWAY, UAE, LATE, workers=3, fence=fence_of(leases.current(SCHEDULER_LEASE)))
    assert not ok.fenced and ok.errors == [] and ok.mark_overdue > 0
    assert event_count(pool) > base


def test_a_takeover_waits_for_an_in_flight_fenced_write_and_the_next_write_is_refused(db):
    """FOR SHARE: the takeover UPDATE cannot commit while a fenced write holds the lease row, so a write can never slip
    in after the new leader's token exists."""
    url, pool, store, leases = db
    ids = seed_open_cases(store, PATHWAY, 1, salt="f10-race")
    a = leases.acquire(SCHEDULER_LEASE, "sched-a", 30)
    gate, done = threading.Event(), threading.Event()

    def slow_within(cur):
        gate.set()
        time.sleep(0.5)  # hold the transaction (and so the share lock on the lease row) open

    def writer():
        store.append(ack(ids[0]), PATHWAY, fence=fence_of(a), within=slow_within)
        done.set()

    t = threading.Thread(target=writer)
    t.start()
    assert gate.wait(5)
    started = time.monotonic()
    with pool.connection() as conn:  # any change to the lease row blocks on the share lock until the write commits
        conn.execute("UPDATE leader_lease SET expires_at = now() - interval '1 second' WHERE name = %s", (SCHEDULER_LEASE,))
    b = leases.acquire(SCHEDULER_LEASE, "sched-b", 30)
    waited = time.monotonic() - started
    t.join()
    assert done.is_set() and b.token == 2 and waited > 0.2
    with pytest.raises(StaleLeader):
        store.append(Event(ids[0], "accept_ownership", AT + timedelta(days=2), "primary_care_owner:dr_k", {}), PATHWAY, fence=fence_of(a))
