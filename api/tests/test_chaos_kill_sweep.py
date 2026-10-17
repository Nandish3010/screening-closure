"""F4 / T-F5, the event half: kill the sweep part-way through a large sweep, run it again, and the
log holds exactly the events that were planned, each once, and no case was closed.

The watchdog half of T-F5 (the alert when the scheduler stops, and its clearing) is test_chaos.py (A11,
needs the compose stack). This test needs only Postgres: it makes a scratch database with 5,000 open cases,
runs the sweep in a child process, and SIGKILLs the child's whole process group once the sweep has
written part of what it owes (single-process, and the sharded form the scheduler route uses). Then it
sweeps again, kills again part-way through the remainder, and finally lets a sweep finish.

What "exactly the planned events" means: the pure planner (`plan_case`) is run over the seeded cases
before any sweep; its events (id, type, actor, payload, time) are the expected set, and the table must
end up holding that set and nothing else, no id twice, every case log gap-free. Run with `make test-chaos`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from app.calendar import default_calendar
from app.config import settings as app_settings
from app.db import create_pool
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, plan_case
from scratchdb import AT, scratch_database, seed_open_cases

pytestmark = [pytest.mark.chaos, pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")]

PATHWAY = load_pathway(app_settings.pathway_path)
CASES = 5000
NOW = AT + timedelta(days=21)
CHILD = Path(__file__).with_name("chaos_sweep_child.py")


def count_scheduler_events(pool) -> int:
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) FROM events WHERE actor = %s", (SCHEDULER_ACTOR,)).fetchone()[0]


def start_child(url: str, workers: int) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).parents[1]), os.environ.get("PYTHONPATH", "")]))}
    return subprocess.Popen([sys.executable, str(CHILD), url, NOW.isoformat(), str(workers)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=30)


def kill_when(pool, proc: subprocess.Popen, target: int, timeout: float = 120.0) -> tuple[int, float]:
    """SIGKILL the child's process group as soon as the table holds at least `target` scheduler events.
    Returns (events at the moment after the kill settled, seconds from start to kill)."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if proc.poll() is not None:
            raise AssertionError(f"the sweep finished before it could be killed (target {target}): "
                                 f"{proc.stdout.read()} {proc.stderr.read()[-500:]}")
        if count_scheduler_events(pool) >= target:
            killed_after = time.monotonic() - started
            kill_group(proc)
            return count_scheduler_events(pool), killed_after
        time.sleep(0.003)
    kill_group(proc)
    raise AssertionError("the sweep wrote nothing before the timeout")


@pytest.fixture
def seeded():
    with scratch_database("chaoskill") as url:
        pool = create_pool(url, max_size=12)
        try:
            store = PostgresEventStore(pool)
            t0 = time.monotonic()
            seed_open_cases(store, PATHWAY, CASES, salt="kill")
            seeded_in = time.monotonic() - t0
            cal = default_calendar()
            planned = {}
            for case_id, model, err in store.iter_open_case_models(PATHWAY):
                assert err is None
                for e in plan_case(case_id, model, PATHWAY, cal, NOW):
                    planned[e.event_id] = (str(e.case_id), e.type, e.actor, e.payload, e.at)
            assert len(planned) >= CASES // 10
            yield url, pool, store, planned, seeded_in
        finally:
            pool.close()


def snapshot(pool, store):
    with pool.connection() as conn:
        rows = conn.execute("SELECT event_id, case_id, seq, type, actor, payload, at FROM events WHERE actor = %s", (SCHEDULER_ACTOR,)).fetchall()
        dup = conn.execute("SELECT count(*) FROM (SELECT event_id FROM events GROUP BY 1 HAVING count(*) > 1) t").fetchone()[0]
        gaps = conn.execute("SELECT count(*) FROM (SELECT case_id FROM events GROUP BY 1 HAVING max(seq) <> count(*)) t").fetchone()[0]
        closed = conn.execute("SELECT count(*) FROM events WHERE type = 'close'").fetchone()[0]
        closed_cases = conn.execute("SELECT count(*) FROM cases WHERE current_state = %s", (PATHWAY.terminal_state,)).fetchone()[0]
    return rows, dup, gaps, closed, closed_cases


@pytest.mark.parametrize("workers", [1, 3], ids=["single-process", "sharded-3"])
def test_t_f5_a_sweep_killed_part_way_loses_nothing_and_duplicates_nothing(seeded, workers):
    url, pool, store, planned, seeded_in = seeded
    assert count_scheduler_events(pool) == 0
    total = len(planned)

    # first sweep: killed about a quarter of the way through
    first = start_child(url, workers)
    after_first_kill, t_first = kill_when(pool, first, max(total // 4, 1))
    assert 0 < after_first_kill < total, f"the kill missed the sweep: {after_first_kill} of {total} written"
    rows, dup, gaps, closed, closed_cases = snapshot(pool, store)
    assert dup == 0 and gaps == 0 and closed == 0 and closed_cases == 0
    assert {r[0] for r in rows} <= set(planned)  # whatever was written is something that was planned

    # second sweep: resumes, killed again part-way through what is left
    second = start_child(url, workers)
    remaining = total - after_first_kill
    after_second_kill, t_second = kill_when(pool, second, after_first_kill + max(remaining // 2, 1))
    assert after_first_kill < after_second_kill < total or after_second_kill == total
    rows, dup, gaps, closed, closed_cases = snapshot(pool, store)
    assert dup == 0 and gaps == 0 and closed == 0

    # third sweep: left to finish
    t0 = time.monotonic()
    third = start_child(url, workers)
    out, err = third.communicate(timeout=300)
    t_finish = time.monotonic() - t0
    assert third.returncode == 0 and out.startswith("done"), (out, err[-800:])

    rows, dup, gaps, closed, closed_cases = snapshot(pool, store)
    assert len(rows) == total, f"{len(rows)} scheduler events in the log, {total} planned"
    assert dup == 0 and gaps == 0
    assert closed == 0 and closed_cases == 0, "the scheduler closed a case"
    got = {r[0]: (str(r[1]), r[3], r[4], r[5], r[6]) for r in rows}
    assert got == planned, "the log holds something other than exactly the planned events"
    # and one more sweep finds nothing to do
    again = start_child(url, workers)
    out, err = again.communicate(timeout=300)
    assert out.startswith("done 0"), (out, err[-800:])
    assert count_scheduler_events(pool) == total
    print(f"\nchaos: {workers}-worker sweep of {CASES} cases, {total} events planned; killed at {after_first_kill} "
          f"({t_first:.2f} s in) and at {after_second_kill} ({t_second:.2f} s in); the third sweep finished in {t_finish:.2f} s "
          f"and the log holds all {total} once. Seeding took {seeded_in:.1f} s.")
