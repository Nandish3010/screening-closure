from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import consent, notify, payloads, policy, users
from app.auth import Identity, require_identity
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
    replay_model,
)
from app.pathway import Pathway
from app.state_machine import InvalidTransition, PayloadError, validate_payload

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
    # Known contact problem (A7): false = no valid contact details, or {"sms": false} per channel.
    # The notification mock refuses to "send" on a flagged channel. Absent = no known problem.
    contact_valid: bool | dict[str, bool] | None = None


class PostEvent(BaseModel):
    # `actor` is derived from the caller's identity (A8). Sending it is an error, not a hint.
    model_config = ConfigDict(extra="forbid")

    type: str
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


def _actors_json(events, model) -> dict:
    """Display names for every actor in the log and the owner, resolved from the users table
    (A14): the log holds only opaque `role:user_id` strings."""
    actors = {e.actor for e in events}
    if model.owner:
        actors.add(model.owner)
    return {a: users.resolve_actor(a) for a in sorted(actors)}


def _case_json(case_id: UUID) -> dict:
    state = store.current_state(case_id)
    if state is None:
        raise CaseNotFound(str(case_id))
    events = store.events_for(case_id)
    model = replay_model(events, pathway)
    return {
        "case_id": str(case_id),
        "state": state,
        # state, overdue, escalation_level, owner, consent_frozen, last_event_at, ... derived
        # by replaying the events above (see app.state_machine.CaseModel).
        "read_model": model.to_json(),
        # The owner for the UI: {actor, display_name, role}. read_model.owner stays the actor.
        "owner": users.resolve_actor(model.owner),
        "actors": _actors_json(events, model),
        "events": [_event_json(e) for e in events],
    }


@router.post("", status_code=201)
def create_case(body: CreateCase) -> dict:
    case_id = body.case_id or uuid4()
    payload = body.model_dump(exclude={"case_id"})
    for key in ("patient_ref", "result_ref", "contact_valid"):
        if payload.get(key) is None:
            payload.pop(key, None)
    try:
        payloads.validate(CASE_CREATED, payload)
    except payloads.PayloadSchemaError as exc:
        raise HTTPException(422, str(exc))
    genesis = Event(case_id, CASE_CREATED, clock.now(), "system:intake", payload)
    try:
        store.create_case(case_id, pathway.name, genesis, pathway.initial_state)
    except CaseAlreadyExists:
        raise HTTPException(409, "CaseAlreadyExists")
    return _case_json(case_id)


@router.get("")
def list_cases(state: str | None = None) -> list[dict]:
    return [_summary_json(c) for c in store.list_cases(state)]


def _patient_ref(case_id: UUID) -> str | None:
    """The patient the case belongs to, from its genesis event (None if unknown)."""
    events = store.events_for(case_id)
    return events[0].payload.get("patient_ref") if events else None


@router.post("/{case_id}/events", status_code=201)
def post_event(
    case_id: UUID, body: PostEvent, identity: Identity = Depends(require_identity)
) -> dict:
    policy.authorize_event(identity, body.type)
    if store.current_state(case_id) is None:
        raise HTTPException(404, "case not found")
    policy.authorize_case_access(identity, _patient_ref(case_id))
    if identity.role in policy.OWNER_SCOPED:
        model = store.read_model(case_id, pathway)
        if model is None:
            raise HTTPException(404, "case not found")
        policy.authorize_owner_scope(identity, body.type, model)
    at = body.at or clock.now()
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    if at > clock.now():
        raise HTTPException(422, "event time cannot be later than the simulation clock")
    extra = {"event_id": body.event_id} if body.event_id is not None else {}
    event = Event(case_id, body.type, at, identity.actor, body.payload, **extra)
    try:
        payloads.validate(body.type, body.payload)  # free text cannot enter the log (A14)
        validate_payload(event, pathway)  # malformed payload: 422 before anything is written
        store.append(event, pathway)
    except CaseNotFound:
        raise HTTPException(404, "case not found")
    except (PayloadError, payloads.PayloadSchemaError) as exc:
        raise HTTPException(422, str(exc))
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    except EventIdConflict:
        raise HTTPException(409, "event_id already used by another case")
    if body.type == "consent_updated":
        # Withdrawal freezes outreach and flags the case for human review (system:consent).
        consent.ensure_review_requested(store, pathway, case_id, event.event_id, at)
    return _case_json(case_id)


@router.get("/{case_id}")
def get_case(case_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    try:
        policy.authorize_case_access(identity, _patient_ref(case_id))
        return _case_json(case_id)
    except CaseNotFound:
        raise HTTPException(404, "case not found")


@router.get("/{case_id}/notifications")
def case_notifications(case_id: UUID, identity: Identity = Depends(require_identity)) -> list[dict]:
    """The case's notification log (A7), oldest first. Owners see only their own cases."""
    model = store.read_model(case_id, pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "view_notifications", model)
    return [n.to_json() for n in notify.log.for_case(case_id)]
