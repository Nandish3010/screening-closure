"""A22 against Postgres (T-A62, T-A63 integration half): the ledger's trigger and a whole run on its own database.

The full run uses a scratch database (`scratchdb.scratch_database`): an agency run reads every case, and the shared
test database holds other tests' cases.
"""

from __future__ import annotations

import os
import threading
from uuid import uuid4

import psycopg
import pytest

import agency_world as world
import api_helpers  # noqa: F401 - imports app.main, which loads the pathway
from app import evidence, notify, ops, users
from app.agency import runner
from app.agency.ledger import NEEDS_HUMAN, QUEUED, LedgerError, PostgresTaskLedger, Task
from app.clock import clock
from app.db import create_pool, run_migrations
from app.routers import cases, drafts
from app.store_pg import PostgresEventStore
from scratchdb import scratch_database

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


def _root(L: PostgresTaskLedger) -> Task:
    rid = uuid4()
    t = Task(rid, rid, None, "supervisor", "supervisor", None, QUEUED, {"trigger": "test"}, clock.now(),
             "system:agency:supervisor")
    assert L.add(t) is True
    assert L.add(t) is False  # the same id again inserts nothing
    return t


def test_the_trigger_keeps_the_ledger_append_only_except_status_moves(pool):
    L = PostgresTaskLedger(pool)
    t = _root(L)
    L.start(t.id, clock.now())
    with pool.connection() as conn:
        for sql in (
            "DELETE FROM agency_tasks WHERE id = %s",
            "UPDATE agency_tasks SET input = '{\"x\": 1}' WHERE id = %s",
            "UPDATE agency_tasks SET department = 'outreach' WHERE id = %s",
            "UPDATE agency_tasks SET status = 'queued' WHERE id = %s",
            "UPDATE agency_tasks SET status = 'done' WHERE id = %s",  # done without provenance: the CHECK
        ):
            with pytest.raises(psycopg.Error), conn.transaction():
                conn.execute(sql, (t.id,))
        with pytest.raises(psycopg.Error), conn.transaction():
            conn.execute("TRUNCATE agency_tasks")
    done = L.finish(t.id, "done", clock.now(), {"summary": "ok"}, prompt_hash="0" * 64, provider="rules",
                    model_id="m", fallback_used=False)
    assert done.status == "done"
    with pool.connection() as conn:
        with pytest.raises(psycopg.Error), conn.transaction():
            conn.execute("UPDATE agency_tasks SET output = '{\"summary\": \"rewritten\"}' WHERE id = %s", (t.id,))
        with pytest.raises(psycopg.Error), conn.transaction():
            conn.execute("UPDATE agency_tasks SET status = 'failed' WHERE id = %s", (t.id,))
    with pytest.raises(LedgerError):
        L.start(t.id, clock.now())
    assert L.get(t.id).output == {"summary": "ok"}


def test_a_waiting_task_is_resolved_once(pool):
    L = PostgresTaskLedger(pool)
    t = _root(L)
    L.start(t.id, clock.now())
    L.finish(t.id, NEEDS_HUMAN, clock.now(), {"summary": "a person"}, prompt_hash=None, provider="rules",
             model_id="m", fallback_used=False)
    L.resolve(t.id, "done", clock.now(), "coordinator:abc", {"reason": "case_moved_on"})
    with pytest.raises(LedgerError):
        L.resolve(t.id, "done", clock.now(), "coordinator:def", {"reason": "again"})
    with pool.connection() as conn:
        with pytest.raises(psycopg.Error), conn.transaction():
            conn.execute("UPDATE agency_tasks SET resolved_by = 'someone' WHERE id = %s", (t.id,))


@pytest.fixture
def scratch(monkeypatch):
    with scratch_database("agency") as url:
        p = create_pool(url, max_size=6)
        try:
            monkeypatch.setattr(cases, "store", PostgresEventStore(p))
            monkeypatch.setattr(drafts, "_pg", None)
            monkeypatch.setattr(drafts, "_pg_outbox", None)
            monkeypatch.setattr(runner, "_pg", None)
            monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
            monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
            monkeypatch.setattr(users, "directory", users.InMemoryUserDirectory())
            monkeypatch.setattr(evidence, "verifier", evidence.AcceptAllVerifier())
            monkeypatch.delenv("LLM_PROVIDER", raising=False)
            clock.reset()
            runner.reset_sweep_counter()
            ids = world.build_cohort()
            yield p, ids
        finally:
            p.close()
            clock.reset()


def q(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def test_a_whole_run_on_postgres_is_idempotent_complete_and_writes_no_agency_event(scratch):
    p, ids = scratch
    assert isinstance(runner.ledger(), PostgresTaskLedger)
    events_before = q(p, "SELECT count(*) FROM events")[0][0]
    results: list[dict] = []
    threads = [threading.Thread(target=lambda: results.append(runner.run_agency(trigger="manual"))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["created"] for r in results) == [False, False, True]
    assert len({r["run_id"] for r in results}) == 1
    rows = q(p, "SELECT count(*) FROM agency_tasks")[0][0]
    again = runner.run_agency(trigger="manual")
    assert again["created"] is False and q(p, "SELECT count(*) FROM agency_tasks")[0][0] == rows
    # provenance on every finished row; nothing left queued or running
    assert q(p, "SELECT count(*) FROM agency_tasks WHERE status IN ('queued', 'running')")[0][0] == 0
    assert q(p, "SELECT count(*) FROM agency_tasks WHERE provider IS NULL OR model_id IS NULL"
                " OR fallback_used IS NULL OR prompt_hash IS NULL")[0][0] == 0
    # the only events the run caused: model drafts, written by the drafting path; none by an agency actor
    new = q(p, "SELECT type, actor FROM events WHERE actor LIKE 'system:agency:%%' OR type = 'message_drafted'")
    assert new and all(t == "message_drafted" and a == "model:template" for t, a in new)
    assert q(p, "SELECT count(*) FROM events")[0][0] == events_before + len(new)
    drafted = q(p, "SELECT created_by, approved_at FROM drafts")
    assert drafted and all(c == "system:agency:outreach" and a is None for c, a in drafted)
    # the inbox is what needs a person; the states did not move
    waiting = q(p, "SELECT count(*) FROM agency_tasks WHERE status = 'needs_human'")[0][0]
    assert waiting >= 10
    assert q(p, "SELECT count(*) FROM cases WHERE current_state = 'closed'")[0][0] == 1
