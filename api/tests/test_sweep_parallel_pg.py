"""A17: the parallel sweep behind POST /scheduler/sweep (`make test-db`).

Every test builds its own scratch database (api/tests/scratchdb.py), so a full sweep cannot touch
other tests' cases. The route is driven through the FastAPI TestClient with `cases.store` pointed at
the scratch Postgres store and the sim clock set by hand (no lifespan), exactly the objects the
route uses in the container.
"""

import os
import signal
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.db import create_pool
from app.main import app
from app.pathway import load_pathway
from app.routers import cases, scheduler
from app.store_pg import PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, plan_case, run_sweep
from scratchdb import AT, scratch_database, seed_open_cases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
CASES = 240
NOW = AT + timedelta(days=21)  # long past every SLA: each case gets mark_overdue and the due rungs


@pytest.fixture
def sweep_env(monkeypatch):
    """(client, store, pool, url) over a fresh scratch database holding CASES cases."""
    monkeypatch.setenv("SWEEP_WORKERS", "3")
    scheduler.reset_pool()
    with scratch_database("a17") as url:
        pool = create_pool(url, max_size=6)
        store = PostgresEventStore(pool)
        seed_open_cases(store, PATHWAY, CASES)
        monkeypatch.setattr(cases, "store", store)
        monkeypatch.setattr(cases, "pathway", PATHWAY)
        monkeypatch.setattr(clock, "_now", NOW)
        try:
            yield TestClient(app), store, pool, url
        finally:
            scheduler.reset_pool()
            pool.close()


def planned_ids(store):
    """Event ids the pure planner says the sweep owes at NOW, computed before any sweep runs."""
    cal = default_calendar()
    out = set()
    for case_id, model, err in store.iter_open_case_models(PATHWAY):
        assert err is None
        out |= {e.event_id for e in plan_case(case_id, model, PATHWAY, cal, NOW)}
    return out


def scheduler_rows(pool):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT event_id, case_id, seq, type, payload FROM events WHERE actor = %s ORDER BY case_id, seq",
            (SCHEDULER_ACTOR,),
        ).fetchall()


def test_t_a17_route_runs_the_sharded_sweep_and_emits_exactly_the_planned_events(sweep_env):
    client, store, pool, _ = sweep_env
    expected = planned_ids(store)
    assert len(expected) >= CASES  # at least one mark_overdue per case

    out = client.post("/scheduler/sweep", headers=SCHED).json()
    assert out["workers"] == 3
    assert out["cases_evaluated"] == CASES
    assert out["mark_overdue"] == CASES and out["errors"] == [] and out["rejected"] == 0
    assert out["mark_overdue"] + out["escalate"] == len(expected)

    rows = scheduler_rows(pool)
    assert len(rows) == len(expected)  # no duplicate events across shards
    assert {r[0] for r in rows} == expected
    with pool.connection() as conn:
        dup = conn.execute("SELECT count(*) FROM (SELECT event_id FROM events GROUP BY 1 HAVING count(*) > 1) t").fetchone()[0]
        gaps = conn.execute("SELECT count(*) FROM (SELECT case_id FROM events GROUP BY 1 HAVING max(seq) <> count(*)) t").fetchone()[0]
        per_case = conn.execute(
            "SELECT count(*) FROM (SELECT case_id FROM events WHERE type = 'mark_overdue' GROUP BY 1 HAVING count(*) <> 1) t"
        ).fetchone()[0]
        closed = conn.execute("SELECT count(*) FROM events WHERE type = 'close'").fetchone()[0]
    assert dup == 0 and gaps == 0 and per_case == 0 and closed == 0


def test_t_a17_a_second_sweep_emits_nothing(sweep_env):
    client, _, pool, _ = sweep_env
    first = client.post("/scheduler/sweep", headers=SCHED).json()
    before = scheduler_rows(pool)
    assert first["mark_overdue"] == CASES
    again = client.post("/scheduler/sweep", headers=SCHED).json()
    assert again["workers"] == 3 and again["cases_evaluated"] == CASES
    assert again["mark_overdue"] == again["escalate"] == again["handoff_bounced"] == 0
    assert again["errors"] == [] and again["rejected"] == 0
    assert scheduler_rows(pool) == before


def test_t_a17_overlapping_sweeps_do_not_duplicate_events(sweep_env):
    client, store, pool, _ = sweep_env
    expected = planned_ids(store)
    barrier = threading.Barrier(3)
    results = []

    def go():
        barrier.wait()
        results.append(client.post("/scheduler/sweep", headers=SCHED).json())

    threads = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r["mark_overdue"] + r["escalate"] for r in results) == len(expected)  # one sweep did the work
    assert {r[0] for r in scheduler_rows(pool)} == expected and len(scheduler_rows(pool)) == len(expected)


def test_t_a17_sharded_and_single_process_sweeps_write_identical_logs(sweep_env):
    client, _, pool, _ = sweep_env
    client.post("/scheduler/sweep", headers=SCHED)
    sharded = scheduler_rows(pool)
    # a second scratch database, same cases, swept in one process
    with scratch_database("a17single") as url2:
        pool2 = create_pool(url2, max_size=4)
        try:
            store2 = PostgresEventStore(pool2)
            seed_open_cases(store2, PATHWAY, CASES)
            result = run_sweep(store2, PATHWAY, default_calendar(), NOW)
            assert result.mark_overdue == CASES
            assert scheduler_rows(pool2) == sharded
        finally:
            pool2.close()


def test_t_a17_workers_1_keeps_the_single_process_sweep(sweep_env, monkeypatch):
    client, _, pool, _ = sweep_env
    monkeypatch.setenv("SWEEP_WORKERS", "1")
    scheduler.reset_pool()
    out = client.post("/scheduler/sweep", headers=SCHED).json()
    assert out["workers"] == 1 and out["mark_overdue"] == CASES
    assert not scheduler.get_pool().started  # no worker processes were created
    assert len(scheduler_rows(pool)) == out["mark_overdue"] + out["escalate"]


def test_t_a17_a_killed_worker_is_replaced_and_the_sweep_retried(sweep_env):
    client, _, pool, _ = sweep_env
    assert client.post("/scheduler/sweep", headers=SCHED).json()["workers"] == 3  # pool is up and warm
    runner = scheduler.get_pool()
    assert runner.started
    victim = next(iter(runner._executor._processes.values()))
    os.kill(victim.pid, signal.SIGKILL)
    victim.join(timeout=10)
    out = client.post("/scheduler/sweep", headers=SCHED).json()  # idempotent: the retry finds nothing left to do
    assert out["workers"] == 3 and out["cases_evaluated"] == CASES and out["errors"] == []
    assert len({r[0] for r in scheduler_rows(pool)}) == len(scheduler_rows(pool))
