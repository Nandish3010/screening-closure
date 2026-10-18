"""Read-side shapes for the case routes (A9): the worklist row and the timeline.

Pure functions over a case's events and read model. Nothing here writes, and nothing reads a
clock: "now" is passed in (the sim clock).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app import users
from app.calendar import WorkingCalendar
from app.events import Event, LoadedCase
from app.pathway import Pathway
from app.state_machine import CASE_CREATED, CaseModel, genesis, initial_model, step
from app.state_machine import _aware as _utc
from app.sweep import due_at_for, milestone_due


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def event_json(e: Event) -> dict:
    return {
        "seq": e.seq,
        "event_id": str(e.event_id),
        "type": e.type,
        "at": e.at.isoformat(),
        "actor": e.actor,
        "payload": e.payload,
    }


def milestone_due_dates(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> dict[str, datetime | None]:
    """Each milestone's due date: the breach's own once overdue, else computed from its start. None where the
    milestone has not started or the case closed without meeting it (void). A met milestone keeps its due date."""
    out: dict[str, datetime | None] = {}
    for name, d in pathway.milestones.items():
        ms = model.milestones.get(name)
        if ms is None or ms.started_at is None or ms.void:
            out[name] = None
        elif ms.overdue and ms.breach_due_at is not None:
            out[name] = ms.breach_due_at
        else:
            out[name] = milestone_due(ms.started_at, d.working_days, calendar)
    return out


def milestones_json(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> dict:
    """The case page's `milestones` (A18): {name: {due_at, met_at, overdue, ...}} for every milestone of the pathway.
    `overdue` is the milestone's own breach flag (true from the sweep's `mark_overdue` until the milestone is met);
    `met_late` says a met milestone was met after its due date."""
    dues = milestone_due_dates(model, pathway, calendar)
    out = {}
    for name, d in pathway.milestones.items():
        ms = model.milestones.get(name)
        due = dues[name]
        met_late = None
        if ms is not None and ms.met_at is not None and due is not None:
            met_late = _utc(ms.met_at) > _utc(due)
        out[name] = {
            "due_at": iso(due),
            "met_at": iso(ms.met_at) if ms else None,
            "overdue": bool(ms and ms.overdue),
            "started_at": iso(ms.started_at) if ms else None,
            "working_days": d.working_days,
            "met_late": met_late,
            "void": bool(ms and ms.void),
            "escalation_level": ms.escalation_level if ms else 0,
        }
    return out


def current_due(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> datetime | None:
    """The next deadline on the case: the earliest of the current state's SLA expiry (the breach's due date once
    overdue) and every running milestone's due date (A18); None where nothing applies (closed, consent withdrawn
    with no running milestone)."""
    if model.overdue and model.breach_due_at is not None:
        state_due = model.breach_due_at
    else:
        state_due = due_at_for(model, pathway, calendar)
    running = [
        due for name, due in milestone_due_dates(model, pathway, calendar).items()
        if due is not None and (ms := model.milestones.get(name)) is not None and ms.met_at is None
    ]
    candidates = [d for d in (state_due, *running) if d is not None]
    return min(candidates, key=_utc) if candidates else None


def watchdog_flag_json(model: CaseModel) -> dict | None:
    flag = model.watchdog_flag
    if not flag:
        return None
    out = {"reason": flag["reason"], "at": flag["at"]}
    if flag.get("idle_days") is not None:
        out["idle_days"] = flag["idle_days"]
    return out


def case_row(lc: LoadedCase, pathway: Pathway, calendar: WorkingCalendar) -> dict | None:
    """One row of `GET /cases`. None for a case whose log does not replay (it has no read model)."""
    m = lc.model
    if m is None:
        return None
    return {
        "case_id": str(lc.case_id),
        "pathway": lc.pathway,
        "state": m.state,
        "created_at": iso(lc.created_at),
        "updated_at": iso(m.last_event_at),
        "patient_ref": lc.patient_ref,
        "owner": users.resolve_actor(m.owner),
        "provider": m.provider,
        "due_at": iso(current_due(m, pathway, calendar)),
        "overdue": m.any_overdue,  # the state SLA or any milestone (A18)
        "escalation_level": max([m.escalation_level, *(x.escalation_level for x in m.milestones.values())]),
        "consent_frozen": m.consent_frozen,
        "last_event_at": iso(m.last_event_at),
        "watchdog_flagged": watchdog_flag_json(m),
        "pending_handoff": dict(m.pending_handoff) if m.pending_handoff else None,
        "risk_score": None,  # D9 fills this; the worklist falls back to SLA order without it
    }


def matches(row: dict, *, owner=None, overdue=None, provider=None, flagged=None) -> bool:
    """The `GET /cases` filters other than `state` (applied by the store)."""
    if owner:
        o = row["owner"]
        if o is None:
            return False
        # F8-1: opaque identifiers only (the actor `role:user_id`, or its bare user id), never the display
        # name: a name in the query string would reach the access log, and a name is not an identity.
        _, user_id = users.split_actor(o["actor"])
        if owner not in (o["actor"], user_id):
            return False
    if overdue is not None and row["overdue"] is not overdue:
        return False
    if provider is not None and row["provider"] != provider:
        return False
    if flagged is not None and (row["watchdog_flagged"] is not None) is not flagged:
        return False
    return True


# ---- timeline ------------------------------------------------------------------------------------


def timeline(
    events: list[Event], pathway: Pathway, calendar: WorkingCalendar, sim_time: datetime
) -> dict:
    """Every event plus the items derived from them, in one list ordered by `at`.

    Items (`kind`): `event` (the raw log row), `sla_started` (a state's SLA clock began; `at` is
    the clock start, which is the referral acceptance for the confirmatory SLA), `sla_breached`
    (`at` is the due date, `detected_at` when the sweep recorded it), `escalation` (a ladder rung),
    `handoff_bounce` (the scheduler returned an unanswered referral), `watchdog_flag` (idle
    case; `cleared_at` once anything else happened), `milestone_started` / `milestone_breached` / `milestone_met`
    (A18: a milestone clock began, was breached at its due date, or was met; `late` on the last says it was met
    after the due date) and `backdated` (T-F4, ADR-027: an event accepted
    with an `at` earlier than the previous event's; `behind_seconds` says by how much). Derived items are computed from the log, not
    stored. Ties are broken by the source event's position, the event first.
    """
    sortable: list[tuple[datetime, int, int, dict]] = []

    def add(at: datetime, seq: int, sub: int, item: dict) -> None:
        sortable.append((at, seq, sub, item))

    model = initial_model(pathway)
    last_sla: tuple | None = None
    seen_ms: dict[str, tuple] = {}  # milestone -> (started_at, met_at) already turned into items
    open_flag: dict | None = None
    last_escalation: dict | None = None
    last_rung_by_scope: dict[str, dict] = {}  # "state" or a milestone name -> its latest escalation item
    previous: Event | None = None
    for ev in events:
        seq = ev.seq or 0
        before = model
        model = genesis(model, ev, pathway) if ev.type == CASE_CREATED else step(model, ev, pathway)
        add(ev.at, seq, 0, {"kind": "event", **event_json(ev)})
        for name, d in pathway.milestones.items():
            ms = model.milestones.get(name)
            if ms is None:
                continue
            if ms.started_at is not None and seen_ms.get(name, (None, None))[0] != ms.started_at:
                add(ms.started_at, seq, 1, {
                    "kind": "milestone_started", "at": ms.started_at.isoformat(), "milestone": name,
                    "working_days": d.working_days, "from_event": d.from_event, "until_event": d.until_event,
                    "due_at": iso(milestone_due(ms.started_at, d.working_days, calendar)),
                })
            if ms.met_at is not None and seen_ms.get(name, (None, None))[1] is None:
                due_met = milestone_due(ms.started_at, d.working_days, calendar) if ms.started_at else None
                add(ms.met_at, seq, 1, {
                    "kind": "milestone_met", "at": ms.met_at.isoformat(), "milestone": name,
                    "due_at": iso(due_met), "event_id": str(ev.event_id),
                    "late": bool(due_met is not None and _utc(ms.met_at) > _utc(due_met)),
                })
            seen_ms[name] = (ms.started_at, ms.met_at)
        if previous is not None and _utc(ev.at) < _utc(previous.at):
            # T-F4 / ADR-027: accepted out of time order, ordered by seq in the log, flagged here.
            add(ev.at, seq, 1, {
                "kind": "backdated", "at": iso(ev.at), "event_id": str(ev.event_id), "event_type": ev.type,
                "previous_event_id": str(previous.event_id), "previous_at": iso(previous.at),
                "behind_seconds": int((_utc(previous.at) - _utc(ev.at)).total_seconds()),
            })
        previous = ev

        if open_flag is not None and ev.type != "watchdog_flagged":
            open_flag["cleared_at"] = ev.at.isoformat()
            open_flag = None
        if ev.type == "watchdog_flagged":
            open_flag = {
                "kind": "watchdog_flag", "at": ev.at.isoformat(), "reason": ev.payload.get("reason"),
                "idle_days": ev.payload.get("idle_days"), "cleared_at": None, "event_id": str(ev.event_id),
            }
            add(ev.at, seq, 1, open_flag)
        elif ev.type == "mark_overdue" and ev.payload.get("milestone"):
            due = ev.payload["due_at"]
            add(datetime.fromisoformat(due.replace("Z", "+00:00")), seq, 1, {
                "kind": "milestone_breached", "at": due, "detected_at": ev.at.isoformat(),
                "milestone": ev.payload["milestone"], "due_at": due, "event_id": str(ev.event_id),
            })
        elif ev.type == "mark_overdue":
            due = ev.payload["due_at"]
            sla = ev.payload.get("sla_event") or (pathway.sla_for_state(model.state) or (None,))[0]
            add(datetime.fromisoformat(due.replace("Z", "+00:00")), seq, 1, {
                "kind": "sla_breached", "at": due, "detected_at": ev.at.isoformat(), "sla": sla,
                "state": ev.payload["state"], "due_at": due, "event_id": str(ev.event_id),
            })
        elif ev.type == "escalate":
            rung = pathway.rung(str(ev.payload.get("rung")))
            last_escalation = {
                "kind": "escalation", "at": ev.at.isoformat(), "rung": ev.payload.get("rung"),
                "milestone": ev.payload.get("milestone"),
                "level": ev.payload.get("level") or pathway.rung_level(str(ev.payload.get("rung"))),
                "target_role": ev.payload.get("target") or (rung.target if rung else None),
                "after_breach_days": rung.after_breach_days if rung else None,
                "action": ev.payload.get("action") or (rung.action if rung else None),
                "rung_due_at": ev.payload.get("rung_due_at"), "accepted": False, "accepted_at": None,
                "accepted_by": None, "event_id": str(ev.event_id),
            }
            add(ev.at, seq, 1, last_escalation)
            last_rung_by_scope[ev.payload.get("milestone") or "state"] = last_escalation
        elif ev.type == "escalation_accepted" and last_escalation is not None:
            # the ladder whose acceptance flag the event set (a milestone's, else the state's)
            scope = next((n for n, m in model.milestones.items()
                          if m.escalation_accepted and not before.milestones[n].escalation_accepted), "state")
            target = last_rung_by_scope.get(scope, last_escalation)
            target.update(accepted=True, accepted_at=ev.at.isoformat(), accepted_by=ev.actor)
        elif ev.type == "handoff_rejected" and ev.payload.get("bounced") is True:
            add(ev.at, seq, 1, {
                "kind": "handoff_bounce", "at": ev.at.isoformat(), "to_provider": ev.payload.get("to_provider"),
                "returned_to": ev.payload.get("returned_to"), "requested_at": ev.payload.get("requested_at"),
                "reason": ev.payload.get("reason"), "event_id": str(ev.event_id),
            })

        sla = pathway.sla_for_state(model.state) if model.state in pathway.open_states else None
        if sla is not None and model.clock_started_at is not None:
            key = (sla[0], model.clock_started_at)
            if key != last_sla:
                last_sla = key
                due = due_at_for(model, pathway, calendar)
                add(model.clock_started_at, seq, 1, {
                    "kind": "sla_started", "at": model.clock_started_at.isoformat(), "state": model.state,
                    "sla": sla[0], "working_days": sla[1], "due_at": iso(due),
                    "clock_start": pathway.clock_start_for(sla[0]),
                })
    sortable.sort(key=lambda t: (t[0].astimezone(UTC), t[1], t[2]))
    return {"sim_time": sim_time.isoformat(), "items": [t[3] for t in sortable]}
