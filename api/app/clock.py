"""Injectable simulation clock. All timers must read time from here, never datetime.now().

The clock only moves forward (`advance`); `reset` is the one explicit rewind, used by the demo.
When bound to a `ClockStore` (Postgres in the compose stack) every move is persisted, so an API
restart keeps the simulated time. Single API replica assumed: the current time is cached in
process and the store is only read at `bind`.
"""

import threading
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
    def __init__(self, start: datetime, store: ClockStore | None = None):
        self._start = start
        self._now = start
        self._store = None
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
            return self._now

    def now(self) -> datetime:
        return self._now

    def advance(self, days: float = 0, hours: float = 0) -> datetime:
        if days < 0 or hours < 0:
            raise ValueError("SimClock only moves forward; use reset() to rewind")
        with self._lock:
            new = self._now + timedelta(days=days, hours=hours)
            if self._store is not None:
                self._store.save(self._start, new)  # persist first: memory never runs ahead
            self._now = new
            return new

    def reset(self) -> datetime:
        with self._lock:
            if self._store is not None:
                self._store.save(self._start, self._start)
            self._now = self._start
            return self._now


clock = SimClock(_parse(settings.sim_clock_start))
