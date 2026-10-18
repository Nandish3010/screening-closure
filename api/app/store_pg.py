"""Postgres-backed event store. Appends are transactional and re-verify the log."""

from __future__ import annotations

import logging
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
    EventContentConflict,
    EventIdConflict,
    LoadedCase,
    StateCorruption,
    check_batch_ids,
    event_digest,
)
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition, replay_model, step

log = logging.getLogger("app.store_pg")

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


def _fresh_events(case_id: UUID, events: Sequence[Event], known: dict) -> list[Event]:
    """The events not yet stored, after the idempotency checks: one already on this case with the same content is
    skipped (a retry), with different content EventContentConflict, on another case EventIdConflict."""
    for e in events:
        stored = known.get(e.event_id)
        if stored is None:
            continue
        if stored[0] != case_id:
            raise EventIdConflict(str(e.event_id))
        if stored[1] != event_digest(e.type, e.at, e.actor, e.payload):
            raise EventContentConflict(
                f"event_id {e.event_id} is already used by a different event on this case")
    return [e for e in events if e.event_id not in known]


def _replay_verified(case_id: UUID, rows: list, stored_state: str, pathway: Pathway) -> CaseModel:
    """Replay the stored log and check it against `cases.current_state` (the engine's safety property)."""
    history = [_event_from_row(case_id, r) for r in rows]
    try:
        model = replay_model(history, pathway)
    except InvalidTransition as exc:
        raise StateCorruption(f"case {case_id}: stored log does not replay: {exc}") from exc
    if model.state != stored_state:
        raise StateCorruption(
            f"case {case_id}: cases.current_state={stored_state!r} but replay gives {model.state!r}"
        )
    return model


class PostgresEventStore:
    def __init__(self, pool: ConnectionPool, calendar=None) -> None:
        self._pool = pool
        # The working-day calendar `cases.next_due_at` is computed with (A20); the configured one when not given.
        self._calendar = calendar
        self._basis_cache: tuple | None = None  # (pathway, calendar, fingerprint)

    def _timing(self, pathway: Pathway):
        """(calendar, fingerprint) that `cases.next_due_at` is written under for this pathway."""
        from app.sweep import timing_fingerprint

        calendar = self._calendar
        if calendar is None:
            from app.calendar import default_calendar

            calendar = default_calendar()
        cached = self._basis_cache
        if cached is None or cached[0] is not pathway or cached[1] is not calendar:
            cached = self._basis_cache = (pathway, calendar, timing_fingerprint(pathway, calendar))
        return calendar, cached[2]

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

    def append(self, event: Event, pathway: Pathway, *, within=None, guard=None) -> str:
        return self.append_many([event], pathway, within=within, guard=guard)

    def append_many(
        self, events: Sequence[Event], pathway: Pathway, *, within=None, guard=None
    ) -> str:
        """Append one or more events of ONE case in a single transaction: one row lock, one log
        read and replay, one commit. Each event is checked against the state the previous one
        produced, exactly as if appended one after another, but the batch is all-or-nothing:
        the first invalid event raises InvalidTransition and nothing is written.

        An event whose event_id is already on this case with the same content (type, actor,
        payload, time) is skipped (idempotent retry); with different content it raises
        EventContentConflict, and one that is on another case raises EventIdConflict, as does an
        id that appears twice in the batch. Returns the case's state afterwards.

        `within(cur)`, if given, runs in the same transaction after the events are inserted
        (D5 records a draft's approval there): if it raises, the events roll back with it.

        `guard(model)`, if given, runs after the log is replayed and before the first event is
        checked, under the same row lock: the caller's authorisation (owner scope) and time
        (not backdated) checks are re-evaluated on the model the events will actually be applied
        to, not on the read it made earlier. Whatever it raises propagates and nothing is written.
        It is skipped when every event was already stored (an idempotent retry).
        """
        if not events:
            raise ValueError("append_many needs at least one event")
        case_id = events[0].case_id
        if any(e.case_id != case_id for e in events):
            raise ValueError("append_many takes events of a single case")
        check_batch_ids(events)  # a duplicate would be dropped as a "retry" of its twin
        # `with pool.connection()` commits on clean exit and rolls back on any exception,
        # so an InvalidTransition (or any other error) leaves no row and no state change.
        #
        # `within(cur)`, if given, runs in this same transaction after the event is accepted and
        # inserted (D5 uses it to record a draft's approval): if it raises, the event is rolled
        # back with it. It is skipped when the event id was already stored (an idempotent retry).
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
                "SELECT event_id, case_id, type, at, actor, payload FROM events WHERE event_id = ANY(%s)",
                ([e.event_id for e in events],),
            )
            known = {r[0]: (r[1], event_digest(r[2], r[3], r[4], r[5])) for r in cur.fetchall()}
            fresh = _fresh_events(case_id, events, known)
            if not fresh:
                return stored_state

            cur.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE case_id = %s ORDER BY seq", (case_id,)
            )
            rows = cur.fetchall()
            model = _replay_verified(case_id, rows, stored_state, pathway)

            if guard is not None:
                guard(model)
            for e in fresh:
                model = step(model, e, pathway)  # InvalidTransition -> rollback, nothing written

            next_seq = (rows[-1][0] if rows else 0) + 1
            for offset, e in enumerate(fresh):
                _insert_event(cur, e, next_seq + offset)
            due, basis = self._next_due(model, pathway)
            cur.execute(
                "UPDATE cases SET current_state = %s, updated_at = now(), next_due_at = %s, next_due_basis = %s"
                " WHERE id = %s",
                (model.state, due, basis, case_id),
            )
            if within is not None:
                within(cur)
            return model.state

    def append_batches(self, batches: Sequence[Sequence[Event]], pathway: Pathway) -> list:
        """`append_many` for several cases at once, in ONE transaction (A20, the sweep's write path).

        `batches` holds one list of events per case (each list is what `append_many` would take: events of a single
        case, applied in order). Each case keeps the per-case semantics of `append_many`: its row is locked
        (`FOR UPDATE`, taken in case-id order, so two overlapping batches cannot deadlock each other), its stored log
        is re-read, replayed and checked against `cases.current_state`, an event already stored with the same content is a
        no-op, one with different content is `EventContentConflict`, and every event must be a valid step from the
        state the one before it produced. What is shared is only the round trips and the commit.

        A case that is refused does not lose the others. The refusal is returned in that case's slot of the result
        (an exception instance: InvalidTransition, EventContentConflict, EventIdConflict, CaseNotFound,
        StateCorruption or ValueError, as `append_many` would have raised) and nothing is written for that case; every
        other case's events are committed together. A successful slot is the case's state afterwards, as `append_many`
        returns it. The row locks of all the cases are held until the single commit.

        Anything that is not a per-case refusal (a lost connection, a deadlock, an event id taken by a concurrent
        writer between the check and the insert) raises, and then NOTHING of the batch is written: the caller falls
        back to `append_many` case by case, which finds each case in a known state. `within` and `guard` are not
        offered here: they exist for the API's single-case writes."""
        if not batches:
            return []
        for events in batches:
            if not events:
                raise ValueError("append_batches needs at least one event per case")
        outcomes: list = [None] * len(batches)
        slots: dict[UUID, int] = {}
        for i, events in enumerate(batches):
            case_id = events[0].case_id
            if case_id in slots:
                raise ValueError(f"append_batches takes each case once; {case_id} appears twice")
            slots[case_id] = i
            if any(e.case_id != case_id for e in events):
                outcomes[i] = ValueError("append_many takes events of a single case")
            else:
                try:
                    check_batch_ids(events)
                except EventIdConflict as exc:
                    outcomes[i] = exc
        live = [cid for cid, i in slots.items() if outcomes[i] is None]
        if not live:
            return outcomes
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, pathway, current_state FROM cases WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                (live,),
            )
            case_rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            for cid in live:
                row = case_rows.get(cid)
                if row is None:
                    outcomes[slots[cid]] = CaseNotFound(str(cid))
                elif row[0] != pathway.name:
                    outcomes[slots[cid]] = ValueError(
                        f"case {cid} belongs to pathway {row[0]!r}, not {pathway.name!r}")
            live = [cid for cid in live if outcomes[slots[cid]] is None]
            if not live:
                return outcomes
            cur.execute(
                "SELECT event_id, case_id, type, at, actor, payload FROM events WHERE event_id = ANY(%s)",
                ([e.event_id for cid in live for e in batches[slots[cid]]],),
            )
            known = {r[0]: (r[1], event_digest(r[2], r[3], r[4], r[5])) for r in cur.fetchall()}
            fresh_by_case: dict[UUID, list[Event]] = {}
            for cid in live:
                try:
                    fresh = _fresh_events(cid, batches[slots[cid]], known)
                except (EventIdConflict, EventContentConflict) as exc:
                    outcomes[slots[cid]] = exc
                    continue
                if fresh:
                    fresh_by_case[cid] = fresh
                else:
                    outcomes[slots[cid]] = case_rows[cid][1]  # every event already stored: an idempotent retry
            if not fresh_by_case:
                return outcomes
            cur.execute(
                f"SELECT case_id, {_EVENT_COLS} FROM events WHERE case_id = ANY(%s) ORDER BY case_id, seq",
                (list(fresh_by_case),),
            )
            logs: dict[UUID, list] = {}
            for row in cur.fetchall():
                logs.setdefault(row[0], []).append(row[1:])
            insert_rows: list[tuple] = []
            updates: list[tuple] = []
            for cid, fresh in fresh_by_case.items():
                rows = logs.get(cid, [])
                try:
                    model = _replay_verified(cid, rows, case_rows[cid][1], pathway)
                    for e in fresh:
                        model = step(model, e, pathway)
                except (InvalidTransition, StateCorruption) as exc:
                    outcomes[slots[cid]] = exc
                    continue
                next_seq = (rows[-1][0] if rows else 0) + 1
                for offset, e in enumerate(fresh):
                    insert_rows.append((e.event_id, cid, next_seq + offset, e.type, e.at, e.actor, Jsonb(e.payload)))
                due, basis = self._next_due(model, pathway)
                updates.append((model.state, due, basis, cid))
                outcomes[slots[cid]] = model.state
            if insert_rows:
                cur.executemany(_INSERT_EVENT, insert_rows)
                cur.executemany(
                    "UPDATE cases SET current_state = %s, updated_at = now(), next_due_at = %s, next_due_basis = %s"
                    " WHERE id = %s",
                    updates,
                )
        return outcomes

    def _next_due(self, model: CaseModel, pathway: Pathway):
        """(next_due_at, basis) for the model as the sweep index stores them (A20). NEVER when nothing is left to
        watch. A failure to compute leaves both NULL ("not computed": the sweep always evaluates such a case), so a
        bug here can cost time but cannot hide a breach."""
        from app.sweep import NEVER, next_due_at

        calendar, basis = self._timing(pathway)
        try:
            due = next_due_at(model, pathway, calendar)
        except Exception:  # noqa: BLE001 - the index is derived; never let it fail an append
            log.exception("next_due_at failed for a case in state %s; leaving it uncomputed", model.state)
            return None, None
        return (NEVER if due is None else due), basis

    def events_for(self, case_id: UUID) -> list[Event]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE case_id = %s ORDER BY seq", (case_id,)
            ).fetchall()
        return [_event_from_row(case_id, r) for r in rows]

    def read_model(self, case_id: UUID, pathway: Pathway) -> CaseModel | None:
        events = self.events_for(case_id)
        return replay_model(events, pathway) if events else None

    @staticmethod
    def _shard_sql(shard) -> tuple[str, list]:
        if shard is None:
            return "", []
        index, count = shard
        if not (isinstance(count, int) and isinstance(index, int) and 0 <= index < count):
            raise ValueError(f"shard must be (index, count) with 0 <= index < count, got {shard!r}")
        return " AND mod(hashtext(id::text)::bigint & 2147483647, %s) = %s", [count, index]

    def _read_chunk(self, part: list[UUID]) -> dict[UUID, list[Event]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT case_id, {_EVENT_COLS} FROM events"
                " WHERE case_id = ANY(%s) ORDER BY case_id, seq",
                (part,),
            ).fetchall()
        by_case: dict[UUID, list[Event]] = {}
        for row in rows:
            by_case.setdefault(row[0], []).append(_event_from_row(row[0], row[1:]))
        return by_case

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
        shard_sql, shard_params = self._shard_sql(shard)
        sql, params = sql + shard_sql, params + shard_params
        with self._pool.connection() as conn:
            ids = [r[0] for r in conn.execute(sql + " ORDER BY id", params).fetchall()]
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            by_case = self._read_chunk(part)
            for case_id in part:
                try:
                    yield case_id, replay_model(by_case.get(case_id, []), pathway), None
                except InvalidTransition as exc:
                    yield case_id, None, str(exc)

    def count_open_cases(self, pathway: Pathway, shard=None) -> int:
        """How many non-closed cases of the pathway (in the shard) there are: what a full scan would read."""
        sql = "SELECT count(*) FROM cases WHERE pathway = %s AND current_state <> %s"
        params: list = [pathway.name, pathway.terminal_state]
        shard_sql, shard_params = self._shard_sql(shard)
        with self._pool.connection() as conn:
            return int(conn.execute(sql + shard_sql, params + shard_params).fetchone()[0])

    def iter_due_case_models(self, pathway: Pathway, calendar, now, chunk: int = 500, shard=None):
        """Like `iter_open_case_models`, but only the cases the scheduler can have something to do for at `now` (A20).

        Selected: `next_due_at <= now`, and every case whose `next_due_at` is NULL or was written under another
        timing fingerprint (a case created since the last sweep, a row from before migration 012, a changed calendar
        or pathway). Those "not computed" cases are replayed like the rest and their due time is written back
        before they are yielded (under the row lock, and only if no event arrived since the read; a case locked by
        a writer is skipped, since that writer sets its own value), so the next sweep skips them. A case whose log does
        not replay is yielded with its error on every sweep and stays NULL, as in the full scan.

        Everything not selected has `next_due_at > now`: `plan_case` would return nothing for it
        (`app.sweep.next_due_at`, property-tested), so the events a sweep emits are those of a full scan."""
        from app.sweep import NEVER, next_due_at, timing_fingerprint

        basis = timing_fingerprint(pathway, calendar)
        sql = (
            "SELECT id, (next_due_at IS NULL OR next_due_basis IS DISTINCT FROM %s) FROM cases"
            " WHERE pathway = %s AND current_state <> %s"
            " AND (next_due_at IS NULL OR next_due_basis IS DISTINCT FROM %s OR next_due_at <= %s)"
        )
        params: list = [basis, pathway.name, pathway.terminal_state, basis, now]
        shard_sql, shard_params = self._shard_sql(shard)
        with self._pool.connection() as conn:
            rows = conn.execute(sql + shard_sql + " ORDER BY id", params + shard_params).fetchall()
        for i in range(0, len(rows), chunk):
            part = [r[0] for r in rows[i : i + chunk]]
            stale = {r[0] for r in rows[i : i + chunk] if r[1]}
            by_case = self._read_chunk(part)
            out: list[tuple] = []
            heal: list[tuple] = []  # (case_id, last seq read, next_due_at)
            for case_id in part:
                events = by_case.get(case_id, [])
                try:
                    model = replay_model(events, pathway)
                except InvalidTransition as exc:
                    out.append((case_id, None, str(exc)))
                    continue
                out.append((case_id, model, None))
                if case_id in stale and events:
                    try:
                        due = next_due_at(model, pathway, calendar)
                    except Exception:  # noqa: BLE001 - leave it uncomputed: evaluated again next time
                        log.exception("next_due_at failed for case %s; leaving it uncomputed", case_id)
                        continue
                    heal.append((case_id, events[-1].seq, NEVER if due is None else due))
            if heal:
                self._write_next_due(heal, basis)
            yield from out

    def _write_next_due(self, heal: list[tuple], basis: str) -> None:
        """Store `next_due_at` for cases the sweep evaluated without one. Each is locked first and written only if
        its log is still as long as when it was read; a case a writer holds is left to that writer."""
        ids = [h[0] for h in heal]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM cases WHERE id = ANY(%s) FOR UPDATE SKIP LOCKED", (ids,))
            locked = {r[0] for r in cur.fetchall()}
            cur.execute(
                "SELECT case_id, max(seq) FROM events WHERE case_id = ANY(%s) GROUP BY case_id", (list(locked),)
            )
            current = dict(cur.fetchall())
            cur.executemany(
                "UPDATE cases SET next_due_at = %s, next_due_basis = %s WHERE id = %s",
                [(due, basis, cid) for cid, seq, due in heal if cid in locked and current.get(cid) == seq],
            )

    def has_events_after(self, at) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT EXISTS (SELECT 1 FROM events WHERE at > %s)", (at,)).fetchone()
        return bool(row[0])

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

    def add(self, n, cur=None) -> None:
        """Insert the row; idempotent on `id` (the outbox drain uses a deterministic id, so a drain that
        is run again after a crash cannot log the same notification twice). With `cur`, the insert is part
        of the caller's transaction (the `message_sent` event commits with it, or neither does)."""
        sql = (f"INSERT INTO notifications ({self._COLS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
               " ON CONFLICT (id) DO NOTHING")
        params = (n.id, n.case_id, n.channel, n.language, n.purpose, n.body, n.status, n.reason,
                  n.event_id, n.created_at)
        if cur is not None:
            cur.execute(sql, params)
            return
        with self._pool.connection() as conn:
            conn.execute(sql, params)

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


class PostgresProjectionQueue:
    """Retry queue for the FHIR projection (migrations 006 and 010): ids and the last error, no clinical data.

    `next_attempt_at` is set by the database clock from the retry policy; a row that has used all its attempts
    moves to `fhir_projection_dead_letter` in the same transaction."""

    def __init__(self, pool: ConnectionPool, policy=None) -> None:
        from app.fhir_projection import RetryPolicy

        self._pool = pool
        self.policy = policy or RetryPolicy()

    def add(self, event_id: UUID, case_id: UUID, error: str) -> bool:
        p = self.policy
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "INSERT INTO fhir_projection_queue (event_id, case_id, last_error, next_attempt_at)"
                " VALUES (%s, %s, %s, now())"
                " ON CONFLICT (event_id) DO UPDATE SET attempts = fhir_projection_queue.attempts + 1,"
                " last_error = EXCLUDED.last_error, last_attempt_at = now(),"
                # attempts after this failure = attempts + 1; the first failure is retried at once, then 2^k * base
                " next_attempt_at = now() + make_interval(secs => LEAST(%s::float8,"
                "   %s::float8 * power(2, GREATEST(fhir_projection_queue.attempts + 1 - 2, 0))))"
                " RETURNING attempts",
                (event_id, case_id, error, p.cap_seconds, p.base_seconds),
            ).fetchone()
            if row[0] < p.max_attempts:
                return False
            conn.execute(
                "WITH gone AS (DELETE FROM fhir_projection_queue WHERE event_id = %s"
                " RETURNING event_id, case_id, attempts, last_error, queued_at)"
                " INSERT INTO fhir_projection_dead_letter (event_id, case_id, attempts, last_error, queued_at)"
                " SELECT * FROM gone ON CONFLICT (event_id) DO NOTHING",
                (event_id,),
            )
            return True

    def pending(self, limit: int = 100, *, due_only: bool = False) -> list:
        where = " WHERE next_attempt_at <= now()" if due_only else ""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT event_id, case_id, attempts FROM fhir_projection_queue" + where
                + " ORDER BY next_attempt_at, event_id LIMIT %s",
                (limit,),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def remove(self, event_id: UUID) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM fhir_projection_queue WHERE event_id = %s", (event_id,))

    def count(self, case_id: UUID | None = None) -> int:
        sql, params = "SELECT count(*) FROM fhir_projection_queue", ()
        if case_id is not None:
            sql, params = sql + " WHERE case_id = %s", (case_id,)
        with self._pool.connection() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def dead_count(self) -> int:
        with self._pool.connection() as conn:
            return int(conn.execute("SELECT count(*) FROM fhir_projection_dead_letter").fetchone()[0])

    def dead_since(self):
        with self._pool.connection() as conn:
            return conn.execute("SELECT min(dead_at) FROM fhir_projection_dead_letter").fetchone()[0]

    def revive_dead(self) -> int:
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                "WITH gone AS (DELETE FROM fhir_projection_dead_letter RETURNING event_id, case_id, last_error, queued_at)"
                " INSERT INTO fhir_projection_queue (event_id, case_id, attempts, last_error, queued_at, next_attempt_at)"
                " SELECT event_id, case_id, 1, last_error, queued_at, now() FROM gone"
                " ON CONFLICT (event_id) DO NOTHING RETURNING event_id"
            ).fetchall()
        return len(rows)
