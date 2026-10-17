"""Service heartbeats and system alerts (A11, ADR-023).

The scheduler posts a heartbeat on every loop (even when its sweep failed) and the watchdog
posts its own. Both are stored with their wall-clock arrival time, and the *age* of a heartbeat
is computed by the store (the database's `now()`), so nothing in the API reads the wall clock
except the in-memory store's injectable `wall` function, which is marked liveness-only.

An alert is an episode: opened by the watchdog when a service goes quiet, cleared when it
resumes. `GET /alerts` lists them; they are not case events (there is no case to put them on).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Protocol

HEARTBEAT_MAX_AGE_SECONDS = 60  # the scheduler is "down" after this long without a heartbeat
WARMUP_SECONDS = 60  # a missing heartbeat is forgiven until the watchdog itself has run this long

SCHEDULER_DOWN = "scheduler_down"
SCHEDULER_FAILING = "scheduler_failing"
WATCHDOG_DOWN = "watchdog_down"  # derived on read from the watchdog's own heartbeat


@dataclass(frozen=True)
class Heartbeat:
    service: str
    last_beat_at: datetime
    age_seconds: float
    info: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"service": self.service, "last_beat_at": self.last_beat_at.isoformat(),
                "age_seconds": round(self.age_seconds, 3), **self.info}


@dataclass(frozen=True)
class Alert:
    id: int
    kind: str
    since: datetime
    cleared_at: datetime | None
    detail: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "since": self.since.isoformat(),
                "cleared_at": self.cleared_at.isoformat() if self.cleared_at else None,
                "detail": self.detail}


class OpsStore(Protocol):
    def beat(self, service: str, info: dict) -> None: ...

    def heartbeats(self) -> dict[str, Heartbeat]: ...

    def open_alert(self, kind: str, detail: dict) -> bool:
        """Open an alert unless one of this kind is already open. True if it was newly opened."""
        ...

    def clear_alert(self, kind: str) -> bool:
        """Clear the open alert of this kind, if any. True if one was cleared."""
        ...

    def alerts(self, active_only: bool = False, limit: int = 100) -> list[Alert]:
        """Newest first."""
        ...


class InMemoryOpsStore:
    """Process-local store. `wall` returns epoch seconds; tests inject a fake one."""

    def __init__(self, wall: Callable[[], float] = time.time) -> None:  # wall-clock-ok: liveness heartbeat only
        self._wall = wall
        self._lock = threading.Lock()
        self._beats: dict[str, tuple[float, dict]] = {}
        self._alerts: list[dict] = []

    def _dt(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, UTC)

    def beat(self, service: str, info: dict) -> None:
        with self._lock:
            self._beats[service] = (self._wall(), dict(info))

    def heartbeats(self) -> dict[str, Heartbeat]:
        with self._lock:
            now = self._wall()
            return {s: Heartbeat(s, self._dt(ts), now - ts, dict(info)) for s, (ts, info) in self._beats.items()}

    def open_alert(self, kind: str, detail: dict) -> bool:
        with self._lock:
            if any(a["kind"] == kind and a["cleared_at"] is None for a in self._alerts):
                return False
            self._alerts.append({"id": len(self._alerts) + 1, "kind": kind, "since": self._wall(),
                                 "cleared_at": None, "detail": dict(detail)})
            return True

    def clear_alert(self, kind: str) -> bool:
        with self._lock:
            for a in self._alerts:
                if a["kind"] == kind and a["cleared_at"] is None:
                    a["cleared_at"] = self._wall()
                    return True
            return False

    def alerts(self, active_only: bool = False, limit: int = 100) -> list[Alert]:
        with self._lock:
            rows = [a for a in self._alerts if not active_only or a["cleared_at"] is None]
            return [Alert(a["id"], a["kind"], self._dt(a["since"]),
                          self._dt(a["cleared_at"]) if a["cleared_at"] is not None else None, a["detail"])
                    for a in sorted(rows, key=lambda a: -a["id"])[:limit]]


# Bound by app.main at start-up to the Postgres store when DATABASE_URL is set.
store: OpsStore = InMemoryOpsStore()


# ---- the decision, as a pure function -------------------------------------------------------------


def scheduler_status(
    heartbeat: Heartbeat | None,
    watchdog_uptime_seconds: float,
    *,
    max_age: float = HEARTBEAT_MAX_AGE_SECONDS,
    warmup: float = WARMUP_SECONDS,
) -> tuple[str, dict]:
    """(status, detail). Status is one of:

    ok        a heartbeat younger than `max_age` whose sweep is working
    failing   the scheduler is alive but its sweeps keep failing (alive and useless)
    down      the heartbeat is older than `max_age` seconds, or missing after the warm-up
    warming   no heartbeat yet and the watchdog has only just started: no verdict
    """
    if heartbeat is None:
        if watchdog_uptime_seconds < warmup:
            return "warming", {"reason": "no heartbeat yet", "watchdog_uptime_seconds": round(watchdog_uptime_seconds, 1)}
        return "down", {"reason": "missing", "age_seconds": None}
    if heartbeat.age_seconds > max_age:
        return "down", {"reason": "stale", "age_seconds": round(heartbeat.age_seconds, 1),
                        "last_beat_at": heartbeat.last_beat_at.isoformat()}
    info = heartbeat.info
    failures = info.get("consecutive_failures") or 0
    interval = info.get("interval_seconds") or 10
    if info.get("sweep_ok") is False and failures * interval >= max_age:
        return "failing", {"reason": "sweeps failing", "consecutive_failures": failures,
                           "age_seconds": round(heartbeat.age_seconds, 1)}
    return "ok", {"age_seconds": round(heartbeat.age_seconds, 1)}
