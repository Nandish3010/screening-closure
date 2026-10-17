"""Append-only event log. State is never stored, only derived by replay.

TODO: persist to Postgres (table DDL in migrations/001_events.sql) and make
event handling idempotent (idempotency key per event).
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.pathway import Pathway
from app.state_machine import apply

CASE_CREATED = "case_created"  # genesis marker; does not change state


@dataclass(frozen=True)
class Event:
    case_id: UUID
    type: str
    at: datetime
    actor: str
    payload: dict = field(default_factory=dict)


class CaseNotFound(KeyError):
    pass


class EventStore:
    """In-memory placeholder. Only append() and read access; no update, no delete."""

    def __init__(self) -> None:
        self._events: dict[UUID, list[Event]] = {}

    def append(self, event: Event) -> None:
        self._events.setdefault(event.case_id, []).append(event)

    def events(self, case_id: UUID) -> tuple[Event, ...]:
        if case_id not in self._events:
            raise CaseNotFound(str(case_id))
        return tuple(self._events[case_id])

    def case_ids(self) -> list[UUID]:
        return list(self._events)

    def replay(self, case_id: UUID, pathway: Pathway) -> str:
        return replay(self.events(case_id), pathway)


def replay(events, pathway: Pathway) -> str:
    """Deterministically fold events into a state, starting from the pathway's initial state."""
    state = pathway.initial_state
    for ev in events:
        if ev.type == CASE_CREATED:
            continue
        state = apply(state, ev, pathway)
    return state


store = EventStore()
