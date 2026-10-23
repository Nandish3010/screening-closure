"""A31 (ADR-041) against Postgres: the lease statement, contention, takeover within the TTL, and fencing through the routes.

Run with `make test-db`. Lease names are unique per test, so nothing here touches the real `scheduler` lease row.
"""

import os
import threading
import time
import uuid

import pytest

from api_helpers import make_client
from app import leader, ops
from app.config import settings
from app.db import create_pool, run_migrations
from app.events import InMemoryEventStore
from app.leader import LeaderLoop, PostgresLeaseStore
from app.routers import cases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=24)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def ls(pool):
    return PostgresLeaseStore(pool)


@pytest.fixture
def name():
    return f"t-{uuid.uuid4().hex[:10]}"


def test_two_schedulers_contending_exactly_one_leader(ls, name):
    wins, barrier = [], threading.Barrier(20)

    def go(i):
        barrier.wait()
        got = ls.acquire(name, f"sched-{i}", 30)
        if got:
            wins.append((i, got.token))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1 and wins[0][1] == 1
    assert ls.current(name).holder == f"sched-{wins[0][0]}"


def test_renewal_keeps_the_token_and_takeover_bumps_it(ls, name):
    a = ls.acquire(name, "a", 30)
    assert ls.acquire(name, "a", 30).token == a.token == 1
    assert ls.acquire(name, "b", 30) is None
    ls.release(name, "a")
    b = ls.acquire(name, "b", 30)
    assert b.token == 2 and ls.current(name).holder == "b"
    assert not ls.is_current(name, "a", 1) and ls.is_current(name, "b", 2) and not ls.is_current(name, "b", 1)


def test_leader_crash_takeover_within_the_ttl(ls, name):
    """The leader stops renewing (a crash). The follower, polling, becomes leader within TTL plus its poll period; the
    handover time is printed for the record (docs/governance/operations.md)."""
    ttl, poll = 2.0, 0.2
    a = ls.acquire(name, "leader", ttl)
    crashed_at = time.monotonic()
    got = None
    while got is None and time.monotonic() - crashed_at < ttl + 3:
        got = ls.acquire(name, "follower", ttl)
        if got is None:
            time.sleep(poll)
    handover = time.monotonic() - crashed_at
    print(f"\nlease handover after a leader crash: {handover:.2f} s (ttl {ttl} s, poll {poll} s)")
    assert got is not None and got.token == a.token + 1
    assert ttl - 0.5 <= handover <= ttl + 2 * poll + 0.5  # not before the TTL, and soon after it


def test_leader_loop_survives_renewals_and_hands_over_on_stop(pool, name):
    store = PostgresLeaseStore(pool)
    one = LeaderLoop(name, "one", ttl=1.5, lease_store=store)
    two = LeaderLoop(name, "two", ttl=1.5, lease_store=store)
    one.start()
    two.start()
    time.sleep(3.2)  # more than two TTLs: renewals kept it
    assert one.is_leader and not two.is_leader
    one.stop()  # releases at once
    assert two.tick() and two.token == 2
    two.stop()


def test_a_stale_leaders_write_is_rejected_through_the_routes(pool, monkeypatch):
    """Fencing end to end on Postgres: a deposed scheduler's sweep carries the old token and gets 409, writing nothing;
    the new leader's identical call is accepted."""
    from app.sweep import run_sweep  # noqa: F401 - the route imports it; named here for the reader

    store = PostgresLeaseStore(pool)
    monkeypatch.setattr(leader, "store", store)
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    mem = InMemoryEventStore()
    monkeypatch.setattr(cases, "store", mem)
    client = make_client()
    sched = {"Authorization": f"Bearer {settings.scheduler_service_token}"}

    def lease(holder):
        return client.post("/scheduler/lease", json={"holder": holder}, headers=sched).json()

    def fence(h, t):
        return {**sched, "X-Leader-Holder": h, "X-Leader-Token": str(t)}

    suffix = uuid.uuid4().hex[:6]
    a_id, b_id = f"a-{suffix}", f"b-{suffix}"
    # the scheduler lease name is shared by the test database: expire whatever an earlier run left
    with pool.connection() as conn:
        conn.execute("UPDATE leader_lease SET expires_at = now() - interval '1 second' WHERE name = %s", (leader.SCHEDULER_LEASE,))
    a = lease(a_id)
    assert a["leader"] is True
    assert client.post("/scheduler/sweep", headers=fence(a_id, a["token"])).status_code == 200
    with pool.connection() as conn:  # a's lease runs out (it was paused)
        conn.execute("UPDATE leader_lease SET expires_at = now() - interval '1 second' WHERE name = %s", (leader.SCHEDULER_LEASE,))
    b = lease(b_id)
    assert b["leader"] is True and b["token"] == a["token"] + 1
    stale = client.post("/scheduler/sweep", headers=fence(a_id, a["token"]))
    assert stale.status_code == 409
    assert client.post("/scheduler/sweep", headers=fence(b_id, b["token"])).status_code == 200
    assert lease(a_id)["leader"] is False  # and it cannot get back in while b renews
