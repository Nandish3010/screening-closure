"""Append-only event log. The event log is the source of truth; state is derived by replay.

`cases.current_state` in Postgres is a read model derived from the log and re-verified
against a replay on every append. Two stores implement the same `EventStore` protocol:
`InMemoryEventStore` (unit tests, no-docker runs) and `PostgresEventStore` (app.store_pg).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.pathway import Pathway
from app.state_machine import (  # noqa: F401  (CASE_CREATED and replay_model are re-exported)
    CASE_CREATED,
    CaseModel,
    genesis,
    is_automated,
    replay_model,
    start_genesis_milestones,
    step,
)


@dataclass(frozen=True)
class Event:
    case_id: UUID
    type: str
    at: datetime
    actor: str
    payload: dict = field(default_factory=dict)
    # Idempotency key: appending the same event_id to a case twice inserts once.
    event_id: UUID = field(default_factory=uuid4)
    # Position in the case's log (1-based, gap-free). Assigned by the store on append.
    seq: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class CaseSummary:
    case_id: UUID
    pathway: str
    current_state: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LoadedCase:
    """A case with its full log and read model, for the list routes and metrics (A9, A10).

    `model` is None (and `error` set) when the stored log does not replay."""

    case_id: UUID
    pathway: str
    created_at: datetime  # `at` of the genesis event: simulation time
    events: list
    model: CaseModel | None
    error: str | None = None

    @property
    def patient_ref(self) -> str | None:
        return self.events[0].payload.get("patient_ref") if self.events else None


class CaseNotFound(KeyError):
    pass


class CaseAlreadyExists(Exception):
    pass


class EventIdConflict(Exception):
    """The event_id is already used by an event on a different case, or twice in one batch."""


class EventContentConflict(EventIdConflict):
    """The event_id is already used on THIS case by an event with different content (type, actor,
    payload or time). A retry of the same request repeats it exactly; anything else is a client bug
    (or an attempt to overwrite history by id) and is refused, not swallowed as a no-op."""


def event_digest(type_: str, at: datetime, actor: str, payload: dict) -> str:
    """sha256 over what makes an event THIS event: type, actor, payload and `at` (UTC). The idempotency
    check compares the digest of a repeated `event_id` with that of the stored event.

    Events from automated actors (the sweep, the watchdog, the notifier, the consent service) leave `at`
    out: their ids are derived from the cause (a clock start, a request, a draft), and `at` is merely
    when a detector noticed, which two concurrent detectors legitimately disagree on by a moment. What
    they did is in the type and payload; those still have to match."""
    when = None if is_automated(actor) else (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    blob = json.dumps([type_, actor, when, payload], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def digest_of(event: "Event") -> str:
    return event_digest(event.type, event.at, event.actor, event.payload)


def check_batch_ids(events) -> None:
    """Two events of one batch may not share an id: the second would be silently dropped as a retry
    of the first. Raises EventIdConflict."""
    seen: set[UUID] = set()
    for e in events:
        if e.event_id in seen:
            raise EventIdConflict(f"event_id {e.event_id} appears twice in one batch")
        seen.add(e.event_id)


class StateCorruption(Exception):
    """The stored current_state disagrees with a replay of the event log."""


class EventStore(Protocol):
    def create_case(
        self, case_id: UUID, pathway_name: str, genesis_event: Event, initial_state: str
    ) -> None:
        """Create a case and write its genesis event (seq 1) atomically."""
        ...

    def append(self, event: Event, pathway: Pathway, *, within=None, guard=None) -> str:
        """Validate the event against the state machine and append it atomically.

        `within`, if given, is called inside the same atomic step once the event is accepted;
        if it raises, nothing is written (D5 records a draft's approval this way).

        `guard(model)`, if given, is called inside the same atomic step, under the case lock, with the
        read model replayed from the log as it stands, BEFORE the state machine sees the event. It
        re-evaluates a decision the caller made on an earlier read (owner scope, backdating): whatever it
        raises propagates, nothing is written. It is not called for a repeated event_id (a retry).

        Returns the new state. Raises CaseNotFound, InvalidTransition (nothing written),
        EventIdConflict, EventContentConflict or StateCorruption. A repeated event_id on the same
        case with the same content is a no-op that returns the current state; with different
        content (type, actor, payload, time) it is an EventContentConflict (HTTP 409).
        """
        ...

    def events_for(self, case_id: UUID) -> list[Event]: ...

    def current_state(self, case_id: UUID) -> str | None: ...

    def read_model(self, case_id: UUID, pathway: Pathway) -> CaseModel | None:
        """The read model (state, overdue, escalation_level, owner...) derived by replay."""
        ...

    def list_cases(self, state: str | None = None) -> list[CaseSummary]: ...

    def has_events_after(self, at: datetime) -> bool:
        """True if any event of any case is dated strictly after `at` (SEC-1: the sim clock may only be
        reset while nothing has happened after its start)."""
        ...

    def latest_event_at(self) -> datetime | None:
        """The latest `at` of any event of any case, or None for an empty store (A30: a switch from wall to sim mode must not
        leave the sim clock behind the log)."""
        ...

    def abstention_counts(self) -> tuple[int, int] | None:
        """(draft attempts, abstentions) from the `drafts` table, or None where it does not
        exist (the AI cut's migration may not be applied)."""
        ...

    def load_cases(self, pathway: Pathway, state: str | None = None) -> list[LoadedCase]:
        """Every case of the pathway (optionally only those currently in `state`) with its log
        and read model, ordered by (genesis time, case id). One batch read, not one per case."""
        ...

    def iter_open_case_models(self, pathway: Pathway, only=None, states=None):
        """Yield (case_id, model | None, error | None) for every non-closed case of the pathway
        (optionally only those in `only`, and only those whose current state is one of `states`: the filter is
        applied by the store before any log is read). Used by the scheduler sweep and the automation scans."""
        ...


def replay(events, pathway: Pathway) -> str:
    """Deterministically fold events into a state, starting from the pathway's initial state."""
    return replay_model(events, pathway).state


@dataclass
class _Case:
    pathway: str
    model: CaseModel
    created_at: datetime
    updated_at: datetime
    events: list[Event] = field(default_factory=list)


class InMemoryEventStore:
    """Process-local store with the same semantics as the Postgres one (no durability)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cases: dict[UUID, _Case] = {}
        self._event_ids: dict[UUID, tuple[UUID, str]] = {}  # event_id -> (case_id, content digest)

    def create_case(
        self, case_id: UUID, pathway_name: str, genesis_event: Event, initial_state: str
    ) -> None:
        if genesis_event.type != CASE_CREATED or genesis_event.case_id != case_id:
            raise ValueError("genesis_event must be a case_created event for this case")
        with self._lock:
            if case_id in self._cases:
                raise CaseAlreadyExists(str(case_id))
            if genesis_event.event_id in self._event_ids:
                raise EventIdConflict(str(genesis_event.event_id))
            self._event_ids[genesis_event.event_id] = (case_id, digest_of(genesis_event))
            self._cases[case_id] = _Case(
                pathway_name,
                genesis(CaseModel(state=initial_state), genesis_event),
                genesis_event.at,
                genesis_event.at,
                [dataclasses.replace(genesis_event, seq=1)],
            )

    def append(self, event: Event, pathway: Pathway, *, within=None, guard=None) -> str:
        # `within(None)`, if given, runs after the event is accepted and before anything is
        # stored, under the store lock: if it raises, nothing is written (see the Postgres store).
        with self._lock:
            case = self._cases.get(event.case_id)
            if case is None:
                raise CaseNotFound(str(event.case_id))
            known = self._event_ids.get(event.event_id)
            if known is not None:
                if known[0] != event.case_id:
                    raise EventIdConflict(str(event.event_id))
                if known[1] != digest_of(event):
                    raise EventContentConflict(
                        f"event_id {event.event_id} is already used by a different event on this case")
                return case.model.state
            if guard is not None:
                guard(case.model)
            new_model = step(case.model, event, pathway)  # raises InvalidTransition
            if within is not None:
                within(None)
            case.events.append(dataclasses.replace(event, seq=len(case.events) + 1))
            case.model = new_model
            case.updated_at = event.at
            self._event_ids[event.event_id] = (event.case_id, digest_of(event))
            return new_model.state

    def events_for(self, case_id: UUID) -> list[Event]:
        with self._lock:
            case = self._cases.get(case_id)
            return list(case.events) if case else []

    def current_state(self, case_id: UUID) -> str | None:
        with self._lock:
            case = self._cases.get(case_id)
            return case.model.state if case else None

    def read_model(self, case_id: UUID, pathway: Pathway) -> CaseModel | None:
        with self._lock:
            case = self._cases.get(case_id)
            return start_genesis_milestones(case.model, pathway) if case else None

    def iter_open_case_models(self, pathway: Pathway, only=None, states=None):
        only = set(only) if only is not None else None
        states = set(states) if states is not None else None
        with self._lock:
            snapshot = [
                (cid, start_genesis_milestones(c.model, pathway)) for cid, c in self._cases.items()
                if c.pathway == pathway.name and c.model.state != pathway.terminal_state
                and (only is None or cid in only) and (states is None or c.model.state in states)
            ]
        for cid, model in snapshot:
            yield cid, model, None

    def has_events_after(self, at: datetime) -> bool:
        with self._lock:
            return any(e.at > at for c in self._cases.values() for e in c.events)

    def latest_event_at(self) -> datetime | None:
        with self._lock:
            return max((e.at for c in self._cases.values() for e in c.events), default=None)

    def abstention_counts(self) -> tuple[int, int] | None:
        return None  # no drafts table in memory

    def load_cases(self, pathway: Pathway, state: str | None = None) -> list[LoadedCase]:
        with self._lock:
            out = [
                LoadedCase(cid, c.pathway, c.events[0].at, list(c.events), start_genesis_milestones(c.model, pathway))
                for cid, c in self._cases.items()
                if c.pathway == pathway.name and (state is None or c.model.state == state)
            ]
        return sorted(out, key=lambda lc: (lc.created_at, str(lc.case_id)))

    def list_cases(self, state: str | None = None) -> list[CaseSummary]:
        with self._lock:
            return [
                CaseSummary(cid, c.pathway, c.model.state, c.created_at, c.updated_at)
                for cid, c in self._cases.items()
                if state is None or c.model.state == state
            ]
