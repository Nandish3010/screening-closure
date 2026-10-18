"""T-F8 support: the load harness and the two sweep optimisations, on a small seeded database.

Uses the throwaway `closure_load` database on the compose Postgres (:5433) and nothing else.
Skipped, with a reason, if Postgres is not reachable.
"""

from __future__ import annotations

import pytest

import seed_cases as sc

pytestmark = pytest.mark.skipif(not sc.pg_reachable(), reason="Postgres on localhost:5433 not reachable")


@pytest.fixture(scope="module")
def loaded():
    stats = sc.seed(400, scenario="backlog", log=lambda *_: None)  # backlog: plenty to emit
    from app.db import create_pool

    pool = create_pool(sc.load_url(), max_size=4)
    yield stats, pool
    pool.close()
    sc.drop_database()


def test_seeding_guard_refuses_other_databases():
    with pytest.raises(SystemExit):
        sc.assert_throwaway("closure")
    with pytest.raises(SystemExit):
        sc.assert_throwaway("closure_test")
    sc.assert_throwaway("closure_load")


def test_seeded_cases_replay_and_spread_over_states(loaded):
    stats, pool = loaded
    assert stats.cases == 400 and len(stats.by_state) >= 9
    pathway, _ = sc.load_engine()
    from app.store_pg import PostgresEventStore

    seen = list(PostgresEventStore(pool).iter_open_case_models(pathway))
    assert len(seen) == 400 and all(err is None for _, _, err in seen)


def test_shards_are_disjoint_and_exhaustive(loaded):
    _, pool = loaded
    pathway, _ = sc.load_engine()
    from app.store_pg import PostgresEventStore

    store = PostgresEventStore(pool)
    everything = {cid for cid, _, _ in store.iter_open_case_models(pathway)}
    parts = [{cid for cid, _, _ in store.iter_open_case_models(pathway, shard=(k, 5))} for k in range(5)]
    assert set().union(*parts) == everything
    assert sum(len(p) for p in parts) == len(everything)
    assert all(parts)  # no empty slice at this size
    with pytest.raises(ValueError):
        list(store.iter_open_case_models(pathway, shard=(5, 5)))


def test_append_many_is_atomic_idempotent_and_ordered(loaded):
    _, pool = loaded
    pathway, calendar = sc.load_engine()
    from app.events import Event
    from app.state_machine import InvalidTransition
    from app.store_pg import PostgresEventStore
    from app.sweep import plan_case

    store = PostgresEventStore(pool)
    for case_id, model, _ in store.iter_open_case_models(pathway):
        planned = plan_case(case_id, model, pathway, calendar, sc.SWEEP_NOW)
        if len(planned) >= 2:
            break
    else:
        pytest.fail("seed produced no case with two pending events")
    before = len(store.events_for(case_id))

    # an invalid event at the end rolls the whole batch back
    bad = Event(case_id, "close", sc.SWEEP_NOW, "system:scheduler", {})
    with pytest.raises(InvalidTransition):
        store.append_many([*planned, bad], pathway)
    assert len(store.events_for(case_id)) == before

    state = store.append_many(planned, pathway)
    log = store.events_for(case_id)
    assert [e.seq for e in log] == list(range(1, len(log) + 1))
    assert [e.type for e in log[before:]] == [e.type for e in planned]
    assert store.current_state(case_id) == state

    # retrying the same batch is a no-op
    assert store.append_many(planned, pathway) == state
    assert len(store.events_for(case_id)) == before + len(planned)


def test_parallel_sweep_equals_single_process_sweep(loaded):
    _, pool = loaded
    from app.sweep import make_sweep_executor, run_sweep, run_sweep_parallel
    from app.store_pg import PostgresEventStore

    pathway, calendar = sc.load_engine()
    store = PostgresEventStore(pool)

    def snapshot():
        with pool.connection() as conn:
            return conn.execute("SELECT event_id, case_id, seq, type FROM events ORDER BY event_id").fetchall()

    # Sweep the backlog sharded; a single-process sweep afterwards must find nothing left to do
    # and the rows must be exactly the planner's expectation, each exactly once.
    before = len(snapshot())
    executor = make_sweep_executor(3)
    try:
        result = run_sweep_parallel(sc.load_url(), pathway, calendar, sc.SWEEP_NOW, workers=3, executor=executor)
    finally:
        executor.shutdown()
    assert not result.errors and result.rejected == 0
    emitted = result.mark_overdue + result.escalate + result.handoff_bounced
    rows = snapshot()
    assert len(rows) == before + emitted
    again = run_sweep(store, pathway, calendar, sc.SWEEP_NOW)
    assert again.mark_overdue + again.escalate + again.handoff_bounced == 0
    assert len(snapshot()) == len(rows)
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids))


def test_seeded_due_times_are_what_the_store_would_have_written(loaded):
    """A20: the seed writes `cases.next_due_at` like the store does after the last append (derived from the log),
    so the timed sweep starts from a warm index, and an indexed sweep and a full scan read the same cases' work."""
    _, pool = loaded
    pathway, calendar = sc.load_engine()
    from app.store_pg import PostgresEventStore
    from app.sweep import NEVER, next_due_at, timing_fingerprint

    basis = timing_fingerprint(pathway, calendar)
    with pool.connection() as conn:
        stored = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, next_due_at, next_due_basis FROM cases").fetchall()}
    seen = 0
    for case_id, model, _ in PostgresEventStore(pool).iter_open_case_models(pathway):
        want = next_due_at(model, pathway, calendar)
        assert stored[case_id] == (NEVER if want is None else want, basis)
        seen += 1
    assert seen == 400
