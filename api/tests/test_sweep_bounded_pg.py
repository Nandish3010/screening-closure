"""M7-7 (g) against Postgres: bounded work per sweep, and the multi-row write path.

* `run_sweep(max_cases=N)` evaluates at most N due cases, says it stopped (`budget_exhausted`, `due_remaining`), and the backlog
  drains over successive calls into exactly the events (ids and payloads) one unbounded sweep writes: nothing is lost,
  nothing is doubled, nothing is skipped for good;
* a time budget stops a sweep the same way (a fake timer: one "second" per call);
* the batched write (one multi-row INSERT and UPDATE per batch) keeps the fence: a stale lease writes nothing, whole batch.
"""

import os
from datetime import timedelta

import pytest

from app.calendar import default_calendar
from app.config import settings as app_settings
from app.db import create_pool
from app.leader import Fence, StaleLeader
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, run_sweep
from scratchdb import AT, scratch_database, seed_open_cases

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")]
PATHWAY = load_pathway(app_settings.pathway_path)
CAL = default_calendar()
N = 60


def sched_events(pool):
    with pool.connection() as conn:
        return conn.execute("SELECT event_id, case_id, seq, type, payload FROM events WHERE actor = %s ORDER BY case_id, seq",
                            (SCHEDULER_ACTOR,)).fetchall()


@pytest.fixture
def twin():
    from contextlib import ExitStack

    with ExitStack() as st:
        pools, stores = [], []
        for tag in ("bnda", "bndb"):
            url = st.enter_context(scratch_database(tag))
            pool = create_pool(url, max_size=4)
            st.callback(pool.close)
            pools.append(pool)
            stores.append(PostgresEventStore(pool))
            seed_open_cases(stores[-1], PATHWAY, N, salt="bounded")
        yield pools, stores


def test_a_backlog_drains_over_bounded_sweeps_into_the_same_log(twin):
    (pa, pb), (a, b) = twin
    now = AT + timedelta(days=30)  # every case is long past its SLA: all N are due
    whole = run_sweep(b, PATHWAY, CAL, now)
    assert whole.cases_evaluated == N and not whole.budget_exhausted
    calls, evaluated = 0, 0
    while True:
        r = run_sweep(a, PATHWAY, CAL, now, max_cases=8, budget_seconds=0)
        calls += 1
        evaluated += r.cases_evaluated
        assert r.cases_evaluated <= 8 and not r.errors
        if r.budget_exhausted:
            assert r.due_remaining > 0
        else:
            break
        assert calls < 40
    assert calls >= N // 8 and evaluated >= N
    assert sched_events(pa) == sched_events(pb) and sched_events(pa)  # same events, same ids, same payloads


def test_a_time_budget_stops_a_sweep_and_the_next_one_resumes(twin):
    (pa, pb), (a, b) = twin
    now = AT + timedelta(days=30)
    run_sweep(b, PATHWAY, CAL, now)
    ticks = iter(range(10**6))
    first = run_sweep(a, PATHWAY, CAL, now, max_cases=0, budget_seconds=3, timer=lambda: next(ticks))
    assert first.budget_exhausted and 0 < first.cases_evaluated < N and first.due_remaining > 0
    for _ in range(60):
        r = run_sweep(a, PATHWAY, CAL, now, max_cases=0, budget_seconds=3, timer=lambda: next(ticks))
        if not r.budget_exhausted:
            break
    assert not r.budget_exhausted and sched_events(pa) == sched_events(pb)


def test_the_multi_row_write_path_keeps_the_fence(twin):
    (pa, pb), (a, b) = twin
    now = AT + timedelta(days=30)
    stale = Fence("scheduler", "nobody", 99)
    r = run_sweep(a, PATHWAY, CAL, now, fence=stale)
    assert r.fenced and sched_events(pa) == []  # the first batch's transaction was refused: nothing written
    ok = run_sweep(a, PATHWAY, CAL, now)  # the same sweep unfenced writes
    assert ok.cases_evaluated > 0 and sched_events(pa)
    # straight at the store: a batch of many cases with a stale fence raises StaleLeader and writes no row at all
    from app.sweep import plan_case

    batches = []
    for case_id, model, _ in b.iter_open_case_models(PATHWAY):
        planned = plan_case(case_id, model, PATHWAY, CAL, now)
        if planned:
            batches.append(planned)
    assert len(batches) == N
    with pytest.raises(StaleLeader):
        b.append_batches(batches, PATHWAY, fence=stale)
    assert sched_events(pb) == []
    assert all(isinstance(o, str) for o in b.append_batches(batches, PATHWAY))  # unfenced: every case committed in one transaction
    assert sched_events(pb) == sched_events(pa)
