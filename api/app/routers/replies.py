"""Inbound patient replies (D13): store the text, classify it, log it, and take the safe automatic step.

    POST /cases/{id}/replies   coordinator   {channel, text, language_hint?, reply_id?}
    GET  /cases/{id}/replies   coordinator, supervisor   the stored replies of the case, with the text

`POST` does, in this order: classify the text (`app.triage.classify`, a small model running on this machine, or the
fail-safe answer "a person must look" when the model is not there); store the text and the classification in
`patient_replies` (migration 013); append `reply_received {channel, text_ref}` and `reply_classified {intent,
confidence, model_id, needs_human, text_ref}` to the case as `system:triage` (the text is never in the log); and then
at most ONE of the following effects, none of which changes the state of the case or closes anything:

    confirm                     nothing beyond the log
    reschedule, cannot_attend   callback_requested {reason: reschedule | cannot_attend}
    dont_understand            callback_requested {reason: patient_does_not_understand}
    wrong_number                review_requested {reason: wrong_number}
    already_done                review_requested {reason: already_done}
    opt_out                     review_requested {reason: opt_out}    (never withdraws consent: only a person does that)
    anything else a person must look at (question, other, low confidence, no model)
                                review_requested {reason: triage_uncertain}

`needs_human` replies get a `review_requested`, never a callback: when the system is not sure, it asks a person to
read the message instead of guessing what the patient wants. A retry with the same `reply_id` is the same reply: the
stored classification is reused and every event id derives from `reply_id`, so nothing is written twice.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import payloads
from app.auth import Identity, require_identity
from app.clock import clock
from app.events import CaseNotFound, Event, EventContentConflict, EventIdConflict
from app.routers import cases
from app.state_machine import TRIAGE_ACTOR, InvalidTransition
from app.triage import classify as triage
from app.triage import dialect
from app.triage.reply_store import InMemoryReplyStore, PostgresReplyStore, ReplyRecord, ReplyStore

log = logging.getLogger("app.replies")
router = APIRouter(tags=["replies"])

# Process-wide store for the in-memory event store; with Postgres it is built from the event store's pool.
store: ReplyStore = InMemoryReplyStore()
_pg: PostgresReplyStore | None = None

REPLIES_NAMESPACE = uuid.UUID("6f1d0c7a-3a52-4b91-8d1e-0c4a9e5b7d23")  # never change: event ids derive from it

# what each intent triggers when the system may act on its own (needs_human is false)
CALLBACK_REASON = {"reschedule": "reschedule", "cannot_attend": "cannot_attend", "dont_understand": "patient_does_not_understand"}
REVIEW_REASON = {"wrong_number": "wrong_number", "already_done": "already_done"}


def reply_store() -> ReplyStore:
    global _pg
    pool = getattr(cases.store, "_pool", None)
    if pool is None:
        return store
    if _pg is None or _pg.pool is not pool:
        _pg = PostgresReplyStore(pool)
    return _pg


class PostReply(BaseModel):
    # The sender, the case and the time come from the identity, the path and the simulation clock, never the body.
    model_config = ConfigDict(extra="forbid")

    channel: Literal["sms", "whatsapp", "phone", "letter"]
    text: str = Field(min_length=1, max_length=4000)
    language_hint: Literal["en", "ar"] | None = None
    reply_id: UUID | None = None  # optional idempotency key for client retries


def event_id(reply_id: UUID, kind: str) -> UUID:
    return uuid.uuid5(REPLIES_NAMESPACE, f"{reply_id}|{kind}")


def effect_for(result: dict) -> tuple[str, dict] | None:
    """The one automatic step for a classification: (event type, payload without source_event_id), or None.

    A reply a person must look at gets a review request, whatever the intent; only a confident, auto-handled
    intent gets its own step. Nothing here is clinical and nothing closes or changes the state of a case."""
    intent = result["intent"]
    if result["needs_human"]:
        return "review_requested", {"reason": "opt_out" if intent == "opt_out" else "triage_uncertain"}
    if intent in CALLBACK_REASON:
        return "callback_requested", {"reason": CALLBACK_REASON[intent]}
    if intent in REVIEW_REASON:
        return "review_requested", {"reason": REVIEW_REASON[intent]}
    return None  # confirm


def _append(case_id: UUID, kind: str, payload: dict, eid: UUID, at) -> None:
    payloads.validate(kind, payload)  # a bug here must not put free text in the log either
    cases.store.append(Event(case_id, kind, at, TRIAGE_ACTOR, payload, eid), cases.pathway)


@router.post("/cases/{case_id}/replies", status_code=201)
def post_reply(case_id: UUID, body: PostReply, identity: Identity = Depends(require_identity)) -> dict:
    state = cases.store.current_state(case_id)
    if state is None:
        raise HTTPException(404, "case not found")
    replies = reply_store()
    reply_id = body.reply_id or uuid4()
    record = replies.get(reply_id)
    if record is not None and record.case_id != case_id:
        raise HTTPException(409, "reply_id already used by another case")
    if record is None:
        result = triage.classify(body.text, body.language_hint)
        closed = state == cases.pathway.terminal_state
        record = replies.add(ReplyRecord(
            reply_id, case_id, body.channel, body.text, body.language_hint, result["intent"], result["confidence"],
            result["model_id"], result["needs_human"], not closed, clock.now(), identity.actor,
        ))
    # From here on only the STORED record is used, so a retry (or a lost race) behaves like the first call.
    result = {"intent": record.intent, "confidence": record.confidence, "model_id": record.model_id,
              "needs_human": record.needs_human}
    applied: list[str] = []
    if record.logged:
        at = record.received_at
        try:
            _append(case_id, "reply_received", {"channel": record.channel, "text_ref": str(reply_id)},
                    event_id(reply_id, "reply_received"), at)
            applied.append("reply_received")
            classified_id = event_id(reply_id, "reply_classified")
            _append(case_id, "reply_classified", {**result, "text_ref": str(reply_id)}, classified_id, at)
            applied.append("reply_classified")
            effect = effect_for(result)
            if effect is not None:
                kind, payload = effect
                _append(case_id, kind, {**payload, "source_event_id": str(classified_id)}, event_id(reply_id, kind), at)
                applied.append(kind)
        except CaseNotFound:
            raise HTTPException(404, "case not found")
        except (EventContentConflict, EventIdConflict):
            raise HTTPException(409, "reply_id already used by a different reply")
        except InvalidTransition as exc:
            # The case closed between the check and the write. The text is stored and must still be read.
            log.warning("reply %s stored but not logged: %s", reply_id, exc)
    return {
        "reply_id": str(reply_id), "case_id": str(case_id), "channel": record.channel, **result,
        "logged": bool(applied), "applied": applied,
        # additive, display only: the language group whose rule (dialect.py, HZ-43) is why a person reads this reply, else null
        "forced_reason": dialect.group_reason(result, record.text),
        "note": None if applied else "the case is closed: the reply is stored for a person to read, nothing was logged",
    }


@router.get("/cases/{case_id}/replies")
def list_replies(case_id: UUID, identity: Identity = Depends(require_identity)) -> list[dict]:
    """The case's stored replies, oldest first, with the text: for the person who has to read a flagged one."""
    if cases.store.current_state(case_id) is None:
        raise HTTPException(404, "case not found")
    return [r.to_json() for r in reply_store().list_for_case(case_id)]
