"""The send outbox (SEC-2, ADR-030): approved drafts waiting to be handed to the notification mock.

The approval of a draft, its `message_approved` event and its outbox row are written in ONE transaction
(`routers/drafts.py`, the `within` hook of the event store), so there is no moment at which a draft is
approved but nothing will send it. `notify.drain_outbox` then delivers every pending row, idempotently
(deterministic notification id and `message_sent` event id; the state machine accepts one `message_sent`
per approved draft) and marks it, in the same transaction as the `message_sent` event and the
notification row. A crash anywhere leaves the row pending; the next drain finishes it exactly once.

Two implementations with one behaviour: in memory (unit tests, no-database runs) and the Postgres
`outbox` table (migration 010).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID

PENDING, SENT, BLOCKED = "pending", "sent", "blocked"


@dataclass(frozen=True)
class OutboxItem:
    draft_id: UUID
    case_id: UUID
    channel: str
    language: str
    purpose: str
    approved_at: datetime  # simulation time of the approval: the `at` of the message_sent event
    status: str = PENDING
    reason: str | None = None  # why it was blocked (a notification status, or `case_state`)
    notification_id: UUID | None = None
    attempts: int = 0
    last_error: str | None = None


class Outbox(Protocol):
    def enqueue(self, item: OutboxItem, cur=None) -> None:
        """Add the row. Idempotent: a second enqueue of the same draft changes nothing. `cur` is the
        caller's Postgres cursor, so the row commits with the approval it belongs to."""
        ...

    def pending(self, limit: int = 100, only=None) -> list[OutboxItem]:
        """Pending rows, oldest first (`only`: restrict to those draft ids)."""
        ...

    def get(self, draft_id: UUID) -> OutboxItem | None: ...

    def complete(self, draft_id: UUID, status: str, reason: str | None = None,
                 notification_id: UUID | None = None, cur=None) -> bool:
        """Mark a PENDING row sent or blocked. True if this call did it, False if it was already complete."""
        ...

    def record_failure(self, draft_id: UUID, error: str) -> None:
        """A drain attempt failed for a reason that may pass (the database, a timeout): count it, keep the row pending."""
        ...


class InMemoryOutbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[UUID, OutboxItem] = {}

    def enqueue(self, item: OutboxItem, cur=None) -> None:
        with self._lock:
            self._rows.setdefault(item.draft_id, item)

    def pending(self, limit: int = 100, only=None) -> list[OutboxItem]:
        only = None if only is None else set(only)
        with self._lock:
            rows = [r for r in self._rows.values() if r.status == PENDING and (only is None or r.draft_id in only)]
        return rows[:limit]  # dicts keep insertion order: oldest first

    def get(self, draft_id: UUID) -> OutboxItem | None:
        with self._lock:
            return self._rows.get(draft_id)

    def complete(self, draft_id, status, reason=None, notification_id=None, cur=None) -> bool:
        with self._lock:
            row = self._rows.get(draft_id)
            if row is None or row.status != PENDING:
                return False
            self._rows[draft_id] = replace(row, status=status, reason=reason, notification_id=notification_id)
            return True

    def record_failure(self, draft_id, error) -> None:
        with self._lock:
            row = self._rows.get(draft_id)
            if row is not None and row.status == PENDING:
                self._rows[draft_id] = replace(row, attempts=row.attempts + 1, last_error=error[:500])


class PostgresOutbox:
    _COLS = "draft_id, case_id, channel, language, purpose, approved_at, status, reason, notification_id, attempts, last_error"

    def __init__(self, pool) -> None:
        self.pool = pool

    def enqueue(self, item: OutboxItem, cur=None) -> None:
        sql = (
            "INSERT INTO outbox (draft_id, case_id, channel, language, purpose, approved_at)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (draft_id) DO NOTHING"
        )
        params = (item.draft_id, item.case_id, item.channel, item.language, item.purpose, item.approved_at)
        if cur is not None:
            cur.execute(sql, params)
        else:
            with self.pool.connection() as conn:
                conn.execute(sql, params)

    def pending(self, limit: int = 100, only=None) -> list[OutboxItem]:
        sql = f"SELECT {self._COLS} FROM outbox WHERE status = 'pending'"
        params: list = []
        if only is not None:
            sql += " AND draft_id = ANY(%s)"
            params.append(list(only))
        with self.pool.connection() as conn:
            rows = conn.execute(sql + " ORDER BY seq LIMIT %s", (*params, limit)).fetchall()
        return [OutboxItem(*r) for r in rows]

    def get(self, draft_id: UUID) -> OutboxItem | None:
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {self._COLS} FROM outbox WHERE draft_id = %s", (draft_id,)).fetchone()
        return OutboxItem(*row) if row else None

    def complete(self, draft_id, status, reason=None, notification_id=None, cur=None) -> bool:
        sql = (
            "UPDATE outbox SET status = %s, reason = %s, notification_id = %s, completed_at = now()"
            " WHERE draft_id = %s AND status = 'pending'"
        )
        params = (status, reason, notification_id, draft_id)
        if cur is not None:
            return cur.execute(sql, params).rowcount > 0
        with self.pool.connection() as conn:
            return conn.execute(sql, params).rowcount > 0

    def record_failure(self, draft_id, error) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = %s WHERE draft_id = %s AND status = 'pending'",
                (error[:500], draft_id),
            )
