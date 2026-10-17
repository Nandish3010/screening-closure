"""AI message drafts (D4) and the abstention metric (D11).

The model drafts, a human approves (docs/governance/ai-boundary.md). This module holds the only
route that calls a drafting provider. It never changes a case's state: it appends the
annotation `message_drafted` as the automated actor `model:<provider>`, and stores the draft,
the exact prompt and the provider as provenance. Approval and sending are D5.

Routes (roles in `app.policy.ROUTE_POLICY`, registered below so a missing entry fails closed):

* `POST /cases/{id}/drafts`  primary_care_owner, specialist_owner, coordinator, supervisor
* `GET  /cases/{id}/drafts`  same roles
* `GET  /metrics/abstention` coordinator, supervisor
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app import metrics_abstention, policy
from app.ai.draft_store import (
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

DRAFT_ROLES = frozenset({policy.PCO, policy.SPEC, policy.COORD, policy.SUPER})
METRICS_ROLES = frozenset({policy.COORD, policy.SUPER})

# Fail closed: a route without an entry in ROUTE_POLICY is refused for everyone. Patients, the
# lab and every automated actor (including `model:*`, which cannot authenticate) are not listed.
policy.ROUTE_POLICY[("POST", "/cases/{case_id}/drafts")] = DRAFT_ROLES
policy.ROUTE_POLICY[("GET", "/cases/{case_id}/drafts")] = DRAFT_ROLES
policy.ROUTE_POLICY[("GET", "/metrics/abstention")] = METRICS_ROLES


def draft_store() -> DraftStore:
    """The Postgres draft store when the event store is Postgres, else the in-memory one."""
    global _pg
    pool = getattr(cases.store, "_pool", None)  # PostgresEventStore keeps its pool here
    if pool is None:
        return store
    if _pg is None or _pg.pool is not pool:
        _pg = PostgresDraftStore(pool)
    return _pg


class CreateDraft(BaseModel):
    # The requester comes from the bearer token, never from the body.
    model_config = ConfigDict(extra="forbid")

    purpose: Purpose
    channel: Channel
    language: Language


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


def split_actor(actor: str | None) -> tuple[str, str] | None:
    """`role:name` -> (role, name)."""
    if not actor or ":" not in actor:
        return None
    role, name = actor.split(":", 1)
    return role, name


def build_request(case_id: UUID, body: CreateDraft, identity: Identity) -> DraftRequest:
    model = cases.store.read_model(case_id, cases.pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    if model.consent_frozen:
        raise HTTPException(409, "outreach is frozen: the patient has withdrawn consent")
    if model.state == cases.pathway.terminal_state:
        raise HTTPException(409, "the case is closed: no new drafts")

    # The patient hears from the case owner. A case without an owner yet is drafted in the
    # requester's name (they are the human who will approve it).
    owner_role, owner_name = split_actor(model.owner) or (identity.role, identity.name)
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
            next_step=DEFAULT_NEXT_STEP[body.language],
            pathway_name=cases.pathway.name,
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
            "text_ar": text_ar,
            "text_en": text_en,
            "provider": result.provider,
            "fallback_used": result.fallback_used,
            "prompt_hash": digest,
        },
    )
    try:
        cases.store.append(event, cases.pathway)
    except CaseNotFound:
        raise HTTPException(404, "case not found") from None
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from None
    except EventIdConflict:  # cannot happen with a fresh uuid4; do not leak a 500 if it ever does
        raise HTTPException(409, "event id conflict") from None

    abstained = result.abstention_reason
    draft_store().add(
        DraftRecord(
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
        ),
        PromptRecord(digest, SYSTEM_PROMPT, build_prompt(req)),
    )
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
def list_drafts(case_id: UUID) -> list[dict]:
    if cases.store.current_state(case_id) is None:
        raise HTTPException(404, "case not found")
    return [_draft_json(d) for d in draft_store().list_for_case(case_id)]


@router.get("/metrics/abstention")
def abstention_metric() -> dict:
    return metrics_abstention.summarise(draft_store().attempt_counts())
