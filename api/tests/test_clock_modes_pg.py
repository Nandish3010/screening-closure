"""A30 (ADR-041): switching clock modes does not corrupt stored state, on a scratch Postgres database.

Run with `make test-db`. A scratch database is created and dropped by the test (scratchdb.py), so the shared test database's
clock row and events are untouched.
"""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from scratchdb import scratch_database

from app import main
from app.calendar import WorkingCalendar
from app.clock import SimClock, WallClock
from app.config import settings
from app.db import create_pool
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.routers import cases
from app.store_pg import PostgresClockStore, PostgresEventStore
from app.sweep import run_sweep

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
WALL_NOW = datetime(2026, 10, 23, 9, 30, tzinfo=UTC)


def new_case(store, at):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, at, "system:intake", {}, uuid4()), PATHWAY.initial_state)
    return cid


def fingerprint(pool):
    """A digest of every event row and the sim clock row: what a mode switch must not change."""
    with pool.connection() as conn:
        ev = conn.execute("SELECT case_id, seq, type, at, actor, payload::text FROM events ORDER BY id").fetchall()
        ck = conn.execute("SELECT clock_id, start_at, now_at FROM sim_clock ORDER BY clock_id").fetchall()
    return hashlib.sha256(repr((ev, ck)).encode()).hexdigest(), len(ev), ck


def test_wall_then_sim_then_wall_leaves_history_and_the_clock_row_intact(monkeypatch):
    with scratch_database("clockmode") as url:
        pool = create_pool(url, max_size=4)
        try:
            store = PostgresEventStore(pool)
            monkeypatch.setattr(cases, "store", store)
            clock_store = PostgresClockStore(pool, "main")

            # 1. a stretch of simulated time: the clock moves, an event is dated on it
            sim = SimClock(AT)
            monkeypatch.setattr(main, "clock", sim)
            main._start_clock(clock_store)
            sim.advance(days=10)
            sim_case = new_case(store, sim.now())
            before_wall = fingerprint(pool)
            assert before_wall[2][0][2] == AT + timedelta(days=10)

            # 2. wall mode: the sim row is not read or written; real-time events are appended after the sim ones
            wall = WallClock(source=lambda: WALL_NOW)
            monkeypatch.setattr(main, "clock", wall)
            main._start_clock(clock_store)
            assert fingerprint(pool) == before_wall  # starting in wall mode changed nothing
            wall_case = new_case(store, wall.now())
            result = run_sweep(store, PATHWAY, WorkingCalendar(), wall.now())
            assert result.errors == [] and result.mark_overdue >= 1  # the sim-era case is long overdue in real time
            sim_rows_during_wall = fingerprint(pool)[2]
            assert sim_rows_during_wall == before_wall[2]  # the sim clock row is exactly as it was
            history = store.events_for(sim_case)
            assert history[0].at == AT + timedelta(days=10) and history[0].type == CASE_CREATED

            # 3. back to sim mode: it resumes, finds events up to the wall-time ones, and moves FORWARD to them
            sim2 = SimClock(AT)
            monkeypatch.setattr(main, "clock", sim2)
            n_events = fingerprint(pool)[1]
            main._start_clock(clock_store)
            assert sim2.now() >= WALL_NOW
            assert fingerprint(pool)[1] == n_events  # no event written or lost by the switch
            assert store.events_for(sim_case)[:1] == history[:1]
            assert store.read_model(wall_case, PATHWAY) is not None  # replay is fine

            # 4. and one more wall start is accepted (the log is not in the future of real time)
            monkeypatch.setattr(main, "clock", WallClock(source=lambda: WALL_NOW + timedelta(hours=1)))
            main._start_clock(clock_store)
        finally:
            pool.close()


def test_wall_mode_refuses_a_database_whose_simulated_events_lie_in_the_future(monkeypatch):
    with scratch_database("clockfuture") as url:
        pool = create_pool(url, max_size=4)
        try:
            store = PostgresEventStore(pool)
            monkeypatch.setattr(cases, "store", store)
            new_case(store, WALL_NOW + timedelta(days=90))
            monkeypatch.setattr(main, "clock", WallClock(source=lambda: WALL_NOW))
            before = fingerprint(pool)
            with pytest.raises(RuntimeError, match="CLOCK_MODE=wall"):
                main._start_clock(PostgresClockStore(pool, "main"))
            assert fingerprint(pool) == before
        finally:
            pool.close()
