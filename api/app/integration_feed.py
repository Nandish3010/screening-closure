"""The integration feed: a bounded, in-process list of the last few messages that crossed an integration boundary.

It exists for the judge-facing Integrations page (`GET /integrations/feed`). It records METADATA ONLY, one short entry
per HL7 message received, per FHIR resource write or evidence lookup, and per notification handed to the mock: the kind,
the direction, a fixed phrase, a status, the simulation time and, where there is one, a short opaque hash. Never a name,
number, address, message text, identifier or any HL7 content (the same rule as the HL7 log line, `docs/governance/hl7-adapter.md`).

Properties that matter:

* Bounded: a ring of `MAX_ENTRIES`; the oldest entry is dropped. Memory cannot grow with traffic.
* Best effort: `record` never raises and never blocks the caller; a failure here cannot affect a case or an ACK.
* Per process: with two API replicas each holds its own ring. Restarting the process empties it.
* Simulation time: entries carry `clock.now()`, never the wall clock.
* Synthetic: everything that flows through this build is synthetic. The route says so in every response.
"""
from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import asdict, dataclass

from app.clock import clock

MAX_ENTRIES = 200
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
KINDS = ("hl7", "fhir", "notification")
DIRECTIONS = ("in", "out")


@dataclass(frozen=True)
class FeedEntry:
    seq: int
    at: str  # simulation time, ISO 8601
    kind: str  # hl7 | fhir | notification
    direction: str  # in | out
    summary: str  # a fixed phrase built from codes, never from content
    status: str  # an ACK code, ok, queued, found, not_found, sent, blocked_consent, ...
    ref: str | None = None  # short opaque hash, never an identifier

    def to_json(self) -> dict:
        return asdict(self)


_lock = threading.Lock()
_ring: deque[FeedEntry] = deque(maxlen=MAX_ENTRIES)
_seq = itertools.count(1)


def record(kind: str, direction: str, summary: str, status: str, ref: str | None = None) -> None:
    """Add one entry. Never raises."""
    try:
        if kind not in KINDS or direction not in DIRECTIONS:
            return
        with _lock:
            _ring.append(FeedEntry(next(_seq), clock.now().isoformat(), kind, direction, summary[:120], status[:40],
                                   ref[:16] if ref else None))
    except Exception:  # noqa: BLE001 - observability must not break the integration it observes
        return


def latest(limit: int = DEFAULT_LIMIT) -> list[FeedEntry]:
    """The newest `limit` entries, newest first. `limit` is clamped to 1..MAX_LIMIT."""
    n = max(1, min(int(limit), MAX_LIMIT))
    with _lock:
        return list(reversed(list(_ring)[-n:]))


def clear() -> None:
    """For tests."""
    with _lock:
        _ring.clear()
