"""F10: the leader lease is a storage-level fence. A write that carries a `Fence` is refused inside the store's own write
step when the lease has moved on, so a leader deposed mid-sweep cannot keep writing until its request ends (in memory here;
Postgres in test_leader_fence_pg.py)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from api_helpers import make_client
from app import leader, ops
from app.calendar import WorkingCalendar
from app.config import settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.leader import Fence, InMemoryLeaseStore, StaleLeader
from app.pathway import load_pathway
from app.routers import cases
from app.sweep import run_sweep

PATHWAY = load_pathway(settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
LATE = UAE.add_working_days(AT, 40)
NAME = leader.SCHEDULER_LEASE


class Tick:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


@pytest.fixture
def tick():
    return Tick()


@pytest.fixture
def lease_store(tick, monkeypatch):
    s = InMemoryLeaseStore(now=tick)
    monkeypatch.setattr(leader, "store", s)
    return s


def seed(store, n=3):
    ids = []
    for _ in range(n):
        cid = uuid4()
        store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
        ids.append(cid)
    return ids


def total_events(store, ids):
    return sum(len(store.events_for(c)) for c in ids)


def fence_of(lease):
    return Fence(lease.name, lease.holder, lease.token)


def test_a_deposed_leaders_append_is_refused_and_nothing_is_written(lease_store, tick):
    store = InMemoryEventStore()
    ids = seed(store)
    a = lease_store.acquire(NAME, "sched-a", 30)
    fence = fence_of(a)
    tick.t += 31
    b = lease_store.acquire(NAME, "sched-b", 30)
    assert b.token == 2
    before = total_events(store, ids)
    with pytest.raises(StaleLeader):
        store.append(Event(ids[0], "acknowledge", AT + timedelta(days=1), "lab:l", {}), PATHWAY, fence=fence)
    assert total_events(store, ids) == before


def test_the_current_leaders_append_goes_through(lease_store):
    store = InMemoryEventStore()
    ids = seed(store, 1)
    a = lease_store.acquire(NAME, "sched-a", 30)
    store.append(Event(ids[0], "acknowledge", AT + timedelta(days=1), "lab:l", {}), PATHWAY, fence=fence_of(a))
    assert len(store.events_for(ids[0])) == 2


def test_an_expired_but_not_taken_over_lease_also_refuses(lease_store, tick):
    store = InMemoryEventStore()
    ids = seed(store, 1)
    a = lease_store.acquire(NAME, "sched-a", 30)
    tick.t += 31
    with pytest.raises(StaleLeader):
        store.append(Event(ids[0], "acknowledge", AT + timedelta(days=1), "lab:l", {}), PATHWAY, fence=fence_of(a))


def test_a_sweep_by_a_deposed_leader_writes_nothing_and_reports_fenced(lease_store, tick):
    store = InMemoryEventStore()
    ids = seed(store)
    a = lease_store.acquire(NAME, "sched-a", 30)
    tick.t += 31
    lease_store.acquire(NAME, "sched-b", 30)
    before = total_events(store, ids)
    result = run_sweep(store, PATHWAY, UAE, LATE, fence=fence_of(a))
    assert result.fenced is True
    assert result.mark_overdue == 0 and result.escalate == 0
    assert any("stale leader" in e for e in result.errors)
    assert total_events(store, ids) == before


def test_the_same_sweep_with_the_current_token_writes(lease_store):
    store = InMemoryEventStore()
    ids = seed(store)
    a = lease_store.acquire(NAME, "sched-a", 30)
    result = run_sweep(store, PATHWAY, UAE, LATE, fence=fence_of(a))
    assert result.fenced is False and result.errors == []
    assert result.mark_overdue > 0 and total_events(store, ids) > len(ids)


def test_a_sweep_deposed_midway_stops_at_the_first_refusal(lease_store, tick):
    """The lease moves after the first case was written: the rest are refused and the sweep does not carry on."""
    store = InMemoryEventStore()
    ids = seed(store, 5)
    a = lease_store.acquire(NAME, "sched-a", 30)
    real, calls = store.append, []

    def append(event, pathway, **kw):
        if calls:  # after the first accepted write, somebody else takes over
            tick.t += 31
            lease_store.acquire(NAME, "sched-b", 30)
        calls.append(event)
        return real(event, pathway, **kw)

    store.append = append
    result = run_sweep(store, PATHWAY, UAE, LATE, fence=fence_of(a))
    assert result.fenced is True
    assert len(calls) == 2  # the one written, then the one refused; no retry, no further case
    assert total_events(store, ids) == len(ids) + 1


def test_without_a_fence_behaviour_is_unchanged(lease_store):
    store = InMemoryEventStore()
    ids = seed(store)
    result = run_sweep(store, PATHWAY, UAE, LATE)
    assert result.fenced is False and result.mark_overdue > 0 and total_events(store, ids) > len(ids)


# ---- the route ----------------------------------------------------------------------------------------------------

client = make_client()
SCHED = {"Authorization": f"Bearer {settings.scheduler_service_token}"}


def headers(holder, token):
    return {**SCHED, "X-Leader-Holder": holder, "X-Leader-Token": str(token)}


def test_route_sweep_deposed_after_the_entry_check_returns_409_and_writes_nothing(lease_store, tick, monkeypatch):
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    store = InMemoryEventStore()
    monkeypatch.setattr(cases, "store", store)
    ids = seed(store)
    from app.clock import clock

    monkeypatch.setattr(clock, "now", lambda: LATE)
    a = lease_store.acquire(NAME, "sched-a", 30)
    # the entry check passes; then the lease moves before the sweep's first write (the "mid-sweep" deposition)
    from app.routers import scheduler

    real = scheduler._sweep

    def deposed_then_sweep(now, fence=None):
        tick.t += 31
        lease_store.acquire(NAME, "sched-b", 30)
        return real(now, fence)

    monkeypatch.setattr(scheduler, "_sweep", deposed_then_sweep)
    before = total_events(store, ids)
    r = client.post("/scheduler/sweep", headers=headers("sched-a", a.token))
    assert r.status_code == 409 and "stale leader" in r.json()["detail"]
    assert total_events(store, ids) == before


def test_route_automation_and_evidence_scans_carry_the_fence_into_their_writes(lease_store, tick, monkeypatch):
    from app.routers import scheduler

    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    store = InMemoryEventStore()
    monkeypatch.setattr(cases, "store", store)
    a = lease_store.acquire(NAME, "sched-a", 30)
    seen = []
    for fn in ("run_automation_scan", "scan_evidence"):
        def fake(st, *args, _fn=fn, **kw):
            seen.append(_fn)
            tick.t += 31
            lease_store.acquire(NAME, "sched-b", 30)  # deposed after the entry check, before the first write
            cid = uuid4()
            st.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
            st.append(Event(cid, "acknowledge", AT + timedelta(days=1), "lab:l", {}), PATHWAY)
            return {}

        monkeypatch.setattr(scheduler.automation, fn, fake)
    r1 = client.post("/scheduler/automation-scan", headers=headers("sched-a", a.token))
    assert r1.status_code == 409 and "stale leader" in r1.json()["detail"]
    b = lease_store.current(NAME)
    tick.t += 31
    c = lease_store.acquire(NAME, "sched-c", 30)  # a fresh leader for the second route
    r2 = client.post("/scheduler/evidence-scan", headers=headers("sched-c", c.token))
    assert r2.status_code == 409 and seen == ["run_automation_scan", "scan_evidence"] and b is not None
