"""Postgres-backed event store. Appends are transactional and re-verify the log."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from psycopg import errors
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.events import (
    CASE_CREATED,
    CaseAlreadyExists,
    CaseNotFound,
    CaseSummary,
    Event,
    EventIdConflict,
    StateCorruption,
)
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition, replay_model, step

_EVENT_COLS = "seq, event_id, type, at, actor, payload"
_INSERT_EVENT = (
    "INSERT INTO events (event_id, case_id, seq, type, at, actor, payload)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def _event_from_row(case_id: UUID, row) -> Event:
    seq, event_id, type_, at, actor, payload = row
    return Event(case_id, type_, at, actor, payload, event_id, seq)


def _insert_event(cur, event: Event, seq: int) -> None:
    try:
        cur.execute(
            _INSERT_EVENT,
            (event.event_id, event.case_id, seq, event.type, event.at, event.actor,
             Jsonb(event.payload)),
        )
    except errors.UniqueViolation as exc:
        # Only the global event_id constraint is an expected client-level conflict.
        # (case_id, seq) violations would be a locking bug and must surface as-is.
        if exc.diag.constraint_name == "events_event_id_key":
            raise EventIdConflict(str(event.event_id)) from exc
        raise


class PostgresEventStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create_case(
        self, case_id: UUID, pathway_name: str, genesis_event: Event, initial_state: str
    ) -> None:
        if genesis_event.type != CASE_CREATED or genesis_event.case_id != case_id:
            raise ValueError("genesis_event must be a case_created event for this case")
        with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO cases (id, pathway, current_state) VALUES (%s, %s, %s)",
                    (case_id, pathway_name, initial_state),
                )
            except errors.UniqueViolation as exc:
                raise CaseAlreadyExists(str(case_id)) from exc
            _insert_event(cur, genesis_event, 1)

    def append(self, event: Event, pathway: Pathway) -> str:
        return self.append_many([event], pathway)

    def append_many(self, events: Sequence[Event], pathway: Pathway) -> str:
        """Append one or more events of ONE case in a single transaction: one row lock, one log
        read and replay, one commit. Each event is checked against the state the previous one
        produced, exactly as if appended one after another, but the batch is all-or-nothing:
        the first invalid event raises InvalidTransition and nothing is written.

        An event whose event_id is already on this case is skipped (idempotent retry); one that
        is on another case raises EventIdConflict. Returns the case's state afterwards.
        """
        if not events:
            raise ValueError("append_many needs at least one event")
        case_id = events[0].case_id
        if any(e.case_id != case_id for e in events):
            raise ValueError("append_many takes events of a single case")
        # `with pool.connection()` commits on clean exit and rolls back on any exception,
        # so an InvalidTransition (or any other error) leaves no row and no state change.
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Serialise writers on this case; held until commit/rollback.
            cur.execute(
                "SELECT pathway, current_state FROM cases WHERE id = %s FOR UPDATE", (case_id,)
            )
            case_row = cur.fetchone()
            if case_row is None:
                raise CaseNotFound(str(case_id))
            case_pathway, stored_state = case_row
            if case_pathway != pathway.name:
                raise ValueError(
                    f"case {case_id} belongs to pathway {case_pathway!r}, not {pathway.name!r}"
                )

            # Idempotent retry: checked under the case lock, before the transition check
            # (a retried event is usually no longer valid from the state it produced).
            cur.execute(
                "SELECT event_id, case_id FROM events WHERE event_id = ANY(%s)",
                ([e.event_id for e in events],),
            )
            known = dict(cur.fetchall())
            for e in events:
                if known.get(e.event_id, case_id) != case_id:
                    raise EventIdConflict(str(e.event_id))
            fresh = [e for e in events if e.event_id not in known]
            if not fresh:
                return stored_state

            cur.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE case_id = %s ORDER BY seq", (case_id,)
            )
            rows = cur.fetchall()
            history = [_event_from_row(case_id, r) for r in rows]
            try:
                model = replay_model(history, pathway)
            except InvalidTransition as exc:
                raise StateCorruption(
                    f"case {case_id}: stored log does not replay: {exc}"
                ) from exc
            if model.state != stored_state:
                raise StateCorruption(
                    f"case {case_id}: cases.current_state={stored_state!r} "
                    f"but replay gives {model.state!r}"
                )

            for e in fresh:
                model = step(model, e, pathway)  # InvalidTransition -> rollback, nothing written

            next_seq = (rows[-1][0] if rows else 0) + 1
            for offset, e in enumerate(fresh):
                _insert_event(cur, e, next_seq + offset)
            cur.execute(
                "UPDATE cases SET current_state = %s, updated_at = now() WHERE id = %s",
                (model.state, case_id),
            )
            return model.state

    def events_for(self, case_id: UUID) -> list[Event]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE case_id = %s ORDER BY seq", (case_id,)
            ).fetchall()
        return [_event_from_row(case_id, r) for r in rows]

    def read_model(self, case_id: UUID, pathway: Pathway) -> CaseModel | None:
        events = self.events_for(case_id)
        return replay_model(events, pathway) if events else None

    def iter_open_case_models(self, pathway: Pathway, only=None, chunk: int = 500, shard=None):
        """Yield (case_id, model | None, error | None) for every non-closed case of the pathway.

        `only` restricts the sweep to those case ids. `shard=(k, n)` restricts it to the k-th
        of n disjoint, exhaustive slices of the cases (a stable hash of the case id), so n
        workers can sweep in parallel without overlap. Cases are replayed in chunks so memory
        stays bounded; a log that does not replay yields (case_id, None, error).
        """
        sql = "SELECT id FROM cases WHERE pathway = %s AND current_state <> %s"
        params: list = [pathway.name, pathway.terminal_state]
        if only is not None:
            sql += " AND id = ANY(%s)"
            params.append(list(only))
        if shard is not None:
            index, count = shard
            if not (isinstance(count, int) and isinstance(index, int) and 0 <= index < count):
                raise ValueError(f"shard must be (index, count) with 0 <= index < count, got {shard!r}")
            sql += " AND mod(hashtext(id::text)::bigint & 2147483647, %s) = %s"
            params += [count, index]
        with self._pool.connection() as conn:
            ids = [r[0] for r in conn.execute(sql + " ORDER BY id", params).fetchall()]
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            with self._pool.connection() as conn:
                rows = conn.execute(
                    f"SELECT case_id, {_EVENT_COLS} FROM events"
                    " WHERE case_id = ANY(%s) ORDER BY case_id, seq",
                    (part,),
                ).fetchall()
            by_case: dict[UUID, list[Event]] = {}
            for row in rows:
                by_case.setdefault(row[0], []).append(_event_from_row(row[0], row[1:]))
            for case_id in part:
                try:
                    yield case_id, replay_model(by_case.get(case_id, []), pathway), None
                except InvalidTransition as exc:
                    yield case_id, None, str(exc)

    def current_state(self, case_id: UUID) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT current_state FROM cases WHERE id = %s", (case_id,)
            ).fetchone()
        return row[0] if row else None

    def list_cases(self, state: str | None = None) -> list[CaseSummary]:
        sql = "SELECT id, pathway, current_state, created_at, updated_at FROM cases"
        params: tuple = ()
        if state is not None:
            sql += " WHERE current_state = %s"
            params = (state,)
        sql += " ORDER BY created_at, id"
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [CaseSummary(*r) for r in rows]


class PostgresClockStore:
    """Persists a SimClock in the `sim_clock` table (migration 002), one row per clock_id."""

    def __init__(self, pool: ConnectionPool, clock_id: str = "main") -> None:
        self._pool = pool
        self._clock_id = clock_id

    def load(self):
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT now_at FROM sim_clock WHERE clock_id = %s", (self._clock_id,)
            ).fetchone()
        return row[0] if row else None

    def save(self, start, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO sim_clock (clock_id, start_at, now_at) VALUES (%s, %s, %s)"
                " ON CONFLICT (clock_id) DO UPDATE SET now_at = EXCLUDED.now_at",
                (self._clock_id, start, now),
            )

    def delete(self) -> None:
        """Remove the clock row (tests; the API never deletes its clock)."""
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM sim_clock WHERE clock_id = %s", (self._clock_id,))
