import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import automation, confirmatory, consent, effort, evidence, fhir_projection, notify, payloads, policy, readmodel, users
from app.calendar import default_calendar
from app.config import settings as app_settings
from app.auth import Identity, require_identity
from app.clock import clock
from app.events import (
    CASE_CREATED,
    CaseAlreadyExists,
    CaseNotFound,
    Event,
    EventContentConflict,
    EventIdConflict,
    EventStore,
    InMemoryEventStore,
    LoadedCase,
    replay_model,
)
from app.pathway import Pathway
from app.risk import score as risk
from app.state_machine import InvalidTransition, PayloadError, validate_payload

log = logging.getLogger("app.cases")
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
    # A21: the primary-care provider the patient is registered with (config/providers.yaml maps it to a default owner).
    registered_provider: str | None = None


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


# ---- the loss-to-follow-up risk score (D9) --------------------------------------------------------
# The ONLY place in the API that computes or reads it (T-D10, tests/test_risk_boundary.py): it is
# serialized into the worklist row and the case page for ordering and explanation, and feeds nothing
# else: not the sweep, not a state, not an escalation, not a closure. Patients never see it. A failure
# to score (a missing or corrupt model file) leaves the field null and never breaks the worklist.


def _sees_risk(identity: Identity | None) -> bool:
    return identity is not None and identity.role != "patient"


def _risk_score(lc: LoadedCase, now: datetime) -> float | None:
    try:
        return risk.score_case(lc.model, lc.events, lc.created_at, now)
    except Exception:  # noqa: BLE001 - an ordering aid must never take the worklist down
        log.warning("risk score unavailable for case %s", lc.case_id, exc_info=True)
        return None


def _risk_explained(model, events, now: datetime) -> dict | None:
    try:
        return risk.explain_case(model, events, events[0].at, now)
    except Exception:  # noqa: BLE001
        log.warning("risk explanation unavailable", exc_info=True)
        return None


def factors_for_agency(case_id: UUID) -> dict | None:
    """The staff case page's explanation (`{score, top_factors}`) for one case, for the agency's one-sentence
    explainer (A22): read-only, shown to a person, and read by no plan, dispatch or decision of the agency
    (tests/test_agency.py proves the plans are the same with it randomised). None for an unknown case."""
    events = store.events_for(case_id)
    if not events:
        return None
    return _risk_explained(replay_model(events, pathway), events, clock.now())


def _case_json(case_id: UUID, identity: Identity | None = None) -> dict:
    """The case page. `identity` decides whether the risk fields are filled (staff yes, patient and
    unauthenticated internal callers no)."""
    state = store.current_state(case_id)
    if state is None:
        raise CaseNotFound(str(case_id))
    events = store.events_for(case_id)
    model = replay_model(events, pathway)
    row = readmodel.case_row(
        LoadedCase(case_id, pathway.name, events[0].at, events, model), pathway, default_calendar()
    )
    if identity is not None and identity.role == "patient":
        # SEC-2: a patient's case page is the redacted one (no staff notes or reasons, no escalation
        # internals, no staff names: roles only).
        return readmodel.patient_case_json(row, model, events)
    explained = _risk_explained(model, events, clock.now()) if _sees_risk(identity) else None
    row["risk_score"] = explained["score"] if explained else None
    return {
        # The worklist fields (owner as {actor, display_name, role}, due_at, ...) so a case page
        # needs no second request; the keys below it are the case itself.
        **row,
        "case_id": str(case_id),
        "state": state,
        # state, overdue, escalation_level, owner, consent_frozen, last_event_at, ... derived
        # by replaying the events above (see app.state_machine.CaseModel).
        "read_model": model.to_json(),
        # A18: {referral_requested: {due_at, met_at, overdue, ...}, confirmatory_completed: {...}}; the clocks that
        # span states, with their due dates (read_model.milestones holds the raw replayed state, without due dates).
        "milestones": readmodel.milestones_json(model, pathway, default_calendar()),
        "actors": _actors_json(events, model),
        "events": [_event_json(e) for e in events],
        # A21: who did the work of this case, people or the automation, and the (assumed) minutes either way.
        "effort": effort.case_effort(events),
        # D9: {score, top_factors: [{feature, value, contribution}]}; null for a patient or if unscorable.
        "risk": explained,
    }


@router.post("", status_code=201)
def create_case(body: CreateCase) -> dict:
    case_id = body.case_id or uuid4()
    payload = body.model_dump(exclude={"case_id"})
    for key in ("patient_ref", "result_ref", "contact_valid", "registered_provider"):
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
    # A21 (ADR-032): the result is acknowledged and its owner named at once, by the automation, not by a person. A no-op
    # unless AUTOMATION_ENABLED; it never fails the ingest (a failure is healed by the scheduler's automation scan).
    automation.on_ingest(store, pathway, case_id, payload, clock.now())
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
    (genesis time, then case id). Filters: `state`, `owner` (the opaque actor `role:user_id` or its
    bare user id, never a display name: F8-1), `overdue`, `provider`, `flagged` (open watchdog flag). A patient sees only their own
    cases; everyone else sees all. `risk_score` (D9) is the modelled probability of loss to follow-up,
    for ordering only: the rows come back in the same order with or without it, and a patient's rows
    carry null."""
    calendar = default_calendar()
    now = clock.now()
    with_risk = _sees_risk(identity)
    rows = []
    for lc in store.load_cases(pathway, state):
        if identity.role == "patient" and (identity.patient_ref is None or lc.patient_ref != identity.patient_ref):
            continue
        row = readmodel.case_row(lc, pathway, calendar)
        if row is not None and readmodel.matches(row, owner=owner, overdue=overdue, provider=provider, flagged=flagged):
            if with_risk:  # D9: filled last, so no filter or order above depends on it
                row["risk_score"] = _risk_score(lc, now)
            rows.append(readmodel.patient_case_row(row) if identity.role == "patient" else row)  # SEC-2: redacted for a patient
    return rows


def _patient_ref(case_id: UUID) -> str | None:
    """The patient the case belongs to, from its genesis event (None if unknown)."""
    events = store.events_for(case_id)
    return events[0].payload.get("patient_ref") if events else None


def _check_signature_and_evidence(identity: Identity, event_type: str, payload: dict, case_id: UUID) -> None:
    """A15 (ADR-021): a signature is the signer's own, and closure evidence must resolve AND be this
    case's own (SEC-2: the resource must be the one named, and belong to this case or its patient)."""
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
        if not evidence.well_formed_ref(ref):  # `Procedure/.`: the URL would collapse to a search
            raise HTTPException(422, f"evidence_ref {ref!r} is not a resource reference")
        try:
            resolves = evidence.verifier.exists(ref, case_id=case_id, patient_ref=_patient_ref(case_id))
        except evidence.EvidenceUnavailable as exc:
            raise HTTPException(503, f"cannot verify closure evidence: {exc}")
        if not resolves:
            raise HTTPException(422, f"evidence_ref {ref!r} does not resolve to a resource of this case")


def _refuse_backdated(model, at: datetime) -> None:
    """SEC-2 (ADR-027 amended): a user's event may not be dated before the case's latest event. Evaluated
    under the case's row lock on the replayed model, so two requests cannot both pass on a stale read."""
    last = model.last_event_at
    if last is not None and at < (last if last.tzinfo else last.replace(tzinfo=UTC)):
        raise HTTPException(
            422,
            f"backdated: event time {at.isoformat()} is before the case's latest event ({last.isoformat()}); "
            "the log runs forward in time",
        )


def _event_guard(identity: Identity, event_type: str, at: datetime):
    """What the store re-checks under the case lock, before the state machine sees the event, on the
    model replayed from the log as it now stands: the caller's owner scope (SEC-2: a reassignment or a
    handoff accepted between our read and our write is seen, 403 and no row), and, for a user token,
    that the event is not backdated. Raises HTTPException."""
    owner = policy.owner_guard(identity, event_type)

    def guard(model) -> None:
        owner(model)  # a no-op for roles that are not owner-scoped
        if identity.role in policy.ALL_USERS:
            _refuse_backdated(model, at)

    return guard


@router.post("/{case_id}/events", status_code=201)
def post_event(
    case_id: UUID, body: PostEvent, identity: Identity = Depends(require_identity)
) -> dict:
    policy.authorize_event(identity, body.type)
    if store.current_state(case_id) is None:
        raise HTTPException(404, "case not found")
    policy.authorize_case_access(identity, _patient_ref(case_id))
    if identity.role in policy.OWNER_SCOPED:
        # An early answer without the lock; the authoritative check is the guard handed to the store.
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
        _check_signature_and_evidence(identity, body.type, body.payload, case_id)
        store.append(event, pathway, guard=_event_guard(identity, body.type, at))
    except CaseNotFound:
        raise HTTPException(404, "case not found")
    except (PayloadError, payloads.PayloadSchemaError) as exc:
        raise HTTPException(422, str(exc))
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    except EventContentConflict:
        raise HTTPException(409, "event_id already used by a different event on this case: a retry repeats "
                                 "the same type, payload and time; use a new event_id for a new event")
    except EventIdConflict:
        raise HTTPException(409, "event_id already used by another case")
    if body.type == "consent_updated":
        # Withdrawal freezes outreach and flags the case for human review (system:consent).
        consent.ensure_review_requested(store, pathway, case_id, event.event_id, at)
    return _case_json(case_id, identity)


@router.get("/{case_id}")
def get_case(case_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    try:
        policy.authorize_case_access(identity, _patient_ref(case_id))
        return _case_json(case_id, identity)
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
    if identity.role == "patient":
        # SEC-2: the patient's own timeline, redacted: visible steps only, roles instead of names.
        return {"case_id": str(case_id), **readmodel.patient_timeline(events, clock.now()),
                "actors": readmodel.patient_actors(events)}
    model = replay_model(events, pathway)
    return {
        "case_id": str(case_id),
        **readmodel.timeline(events, pathway, default_calendar(), clock.now()),
        "actors": _actors_json(events, model),
    }


@router.get("/{case_id}/fhir")
def case_fhir(case_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    """The FHIR resources the projection writes for this case (A12): their ids, computed from the
    log, plus how many events are still waiting in the retry queue. Owners see only their own case."""
    model = store.read_model(case_id, pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "view_fhir", model)
    projector = getattr(store, "projector", None)
    return {
        "case_id": str(case_id),
        "projection_enabled": projector is not None,
        "fhir_base_url": app_settings.fhir_base_url,
        "events_waiting": projector.queue.count(case_id) if projector is not None else 0,
        **fhir_projection.case_resource_ids(pathway, default_calendar(), case_id, store.events_for(case_id)),
    }


@router.get("/{case_id}/notifications")
def case_notifications(case_id: UUID, identity: Identity = Depends(require_identity)) -> list[dict]:
    """The case's notification log (A7), oldest first. Owners see only their own cases."""
    model = store.read_model(case_id, pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "view_notifications", model)
    return [n.to_json() for n in notify.log.for_case(case_id)]


# States from which the colonoscopy result can be recorded: the test is booked or already
# completed (the clinician may record the evidence after `complete_confirmatory`), or the case is
# back for review. Earlier states have no colonoscopy to record; a closed case needs a reopen first.
RESULT_STATES = frozenset({"confirmatory_booked", "confirmatory_completed", "clinician_reviewed", "reopened"})


@router.post("/{case_id}/confirmatory-result", status_code=201)
def record_confirmatory_result(case_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    """Record the confirmatory colonoscopy (demo, E8): creates a synthetic `Procedure` and
    `DiagnosticReport` in HAPI for the case's patient and returns their references. The case owner
    only (a primary_care_owner or specialist_owner who owns it). It appends no event: the owner
    then posts `complete_confirmatory {result_ref}` and `close {evidence_ref}` with the reference."""
    model = store.read_model(case_id, pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "record_confirmatory_result", model)
    if model.state not in RESULT_STATES:
        raise HTTPException(409, f"no colonoscopy to record in state {model.state!r}")
    try:
        refs = confirmatory.recorder.record(case_id, _patient_ref(case_id), clock.now())
    except confirmatory.RecorderUnavailable as exc:
        raise HTTPException(503, f"cannot record the colonoscopy result: {exc}")
    return {"case_id": str(case_id), "evidence_ref": refs["procedure_ref"], **refs, "recorded_at": clock.now().isoformat()}
