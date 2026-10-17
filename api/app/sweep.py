"""Scheduler SLA sweep: find breaches and emit `mark_overdue` (A3), then walk the ladder (A4).

The planner (`plan_case`) is a pure function of (case model, pathway, calendar, now). It never
reads a clock and never produces anything but `mark_overdue` / `escalate` events from
`system:scheduler`; in particular it can never produce `close` (T-A16). `run_sweep` feeds it
every open case from a store and appends what it plans.

Idempotency: each planned event has a deterministic id, uuid5 over (case, state, due date
[, rung]). Running the sweep again, or two sweeps at once, therefore inserts each event once:
the store treats a repeated event_id on the same case as a no-op. The state machine adds a
second line of defence (a case that is already overdue refuses another `mark_overdue`).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.calendar import WorkingCalendar
from app.events import CaseNotFound, Event, EventIdConflict, EventStore
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition

log = logging.getLogger("app.sweep")

SCHEDULER_ACTOR = "system:scheduler"
# The only event types the scheduler may ever emit (mirrors app.policy.EVENT_POLICY).
SCHEDULER_EVENT_TYPES = frozenset({"mark_overdue", "escalate", "watchdog_flagged"})
# Fixed namespace for deterministic event ids (never change: it would break idempotency).
SWEEP_NAMESPACE = uuid.UUID("5c6d0a9e-77f1-4b8e-9d0c-0f3f6a1b2e44")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def sweep_event_id(case_id: UUID, state: str, due_at: datetime, kind: str) -> UUID:
    """uuid5(case, state, due date, kind): one id per breach (and per rung)."""
    return uuid.uuid5(SWEEP_NAMESPACE, f"{case_id}|{state}|{_utc(due_at).isoformat()}|{kind}")


def due_at_for(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> datetime | None:
    """When the current state's SLA expires, or None if the state has no SLA to watch.

    The clock starts at the event that entered the state (`model.clock_started_at`) and the
    SLA length is counted in working days.
    """
    sla = pathway.sla_for_state(model.state)
    if sla is None or model.clock_started_at is None or model.state not in pathway.open_states:
        return None
    _, days = sla
    return _utc(calendar.add_working_days(model.clock_started_at, days))


def plan_case(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> list[Event]:
    """The events the scheduler should append for this case at sim time `now`, in order."""
    due = due_at_for(model, pathway, calendar)
    if due is None or now <= due:
        return []
    planned: list[Event] = []
    sla_event, sla_days = pathway.sla_for_state(model.state)
    if not model.overdue:
        planned.append(
            Event(
                case_id,
                "mark_overdue",
                now,
                SCHEDULER_ACTOR,
                {
                    "state": model.state,
                    "due_at": due.isoformat(),
                    "sla_event": sla_event,
                    "sla_working_days": sla_days,
                },
                sweep_event_id(case_id, model.state, due, "mark_overdue"),
            )
        )
    return planned


@dataclass
class SweepResult:
    sim_time: str
    cases_evaluated: int = 0
    mark_overdue: int = 0
    escalate: int = 0
    rejected: int = 0  # planned events the state machine refused (a race with another writer)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "sim_time": self.sim_time,
            "cases_evaluated": self.cases_evaluated,
            "mark_overdue": self.mark_overdue,
            "escalate": self.escalate,
            "rejected": self.rejected,
            "errors": list(self.errors),
        }


def run_sweep(
    store: EventStore,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    only=None,
) -> SweepResult:
    """Evaluate every open case (or only those in `only`) at sim time `now` and append the
    planned events. Safe to run repeatedly and concurrently."""
    result = SweepResult(sim_time=_utc(now).isoformat())
    for case_id, model, error in store.iter_open_case_models(pathway, only=only):
        result.cases_evaluated += 1
        if model is None:
            result.errors.append(f"{case_id}: {error}")
            log.error("case %s does not replay: %s", case_id, error)
            continue
        for event in plan_case(case_id, model, pathway, calendar, now):
            if event.type not in SCHEDULER_EVENT_TYPES or event.actor != SCHEDULER_ACTOR:
                raise RuntimeError(f"scheduler planned a forbidden event: {event.type}")
            try:
                store.append(event, pathway)
            except InvalidTransition as exc:
                # Another writer moved the case between our read and our write; the next
                # sweep re-plans from the new state.
                result.rejected += 1
                log.info("case %s: %s refused: %s", case_id, event.type, exc)
                break
            except (CaseNotFound, EventIdConflict) as exc:
                result.errors.append(f"{case_id}: {type(exc).__name__}: {exc}")
                break
            if event.type == "mark_overdue":
                result.mark_overdue += 1
            else:
                result.escalate += 1
    return result
