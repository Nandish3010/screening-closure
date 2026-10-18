"""The watchdog's idle-case detection (A11, ADR-023).

The dangerous failure is the absence of events, so this looks for cases where nothing has
happened for `WATCHDOG_IDLE_DAYS` of simulation time. Like the sweep, it runs in the API next to
the event store and reads time from the sim clock only; the watchdog *service* just asks for a
scan (`POST /watchdog/scan`) with its own service token, which may emit `watchdog_flagged` and
nothing else.

An idle episode is the stretch since a case's last real event (`last_activity_at`: any event but
a watchdog flag). The flag's id is a uuid5 of (case, that event's id), never its time (SEC-2): two
quiet stretches that start at the same instant are different episodes. Scanning again, or two scans
at once, flags an episode once. Any later event clears the flag (the read model drops
`watchdog_flag`), and a new quiet stretch is a new episode with a new id. A closed case is never
flagged.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import payloads
from app.events import CaseNotFound, Event, EventIdConflict, EventStore
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition

log = logging.getLogger("app.watchdog")

WATCHDOG_ACTOR = "system:watchdog"
# Fixed namespace for deterministic event ids (never change: it would break idempotency).
WATCHDOG_NAMESPACE = uuid.UUID("c3f1d6a8-52b9-4e07-8a1d-6b0e9f4c7d25")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def idle_flag_event_id(case_id: UUID, started_by: UUID | str) -> UUID:
    """One id per idle episode: `started_by` is the id of the event the quiet stretch runs from."""
    return uuid.uuid5(WATCHDOG_NAMESPACE, f"{case_id}|{started_by}|idle")


def plan_idle_flag(
    case_id: UUID, model: CaseModel, pathway: Pathway, now: datetime, idle_days: int
) -> Event | None:
    """`watchdog_flagged {reason: idle}` for an open case quiet for at least `idle_days`, once per
    episode; None otherwise. Pure."""
    if model.state == pathway.terminal_state or model.last_activity_at is None:
        return None
    if model.watchdog_flag is not None:
        return None  # already flagged since the last real event
    idle = _utc(now) - _utc(model.last_activity_at)
    if idle < timedelta(days=idle_days):
        return None
    return Event(
        case_id,
        "watchdog_flagged",
        now,
        WATCHDOG_ACTOR,
        {
            "reason": "idle",
            "idle_days": idle.days,
            "last_activity_at": _utc(model.last_activity_at).isoformat(),
        },
        idle_flag_event_id(case_id, model.last_activity_event_id),
    )


@dataclass
class ScanResult:
    sim_time: str
    idle_days: int
    cases_evaluated: int = 0
    flagged: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"sim_time": self.sim_time, "idle_days": self.idle_days, "cases_evaluated": self.cases_evaluated,
                "flagged": self.flagged, "rejected": self.rejected, "errors": list(self.errors)}


def run_idle_scan(store: EventStore, pathway: Pathway, now: datetime, idle_days: int, only=None) -> ScanResult:
    """Flag every open case idle for `idle_days` (or only the cases in `only`). Safe to repeat."""
    result = ScanResult(sim_time=_utc(now).isoformat(), idle_days=idle_days)
    for case_id, model, error in store.iter_open_case_models(pathway, only=only):
        result.cases_evaluated += 1
        if model is None:
            result.errors.append(f"{case_id}: {error}")
            continue
        event = plan_idle_flag(case_id, model, pathway, now, idle_days)
        if event is None:
            continue
        if event.type != "watchdog_flagged" or event.actor != WATCHDOG_ACTOR:
            raise RuntimeError(f"watchdog planned a forbidden event: {event.type}")
        try:
            payloads.validate(event.type, event.payload)
            store.append(event, pathway)
        except InvalidTransition as exc:
            result.rejected += 1  # another writer moved the case first; the next scan re-plans
            log.info("case %s: flag refused: %s", case_id, exc)
        except (payloads.PayloadSchemaError, CaseNotFound, EventIdConflict) as exc:
            result.errors.append(f"{case_id}: {type(exc).__name__}: {exc}")
        else:
            result.flagged += 1
    return result
