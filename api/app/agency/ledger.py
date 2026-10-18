"""The agency task ledger (A22, ADR-033): one row per unit of agency work, append-only except status moves.

`Task` is one row. `TaskLedger` is the store protocol with two implementations, `InMemoryTaskLedger` (unit
tests, no-database runs) and `PostgresTaskLedger` (migration 014, whose trigger enforces the same rules as
`check_update` below). A task is created once (its id is deterministic, so inserting it again is a no-op that
reports `False`), then moves along

    queued -> running | failed        running -> done | failed | needs_human        needs_human -> done | failed

and a value, once set (started_at, finished_at, output, provenance, resolution), is never overwritten. A finished
task carries its provenance: which planner or tool produced it (`provider`, `model_id`, `fallback_used`, and the
`prompt_hash` of what it was asked). Times are simulation time.

Nothing here touches a case: the ledger records labour, the event log stays the only source of truth.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID

QUEUED, RUNNING, DONE, FAILED, NEEDS_HUMAN = "queued", "running", "done", "failed", "needs_human"
STATUSES = (QUEUED, RUNNING, DONE, FAILED, NEEDS_HUMAN)
OPEN_STATUSES = frozenset({QUEUED, RUNNING, NEEDS_HUMAN})
TERMINAL_STATUSES = frozenset({DONE, FAILED})
TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RUNNING, FAILED}),
    RUNNING: frozenset({DONE, FAILED, NEEDS_HUMAN}),
    NEEDS_HUMAN: frozenset({DONE, FAILED}),
    DONE: frozenset(),
    FAILED: frozenset(),
}

DEPARTMENTS = ("outreach", "referral_scheduling", "evidence_closure", "patient_support", "quality_ops")
SUPERVISOR = "supervisor"

# Fixed namespace for task ids (never change: a rerun would no longer find its own rows).
TASK_NAMESPACE = uuid.UUID("3a7c2e91-5b4d-4f0a-9e62-c18d7f3b5a40")


def run_id_for(trigger: str, sim_at: datetime) -> UUID:
    """A run is identified by what started it and the simulation time it ran at: the same trigger at the same
    simulated instant is the same run (a repeat inserts nothing)."""
    return uuid.uuid5(TASK_NAMESPACE, f"run|{trigger}|{sim_at.isoformat()}")


def dispatch_id_for(run_id: UUID, department: str) -> UUID:
    return uuid.uuid5(TASK_NAMESPACE, f"{run_id}|dispatch|{department}")


def task_id_for(run_id: UUID, case_id: UUID | None, agent_type: str, key: str = "") -> UUID:
    """Idempotent per (run, case, task type) (and an optional key for case-less tasks)."""
    return uuid.uuid5(TASK_NAMESPACE, f"{run_id}|{case_id or '-'}|{agent_type}|{key}")


class LedgerError(Exception):
    """A write the ledger refuses (bad transition, overwrite, unknown task)."""


@dataclass(frozen=True)
class Task:
    id: UUID
    run_id: UUID
    parent_id: UUID | None
    department: str
    agent_type: str
    case_id: UUID | None
    status: str
    input: dict
    created_at: datetime
    created_by: str
    output: dict | None = None
    prompt_hash: str | None = None
    provider: str | None = None
    model_id: str | None = None
    fallback_used: bool | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution: dict | None = None
    seq: int | None = field(default=None, compare=False)

    def to_json(self) -> dict:
        iso = lambda v: v.isoformat() if v is not None else None  # noqa: E731
        return {
            "task_id": str(self.id),
            "run_id": str(self.run_id),
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "department": self.department,
            "agent_type": self.agent_type,
            "case_id": str(self.case_id) if self.case_id else None,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "provenance": {
                "prompt_hash": self.prompt_hash,
                "provider": self.provider,
                "model_id": self.model_id,
                "fallback_used": self.fallback_used,
            },
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "created_by": self.created_by,
            "resolved_at": iso(self.resolved_at),
            "resolved_by": self.resolved_by,
            "resolution": self.resolution,
        }


_IMMUTABLE = ("id", "run_id", "parent_id", "department", "agent_type", "case_id", "input", "created_at",
              "created_by")
_SET_ONCE = ("started_at", "finished_at", "output", "prompt_hash", "provider", "model_id", "fallback_used",
             "resolved_at", "resolved_by", "resolution")


def check_new(task: Task) -> None:
    if task.status not in STATUSES:
        raise LedgerError(f"unknown status {task.status!r}")
    if task.department not in (SUPERVISOR, *DEPARTMENTS):
        raise LedgerError(f"unknown department {task.department!r}")
    if (task.parent_id is None) != (task.run_id == task.id):
        raise LedgerError("only a run's root task has no parent, and its id is the run id")
    _check_finished(task)


def _check_finished(task: Task) -> None:
    if task.status not in (QUEUED, RUNNING):
        missing = [k for k in ("finished_at", "output", "provider", "model_id", "fallback_used")
                   if getattr(task, k) is None]
        if missing:
            raise LedgerError(f"a {task.status} task must record {missing}")
    if (task.resolved_at is None) != (task.resolution is None):
        raise LedgerError("a resolution has both a time and a body")


def check_update(old: Task, new: Task) -> None:
    """The rules the database trigger enforces (migration 014), for the in-memory ledger."""
    for k in _IMMUTABLE:
        if getattr(old, k) != getattr(new, k):
            raise LedgerError(f"agency task {old.id}: {k} cannot change")
    if new.status != old.status and new.status not in TRANSITIONS[old.status]:
        raise LedgerError(f"agency task {old.id}: {old.status} -> {new.status} is not a permitted transition")
    for k in _SET_ONCE:
        if getattr(old, k) is not None and getattr(new, k) != getattr(old, k):
            raise LedgerError(f"agency task {old.id}: {k} is already recorded")
    _check_finished(new)


class TaskLedger(Protocol):
    def add(self, task: Task) -> bool:
        """Insert a new task; False (and nothing written) if a task with this id exists."""
        ...

    def get(self, task_id: UUID) -> Task | None: ...

    def start(self, task_id: UUID, at: datetime) -> Task: ...

    def finish(self, task_id: UUID, status: str, at: datetime, output: dict, *, prompt_hash: str | None,
               provider: str, model_id: str, fallback_used: bool) -> Task: ...

    def resolve(self, task_id: UUID, status: str, at: datetime, by: str, resolution: dict) -> Task:
        """needs_human -> done | failed, recording who or what resolved it."""
        ...

    def query(self, *, status: str | None = None, department: str | None = None, case_id: UUID | None = None,
              run_id: UUID | None = None, agent_type: str | None = None, limit: int | None = None) -> list[Task]:
        """Matching tasks in creation order (oldest first); `limit` keeps the newest `limit`."""
        ...

    def runs(self, limit: int = 20) -> list[Task]:
        """The root task of the latest runs, newest first."""
        ...


def _finished(task: Task, status: str, at: datetime, output: dict, prompt_hash, provider, model_id,
              fallback_used) -> Task:
    return replace(task, status=status, finished_at=at, output=output, prompt_hash=task.prompt_hash or prompt_hash,
                   provider=provider, model_id=model_id, fallback_used=fallback_used)


class InMemoryTaskLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[UUID, Task] = {}
        self._seq = 0

    def add(self, task: Task) -> bool:
        check_new(task)
        with self._lock:
            if task.id in self._rows:
                return False
            self._seq += 1
            self._rows[task.id] = replace(task, seq=self._seq)
            return True

    def get(self, task_id: UUID) -> Task | None:
        with self._lock:
            return self._rows.get(task_id)

    def _update(self, task_id: UUID, change) -> Task:
        with self._lock:
            old = self._rows.get(task_id)
            if old is None:
                raise LedgerError(f"unknown agency task {task_id}")
            new = change(old)
            check_update(old, new)
            self._rows[task_id] = new
            return new

    def start(self, task_id: UUID, at: datetime) -> Task:
        return self._update(task_id, lambda t: replace(t, status=RUNNING, started_at=at))

    def finish(self, task_id, status, at, output, *, prompt_hash, provider, model_id, fallback_used) -> Task:
        return self._update(task_id, lambda t: _finished(t, status, at, output, prompt_hash, provider, model_id,
                                                         fallback_used))

    def resolve(self, task_id, status, at, by, resolution) -> Task:
        def change(t: Task) -> Task:
            if t.status != NEEDS_HUMAN:
                raise LedgerError(f"agency task {t.id} is {t.status}, not needs_human")
            return replace(t, status=status, resolved_at=at, resolved_by=by, resolution=resolution)

        return self._update(task_id, change)

    def query(self, *, status=None, department=None, case_id=None, run_id=None, agent_type=None, limit=None):
        with self._lock:
            rows = sorted(self._rows.values(), key=lambda t: t.seq)
        out = [t for t in rows
               if (status is None or t.status == status) and (department is None or t.department == department)
               and (case_id is None or t.case_id == case_id) and (run_id is None or t.run_id == run_id)
               and (agent_type is None or t.agent_type == agent_type)]
        return out[-limit:] if limit else out

    def runs(self, limit: int = 20) -> list[Task]:
        with self._lock:
            roots = [t for t in self._rows.values() if t.parent_id is None]
        return sorted(roots, key=lambda t: t.seq, reverse=True)[:limit]


_COLS = ("id, run_id, parent_id, department, agent_type, case_id, status, input, created_at, created_by, output,"
         " prompt_hash, provider, model_id, fallback_used, started_at, finished_at, resolved_at, resolved_by,"
         " resolution, seq")


def _row(r) -> Task:
    return Task(*r[:20], seq=r[20])


def _translate(exc: Exception) -> LedgerError:
    return LedgerError(str(exc).splitlines()[0])


class PostgresTaskLedger:
    """Migration 014. The trigger is the authority on the update rules; the same checks run here first so a
    caller gets the same `LedgerError` from both ledgers."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def add(self, task: Task) -> bool:
        check_new(task)
        with self.pool.connection() as conn:
            cur = conn.execute(
                f"INSERT INTO agency_tasks ({_COLS.rsplit(', seq', 1)[0]}) VALUES ({', '.join(['%s'] * 20)})"
                " ON CONFLICT (id) DO NOTHING",
                (task.id, task.run_id, task.parent_id, task.department, task.agent_type, task.case_id, task.status,
                 json.dumps(task.input), task.created_at, task.created_by,
                 json.dumps(task.output) if task.output is not None else None, task.prompt_hash, task.provider,
                 task.model_id, task.fallback_used, task.started_at, task.finished_at, task.resolved_at,
                 task.resolved_by, json.dumps(task.resolution) if task.resolution is not None else None),
            )
            return cur.rowcount == 1

    def get(self, task_id: UUID) -> Task | None:
        with self.pool.connection() as conn:
            r = conn.execute(f"SELECT {_COLS} FROM agency_tasks WHERE id = %s", (task_id,)).fetchone()
        return _row(r) if r else None

    def _update(self, task_id: UUID, change) -> Task:
        import psycopg

        with self.pool.connection() as conn, conn.transaction():
            r = conn.execute(f"SELECT {_COLS} FROM agency_tasks WHERE id = %s FOR UPDATE", (task_id,)).fetchone()
            if r is None:
                raise LedgerError(f"unknown agency task {task_id}")
            old = _row(r)
            new = change(old)
            check_update(old, new)
            try:
                conn.execute(
                    "UPDATE agency_tasks SET status = %s, output = %s, prompt_hash = %s, provider = %s, model_id = %s,"
                    " fallback_used = %s, started_at = %s, finished_at = %s, resolved_at = %s, resolved_by = %s,"
                    " resolution = %s WHERE id = %s",
                    (new.status, json.dumps(new.output) if new.output is not None else None, new.prompt_hash,
                     new.provider, new.model_id, new.fallback_used, new.started_at, new.finished_at, new.resolved_at,
                     new.resolved_by, json.dumps(new.resolution) if new.resolution is not None else None, task_id),
                )
            except psycopg.Error as exc:  # the trigger refused it
                raise _translate(exc) from None
        return new

    def start(self, task_id, at):
        return self._update(task_id, lambda t: replace(t, status=RUNNING, started_at=at))

    def finish(self, task_id, status, at, output, *, prompt_hash, provider, model_id, fallback_used):
        return self._update(task_id, lambda t: _finished(t, status, at, output, prompt_hash, provider, model_id,
                                                         fallback_used))

    def resolve(self, task_id, status, at, by, resolution):
        def change(t: Task) -> Task:
            if t.status != NEEDS_HUMAN:
                raise LedgerError(f"agency task {t.id} is {t.status}, not needs_human")
            return replace(t, status=status, resolved_at=at, resolved_by=by, resolution=resolution)

        return self._update(task_id, change)

    def query(self, *, status=None, department=None, case_id=None, run_id=None, agent_type=None, limit=None):
        where, params = [], []
        for col, value in (("status", status), ("department", department), ("case_id", case_id),
                           ("run_id", run_id), ("agent_type", agent_type)):
            if value is not None:
                where.append(f"{col} = %s")
                params.append(value)
        sql = f"SELECT {_COLS} FROM agency_tasks" + (f" WHERE {' AND '.join(where)}" if where else "")
        if limit:
            sql = f"SELECT * FROM ({sql} ORDER BY seq DESC LIMIT %s) t ORDER BY seq"
            params.append(limit)
        else:
            sql += " ORDER BY seq"
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row(r) for r in rows]

    def runs(self, limit: int = 20) -> list[Task]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM agency_tasks WHERE parent_id IS NULL ORDER BY seq DESC LIMIT %s", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]
