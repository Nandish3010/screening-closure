from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.clock import clock
from app.events import (
    CASE_CREATED,
    CaseAlreadyExists,
    CaseNotFound,
    CaseSummary,
    Event,
    EventIdConflict,
    EventStore,
    InMemoryEventStore,
)
from app.pathway import Pathway
from app.state_machine import InvalidTransition

router = APIRouter(prefix="/cases", tags=["cases"])

# Both set by app.main at startup (avoids a circular import). The in-memory store is the
# default so unit tests and no-docker runs work without a database.
pathway: Pathway | None = None
store: EventStore = InMemoryEventStore()


class CreateCase(BaseModel):
    # Optional client-chosen id (e.g. uuid5 of the result) so seeding scripts can retry
    # without duplicating cases. Omitted -> the server generates one.
    case_id: UUID | None = None
    patient_ref: str | None = None  # TODO: FHIR Patient reference
    result_ref: str | None = None  # TODO: FHIR DiagnosticReport reference


class PostEvent(BaseModel):
    type: str
    actor: str
    payload: dict = Field(default_factory=dict)
    at: datetime | None = None  # defaults to the sim clock
    event_id: UUID | None = None  # optional idempotency key for client retries


def _event_json(e: Event) -> dict:
    return {
        "seq": e.seq,
        "event_id": str(e.event_id),
        "type": e.type,
        "at": e.at.isoformat(),
        "actor": e.actor,
        "payload": e.payload,
    }


def _summary_json(c: CaseSummary) -> dict:
    return {
        "case_id": str(c.case_id),
        "pathway": c.pathway,
        "state": c.current_state,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _case_json(case_id: UUID) -> dict:
    state = store.current_state(case_id)
    if state is None:
        raise CaseNotFound(str(case_id))
    return {
        "case_id": str(case_id),
        "state": state,
        "events": [_event_json(e) for e in store.events_for(case_id)],
    }


@router.post("", status_code=201)
def create_case(body: CreateCase) -> dict:
    case_id = body.case_id or uuid4()
    payload = body.model_dump(exclude={"case_id"})
    genesis = Event(case_id, CASE_CREATED, clock.now(), "system:intake", payload)
    try:
        store.create_case(case_id, pathway.name, genesis, pathway.initial_state)
    except CaseAlreadyExists:
        raise HTTPException(409, "CaseAlreadyExists")
    return _case_json(case_id)


@router.get("")
def list_cases(state: str | None = None) -> list[dict]:
    return [_summary_json(c) for c in store.list_cases(state)]


@router.post("/{case_id}/events", status_code=201)
def post_event(case_id: UUID, body: PostEvent) -> dict:
    extra = {"event_id": body.event_id} if body.event_id is not None else {}
    event = Event(case_id, body.type, body.at or clock.now(), body.actor, body.payload, **extra)
    try:
        store.append(event, pathway)
    except CaseNotFound:
        raise HTTPException(404, "case not found")
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    except EventIdConflict:
        raise HTTPException(409, "event_id already used by another case")
    return _case_json(case_id)


@router.get("/{case_id}")
def get_case(case_id: UUID) -> dict:
    try:
        return _case_json(case_id)
    except CaseNotFound:
        raise HTTPException(404, "case not found")
