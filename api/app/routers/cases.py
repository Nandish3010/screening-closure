from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import consent, evidence, notify, payloads, policy, readmodel, users
from app.calendar import default_calendar
from app.auth import Identity, require_identity
from app.clock import clock
from app.events import (
    CASE_CREATED,
    CaseAlreadyExists,
    CaseNotFound,
    Event,
    EventIdConflict,
    EventStore,
    InMemoryEventStore,
    LoadedCase,
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


_event_json = readmodel.event_json


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
    row = readmodel.case_row(
        LoadedCase(case_id, pathway.name, events[0].at, events, model), pathway, default_calendar()
    )
    return {
        # The worklist fields (owner as {actor, display_name, role}, due_at, ...) so a case page
        # needs no second request; the keys below it are the case itself.
        **row,
        "case_id": str(case_id),
        "state": state,
        # state, overdue, escalation_level, owner, consent_frozen, last_event_at, ... derived
        # by replaying the events above (see app.state_machine.CaseModel).
        "read_model": model.to_json(),
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
def list_cases(
    state: str | None = None,
    owner: str | None = None,
    overdue: bool | None = None,
    provider: str | None = None,
    flagged: bool | None = None,
    identity: Identity = Depends(require_identity),
) -> list[dict]:
    """The worklist (A9): one row per case with the fields the web needs, in a stable order
    (genesis time, then case id). Filters: `state`, `owner` (an actor, a user id or a display
    name), `overdue`, `provider`, `flagged` (open watchdog flag). A patient sees only their own
    cases; everyone else sees all."""
    calendar = default_calendar()
    rows = []
    for lc in store.load_cases(pathway, state):
        if identity.role == "patient" and (identity.patient_ref is None or lc.patient_ref != identity.patient_ref):
            continue
        row = readmodel.case_row(lc, pathway, calendar)
        if row is not None and readmodel.matches(row, owner=owner, overdue=overdue, provider=provider, flagged=flagged):
            rows.append(row)
    return rows


def _patient_ref(case_id: UUID) -> str | None:
    """The patient the case belongs to, from its genesis event (None if unknown)."""
    events = store.events_for(case_id)
    return events[0].payload.get("patient_ref") if events else None


def _check_signature_and_evidence(identity: Identity, event_type: str, payload: dict) -> None:
    """A15 (ADR-021): a signature is the signer's own, and closure evidence must resolve."""
    signed_by = payload.get("signed_by")
    if event_type in ("clinician_review", "close") and signed_by is not None:
        if not evidence.signed_by_matches(identity, signed_by):
            raise HTTPException(
                403,
                f"signed_by must be your own identity ({identity.actor} or "
                f"Practitioner/{identity.user_id}), not {signed_by!r}",
            )
    if event_type == "close":
        ref = payload["evidence_ref"]  # shape already checked by the payload schema
        try:
            resolves = evidence.verifier.exists(ref)
        except evidence.EvidenceUnavailable as exc:
            raise HTTPException(503, f"cannot verify closure evidence: {exc}")
        if not resolves:
            raise HTTPException(422, f"evidence_ref {ref!r} does not resolve to a resource")


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
        _check_signature_and_evidence(identity, body.type, body.payload)
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


@router.get("/{case_id}/timeline")
def case_timeline(case_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    """The case's audit view (A9): every event in sequence plus the derived items (SLA clocks,
    breaches, ladder rungs, handoff bounces, watchdog flags) in one list ordered by `at`.
    Read-only: this path has no write route."""
    try:
        policy.authorize_case_access(identity, _patient_ref(case_id))
    except CaseNotFound:
        raise HTTPException(404, "case not found")
    events = store.events_for(case_id)
    if not events:
        raise HTTPException(404, "case not found")
    model = replay_model(events, pathway)
    return {
        "case_id": str(case_id),
        **readmodel.timeline(events, pathway, default_calendar(), clock.now()),
        "actors": _actors_json(events, model),
    }


@router.get("/{case_id}/notifications")
def case_notifications(case_id: UUID, identity: Identity = Depends(require_identity)) -> list[dict]:
    """The case's notification log (A7), oldest first. Owners see only their own cases."""
    model = store.read_model(case_id, pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "view_notifications", model)
    return [n.to_json() for n in notify.log.for_case(case_id)]
