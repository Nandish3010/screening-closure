"""The clock every timer reads: `clock.now()`, never datetime.now(). Two implementations, chosen once at import by
CLOCK_MODE (A30, ADR-041): `sim` (default; the injectable simulation clock below, persisted, moved by /clock/*) and
`wall` (real UTC time; nothing to persist, nothing to move, no clock routes). Everything that needs "now" (the
sweep, the watchdog, notify, replies, /health) imports `clock` from here, so the mode is one switch.

Simulation clock:

The clock only moves forward (`advance`); `reset` is the one explicit rewind, used by the demo.
When bound to a `ClockStore` (Postgres in the compose stack) every move is persisted, so an API
restart keeps the simulated time. Several API replicas (A31) share that row: `now()` re-reads it at most every
`refresh_seconds` (default 1), so a move made through one replica reaches the others within a second, and each replica
sees its own moves at once.
"""

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.config import settings


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


class ClockStore(Protocol):
    def load(self) -> datetime | None:
        """The persisted current time, or None if nothing is stored yet."""
        ...

    def save(self, start: datetime, now: datetime) -> None: ...


class SimClock:
    def __init__(self, start: datetime, store: ClockStore | None = None, refresh_seconds: float = 1.0):
        self._start = start
        self._now = start
        self._store = None
        self._refresh = refresh_seconds
        self._read_at = float("-inf")
        self._lock = threading.Lock()
        if store is not None:
            self.bind(store)

    def bind(self, store: ClockStore) -> datetime:
        """Attach a persistent store: resume from its saved time, or save the start time."""
        with self._lock:
            self._store = store
            saved = store.load()
            if saved is None:
                store.save(self._start, self._start)
                self._now = self._start
            else:
                self._now = saved
            self._read_at = time.monotonic()  # wall-clock-ok: cache age only
            return self._now

    @property
    def start(self) -> datetime:
        return self._start

    def now(self) -> datetime:
        if self._store is not None and time.monotonic() - self._read_at >= self._refresh:  # wall-clock-ok: cache age only
            self._reload()
        return self._now

    def _reload(self) -> None:
        """Pick up a move another replica persisted. Forward only here: a rewind is `reset`, done through this replica;
        a failed read keeps what we have."""
        with self._lock:
            try:
                saved = self._store.load()
            except Exception:  # noqa: BLE001 - the database blinked: keep the cached time
                return
            self._read_at = time.monotonic()  # wall-clock-ok: cache age only
            if saved is not None and saved != self._now:
                self._now = saved

    def _catch_up(self) -> None:
        """Before moving: adopt a later time another replica persisted, so two replicas' advances add up instead of one
        overwriting the other (read then write, not atomic across processes: a demo clock, development only)."""
        if self._store is not None:
            saved = self._store.load()
            if saved is not None and saved > self._now:
                self._now = saved

    def advance(self, days: float = 0, hours: float = 0) -> datetime:
        if days < 0 or hours < 0:
            raise ValueError("SimClock only moves forward; use reset() to rewind")
        with self._lock:
            self._catch_up()
            new = self._now + timedelta(days=days, hours=hours)
            if self._store is not None:
                self._store.save(self._start, new)  # persist first: memory never runs ahead
            self._now = new
            self._read_at = time.monotonic()  # wall-clock-ok: cache age only
            return new

    def reset(self) -> datetime:
        with self._lock:
            if self._store is not None:
                self._store.save(self._start, self._start)
            self._now = self._start
            self._read_at = time.monotonic()  # wall-clock-ok: cache age only
            return self._now

    def advance_to(self, at: datetime) -> datetime:
        """Move forward to `at` if it is later than now; never backwards. Used at start-up after a stretch in wall mode."""
        with self._lock:
            self._catch_up()
            if at <= self._now:
                return self._now
            if self._store is not None:
                self._store.save(self._start, at)
            self._now = at
            self._read_at = time.monotonic()  # wall-clock-ok: cache age only
            return at


class WallClock:
    """Real UTC time. Same surface as SimClock; moving it is an error (the routes that did are not registered).
    `source` is injectable so tests can pin the instant."""

    mode = "wall"

    def __init__(self, source=None):
        self._source = source or (lambda: datetime.now(UTC))  # wall-clock-ok: this IS the wall-clock mode
        self._start = self._source()

    def bind(self, store=None) -> datetime:
        return self.now()  # nothing to resume: the sim_clock row is left exactly as it was

    @property
    def start(self) -> datetime:
        return self._start

    def now(self) -> datetime:
        return self._source().astimezone(UTC)

    def advance(self, days: float = 0, hours: float = 0) -> datetime:
        raise RuntimeError("the wall clock cannot be advanced (CLOCK_MODE=wall)")

    def advance_to(self, at: datetime) -> datetime:
        raise RuntimeError("the wall clock cannot be advanced (CLOCK_MODE=wall)")

    def reset(self) -> datetime:
        raise RuntimeError("the wall clock cannot be reset (CLOCK_MODE=wall)")


SimClock.mode = "sim"


def make_clock(mode: str):
    if mode == "wall":
        return WallClock()
    if mode == "sim":
        return SimClock(_parse(settings.sim_clock_start))
    raise ValueError(f"CLOCK_MODE must be 'sim' or 'wall', not {mode!r}")


clock = make_clock(settings.clock_mode)
