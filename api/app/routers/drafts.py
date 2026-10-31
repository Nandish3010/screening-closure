"""AI message drafts (D4) and the abstention metric (D11).

The model drafts, a human approves (docs/governance/ai-boundary.md). This module holds the only
route that calls a drafting provider. It never changes a case's state: it appends the
annotation `message_drafted` as the automated actor `model:<provider>`, and stores the draft,
the exact prompt and the provider as provenance.

It also holds the approval gate (D5): `POST /drafts/{id}/approve` is the only code path that
approves a draft and starts the send, and so the only way a `message_sent` event can come to exist.
The approval, the `message_approved` event and an outbox row (`app.outbox`) commit in ONE transaction;
`drain_outbox()` then hands the row to the notification mock (`notify.drain_outbox`), and the scheduler
loop calls the same drain, so an approval that committed is never lost. Nothing else in the API imports
`notify.send`, `notify.emit_message_sent` or the notifier actor (`tests/test_approval_gate.py` scans the
source for that), `message_sent` and `message_approved` are not postable through
`POST /cases/{id}/events` (empty EVENT_POLICY), and the state machine accepts `message_sent` only from
`system:notifier`, for a draft in the case's approved-unsent set, on a channel with consent at that
moment (SEC-2, ADR-030).

Routes (roles in `app.policy.ROUTE_POLICY`, registered below so a missing entry fails closed):

* `POST /cases/{id}/drafts`        primary_care_owner, specialist_owner (the case's owner), coordinator, supervisor
* `GET  /cases/{id}/drafts`        same roles; a clinician only on a case they own
* `POST /drafts/{id}/approve`      primary_care_owner, specialist_owner (the case's owner), supervisor
* `POST /drafts/{id}/reject`       same roles; body {reason, note?} (reason from the A16 taxonomy)
* `GET  /metrics/abstention`       coordinator, supervisor
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app import metrics_abstention, notify, outbox, payloads, policy, users
from app.ai.draft_store import (
    DraftAlreadyDecided,
    DraftNotFound,
    DraftRecord,
    DraftStore,
    InMemoryDraftStore,
    PostgresDraftStore,
    PromptRecord,
)
from app.ai.provider import (
    SYSTEM_PROMPT,
    Channel,
    DraftRequest,
    Language,
    Purpose,
    UnknownProvider,
    build_prompt,
    get_provider,
    prompt_hash,
)
from app.ai.templates import DEFAULT_NEXT_STEP
from app.ai.validate import draft_with_fallback
from app.auth import Identity, require_identity
from app.calendar import default_calendar
from app.clock import clock
from app.events import CaseNotFound, Event, EventIdConflict
from app.routers import cases
from app.state_machine import InvalidTransition

log = logging.getLogger("app.drafts")

router = APIRouter(tags=["drafts"])

# Process-wide draft store for the in-memory event store (unit tests, no-database runs).
# With Postgres the store is built from the event store's connection pool (see `draft_store`).
store: DraftStore = InMemoryDraftStore()
_pg: PostgresDraftStore | None = None
# The send outbox (migration 011): the Postgres table when the event store is Postgres, else in memory.
# The in-memory outbox belongs to the in-memory draft store it was made for (a fresh draft store, as the
# unit tests install, gets a fresh outbox), so no test can drain another's rows.
_mem_outbox: tuple[object, outbox.InMemoryOutbox] | None = None
_pg_outbox: outbox.PostgresOutbox | None = None

DRAFT_ROLES = frozenset({policy.PCO, policy.SPEC, policy.COORD, policy.SUPER})
METRICS_ROLES = frozenset({policy.COORD, policy.SUPER})
# Approving or rejecting a draft is the case owner's decision (or a supervisor's). Never the
# coordinator, the lab, the patient or any automated actor. Owner scoping is checked per case.
DECIDE_ROLES = frozenset({policy.PCO, policy.SPEC, policy.SUPER})

# Fail closed: a route without an entry in ROUTE_POLICY is refused for everyone. Patients, the
# lab and every automated actor (including `model:*`, which cannot authenticate) are not listed.
policy.ROUTE_POLICY[("POST", "/cases/{case_id}/drafts")] = DRAFT_ROLES
policy.ROUTE_POLICY[("GET", "/cases/{case_id}/drafts")] = DRAFT_ROLES
policy.ROUTE_POLICY[("GET", "/metrics/abstention")] = METRICS_ROLES
policy.ROUTE_POLICY[("POST", "/drafts/{draft_id}/approve")] = DECIDE_ROLES
policy.ROUTE_POLICY[("POST", "/drafts/{draft_id}/reject")] = DECIDE_ROLES


def draft_store() -> DraftStore:
    """The Postgres draft store when the event store is Postgres, else the in-memory one."""
    global _pg
    pool = getattr(cases.store, "_pool", None)  # PostgresEventStore keeps its pool here
    if pool is None:
        return store
    if _pg is None or _pg.pool is not pool:
        _pg = PostgresDraftStore(pool)
    return _pg


def outbox_store() -> outbox.Outbox:
    """The Postgres outbox when the event store is Postgres, else the in-memory one."""
    global _pg_outbox, _mem_outbox
    pool = getattr(cases.store, "_pool", None)
    if pool is None:
        if _mem_outbox is None or _mem_outbox[0] is not store:
            _mem_outbox = (store, outbox.InMemoryOutbox())
        return _mem_outbox[1]
    if _pg_outbox is None or _pg_outbox.pool is not pool:
        _pg_outbox = outbox.PostgresOutbox(pool)
    return _pg_outbox


def drain_outbox(only=None, limit: int = 100) -> list[notify.DrainOutcome]:
    """Send every approved draft that has not gone out yet (or only the drafts in `only`). Idempotent;
    called at the end of an approval and by the scheduler loop (`POST /scheduler/outbox/drain`)."""
    def text_of(draft_id: UUID) -> str | None:
        d = draft_store().get(draft_id)
        return d.text if d is not None else None

    return notify.drain_outbox(outbox_store(), text_of, limit=limit, only=only)


class CreateDraft(BaseModel):
    # The requester comes from the bearer token, never from the body.
    model_config = ConfigDict(extra="forbid")

    purpose: Purpose
    channel: Channel
    language: Language


class RejectDraft(BaseModel):
    """A16: the reason is one of the fixed taxonomy (it goes into the log as `message_rejected`).
    `other_with_note` needs a note, any other reason takes none; the note is kept on the draft row
    (erasable), never in the event."""

    model_config = ConfigDict(extra="forbid")

    reason: Literal["inaccurate", "tone", "wrong_language", "not_needed", "other_with_note"]
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > payloads.NOTE_MAX_LENGTH:
            raise ValueError(f"note must be at most {payloads.NOTE_MAX_LENGTH} characters")
        return value or None

    @model_validator(mode="after")
    def _note_matches_reason(self) -> "RejectDraft":
        if self.reason == "other_with_note" and self.note is None:
            raise ValueError("reason other_with_note needs a note")
        if self.reason != "other_with_note" and self.note is not None:
            raise ValueError("a note is only taken with reason other_with_note")
        return self


# ---------------------------------------------------------------- building the request

_WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"],
}
_MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر",
           "نوفمبر", "ديسمبر"],
}


def format_date(value: object, language: str) -> str | None:
    """`Tuesday 3 November 2026` / `الثلاثاء 3 نوفمبر 2026` from an ISO date or datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    day = default_calendar().local_date(parsed) if parsed.tzinfo else parsed.date()
    return f"{_WEEKDAYS[language][day.weekday()]} {day.day} {_MONTHS[language][day.month - 1]} {day.year}"


def appointment_date(events, state: str, language: str) -> str | None:
    """The booked confirmatory date, formatted, while the case is in `confirmatory_booked`."""
    if state != "confirmatory_booked":
        return None
    for e in reversed(events):
        if e.type == "book_confirmatory":
            raw = e.payload.get("appointment_date") or e.payload.get("appointment_at")
            return format_date(raw, language)
    return None


def owner_identity(owner: str | None) -> tuple[str, str] | None:
    """The owner actor (`role:user_id`, opaque since A14) -> (role, display name from the users
    table). The name goes into the draft text, which lives in the drafts table (erasable), never
    into an event."""
    if not owner or ":" not in owner:
        return None
    resolved = users.resolve_actor(owner)
    return resolved["role"], resolved["display_name"]


def build_request(case_id: UUID, body: CreateDraft, identity: Identity) -> DraftRequest:
    pw = cases.pathway_of(case_id)
    model = cases.store.read_model(case_id, pw)
    if model is None:
        raise HTTPException(404, "case not found")
    # G-1: like every other owner action, a clinician drafts only on a case they own (coordinator and
    # supervisor are not owner-scoped). Checked before the 409 states so a non-owner learns nothing from them.
    policy.authorize_owner_scope(identity, "create_draft", model)
    if model.consent_frozen:
        raise HTTPException(409, "outreach is frozen: the patient has withdrawn consent")
    if model.state == pw.terminal_state:
        raise HTTPException(409, "the case is closed: no new drafts")

    # The patient hears from the case owner. A case without an owner yet is drafted in the
    # requester's name (they are the human who will approve it).
    owner_role, owner_name = owner_identity(model.owner) or (identity.role, identity.name)
    date = appointment_date(cases.store.events_for(case_id), model.state, body.language)
    if body.purpose == "appointment_confirmation" and date is None:
        raise HTTPException(409, "no booked appointment date on this case: cannot confirm an appointment")
    try:
        req = DraftRequest(
            purpose=body.purpose,
            channel=body.channel,
            language=body.language,
            owner_name=owner_name,
            owner_role=owner_role,
            next_step=pw.next_step(body.language, DEFAULT_NEXT_STEP),
            pathway_name=pw.name,
            appointment_date=date,
        )
    except ValueError as exc:
        raise HTTPException(422, f"cannot build a message from this case: {exc}") from None
    return req


# ---------------------------------------------------------------- routes


def _texts(language: str, text: str) -> tuple[str | None, str | None]:
    return (text, None) if language == "ar" else (None, text)


def _draft_json(d: DraftRecord) -> dict:
    text_ar, text_en = _texts(d.language, d.text)
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "draft_id": str(d.draft_id),
        "case_id": str(d.case_id),
        "purpose": d.purpose,
        "channel": d.channel,
        "language": d.language,
        "text": d.text,
        "text_ar": text_ar,
        "text_en": text_en,
        "provider": d.provider,
        "model_id": d.model_id,
        "fallback_used": d.fallback_used,
        "abstention_reason": d.abstention_reason,
        "prompt_hash": d.prompt_hash,
        "created_by": d.created_by,
        "created_at": iso(d.created_at),
        "approved_at": iso(d.approved_at),
        "approved_by": d.approved_by,
        "rejected_at": iso(d.rejected_at),
        "rejected_by": d.rejected_by,
        "rejection_reason": d.rejection_reason,
    }


@router.post("/cases/{case_id}/drafts", status_code=201)
def create_draft(
    case_id: UUID, body: CreateDraft, identity: Identity = Depends(require_identity)
) -> dict:
    req = build_request(case_id, body, identity)
    try:
        provider = get_provider()
    except (UnknownProvider, ValueError) as exc:
        log.error("drafting provider misconfigured: %s", exc)
        raise HTTPException(503, "drafting provider is misconfigured") from None

    result = draft_with_fallback(req, provider)  # never raises for a provider problem
    now = clock.now()
    draft_id = uuid4()
    digest = prompt_hash(req)
    text_ar, text_en = _texts(req.language, result.text)

    # Annotation only: the model actor can emit nothing else (T-D2), and the event changes no state.
    event = Event(
        case_id,
        "message_drafted",
        now,
        f"model:{result.provider}",
        {
            "draft_id": str(draft_id),
            "provider": result.provider,
            "fallback_used": result.fallback_used,
            "prompt_hash": digest,
            "language": req.language,
        },
    )
    abstained = result.abstention_reason
    record = DraftRecord(
        draft_id=draft_id,
        case_id=case_id,
        purpose=req.purpose,
        channel=req.channel,
        language=req.language,
        text=result.text,
        provider=result.provider,
        model_id=result.model_id,
        fallback_used=result.fallback_used,
        abstention_reason=abstained,
        abstained_at=now if abstained else None,
        prompt_hash=digest,
        created_by=identity.actor,
        created_at=now,
    )
    prompt = PromptRecord(digest, SYSTEM_PROMPT, build_prompt(req))
    drafts_db = draft_store()
    try:
        # The strict A14 schema and docs/governance/ai-boundary.md: the log holds ids and hashes, never the text.
        payloads.validate(event.type, event.payload)
        # SEC-2: the event and the draft row are ONE transaction (the `within` hook): a failure between them
        # leaves neither, never a `message_drafted` for a draft that does not exist. Owner scope is re-checked
        # under the case lock, on the replayed model (build_request's check was made on an earlier read; the
        # case may have changed hands since).
        cases.store.append(
            event, cases.pathway_of(case_id),
            within=lambda cur: drafts_db.add(record, prompt, cur=cur),
            guard=policy.owner_guard(identity, "create_draft"),
        )
    except payloads.PayloadSchemaError as exc:  # a provider name outside the allowed shape; never a 500
        log.error("message_drafted payload refused: %s", exc)
        raise HTTPException(503, "drafting provider is misconfigured") from None
    except CaseNotFound:
        raise HTTPException(404, "case not found") from None
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from None
    except EventIdConflict:  # cannot happen with a fresh uuid4; do not leak a 500 if it ever does
        raise HTTPException(409, "event id conflict") from None

    return {
        "draft_id": str(draft_id),
        "text": result.text,
        "text_ar": text_ar,
        "text_en": text_en,
        "language": req.language,
        "provider": result.provider,
        "fallback_used": result.fallback_used,
        "abstention_reason": abstained,
        "prompt_hash": digest,
    }


@router.get("/cases/{case_id}/drafts")
def list_drafts(case_id: UUID, identity: Identity = Depends(require_identity)) -> list[dict]:
    """The case's drafts, text included. Owner-scoped like every other owner action (SEC-2): a clinician
    reads the drafts of cases they own; a coordinator or supervisor reads any."""
    model = cases.store.read_model(case_id, cases.pathway_of(case_id))
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "view_drafts", model)
    return [_draft_json(d) for d in draft_store().list_for_case(case_id)]


@router.get("/metrics/abstention")
def abstention_metric() -> dict:
    return metrics_abstention.summarise(draft_store().attempt_counts())


# ---------------------------------------------------------------- the approval gate (D5)

# Why the notification mock did not send, as the API reports it (the detail is on the
# notification row, `GET /cases/{id}/notifications`). `pending`: the drain could not finish yet; the
# approval stands and the scheduler's next drain sends it.
_BLOCKED = {"blocked_consent": "consent", "blocked_contact": "contact", "case_state": "case_state",
            "draft_missing": "case_state"}


def _decidable(draft_id: UUID, identity: Identity) -> tuple[DraftRecord, object]:
    """Load the draft and its case for a decision, or raise 404 / 403 / 409.

    The owner check is the same one the events route uses: a clinician decides only on cases
    they own; a supervisor on any case.
    """
    draft = draft_store().get(draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    model = cases.store.read_model(draft.case_id, cases.pathway_of(draft.case_id))
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "decide_draft", model)
    if draft.approved_at is not None:
        raise HTTPException(409, "draft is already approved")
    if draft.rejected_at is not None:
        raise HTTPException(409, "draft is already rejected")
    return draft, model


@router.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    """Approve a draft and hand it to the notification mock.

    One transaction (Postgres) or one critical section (in-memory) records the approval on the
    draft, appends `message_approved` as the approver and writes the outbox row, so none of the three
    can exist without the others (ADR-030). Only then is the outbox drained: the notification mock
    writes `message_sent` as `system:notifier` and the notification row (one transaction), or reports
    why it did not (consent / contact / the case moved on): the approval stands and the response says
    `sent: false`. If the drain itself fails the row stays pending, the response says so
    (`blocked_reason: "pending"`) and the scheduler's next drain sends it, once.
    """
    draft, model = _decidable(draft_id, identity)
    if model.consent_frozen:
        raise HTTPException(409, "outreach is frozen: the patient has withdrawn consent")
    pw = cases.pathway_of(draft.case_id)
    if model.state not in pw.open_states:
        raise HTTPException(409, "the case is closed: nothing to approve")

    now = clock.now()
    event = Event(draft.case_id, "message_approved", now, identity.actor, {"draft_id": str(draft_id)})
    drafts_db, outbox_db = draft_store(), outbox_store()

    def decide(cur) -> None:
        approved = drafts_db.approve(draft_id, identity.actor, now, cur=cur)
        outbox_db.enqueue(outbox.OutboxItem(draft_id, draft.case_id, approved.channel, approved.language,
                                            approved.purpose, now), cur=cur)

    try:
        cases.store.append(event, pw, within=decide, guard=policy.owner_guard(identity, "decide_draft"))
    except CaseNotFound:
        raise HTTPException(404, "case not found") from None
    except DraftNotFound:
        raise HTTPException(404, "draft not found") from None
    except DraftAlreadyDecided:  # lost a race with another decision: nothing was written
        raise HTTPException(409, "draft already has a decision") from None
    except InvalidTransition as exc:  # e.g. consent withdrawn or the case closed since the read
        raise HTTPException(409, str(exc)) from None
    except EventIdConflict:
        raise HTTPException(409, "event id conflict") from None

    base = {"draft_id": str(draft_id), "approved_by": identity.actor, "approved_at": now.isoformat()}
    try:
        drain_outbox(only=[draft_id])
    except Exception:  # noqa: BLE001 - the approval has committed; whatever failed, the row is still pending
        log.exception("outbox drain after approving draft %s failed", draft_id)
    done = outbox_db.get(draft_id)
    if done is not None and done.status == outbox.SENT:
        return {**base, "sent": True, "blocked_reason": None, "notification_id": str(done.notification_id)}
    if done is not None and done.status == outbox.BLOCKED:
        return {**base, "sent": False, "blocked_reason": _BLOCKED.get(done.reason, done.reason),
                "notification_id": str(done.notification_id) if done.notification_id else None}
    return {**base, "sent": False, "blocked_reason": "pending", "notification_id": None}


@router.post("/drafts/{draft_id}/reject")
def reject_draft(
    draft_id: UUID, body: RejectDraft, identity: Identity = Depends(require_identity)
) -> dict:
    """Reject a draft with a reason from the taxonomy (A16).

    One transaction (Postgres) or one critical section (in-memory) records the rejection on the
    draft and appends `message_rejected {draft_id, reason}` as the rejecting human, so neither
    exists without the other and the rejection shows on the case timeline. Allowed on a frozen
    case (it can only stop outreach); a closed case takes no annotation, so it answers 409.
    """
    draft, model = _decidable(draft_id, identity)
    if model.state not in cases.pathway_of(draft.case_id).open_states:
        raise HTTPException(409, "the case is closed: nothing to reject")
    now = clock.now()
    stored_reason = body.reason if body.note is None else f"{body.reason}: {body.note}"
    event = Event(draft.case_id, "message_rejected", now, identity.actor,
                  {"draft_id": str(draft_id), "reason": body.reason})
    drafts_db = draft_store()
    try:
        payloads.validate(event.type, event.payload)
        cases.store.append(
            event, cases.pathway_of(draft.case_id),
            within=lambda cur: drafts_db.reject(draft_id, identity.actor, now, stored_reason, cur=cur),
            guard=policy.owner_guard(identity, "decide_draft"),
        )
    except payloads.PayloadSchemaError as exc:  # cannot happen with a validated body; never a 500
        raise HTTPException(422, str(exc)) from None
    except CaseNotFound:
        raise HTTPException(404, "case not found") from None
    except DraftNotFound:
        raise HTTPException(404, "draft not found") from None
    except DraftAlreadyDecided:  # lost a race with another decision: nothing was written
        raise HTTPException(409, "draft already has a decision") from None
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from None
    except EventIdConflict:
        raise HTTPException(409, "event id conflict") from None
    return {"draft_id": str(draft_id), "rejected_by": identity.actor, "rejected_at": now.isoformat(),
            "reason": body.reason, "note": body.note}
