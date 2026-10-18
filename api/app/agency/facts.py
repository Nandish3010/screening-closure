"""What an agent may know about a case: ids, refs, labels, counts and times. Never text (A22, ADR-033).

`case_facts` reads a case's log and read model (the same replay the case routes use) and keeps only fields of a
closed vocabulary. A note (the one free-text field a person may add to an event), a reply's text, a draft's text,
a patient's or a clinician's name: none of them is copied. This is the only view of a case that reaches a
planner, so an instruction smuggled into a patient reply or a note cannot reach one (hazard HZ-23): the
planner sees `pending_replies: [<event id>]` and, once classified, the classifier's labels.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.agency import compat
from app.calendar import default_calendar
from app.events import LoadedCase
from app.pathway import Pathway
from app.readmodel import current_due, milestones_json
from app.state_machine import actor_role, is_automated

Iso = str


class HandoffTried(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_provider: str = Field(max_length=100)
    outcome: Literal["pending", "accepted", "rejected", "bounced"]
    reason: str | None = Field(default=None, max_length=40)  # the taxonomy code, never a note


class MilestoneFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_at: Iso | None
    met: bool
    overdue: bool


class AutoMessage(BaseModel):
    """An automatic template send (A21 `message_sent {auto: true}`): its purpose and time, never its text."""

    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(max_length=40)
    at: Iso


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(max_length=40)
    at: Iso
    by: str = Field(max_length=40)  # the actor's role (`primary_care_owner`, `system`...), never who


class CaseFacts(BaseModel):
    """Strict: an unknown key is an error, so nothing new can slip in without this class changing."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    state: str
    open: bool
    overdue: bool
    escalation_level: int
    escalation_accepted: bool
    owner: str | None  # the opaque actor `role:user_id`, never a name
    owner_role: str | None
    provider: str | None
    consent_frozen: bool
    channels_allowed: list[Literal["sms", "whatsapp", "phone", "letter"]]
    share_with_specialist: bool
    pending_handoff_to: str | None
    handoffs: list[HandoffTried]
    due_at: Iso | None
    milestones: dict[str, MilestoneFact]
    watchdog_flagged: bool
    contact_attempts: int
    messages_sent: int
    last_message_sent_at: Iso | None
    auto_messages: list[AutoMessage]
    language: Literal["en", "ar"] | None
    appointment_at: Iso | None
    result_ref: str | None
    callback_requested_at: Iso | None
    last_human_activity_at: Iso | None
    pending_replies: list[str]  # event ids of replies with no classification yet (D13); never their text
    reply_labels: list[dict]  # the classifier's labels {event_id, intent, needs_human}, never the text
    closure_proposed: bool
    review_reason: str | None
    escalations: int
    patient_ref: str | None  # an opaque FHIR reference (`Patient/<id>`), what the evidence check needs
    history: list[HistoryItem]  # the state-changing steps in order: type, time, role
    events: int
    last_event_seq: int


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def case_facts(lc: LoadedCase, pathway: Pathway) -> CaseFacts | None:
    m = lc.model
    if m is None:
        return None
    calendar = default_calendar()
    handoffs: list[HandoffTried] = []
    contact_attempts = messages_sent = 0
    last_sent = language = appointment = result_ref = callback_at = None
    replies: dict[str, bool] = {}
    labels: list[dict] = []
    closure_proposed = False
    escalations = 0
    autos: list[AutoMessage] = []
    history: list[HistoryItem] = []
    steps = {t.event for t in pathway.transitions} if hasattr(pathway, "transitions") else set()
    for e in lc.events:
        if e.type == "escalate" and not (e.payload or {}).get("attached_to"):
            escalations += 1
        if e.type in steps or e.type == "case_created":
            history.append(HistoryItem(type=e.type, at=e.at.isoformat(),
                                       by="system" if is_automated(e.actor) else actor_role(e.actor)[:40]))
        p = e.payload or {}
        if e.type == "handoff_requested" and isinstance(p.get("to_provider"), str):
            handoffs.append(HandoffTried(to_provider=p["to_provider"][:100], outcome="pending"))
        elif e.type in ("handoff_accepted", "handoff_rejected") and handoffs and handoffs[-1].outcome == "pending":
            outcome = "accepted" if e.type == "handoff_accepted" else ("bounced" if p.get("bounced") else "rejected")
            reason = p.get("reason") if isinstance(p.get("reason"), str) else None
            handoffs[-1] = HandoffTried(to_provider=handoffs[-1].to_provider, outcome=outcome,
                                        reason=reason[:40] if reason else None)
        elif e.type == "contact_patient":
            contact_attempts += 1
        elif e.type == "message_sent":
            messages_sent += 1
            last_sent = e.at
            if p.get("auto") is True:
                autos.append(AutoMessage(purpose=str(p.get("purpose") or "")[:40], at=e.at.isoformat()))
        elif e.type in ("patient_acknowledge", "callback_requested") and p.get("lang") in ("en", "ar"):
            language = p["lang"]
        if e.type == "book_confirmatory":
            appointment = p.get("appointment_at") or p.get("appointment_date")
        elif e.type == "complete_confirmatory" and isinstance(p.get("result_ref"), str):
            result_ref = p["result_ref"]
        elif e.type == "callback_requested":
            callback_at = e.at
        elif e.type == compat.REPLY_RECEIVED:
            replies[str(e.event_id)] = False
        elif e.type == compat.REPLY_CLASSIFIED:
            ref = p.get("reply_event_id") or p.get("source_event_id")
            target = str(ref) if ref else next((k for k, done in reversed(replies.items()) if not done), None)
            if target in replies:
                replies[target] = True
            labels.append({"event_id": target, "intent": str(p.get("intent"))[:40] if p.get("intent") else None,
                           "needs_human": bool(p.get("needs_human"))})
        elif e.type == compat.CLOSURE_PROPOSED:
            closure_proposed = True
    milestones = {
        name: MilestoneFact(due_at=v["due_at"], met=v["met_at"] is not None, overdue=v["overdue"])
        for name, v in milestones_json(m, pathway, calendar).items()
    }
    consent = m.consent or {}
    channels = [c for c in ("sms", "whatsapp", "phone", "letter") if consent.get(f"channel_{c}", True)]
    owner_role = actor_role(m.owner) if m.owner and not is_automated(m.owner) else None
    return CaseFacts(
        case_id=str(lc.case_id),
        state=m.state,
        open=m.state in pathway.open_states,
        overdue=m.any_overdue,
        escalation_level=max([m.escalation_level, *(x.escalation_level for x in m.milestones.values())]),
        escalation_accepted=m.escalation_accepted,
        owner=m.owner,
        owner_role=owner_role,
        provider=m.provider,
        consent_frozen=m.consent_frozen,
        channels_allowed=channels,
        share_with_specialist=bool(consent.get("share_with_specialist", True)),
        pending_handoff_to=(m.pending_handoff or {}).get("to_provider"),
        handoffs=handoffs,
        due_at=_iso(current_due(m, pathway, calendar)),
        milestones=milestones,
        watchdog_flagged=m.watchdog_flag is not None,
        contact_attempts=contact_attempts,
        messages_sent=messages_sent,
        last_message_sent_at=_iso(last_sent),
        auto_messages=autos[-20:],
        language=language,
        appointment_at=appointment if isinstance(appointment, str) else None,
        result_ref=result_ref,
        callback_requested_at=_iso(callback_at),
        last_human_activity_at=_iso(m.last_human_activity_at),
        pending_replies=[k for k, done in replies.items() if not done],
        reply_labels=labels,
        closure_proposed=closure_proposed,
        review_reason=m.review_reason,
        escalations=escalations,
        patient_ref=lc.patient_ref,
        history=history[-60:],
        events=len(lc.events),
        last_event_seq=max((e.seq or 0) for e in lc.events) if lc.events else 0,
    )


def facts_for(case_id: UUID, store, pathway: Pathway) -> CaseFacts | None:
    """One case's facts, read through the store like `GET /cases/{id}`."""
    from app.events import replay_model

    events = store.events_for(case_id)
    if not events:
        return None
    try:
        model = replay_model(events, pathway)
    except Exception:  # noqa: BLE001 - a case that does not replay is reported by the metrics route, not here
        return None
    return case_facts(LoadedCase(case_id, pathway.name, events[0].at, events, model), pathway)
