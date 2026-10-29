"""Leader election by a Postgres lease (A31, ADR-041).

One row per named lease in `leader_lease` (migration 015). `acquire` is a single statement: insert the row, or take it over
when it has expired, or renew it when the caller already holds it. The fencing token goes up only when the holder changes, so
a leader that was paused (GC, a stopped container, a partition) and wakes up after another instance took over carries an old
token, and `is_current` says no. Time is the database's `now()`; in memory (unit tests, no-database runs) it is an injectable
monotonic clock.

Who checks the token, and where. The scheduler routes (`require_current_leader`) check it at request entry and the in-API
background loops (`LeaderLoop`) before each pass, which only avoids wasted work. The check that makes the lease a fence is in
the storage layer (F10): a route builds a `Fence(name, holder, token)` from the headers it validated and hands it down to the
sweep, and the event store re-checks it INSIDE the write transaction, on the same connection as the INSERT. In Postgres that
is `SELECT ... FROM leader_lease WHERE name = %s FOR SHARE`, so a takeover (an UPDATE of that row) waits for the in-flight
write to commit, and a write that starts after the takeover sees the new holder or token and is refused with `StaleLeader`,
writing nothing. A leader deposed mid-sweep therefore stops at its next write instead of at the end of its request, and the
sweep (including each parallel worker) treats the refusal as fatal for the rest of that sweep. A write with no fence (direct
in-process calls, the automation, tests, a call with no leader headers while REQUIRE_LEADER_FENCE is off) is not checked.
Event ids are still deterministic and every append transactional, so even an unfenced stale writer writes nothing twice.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("app.leader")

SCHEDULER_LEASE = "scheduler"
API_LEASE = "api-background"


@dataclass(frozen=True)
class Lease:
    name: str
    holder: str
    token: int
    expires_in: float  # seconds left, by the store's clock

    def to_json(self) -> dict:
        return {"name": self.name, "holder": self.holder, "token": self.token, "expires_in": round(self.expires_in, 3)}


@dataclass(frozen=True)
class Fence:
    """The lease a write was started under: carried into the write transaction and re-checked there (F10)."""

    name: str
    holder: str
    token: int


class StaleLeader(Exception):
    """A fenced write was refused because its lease is no longer current (another holder, a newer token, or expired).
    Nothing was written. Fatal for the sweep that raised it: the caller stops, it does not retry or fall back."""

    def __init__(self, fence: Fence) -> None:
        super().__init__(f"stale leader: {fence.holder!r} (token {fence.token}) no longer holds lease {fence.name!r}")
        self.fence = fence


# Run inside a write transaction. FOR SHARE keeps a takeover (UPDATE of the row) from committing until this write has;
# clock_timestamp() rather than now() so the expiry is judged at the moment of the check, not at the transaction's start.
_FENCE_SQL = (
    "SELECT holder, token, expires_at >= clock_timestamp() FROM leader_lease WHERE name = %s FOR SHARE"
)


def check_fence_pg(cur, fence: Fence) -> None:
    """Raise StaleLeader unless `fence` is the current, unexpired lease. `cur` is the write transaction's cursor."""
    row = cur.execute(_FENCE_SQL, (fence.name,)).fetchone()
    if row is None or row[0] != fence.holder or int(row[1]) != fence.token or not row[2]:
        raise StaleLeader(fence)


def check_fence_memory(fence: Fence, lease_store: "LeaseStore | None" = None) -> None:
    """The in-memory store's counterpart: asks the in-memory (or any) lease store whether `fence` is current."""
    if not (lease_store or store).is_current(fence.name, fence.holder, fence.token):
        raise StaleLeader(fence)


class FencedStore:
    """Wraps an event store so every append / append_many / append_batches carries `fence` (F10). For callers that take a
    store and call `store.append(...)` themselves (the automation and evidence scans): they need no change, and a refused
    write raises `StaleLeader` from inside the store's transaction. Everything else is delegated untouched."""

    def __init__(self, inner, fence: Fence) -> None:
        self._inner, self.fence = inner, fence
        if hasattr(inner, "append_many"):
            self.append_many = self._append_many
        if hasattr(inner, "append_batches"):
            self.append_batches = self._append_batches

    def append(self, event, pathway, **kw):
        return self._inner.append(event, pathway, fence=self.fence, **kw)

    def _append_many(self, events, pathway, **kw):
        return self._inner.append_many(events, pathway, fence=self.fence, **kw)

    def _append_batches(self, batches, pathway, **kw):
        return self._inner.append_batches(batches, pathway, fence=self.fence, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class LeaseStore(Protocol):
    def acquire(self, name: str, holder: str, ttl: float) -> Lease | None:
        """Take or renew the lease for `holder`. None if somebody else holds an unexpired one."""
        ...

    def release(self, name: str, holder: str) -> bool:
        """Give the lease up early (graceful shutdown): it expires now. True if `holder` held it."""
        ...

    def current(self, name: str) -> Lease | None:
        """Whoever holds an unexpired lease, or None."""
        ...

    def is_current(self, name: str, holder: str, token: int) -> bool:
        """True only if `holder` holds an unexpired lease whose token is `token`."""
        ...


class InMemoryLeaseStore:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:  # wall-clock-ok: lease expiry only
        self._now = now
        self._lock = threading.Lock()
        self._rows: dict[str, list] = {}  # name -> [holder, expires_at, token]

    def acquire(self, name, holder, ttl):
        with self._lock:
            t = self._now()
            row = self._rows.get(name)
            if row is None:
                self._rows[name] = row = [holder, t + ttl, 1]
            elif row[0] == holder:
                row[1] = t + ttl
            elif row[1] < t:
                self._rows[name] = row = [holder, t + ttl, row[2] + 1]
            else:
                return None
            return Lease(name, row[0], row[2], row[1] - t)

    def release(self, name, holder):
        with self._lock:
            row = self._rows.get(name)
            if row is None or row[0] != holder:
                return False
            row[1] = self._now() - 1e-6
            return True

    def current(self, name):
        with self._lock:
            row = self._rows.get(name)
            t = self._now()
            if row is None or row[1] < t:
                return None
            return Lease(name, row[0], row[2], row[1] - t)

    def is_current(self, name, holder, token):
        lease = self.current(name)
        return lease is not None and lease.holder == holder and lease.token == token


class PostgresLeaseStore:
    """The `leader_lease` table. Every instance, whatever process or host, sees the same row and the same `now()`."""

    # Insert, or take over an expired row, or renew our own; the WHERE is the whole rule. A different holder bumps the token.
    _ACQUIRE = (
        "INSERT INTO leader_lease (name, holder, expires_at, token)"
        " VALUES (%(name)s, %(holder)s, now() + make_interval(secs => %(ttl)s), 1)"
        " ON CONFLICT (name) DO UPDATE SET"
        "   holder = EXCLUDED.holder,"
        "   expires_at = EXCLUDED.expires_at,"
        "   token = CASE WHEN leader_lease.holder = EXCLUDED.holder THEN leader_lease.token ELSE leader_lease.token + 1 END"
        " WHERE leader_lease.expires_at < now() OR leader_lease.holder = EXCLUDED.holder"
        " RETURNING name, holder, token, extract(epoch FROM (expires_at - now()))"
    )

    def __init__(self, pool) -> None:
        self._pool = pool

    def acquire(self, name, holder, ttl):
        with self._pool.connection() as conn:
            row = conn.execute(self._ACQUIRE, {"name": name, "holder": holder, "ttl": float(ttl)}).fetchone()
        return Lease(row[0], row[1], int(row[2]), float(row[3])) if row else None

    def release(self, name, holder):
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE leader_lease SET expires_at = now() - interval '1 microsecond' WHERE name = %s AND holder = %s"
                " AND expires_at >= now()", (name, holder))
            return cur.rowcount > 0

    def current(self, name):
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT name, holder, token, extract(epoch FROM (expires_at - now())) FROM leader_lease"
                " WHERE name = %s AND expires_at >= now()", (name,)).fetchone()
        return Lease(row[0], row[1], int(row[2]), float(row[3])) if row else None

    def is_current(self, name, holder, token):
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM leader_lease WHERE name = %s AND holder = %s AND token = %s AND expires_at >= now()",
                (name, holder, token)).fetchone()
        return row is not None


# The process-wide store; app.main swaps in the Postgres one. In memory, one process is the only candidate.
store: LeaseStore = InMemoryLeaseStore()


def new_holder_id(prefix: str = "api") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class LeaderLoop:
    """Keeps one lease fresh from a thread for an in-process background loop. `is_leader` is what the loop checks before
    each pass. A failure to reach the store counts as "not leader" (idle is the safe side) once the last good renewal has
    run out. The thread renews every ttl/3, so two missed renewals still leave the lease intact."""

    def __init__(self, name: str, holder: str | None = None, ttl: float = 30.0, lease_store: LeaseStore | None = None):
        self.name, self.holder, self.ttl = name, holder or new_holder_id(), ttl
        self._store = lease_store
        self._lease: Lease | None = None
        self._valid_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def lease_store(self) -> LeaseStore:
        return self._store or store

    def tick(self) -> bool:
        try:
            lease = self.lease_store.acquire(self.name, self.holder, self.ttl)
        except Exception:  # noqa: BLE001 - database trouble: keep what we have until it runs out
            log.exception("lease %s: renewal failed", self.name)
            return self.is_leader
        with self._lock:
            was = self._lease is not None
            self._lease = lease
            self._valid_until = time.monotonic() + (lease.expires_in if lease else 0)  # wall-clock-ok: local expiry guard
        if lease and not was:
            log.info("lease %s: %s is now the leader (token %s)", self.name, self.holder, lease.token)
        elif was and not lease:
            log.info("lease %s: %s lost leadership", self.name, self.holder)
        return lease is not None

    @property
    def is_leader(self) -> bool:
        with self._lock:
            return self._lease is not None and time.monotonic() < self._valid_until  # wall-clock-ok: local expiry guard

    @property
    def token(self) -> int | None:
        with self._lock:
            return self._lease.token if self._lease else None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.tick()

        def run() -> None:
            while not self._stop.wait(self.ttl / 3):
                self.tick()

        self._thread = threading.Thread(target=run, name=f"lease-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.lease_store.release(self.name, self.holder)
        except Exception:  # noqa: BLE001
            log.exception("lease %s: release failed", self.name)


# The API's own background loops (the FHIR projection drain) lead through this; set by app.main.
api_loop: LeaderLoop | None = None


def api_is_leader() -> bool:
    """True when this process may run the API's background work: no loop configured (a single process) means yes."""
    return api_loop is None or api_loop.is_leader
