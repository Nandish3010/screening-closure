"""Access audit (privacy centre): who looked at a patient's record, and when.

The event log records what was DONE to a case; it never recorded who merely READ it. A patient who asks "who looked at my
record?" needs that, so staff reads of a case are written here, in a table of their own (`access_audit`, migration 021),
not in the event log: a read changes no state, so it is not an event, and the state machine, the model checker and the
replay are untouched.

What is recorded, and what is not:

* A READ of one case's page, its timeline, its messages (notification log), its FHIR ids, or its stored patient replies,
  by a signed-in STAFF user (a person: `identity.kind == "user"` and not a patient). One row: case, actor (`role:opaque_id`,
  as in the event log), role, the kind of read, and the simulation time.
* NOT recorded: the patient's own reads; service tokens and in-process automation (the scheduler, watchdog, agency, HL7
  adapter, the automation scans: those are described to the patient in the "how we use your data" panel, they are not
  people looking); the worklist (`GET /cases`), which lists many cases and shows each one's state and owner only; and
  reading the access log itself.

Bounded: a person who keeps a case open re-reads it every few seconds, so a row is written at most once per (case, actor,
kind) per `WINDOW` of simulation time. A case therefore gains at most `#actors x #kinds` rows per window, however often it
is read. The patient's view shows the newest `PATIENT_LIMIT`. The table is append-only (no UPDATE or DELETE for the
application role), like the event log.

A failure to write the audit row never stops the read (clinical availability first); it is logged as a warning.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

log = logging.getLogger("app.access_audit")

WINDOW = timedelta(minutes=15)  # at most one row per (case, actor, kind) per window of simulation time
PATIENT_LIMIT = 100  # the newest rows a patient is shown
STAFF_LIMIT = 500
KINDS = ("case", "timeline", "messages", "fhir", "replies")  # what was read (the route), a fixed vocabulary


@dataclass(frozen=True)
class Access:
    id: UUID
    case_id: UUID
    at: datetime  # simulation time
    actor: str  # `role:opaque_id`, as in the event log
    role: str
    kind: str


class AccessLog(Protocol):
    def record(self, case_id: UUID, actor: str, role: str, kind: str, at: datetime) -> bool:
        """Write one row unless the same (case, actor, kind) has one within `WINDOW` before `at`. True if written."""
        ...

    def for_case(self, case_id: UUID, limit: int) -> list[Access]:
        """The newest `limit` rows of the case, newest first."""
        ...


class InMemoryAccessLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[Access] = []

    def record(self, case_id: UUID, actor: str, role: str, kind: str, at: datetime) -> bool:
        with self._lock:
            for r in reversed(self._rows):
                if r.case_id == case_id and r.actor == actor and r.kind == kind and timedelta(0) <= at - r.at < WINDOW:
                    return False
            self._rows.append(Access(uuid4(), case_id, at, actor, role, kind))
            return True

    def for_case(self, case_id: UUID, limit: int) -> list[Access]:
        with self._lock:
            rows = [r for r in reversed(self._rows) if r.case_id == case_id]  # latest write first on a tie
        rows.sort(key=lambda r: r.at, reverse=True)
        return rows[:limit]


class PostgresAccessLog:
    """The `access_audit` table (migration 021)."""

    _COLS = "id, case_id, at, actor, role, kind"

    def __init__(self, pool) -> None:
        self._pool = pool

    def record(self, case_id: UUID, actor: str, role: str, kind: str, at: datetime) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"INSERT INTO access_audit ({self._COLS}) SELECT %s, %s, %s, %s, %s, %s"
                " WHERE NOT EXISTS (SELECT 1 FROM access_audit WHERE case_id = %s AND actor = %s AND kind = %s"
                "                   AND at <= %s AND %s - at < %s)",
                (uuid4(), case_id, at, actor, role, kind, case_id, actor, kind, at, at, WINDOW),
            )
            return cur.rowcount == 1

    def for_case(self, case_id: UUID, limit: int) -> list[Access]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {self._COLS} FROM access_audit WHERE case_id = %s ORDER BY at DESC, seq DESC LIMIT %s",
                (case_id, limit),
            ).fetchall()
        return [Access(*r) for r in rows]


# Bound by app.main at start-up to the Postgres log when DATABASE_URL is set.
log_store: AccessLog = InMemoryAccessLog()


def note_read(identity, case_id: UUID, kind: str, at: datetime) -> None:
    """Record that `identity` read `kind` of the case. A no-op for a patient and for a service. Never raises."""
    if identity is None or identity.kind != "user" or identity.role == "patient":
        return
    try:
        log_store.record(case_id, identity.actor, identity.role, kind, at)
    except Exception:  # noqa: BLE001 - an audit failure must not take a clinical read down
        log.warning("access audit row not written for case %s", case_id, exc_info=True)
