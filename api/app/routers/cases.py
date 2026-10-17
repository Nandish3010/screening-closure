from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.clock import clock
from app.events import CASE_CREATED, CaseNotFound, Event, store
from app.pathway import Pathway
from app.state_machine import InvalidTransition, apply

router = APIRouter(prefix="/cases", tags=["cases"])

# Set by app.main at startup (avoids a circular import).
pathway: Pathway | None = None


class CreateCase(BaseModel):
    patient_ref: str | None = None  # TODO: FHIR Patient reference
    result_ref: str | None = None  # TODO: FHIR DiagnosticReport reference


class PostEvent(BaseModel):
    type: str
    actor: str
    payload: dict = Field(default_factory=dict)
    at: datetime | None = None  # defaults to the sim clock


def _event_json(e: Event) -> dict:
    return {"type": e.type, "at": e.at.isoformat(), "actor": e.actor, "payload": e.payload}


def _case_json(case_id: UUID) -> dict:
    events = store.events(case_id)
    return {
        "case_id": str(case_id),
        "state": store.replay(case_id, pathway),
        "events": [_event_json(e) for e in events],
    }


@router.post("", status_code=201)
def create_case(body: CreateCase) -> dict:
    case_id = uuid4()
    store.append(
        Event(case_id, CASE_CREATED, clock.now(), "system:intake", body.model_dump())
    )
    return _case_json(case_id)


@router.post("/{case_id}/events", status_code=201)
def post_event(case_id: UUID, body: PostEvent) -> dict:
    try:
        current = store.replay(case_id, pathway)
    except CaseNotFound:
        raise HTTPException(404, "case not found")
    event = Event(case_id, body.type, body.at or clock.now(), body.actor, body.payload)
    try:
        apply(current, event, pathway)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    store.append(event)
    return _case_json(case_id)


@router.get("/{case_id}")
def get_case(case_id: UUID) -> dict:
    try:
        return _case_json(case_id)
    except CaseNotFound:
        raise HTTPException(404, "case not found")
