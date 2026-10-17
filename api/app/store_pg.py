"""Postgres-backed event store. Appends are transactional and re-verify the log."""

from __future__ import annotations

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
    LoadedCase,
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
        # `with pool.connection()` commits on clean exit and rolls back on any exception,
        # so an InvalidTransition (or any other error) leaves no row and no state change.
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Serialise writers on this case; held until commit/rollback.
            cur.execute(
                "SELECT pathway, current_state FROM cases WHERE id = %s FOR UPDATE",
                (event.case_id,),
            )
            case_row = cur.fetchone()
            if case_row is None:
                raise CaseNotFound(str(event.case_id))
            case_pathway, stored_state = case_row
            if case_pathway != pathway.name:
                raise ValueError(
                    f"case {event.case_id} belongs to pathway {case_pathway!r}, "
                    f"not {pathway.name!r}"
                )

            # Idempotent retry: checked under the case lock, before the transition check
            # (a retried event is usually no longer valid from the state it produced).
            cur.execute("SELECT case_id FROM events WHERE event_id = %s", (event.event_id,))
            existing = cur.fetchone()
            if existing is not None:
                if existing[0] == event.case_id:
                    return stored_state
                raise EventIdConflict(str(event.event_id))

            cur.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE case_id = %s ORDER BY seq",
                (event.case_id,),
            )
            rows = cur.fetchall()
            history = [_event_from_row(event.case_id, r) for r in rows]
            try:
                replayed = replay_model(history, pathway)
            except InvalidTransition as exc:
                raise StateCorruption(
                    f"case {event.case_id}: stored log does not replay: {exc}"
                ) from exc
            if replayed.state != stored_state:
                raise StateCorruption(
                    f"case {event.case_id}: cases.current_state={stored_state!r} "
                    f"but replay gives {replayed.state!r}"
                )

            new_state = step(replayed, event, pathway).state  # InvalidTransition -> rollback

            next_seq = (rows[-1][0] if rows else 0) + 1
            _insert_event(cur, event, next_seq)
            cur.execute(
                "UPDATE cases SET current_state = %s, updated_at = now() WHERE id = %s",
                (new_state, event.case_id),
            )
            return new_state

    def events_for(self, case_id: UUID) -> list[Event]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE case_id = %s ORDER BY seq", (case_id,)
            ).fetchall()
        return [_event_from_row(case_id, r) for r in rows]

    def read_model(self, case_id: UUID, pathway: Pathway) -> CaseModel | None:
        events = self.events_for(case_id)
        return replay_model(events, pathway) if events else None

    def iter_open_case_models(self, pathway: Pathway, only=None, chunk: int = 500):
        """Yield (case_id, model | None, error | None) for every non-closed case of the pathway.

        `only` restricts the sweep to those case ids. Cases are replayed in chunks so memory
        stays bounded; a log that does not replay yields (case_id, None, error).
        """
        sql = "SELECT id FROM cases WHERE pathway = %s AND current_state <> %s"
        params: list = [pathway.name, pathway.terminal_state]
        if only is not None:
            sql += " AND id = ANY(%s)"
            params.append(list(only))
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

    def abstention_counts(self) -> tuple[int, int] | None:
        """(draft attempts, abstentions) if the AI cut's `drafts` table exists, else None.

        An abstention is a draft row where the template was used instead of the model and a
        reason was recorded (`abstention_reason IS NOT NULL`). Any schema difference degrades
        to None rather than failing the metrics route."""
        try:
            with self._pool.connection() as conn:
                if conn.execute("SELECT to_regclass('public.drafts')").fetchone()[0] is None:
                    return None
                row = conn.execute("SELECT count(*), count(abstention_reason) FROM drafts").fetchone()
            return int(row[0]), int(row[1])
        except Exception:
            return None

    def load_cases(self, pathway: Pathway, state: str | None = None, chunk: int = 500) -> list[LoadedCase]:
        sql = "SELECT id FROM cases WHERE pathway = %s"
        params: list = [pathway.name]
        if state is not None:
            sql += " AND current_state = %s"
            params.append(state)
        with self._pool.connection() as conn:
            ids = [r[0] for r in conn.execute(sql + " ORDER BY id", params).fetchall()]
        out: list[LoadedCase] = []
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
                events = by_case.get(case_id, [])
                if not events:
                    continue
                try:
                    model, error = replay_model(events, pathway), None
                except InvalidTransition as exc:
                    model, error = None, str(exc)
                out.append(LoadedCase(case_id, pathway.name, events[0].at, events, model, error))
        return sorted(out, key=lambda lc: (lc.created_at, str(lc.case_id)))

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


class PostgresNotificationLog:
    """The `notifications` table (migration 003): an append-only log, never a delivery."""

    _COLS = "id, case_id, channel, language, purpose, body, status, reason, event_id, created_at"

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add(self, n) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO notifications ({self._COLS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (n.id, n.case_id, n.channel, n.language, n.purpose, n.body, n.status, n.reason,
                 n.event_id, n.created_at),
            )

    def for_case(self, case_id: UUID) -> list:
        from app.notify import Notification

        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {self._COLS} FROM notifications WHERE case_id = %s ORDER BY seq",
                (case_id,),
            ).fetchall()
        return [Notification(*r) for r in rows]


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


class PostgresUserDirectory:
    """The `users` table (migration 004): display names, kept out of the event log (A14)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._known: set[str] = set()  # ids written by this process: skips a write per request

    def upsert(self, record) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, role, display_name, provider) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role,"
                " display_name = EXCLUDED.display_name, provider = EXCLUDED.provider,"
                " updated_at = now()",
                (record.user_id, record.role, record.display_name, record.provider),
            )
        self._known.add(record.user_id)

    def get(self, user_id: str):
        from app.users import UserRecord

        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT user_id, role, display_name, provider FROM users WHERE user_id = %s",
                (user_id,),
            ).fetchone()
        return UserRecord(*row) if row else None

    def ensure(self, identity) -> None:
        if identity.user_id in self._known:
            return
        if self.get(identity.user_id) is None:
            from app.users import record_for

            self.upsert(record_for(identity))
        self._known.add(identity.user_id)


class PostgresOpsStore:
    """Heartbeats and system alerts (migration 005). Ages come from the database's now()."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def beat(self, service: str, info: dict) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO heartbeats (service, beat_at, info) VALUES (%s, now(), %s)"
                " ON CONFLICT (service) DO UPDATE SET beat_at = now(), info = EXCLUDED.info",
                (service, Jsonb(info)),
            )

    def heartbeats(self) -> dict:
        from app.ops import Heartbeat

        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT service, beat_at, extract(epoch FROM (now() - beat_at)), info FROM heartbeats"
            ).fetchall()
        return {r[0]: Heartbeat(r[0], r[1], float(r[2]), r[3]) for r in rows}

    def open_alert(self, kind: str, detail: dict) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO system_alerts (kind, detail) VALUES (%s, %s)"
                " ON CONFLICT (kind) WHERE cleared_at IS NULL DO NOTHING RETURNING id",
                (kind, Jsonb(detail)),
            ).fetchone()
        return row is not None

    def clear_alert(self, kind: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE system_alerts SET cleared_at = now() WHERE kind = %s AND cleared_at IS NULL", (kind,)
            )
            return cur.rowcount > 0

    def alerts(self, active_only: bool = False, limit: int = 100) -> list:
        from app.ops import Alert

        sql = "SELECT id, kind, since, cleared_at, detail FROM system_alerts"
        if active_only:
            sql += " WHERE cleared_at IS NULL"
        with self._pool.connection() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [Alert(*r) for r in rows]
