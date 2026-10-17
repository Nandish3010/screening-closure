"""T-A31 / T-A32 against Postgres (`make test-db`): heartbeats and system alerts (migration 005)."""

import os
import time

import pytest

from app.db import create_pool, run_migrations
from app.ops import scheduler_status
from app.store_pg import PostgresOpsStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=4)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def ops_store(pool):
    with pool.connection() as conn:  # these two tables are mutable by design; start clean
        conn.execute("DELETE FROM heartbeats WHERE service LIKE 'pgtest-%'")
        conn.execute("DELETE FROM system_alerts WHERE kind LIKE 'pgtest_%'")
    return PostgresOpsStore(pool)


def test_the_age_of_a_heartbeat_is_computed_by_the_database(ops_store):
    assert "pgtest-svc" not in ops_store.heartbeats()
    ops_store.beat("pgtest-svc", {"sweep_ok": True, "interval_seconds": 10})
    a = ops_store.heartbeats()["pgtest-svc"]
    assert 0 <= a.age_seconds < 5 and a.info == {"sweep_ok": True, "interval_seconds": 10}
    time.sleep(1.2)
    b = ops_store.heartbeats()["pgtest-svc"]
    assert b.age_seconds >= a.age_seconds + 1 and b.last_beat_at == a.last_beat_at
    ops_store.beat("pgtest-svc", {"sweep_ok": False})  # an upsert: one row per service
    c = ops_store.heartbeats()["pgtest-svc"]
    assert c.age_seconds < b.age_seconds and c.info == {"sweep_ok": False}


def test_an_alert_is_an_episode_opened_once_and_cleared_on_resume(ops_store):
    assert ops_store.open_alert("pgtest_down", {"reason": "stale"}) is True
    assert ops_store.open_alert("pgtest_down", {"reason": "stale"}) is False  # one open alert per kind
    (open_,) = [a for a in ops_store.alerts(active_only=True) if a.kind == "pgtest_down"]
    assert open_.cleared_at is None and open_.detail == {"reason": "stale"}
    assert ops_store.clear_alert("pgtest_down") is True
    assert ops_store.clear_alert("pgtest_down") is False
    assert [a for a in ops_store.alerts(active_only=True) if a.kind == "pgtest_down"] == []
    (done,) = [a for a in ops_store.alerts() if a.kind == "pgtest_down"]
    assert done.id == open_.id and done.cleared_at is not None and done.cleared_at >= done.since
    assert ops_store.open_alert("pgtest_down", {}) is True  # a second outage: a new row
    assert len([a for a in ops_store.alerts() if a.kind == "pgtest_down"]) == 2
    assert ops_store.alerts()[0].id > done.id  # newest first


def test_the_verdict_on_a_real_heartbeat(ops_store):
    ops_store.beat("pgtest-svc2", {"sweep_ok": True})
    beat = ops_store.heartbeats()["pgtest-svc2"]
    assert scheduler_status(beat, 500)[0] == "ok"
