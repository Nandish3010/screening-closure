"""Storage for inbound patient replies (D13): the text lives here, never in the event log.

`InMemoryReplyStore` backs unit tests and no-database runs; `PostgresReplyStore` writes `patient_replies`
(migration 013). Both keep the same rows.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ReplyRecord:
    reply_id: UUID
    case_id: UUID
    channel: str
    text: str
    language_hint: str | None
    intent: str
    confidence: float
    model_id: str
    needs_human: bool
    logged: bool
    received_at: datetime
    received_by: str

    def to_json(self) -> dict:
        return {
            "reply_id": str(self.reply_id), "case_id": str(self.case_id), "channel": self.channel, "text": self.text,
            "language_hint": self.language_hint, "intent": self.intent, "confidence": self.confidence,
            "model_id": self.model_id, "needs_human": self.needs_human, "logged": self.logged,
            "received_at": self.received_at.isoformat(), "received_by": self.received_by,
        }


class ReplyStore(Protocol):
    def add(self, record: ReplyRecord) -> ReplyRecord:
        """Insert the reply. If a reply with this id exists already, return the STORED one (a retry), unchanged."""
        ...

    def get(self, reply_id: UUID) -> ReplyRecord | None: ...

    def list_for_case(self, case_id: UUID) -> list[ReplyRecord]:
        """Oldest first."""
        ...


class InMemoryReplyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[UUID, ReplyRecord] = {}

    def add(self, record: ReplyRecord) -> ReplyRecord:
        with self._lock:
            return self._rows.setdefault(record.reply_id, record)

    def get(self, reply_id: UUID) -> ReplyRecord | None:
        with self._lock:
            return self._rows.get(reply_id)

    def list_for_case(self, case_id: UUID) -> list[ReplyRecord]:
        with self._lock:
            return [r for r in self._rows.values() if r.case_id == case_id]  # dicts keep insertion order


_COLUMNS = ("reply_id, case_id, channel, text, language_hint, intent, confidence, model_id, needs_human, logged, "
            "received_at, received_by")


def _record(row) -> ReplyRecord:
    return ReplyRecord(row[0], row[1], row[2], row[3], row[4], row[5], round(float(row[6]), 4), row[7], row[8], row[9],
                       row[10], row[11])


class PostgresReplyStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    def add(self, record: ReplyRecord) -> ReplyRecord:
        with self.pool.connection() as conn:
            conn.execute(
                f"INSERT INTO patient_replies ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (reply_id) DO NOTHING",
                (record.reply_id, record.case_id, record.channel, record.text, record.language_hint, record.intent,
                 record.confidence, record.model_id, record.needs_human, record.logged, record.received_at,
                 record.received_by),
            )
            row = conn.execute(f"SELECT {_COLUMNS} FROM patient_replies WHERE reply_id = %s", (record.reply_id,)).fetchone()
        return _record(row)

    def get(self, reply_id: UUID) -> ReplyRecord | None:
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM patient_replies WHERE reply_id = %s", (reply_id,)).fetchone()
        return _record(row) if row else None

    def list_for_case(self, case_id: UUID) -> list[ReplyRecord]:
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM patient_replies WHERE case_id = %s ORDER BY seq", (case_id,)).fetchall()
        return [_record(r) for r in rows]
