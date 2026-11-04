"""The coordinator's Today screen (C14): what needs a person now, in seven queues, from the read model.

Pure functions over a case's events and read model (`app.events.LoadedCase`), like `app.readmodel`. Nothing here writes, reads a
clock ("now" is passed in) or decides care: it only says which of the queues a case belongs to and why, in codes the web turns
into plain words. Every membership rule is a fact of the replayed model, so the answer is the same one the worklist row and the
case page give; `api/tests/test_today.py` proves that on seeded data.

The queues (`GROUPS`, in the order they are shown):

    urgent       the pathway's top priority tier, open, and no person has acted on the case yet
    overdue      the state SLA or a milestone is breached (the worklist's `overdue`)
    escalations  a rung of an escalation ladder is waiting for a person to accept it
    replies      a patient's reply the triage could not handle (the triage flag), or a call-back the patient asked for, and no
                 person has acted on the case since
    flagged      the watchdog's flag: nobody has looked at the case for the configured idle days
    signatures   a closure waiting for a physician's signature: the evidence scan's proposal, or a declined closure
    agency       tasks an agent prepared for a person (the agency ledger, status `needs_human`; not a case scan)

A case can be in several queues. A closed or erased case is in none.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app import readmodel
from app.calendar import WorkingCalendar
from app.events import LoadedCase
from app.pathway import Pathway
from app.state_machine import TRIAGE_ACTOR, is_automated

CASE_GROUPS = ("urgent", "overdue", "escalations", "replies", "flagged", "signatures")
AGENCY_GROUP = "agency"
GROUPS = (*CASE_GROUPS, AGENCY_GROUP)

# `contact_patient` is what ends a call-back (the case page's own rule: `hasCallbackPending`).
CALLBACK = "callback_requested"
CONTACT = "contact_patient"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if isinstance(dt, datetime) else str(dt)


def _whole_days(since: datetime | None, now: datetime) -> int:
    if since is None:
        return 0
    return max(0, (_aware(now) - _aware(since)).days)


def is_open(lc: LoadedCase, pathway: Pathway) -> bool:
    m = lc.model
    return m is not None and not m.erased and m.state != pathway.terminal_state


def acted_after_flag(events) -> bool:
    """A person has written an event on the case after the triage flag was raised. By position in the log, not by time: the
    sim clock gives a reply and the next click the same instant."""
    flag_at = max((i for i, e in enumerate(events) if e.type == "review_requested" and e.actor == TRIAGE_ACTOR), default=None)
    return flag_at is not None and any(not is_automated(e.actor) for e in events[flag_at + 1:])


def pending_escalation(m, pathway: Pathway) -> dict | None:
    """The first escalation ladder (the state's, then each milestone's) whose latest rung nobody has accepted, with the rung."""
    ladders = [(None, m.escalation_level, m.escalation_accepted, m.escalation_at)]
    ladders += [(name, ms.escalation_level, ms.escalation_accepted, ms.escalation_at) for name, ms in m.milestones.items()]
    for milestone, level, accepted, at in ladders:
        if level > 0 and not accepted:
            rung = pathway.rung_at(level)
            if rung is None:
                continue
            return {"rung": rung.rung, "level": level, "target": rung.target, "accepted_by": list(rung.accepted_by),
                    "milestone": milestone, "at": _iso(at)}
    return None


def pending_callback(events) -> dict | None:
    """The patient's latest call-back request that no person has answered with a recorded contact."""
    pending = None
    for e in events:
        if e.type == CALLBACK:
            pending = {"reason": e.payload.get("reason"), "at": e.at.isoformat()}
        elif e.type == CONTACT:
            pending = None
    return pending


def latest_classification(events) -> dict | None:
    """The latest `reply_classified` payload that sent the reply to a person (`needs_human`), with the event's time."""
    for e in reversed(events):
        if e.type == "reply_classified" and e.payload.get("needs_human") is True:
            return {**e.payload, "at": e.at.isoformat()}
    return None


def memberships(lc: LoadedCase, pathway: Pathway, calendar: WorkingCalendar, now: datetime) -> dict[str, dict]:
    """`{group: item}` for the case: the queues it is in now. Empty for a closed case or a log that does not replay.

    An item is `{case_id, pathway, state, priority, owner, provider, due_at, last_event_at, since, why, actions}`. `since` is when
    the case started needing a person for this queue (queues are shown longest-waiting first). `why` is `{code, ...}`; the web
    words it. `actions` lists the one-click actions that fit the case (ids the web knows); who may do them is the web's and the
    API's RBAC, not this module's."""
    m = lc.model
    if m is None or not is_open(lc, pathway):
        return {}
    row = readmodel.case_row(lc, pathway, calendar)
    if row is None:
        return {}
    prio = row["priority"]
    base = {
        "case_id": row["case_id"], "pathway": row["pathway"], "state": row["state"],
        "priority": prio,  # {tier, rank, rule, reason: {en, ar}, basis}: the worklist row's own
        "owner": row["owner"], "provider": row["provider"], "due_at": row["due_at"], "last_event_at": row["last_event_at"],
    }
    can_take = bool(pathway.transitions_for(m.state, "accept_ownership"))
    out: dict[str, dict] = {}

    def put(group: str, since, why: dict, actions: list[str], **extra) -> None:
        out[group] = {**base, "since": _iso(since) or lc.created_at.isoformat(), "why": why, "actions": actions, **extra}

    # 1. urgent and no person has acted on it yet (only where the pack ranks more than one tier). The genesis event counts as
    # "human activity" for the watchdog's idle clock, so "nobody has acted" is: the last human activity IS the genesis event.
    untouched = m.last_human_activity_event_id == m.created_event_id
    config = pathway.priority
    if prio and config is not None and len(config.tiers) > 1 and prio["rank"] == 0 and untouched:
        put("urgent", lc.created_at, {"code": "urgent_untouched", "tier": prio["tier"], "days": _whole_days(lc.created_at, now)},
            ["open_case", *(["take_ownership"] if can_take else [])])

    # 2. overdue
    if m.any_overdue:
        due = readmodel.current_due(m, pathway, calendar)
        out_days = _whole_days(due, now)
        put("overdue", due, {"code": "overdue", "days": out_days}, ["open_case"], days_overdue=out_days)

    # 3. an escalation waiting for a person to accept it
    esc = pending_escalation(m, pathway)
    if esc is not None:
        put("escalations", esc["at"], {"code": "escalation_waiting", "rung": esc["rung"], "target": esc["target"], "level": esc["level"]},
            ["accept_escalation", "open_case"], escalation=esc)

    # 4. a reply, or a call-back, waiting for a person
    flag = m.triage_review_reason
    waiting_flag = flag is not None and not acted_after_flag(lc.events)
    cb = pending_callback(lc.events)
    if waiting_flag or cb is not None:
        since = m.triage_review_requested_at if waiting_flag else cb["at"]
        actions = ["open_case"]
        if waiting_flag and flag == "wrong_number":
            actions.insert(0, "confirm_contact")
        if not waiting_flag and cb is not None and pathway.transitions_for(m.state, CONTACT):
            actions.insert(0, "record_callback")
        why = {"code": "reply_waiting" if waiting_flag else "callback_waiting", "reason": flag if waiting_flag else cb["reason"]}
        put("replies", since, why, actions, reply={
            "reason": flag if waiting_flag else None, "callback": cb is not None,
            "holds": list(m.outreach_holds), "classification": latest_classification(lc.events) if waiting_flag else None,
        })

    # 5. flagged by the safety net
    if m.watchdog_flag:
        wf = m.watchdog_flag
        put("flagged", wf.get("at"), {"code": "nobody_looking", "reason": wf.get("reason"), "idle_days": wf.get("idle_days")},
            ["open_case", *(["take_ownership"] if can_take else [])], flag={k: wf.get(k) for k in ("reason", "idle_days", "at")})

    # 6. a closure waiting for a physician's signature
    if m.closure_proposal:
        put("signatures", m.closure_proposal.get("proposed_at"),
            {"code": "closure_proposed", "evidence_ref": m.closure_proposal.get("evidence_ref")}, ["open_case"])
    elif m.state == "declined_pending_signoff":
        rec = m.refusal or {}
        put("signatures", rec.get("recorded_at"), {"code": "declined_signoff"}, ["open_case"])
    return out


def order_key(item: dict) -> tuple:
    """Longest-waiting first, then by case id, so a page is stable."""
    return (item["since"], item["case_id"])
