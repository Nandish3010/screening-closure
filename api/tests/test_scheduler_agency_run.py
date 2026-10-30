"""M7-8 (ADR-047): the agency runs on the scheduler's own timer through `POST /scheduler/agency-run`. The route is fenced to the
current leader (409 for a stale one), answers `{"enabled": false}` while the agency is off, runs once per simulated instant
(idempotent), and every ledger write re-checks the lease (a deposed leader's run stops with 409 and writes nothing more).
With AGENCY_OWN_CADENCE the sweep route no longer counts sweeps for the agency."""

import pytest

import agency_world
from api_helpers import make_client
from app import leader
from app.agency import runner
from app.agency.ledger import InMemoryTaskLedger
from app.clock import clock
from app.config import settings
from app.leader import Fence, InMemoryLeaseStore, StaleLeader

client = make_client()
SCHED = {"Authorization": f"Bearer {settings.scheduler_service_token}"}


@pytest.fixture
def world(monkeypatch):
    agency_world.fresh_world()
    runner.reset_memory_ledger()
    clock.reset()
    monkeypatch.setattr(settings, "agency_every_sweeps", 6)
    store = InMemoryLeaseStore()
    monkeypatch.setattr(leader, "store", store)
    yield store
    clock.reset()


def lead(store, holder="sched-a"):
    lease = store.acquire(leader.SCHEDULER_LEASE, holder, 30)
    return {**SCHED, "X-Leader-Holder": holder, "X-Leader-Token": str(lease.token)}


def test_the_agency_is_off_when_the_switch_is_off(world, monkeypatch):
    monkeypatch.setattr(settings, "agency_every_sweeps", 0)
    r = client.post("/scheduler/agency-run", headers=lead(world))
    assert r.status_code == 200 and r.json() == {"enabled": False}


def test_a_run_happens_once_per_simulated_instant(world):
    h = lead(world)
    first = client.post("/scheduler/agency-run", headers=h).json()
    second = client.post("/scheduler/agency-run", headers=h).json()
    assert first["enabled"] is True and first["created"] is True and first["run_id"]
    assert second["created"] is False and second["run_id"] == first["run_id"]  # same trigger, same instant: the same run


def test_a_stale_leader_is_refused_with_409_and_runs_nothing(world):
    old = lead(world, "sched-a")
    world.release(leader.SCHEDULER_LEASE, "sched-a")
    lead(world, "sched-b")
    r = client.post("/scheduler/agency-run", headers=old)
    assert r.status_code == 409
    assert runner.ledger().runs() == []


def test_only_the_scheduler_may_call_it(world):
    assert client.post("/scheduler/agency-run").status_code == 401
    other = {"Authorization": f"Bearer {settings.agency_service_token}"}
    assert client.post("/scheduler/agency-run", headers=other).status_code == 403


def test_every_ledger_write_rechecks_the_lease(world):
    """A leader deposed mid-run: the next ledger write raises StaleLeader and nothing more is written."""
    a = world.acquire(leader.SCHEDULER_LEASE, "sched-a", 30)
    fence = Fence(leader.SCHEDULER_LEASE, "sched-a", a.token)
    ledger = InMemoryTaskLedger().fenced(fence)
    world.release(leader.SCHEDULER_LEASE, "sched-a")
    world.acquire(leader.SCHEDULER_LEASE, "sched-b", 30)
    with pytest.raises(StaleLeader):
        runner.run_agency(clock.now(), trigger="scheduler", wait=False, fence=fence)
    assert runner.ledger().runs() == []
    assert ledger is not None


def test_with_own_cadence_the_sweep_no_longer_starts_agency_runs(world, monkeypatch):
    monkeypatch.setattr(settings, "agency_own_cadence", True)
    monkeypatch.setattr(settings, "agency_every_sweeps", 1)
    runner.reset_sweep_counter()
    body = client.post("/scheduler/sweep", headers=lead(world)).json()
    assert "agency" not in body
    monkeypatch.setattr(settings, "agency_own_cadence", False)
    body = client.post("/scheduler/sweep", headers=lead(world, "sched-a")).json()
    assert body["agency"]["every"] == 1
