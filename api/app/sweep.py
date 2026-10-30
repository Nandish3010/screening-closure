"""Scheduler SLA sweep: find breaches, emit `mark_overdue` (A3), walk the escalation ladder (A4)
and bounce unanswered referral handoffs (A5).

The planner (`plan_case`) is a pure function of (case model, pathway, calendar, now). It never
reads a clock and never produces anything but `mark_overdue` / `escalate` / bounce (`handoff_rejected` with
reason `timeout`) events from `system:scheduler`; in particular it can never produce `close`
(T-A16). `run_sweep` feeds it
every open case from a store and appends what it plans.

Milestones (A18, ADR-028). Besides the current state's SLA the planner evaluates every milestone of the
pathway (referral within 15 working days of the result, colonoscopy within 31 working days of referral). A
milestone breach is `mark_overdue {milestone, due_at}` and the escalation ladder runs for it exactly as for a
state breach, with its own position (`MilestoneState.escalation_level`).

Idempotency: each planned event has a deterministic id, a uuid5 over (case, the `event_id` of the
event that started the clock or made the request, kind [, rung]). Never over a timestamp (SEC-2):
two different clocks that start at the same instant (a state entered, left and entered again; a
handoff requested, bounced and requested again) are different obligations and must not share an
id, or the second would be swallowed as a repeat of the first. A milestone's ids derive from the
event that started the milestone (`MilestoneState.started_event_id`: the `case_created` marker for
`referral_requested`, the first `handoff_requested` for `confirmatory_completed` (A25: it never
restarts), or the event that entered the awaiting state when there was no referral) and their kind is prefixed `milestone:<name>:`, so a
milestone that starts at the same event as a state clock never shares an id with the state's breach.
Running the sweep again, or two sweeps at once, still inserts each event once: the store treats a
repeated event_id on the same case as a no-op. The state machine adds a second line of defence (a
case that is already overdue refuses another `mark_overdue`).

Parallel sweep (A17). `run_sweep_parallel` splits the open cases into `n` disjoint slices swept by `n`
spawned worker processes, each with its own connection pool; `SweepPool` keeps those processes alive for
the scheduler route (`app.routers.scheduler`, `SWEEP_WORKERS`, default min(4, cpus)). The slices are
disjoint and exhaustive and the event ids are deterministic, so the result equals `run_sweep` and a sweep
that is repeated, overlapped or killed part-way only ever leaves each planned event in the log once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app import payloads
from app.calendar import WorkingCalendar
from app.events import CaseNotFound, Event, EventContentConflict, EventIdConflict, EventStore
from app.leader import StaleLeader
from app.pathway import Pathway
from app.state_machine import CONSENT_WITHDRAWN, CaseModel, InvalidTransition, MilestoneState, referral_hold, step

log = logging.getLogger("app.sweep")

SCHEDULER_ACTOR = "system:scheduler"
# The only event types the scheduler may ever emit (mirrors app.policy.EVENT_POLICY).
SCHEDULER_EVENT_TYPES = frozenset({"mark_overdue", "escalate", "handoff_rejected"})
# Fixed namespace for deterministic event ids (never change: it would break idempotency).
SWEEP_NAMESPACE = uuid.UUID("5c6d0a9e-77f1-4b8e-9d0c-0f3f6a1b2e44")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def sweep_event_id(case_id: UUID, started_by: UUID | str, kind: str) -> UUID:
    """uuid5(case, event that started the clock or request, kind): one id per breach (and per rung),
    one per unanswered handoff request. `started_by` is an event id, never a time."""
    return uuid.uuid5(SWEEP_NAMESPACE, f"{case_id}|{started_by}|{kind}")


def due_at_for(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> datetime | None:
    """When the current state's SLA expires, or None if the state has no SLA to watch.

    The clock starts at the event that entered the state (`model.clock_started_at`) and the
    SLA length is counted in working days.
    """
    sla = pathway.sla_for_state(model.state)
    if sla is None or model.clock_started_at is None or model.state not in pathway.open_states:
        return None
    _, days = sla
    return _utc(calendar.add_working_days(model.clock_started_at, days))


def milestone_due(started_at: datetime, working_days: int, calendar: WorkingCalendar) -> datetime:
    """A milestone's due date: `working_days` working days after its start."""
    return _utc(calendar.add_working_days(started_at, working_days))


def milestone_due_at(
    model: CaseModel, name: str, pathway: Pathway, calendar: WorkingCalendar
) -> datetime | None:
    """When the milestone expires, or None if it is not running (not started, met, or void)."""
    definition = pathway.milestones.get(name)
    ms = model.milestones.get(name)
    if definition is None or ms is None or ms.started_at is None or ms.met_at is not None or ms.void:
        return None
    return milestone_due(ms.started_at, definition.working_days, calendar)


def handoff_due_at(
    model: CaseModel, pathway: Pathway, calendar: WorkingCalendar
) -> datetime | None:
    """When the pending handoff request expires unanswered, or None if nothing is pending or the request is held.

    Counted in working days from the request (`handoff_response_working_days`, ADR-016). A36 (ADR-044): the timer does
    not run while the referral is held (consent withdrawn, or sharing with the specialist refused), and when the hold
    ends it starts again, in full, from `resumed_at`.
    """
    pending = model.pending_handoff
    if not pending or referral_hold(model) is not None:
        return None
    requested = datetime.fromisoformat(pending.get("resumed_at") or pending["requested_at"])
    return _utc(calendar.add_working_days(requested, pathway.handoff_response_working_days))


# ---- when can a case next be due? (A20) ----------------------------------------------------------------------

# Stored in `cases.next_due_at` for a case with nothing left to watch. A real instant (not a database `infinity`) so
# every driver reads it back; NULL in that column means "not computed", which the sweep always evaluates.
NEVER = datetime(9999, 12, 31, tzinfo=UTC)
# Bump when the rules in `next_due_at` change, so every stored value is recomputed by the next sweep.
NEXT_DUE_RULES_VERSION = 2  # 2: A36, a held referral has no bounce timer


def _next_trigger(
    due: datetime, overdue: bool, accepted: bool, level: int, pathway: Pathway, calendar: WorkingCalendar
) -> datetime | None:
    """The earliest instant at which one ladder (the state's, or a milestone's) has something to plan.

    Mirrors `plan_case`: the breach itself once `now > due` while it is not yet marked; afterwards the next rung
    (`level + 1`) at its due time, unless a human accepted the ladder or the ladder is exhausted."""
    if not overdue:
        return due
    if accepted or level >= len(pathway.escalation_ladder):
        return None
    rung = pathway.escalation_ladder[level]
    return max(due, _utc(calendar.add_working_days(due, rung.after_breach_days)))


def next_due_at(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> datetime | None:
    """The earliest instant `t` such that `plan_case(..., now=t)` can be non-empty; None if it never can be (nothing
    left to watch until another event arrives).

    A lower bound that is exact up to the boundary (the planner needs `now > due`, this returns `due`): for every
    `now`, `plan_case(model, now)` is empty whenever `now < next_due_at(model)`, so a sweep that evaluates only the
    cases with `next_due_at <= now` plans exactly what a sweep of every case plans (property-tested). It is a pure
    function of the model, the pathway and the calendar, recomputed by the store on every append."""
    triggers: list[datetime] = []
    bounce = handoff_due_at(model, pathway, calendar)
    if bounce is not None:
        triggers.append(bounce)
    due = due_at_for(model, pathway, calendar)
    if due is not None:
        t = _next_trigger(due, model.overdue, model.escalation_accepted, model.escalation_level, pathway, calendar)
        if t is not None:
            triggers.append(t)
    if model.state in pathway.open_states and model.state != CONSENT_WITHDRAWN:
        for name in pathway.milestones:
            due = milestone_due_at(model, name, pathway, calendar)
            if due is None:
                continue
            ms = model.milestones[name]
            t = _next_trigger(due, ms.overdue, ms.escalation_accepted, ms.escalation_level, pathway, calendar)
            if t is not None:
                triggers.append(t)
    return min(triggers) if triggers else None


def timing_fingerprint(pathway: Pathway, calendar: WorkingCalendar) -> str:
    """A short hash of everything `next_due_at` depends on besides the event log: the calendar (weekend, holidays,
    time zone) and the pathway's states, SLAs, escalation ladder, milestones and handoff window. Stored beside
    `cases.next_due_at`; a sweep with another fingerprint re-evaluates (and re-indexes) the cases stored under the old one."""
    material = {
        "rules": NEXT_DUE_RULES_VERSION,
        "open": list(pathway.open_states),
        "sla": {s: pathway.sla_for_state(s) for s in pathway.open_states},
        "ladder": [(r.rung, r.after_breach_days) for r in pathway.escalation_ladder],
        "milestones": [(m.name, m.from_event, m.until_event, m.working_days) for m in pathway.milestones.values()],
        "handoff_days": pathway.handoff_response_working_days,
        "weekend": sorted(calendar.weekend),
        "holidays": sorted(h.isoformat() for h in calendar.holidays),
        "tz": getattr(calendar.tz, "key", None) or str(calendar.tz),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:16]


def plan_bounce(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> Event | None:
    """`handoff_rejected {reason: timeout, bounced: true}` for a request that went unanswered.

    Ownership never moved (it changes only on acceptance), so the bounce returns the case to the
    requester by clearing the pending request; in the referral states it also moves the case to
    `referral_rejected` so it needs a re-route (which has its own SLA and ladder).
    """
    due = handoff_due_at(model, pathway, calendar)
    if due is None or now <= due:
        return None
    pending = model.pending_handoff
    return Event(
        case_id,
        "handoff_rejected",
        now,
        SCHEDULER_ACTOR,
        {
            "reason": pathway.handoff_bounce_reason,
            "bounced": True,
            "to_provider": pending["to_provider"],
            "returned_to": pending["from_owner"],
            "requested_at": pending["requested_at"],
            "due_at": due.isoformat(),
            "working_days": pathway.handoff_response_working_days,
        },
        sweep_event_id(case_id, pending["requested_event_id"], "bounce"),
    )


def plan_case(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> list[Event]:
    """The events the scheduler should append for this case at sim time `now`, in order.

    A bounce comes alone: it can change the case's state, so the overdue/escalation events are
    planned from the new model (`run_sweep` re-plans straight after a bounce).

    Otherwise: every breach (`mark_overdue`) first, then the escalation rungs, with concurrent ladders coalesced
    (A19, ADR-031): a rung to a role that an earlier rung of this plan already goes to rides on that escalation
    (`also_for`), and a rung to a role another ladder of the case escalated earlier on the same working day is
    recorded `attached_to` that escalation. See `_coalesce`.
    """
    bounce = plan_bounce(case_id, model, pathway, calendar, now)
    if bounce is not None:
        return [bounce]
    marks: list[Event] = []
    rungs: list[tuple[str | None, Event]] = []  # (ladder: None for the state's, else the milestone, event)
    due = due_at_for(model, pathway, calendar)
    if due is not None and now > due:
        sla_event, sla_days = pathway.sla_for_state(model.state)
        if not model.overdue:
            marks.append(
                Event(
                    case_id,
                    "mark_overdue",
                    now,
                    SCHEDULER_ACTOR,
                    {
                        "state": model.state,
                        "due_at": due.isoformat(),
                        "sla_event": sla_event,
                        "sla_working_days": sla_days,
                    },
                    sweep_event_id(case_id, model.clock_started_event_id, "mark_overdue"),
                )
            )
        if not model.escalation_accepted:
            rungs.extend(
                (None, e)
                for e in _plan_rungs(case_id, model, pathway, calendar, now, due, model.escalation_level)
            )
    ms_marks, ms_rungs = _plan_milestone_parts(case_id, model, pathway, calendar, now)
    marks.extend(ms_marks)
    rungs.extend(ms_rungs)
    return marks + _coalesce(model, pathway, calendar, now, marks, rungs)


def plan_milestones(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> list[Event]:
    """The events for breached milestones, in the pathway's order: the breach once, then the due rungs (not
    coalesced with the state's ladder: `plan_case` does that)."""
    marks, rungs = _plan_milestone_parts(case_id, model, pathway, calendar, now)
    return [*marks, *(e for _, e in rungs)]


def _plan_milestone_parts(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> tuple[list[Event], list[tuple[str | None, Event]]]:
    """(breaches, rungs) of the milestones in the pathway's order; each rung is (milestone, event).

    Not evaluated in a closed case or while consent is withdrawn (outreach is frozen and a human must review
    the case; the clock keeps running and the breach is planned the moment the case resumes).
    """
    if model.state not in pathway.open_states or model.state == CONSENT_WITHDRAWN:
        return [], []
    marks: list[Event] = []
    rungs: list[tuple[str | None, Event]] = []
    for name, definition in pathway.milestones.items():
        due = milestone_due_at(model, name, pathway, calendar)
        if due is None or now <= due:
            continue
        ms = model.milestones[name]
        if not ms.overdue:
            marks.append(
                Event(
                    case_id,
                    "mark_overdue",
                    now,
                    SCHEDULER_ACTOR,
                    {"milestone": name, "due_at": due.isoformat(), "sla_working_days": definition.working_days},
                    sweep_event_id(case_id, ms.started_event_id, f"milestone:{name}:mark_overdue"),
                )
            )
        if not ms.escalation_accepted:
            rungs.extend(
                (name, e)
                for e in _plan_rungs(case_id, model, pathway, calendar, now, due, ms.escalation_level, milestone=name)
            )
    return marks, rungs


@dataclass
class _Planned:
    """One escalation of the plan: the event, the ladder it belongs to, and the (ladder, level) pairs it covers."""

    scope: str | None
    event: Event
    target: str
    covers: list
    attached: bool = False


def _coalesce(
    model: CaseModel,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    marks: list[Event],
    candidates: list[tuple[str | None, Event]],
) -> list[Event]:
    """Coalesce concurrent ladders (A19, ADR-031): one escalation per role per working day.

    `candidates` are the rungs now due, the state's ladder first, then each milestone's in the pathway's order.
    Each one, in that order:

    1. goes to the same target as an earlier, non-attached escalation of this plan from another ladder: it is not
       emitted; its (ladder, rung, level, due_at, rung_due_at) is added to that escalation's `also_for`. The
       primary keeps its own id (from its own clock), so the id of a ladder's rung never depends on another ladder.
    2. else another ladder of the case has an unaccepted current rung aimed at the same target, recorded on an
       escalation of the same working day as `now` (`calendar.local_date`): the rung is emitted with its normal
       payload and its own deterministic id plus `attached_to` that escalation's event id. It notifies nobody and
       is not counted as a new escalation.
    3. else it stands on its own.

    The escalations are then ordered so that a covered ladder's earlier level comes first (each ladder's levels
    stay increasing), and the whole plan is dry-run through the state machine: if the machine would refuse it,
    the plain, uncoalesced plan is returned instead (always valid: it is what the sweep did before A19).
    """
    plain = [e for _, e in candidates]
    if len(candidates) < 2 and not any(_holder_candidates(model, sc) for sc in (None, *model.milestones)):
        return plain  # nothing to coalesce with and nothing to attach to
    today = calendar.local_date(now)
    # (level, accepted, escalation event id, escalation_at) per ladder as the plan applies, in order
    work = {
        sc: (x.escalation_level, x.escalation_accepted, x.escalation_event_id, x.escalation_at)
        for sc, x in ((sc, _ladder_of(model, sc)) for sc in (None, *model.milestones))
    }
    emitted: list[_Planned] = []
    changed = False
    for scope, ev in candidates:
        target, level = ev.payload["target"], ev.payload["level"]
        primary = next(
            (
                p for p in emitted
                if not p.attached and p.target == target and scope not in {sc for sc, _ in p.covers}
            ),
            None,
        )
        if primary is not None:
            entry = {
                k: ev.payload[k]
                for k in ("state", "milestone", "rung", "level", "due_at", "rung_due_at")
                if k in ev.payload
            }
            primary.event.payload.setdefault("also_for", []).append(entry)
            primary.covers.append((scope, level))
            work[scope] = (level, False, primary.event.event_id, now)
            changed = True
            continue
        holder = next(
            (
                (eid, at)
                for sc, (lv, acc, eid, at) in work.items()
                if sc != scope and lv > 0 and not acc and eid is not None and at is not None
                and (r := pathway.rung_at(lv)) is not None and r.target == target
                and calendar.local_date(at) == today
            ),
            None,
        )
        if holder is not None:
            ev.payload["attached_to"] = str(holder[0])
            work[scope] = (level, False, holder[0], holder[1])
            emitted.append(_Planned(scope, ev, target, [(scope, level)], attached=True))
            changed = True
            continue
        work[scope] = (level, False, ev.event_id, now)
        emitted.append(_Planned(scope, ev, target, [(scope, level)]))
    if not changed:
        return plain
    ordered = _in_ladder_order(model, emitted)
    if ordered is not None:
        events = [p.event for p in ordered]
        try:
            m = model
            for e in [*marks, *events]:
                m = step(m, e, pathway)
        except InvalidTransition:
            ordered = None
    if ordered is None:
        # the coalescing may have edited payloads in place: rebuild the plain events untouched
        return [_without_coalescing(e) for e in plain]
    return events


def _ladder_of(model: CaseModel, scope: str | None):
    """The state's ladder (`scope` None; the model itself) or a milestone's."""
    return model if scope is None else (model.milestones.get(scope) or MilestoneState())


def _holder_candidates(model: CaseModel, scope: str | None) -> bool:
    """True when the ladder has an escalation recorded that a later rung could attach to."""
    x = _ladder_of(model, scope)
    return x.escalation_level > 0 and not x.escalation_accepted and x.escalation_event_id is not None


def _without_coalescing(event: Event) -> Event:
    payload = {k: v for k, v in event.payload.items() if k not in ("also_for", "attached_to")}
    return Event(event.case_id, event.type, event.at, event.actor, payload, event.event_id)


def _in_ladder_order(model: CaseModel, emitted: list[_Planned]) -> list[_Planned] | None:
    """The escalations in an order in which every covered ladder meets its rungs one level at a time, keeping the
    given order where it already does; None if no such order exists."""
    levels = {sc: _ladder_of(model, sc).escalation_level for sc in (None, *model.milestones)}
    pending, out = list(emitted), []
    while pending:
        for i, p in enumerate(pending):
            if all(levels[sc] == lv - 1 for sc, lv in p.covers):
                for sc, lv in p.covers:
                    levels[sc] = lv
                out.append(pending.pop(i))
                break
        else:
            return None
    return out


def _plan_rungs(
    case_id: UUID,
    model: CaseModel,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    due: datetime,
    escalation_level: int,
    milestone: str | None = None,
) -> list[Event]:
    """The escalation rungs now due for this breach, in ladder order, none skipped.

    A rung is due `after_breach_days` working days after the due date (0 = at the breach).
    Rungs already emitted (`escalation_level`) are not planned again. A human accepting a
    rung (`escalation_accepted`) stops the ladder for this breach; the caller checks that.
    `milestone` names the ladder of a milestone breach (A18); without it the state's ladder.
    """
    subject = {"milestone": milestone} if milestone is not None else {"state": model.state}
    if milestone is not None:
        started_by, prefix = model.milestones[milestone].started_event_id, f"milestone:{milestone}:"
    else:
        started_by, prefix = model.clock_started_event_id, ""
    rungs = []
    for level, rung in enumerate(pathway.escalation_ladder, start=1):
        if level <= escalation_level:
            continue
        rung_due = _utc(calendar.add_working_days(due, rung.after_breach_days))
        if now < rung_due:
            break  # later rungs are due even later
        rungs.append(
            Event(
                case_id,
                "escalate",
                now,
                SCHEDULER_ACTOR,
                {
                    "rung": rung.rung,
                    "level": level,
                    "target": rung.target,
                    "action": rung.action,
                    **subject,
                    "due_at": due.isoformat(),
                    "rung_due_at": rung_due.isoformat(),
                },
                sweep_event_id(case_id, started_by, f"{prefix}escalate:{rung.rung}"),
            )
        )
    return rungs


@dataclass
class SweepResult:
    sim_time: str
    cases_evaluated: int = 0
    cases_skipped: int = 0  # open cases not read because `next_due_at` says nothing can be due (A20)
    mark_overdue: int = 0
    escalate: int = 0
    handoff_bounced: int = 0
    rejected: int = 0  # planned events the state machine refused (a race with another writer)
    errors: list[str] = field(default_factory=list)
    # F10: a write was refused because the leader lease is no longer this sweep's (`leader.StaleLeader`). The sweep stopped
    # at that point and wrote nothing more; the route answers 409.
    fenced: bool = False
    # M7-7: the sweep stopped at its per-loop bound (`max_cases` or `budget_seconds`) with due cases left. They keep their
    # `next_due_at`, so the next loop picks them up: a backlog drains across loops instead of stretching one.
    budget_exhausted: bool = False
    due_remaining: int = 0  # due cases not evaluated when it stopped (only set with budget_exhausted)
    # (event_id, case_id) of every event a shard worker appended, filled only when the caller asked
    # for it (`record=True`): the parent process projects them to FHIR (A12) because the workers
    # write through their own plain stores, not the API's projecting wrapper. Not part of to_json.
    emitted_refs: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def to_json(self) -> dict:
        out = self._base_json()
        if self.fenced:
            out["fenced"] = True
        if self.budget_exhausted:
            out["budget_exhausted"] = True
            out["due_remaining"] = self.due_remaining
        return out

    def _base_json(self) -> dict:
        return {
            "sim_time": self.sim_time,
            "cases_evaluated": self.cases_evaluated,
            "cases_skipped": self.cases_skipped,
            "mark_overdue": self.mark_overdue,
            "escalate": self.escalate,
            "handoff_bounced": self.handoff_bounced,
            "rejected": self.rejected,
            "errors": list(self.errors),
        }


DEFAULT_BATCH_CASES = 100


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(name, "").strip() or default), 0)
    except ValueError:
        return default


def _check_planned(planned: list[Event], pathway: Pathway) -> None:
    for event in planned:
        if event.type not in SCHEDULER_EVENT_TYPES or event.actor != SCHEDULER_ACTOR:
            raise RuntimeError(f"scheduler planned a forbidden event: {event.type}")
        if event.type == "handoff_rejected" and (
            event.payload.get("reason") != pathway.handoff_bounce_reason
            or event.payload.get("bounced") is not True
        ):
            raise RuntimeError("scheduler planned a handoff_rejected that is not a bounce")


def _valid_payloads(planned: list[Event]) -> bool:
    try:
        for event in planned:
            payloads.validate(event.type, event.payload)
    except payloads.PayloadSchemaError:
        return False  # the per-event path reports it
    return True


def _count(result: SweepResult, planned: list[Event]) -> None:
    for event in planned:
        if event.type == "mark_overdue":
            result.mark_overdue += 1
        elif event.type == "handoff_rejected":
            result.handoff_bounced += 1
        else:
            result.escalate += 1


def _emit_case(
    store, pathway: Pathway, calendar: WorkingCalendar, now: datetime, result: SweepResult,
    case_id: UUID, model: CaseModel, planned: list[Event], fence=None,
) -> None:
    """Append what was planned for one case, on its own: the original per-case path. `planned` is the first plan; a
    bounce can move the case to another state, so the case is re-planned once from the new model.

    `fence` (F10) rides on every append. A `leader.StaleLeader` is deliberately not caught here: it is not a per-case
    refusal, it propagates to `run_sweep`, which stops the sweep."""
    fkw = {"fence": fence} if fence is not None else {}
    for attempt in range(2):
        bounced = False
        # Fast path: several events for one case (a breach and its first rung) go in one
        # transaction instead of one each. All-or-nothing; if the batch is refused for any
        # reason we fall back to the one-by-one loop below, which keeps its exact behaviour.
        if (
            len(planned) > 1
            and hasattr(store, "append_many")
            and not any(e.type == "handoff_rejected" for e in planned)
            and _valid_payloads(planned)
        ):
            try:
                store.append_many(planned, pathway, **fkw)
            except (InvalidTransition, CaseNotFound, EventIdConflict):
                pass
            else:
                _count(result, planned)
                planned = []
        for event in planned:
            try:
                # Whatever the sweep plans must also pass the payload schema (A14), so a bug
                # here cannot put free text in the log. A planned event that does not is
                # reported and skipped, not allowed to stop the sweep for every other case.
                payloads.validate(event.type, event.payload)
            except payloads.PayloadSchemaError as exc:
                result.errors.append(f"{case_id}: {exc}")
                log.error("case %s: planned %s refused by the payload schema: %s", case_id, event.type, exc)
                break
            try:
                store.append(event, pathway, **fkw)
            except InvalidTransition as exc:
                # Another writer moved the case between our read and our write; the next
                # sweep re-plans from the new state.
                result.rejected += 1
                log.info("case %s: %s refused: %s", case_id, event.type, exc)
                break
            except EventContentConflict as exc:
                # Another detector appended this id with different content a moment ago: a race, like
                # the refusal above; the next sweep re-plans from the log.
                result.rejected += 1
                log.info("case %s: %s refused: %s", case_id, event.type, exc)
                break
            except (CaseNotFound, EventIdConflict) as exc:
                result.errors.append(f"{case_id}: {type(exc).__name__}: {exc}")
                break
            if event.type == "mark_overdue":
                result.mark_overdue += 1
            elif event.type == "handoff_rejected":
                result.handoff_bounced += 1
                bounced = True
            else:
                result.escalate += 1
        if not bounced or attempt == 1:
            return
        model = store.read_model(case_id, pathway)
        if model is None:
            return
        planned = plan_case(case_id, model, pathway, calendar, now)
        _check_planned(planned, pathway)


def run_sweep(
    store: EventStore,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    only=None,
    shard: tuple[int, int] | None = None,
    full_scan: bool | None = None,
    batch_cases: int | None = None,
    fence=None,
    max_cases: int | None = None,
    budget_seconds: float | None = None,
    timer=None,
) -> SweepResult:
    """Evaluate the open cases (or only those in `only`, or only slice `shard=(k, n)`) at sim
    time `now` and append the planned events. Safe to run repeatedly and concurrently.

    With the Postgres store the cases read are those that can have something to do (A20): `next_due_at <= now`, or
    no due time stored yet (`PostgresEventStore.iter_due_case_models`). The events emitted are exactly those of a
    sweep of every case. `full_scan=True` (or the environment variable `SWEEP_FULL_SCAN=1`, for an operator who
    distrusts the index) reads and replays every open case as before; so does `only=` and any store without the index.

    The events of up to `batch_cases` cases (default 100, `SWEEP_BATCH_CASES`) are appended in one transaction when the
    store offers `append_batches`; a case the store refuses is handled on its own, exactly as an unbatched sweep would,
    and never loses the other cases' events.

    `fence` (F10), a `leader.Fence`, is carried into every write transaction and checked there against the lease. The
    first `leader.StaleLeader` ends the sweep: no further case is planned or written, a batch that was refused is NOT
    retried case by case, `SweepResult.fenced` is set and the reason is added to `errors`.

    Bounded work per call (M7-7). `max_cases` (default: `SWEEP_MAX_CASES`, 0 = no bound) caps the due cases evaluated, and
    `budget_seconds` (default: `SWEEP_BUDGET_SECONDS`, 0 = no bound) caps the wall time spent evaluating them; whichever is hit
    first ends the call after the pending batch is written, with `budget_exhausted` and the number of due cases left. A case
    not reached keeps its `next_due_at` and is read first-come by the next call, so a daily catch-up of 1,000,000 open cases
    drains over several loops and every loop stays short. Nothing is skipped or dropped, only deferred. Bounds apply to the
    index path (the Postgres store); a full scan or `only=` is never cut short."""
    batch_cases = DEFAULT_BATCH_CASES if batch_cases is None else batch_cases
    if batch_cases == DEFAULT_BATCH_CASES:
        batch_cases = _env_int("SWEEP_BATCH_CASES", DEFAULT_BATCH_CASES)
    result = SweepResult(sim_time=_utc(now).isoformat())
    # `shard` is only passed to stores that support it (Postgres); the in-memory store sweeps all.
    kwargs = {"shard": shard} if shard is not None else {}
    if full_scan is None:
        full_scan = os.environ.get("SWEEP_FULL_SCAN", "").strip() == "1"
    max_cases = _env_int("SWEEP_MAX_CASES", 0) if max_cases is None else max(max_cases, 0)
    if budget_seconds is None:
        budget_seconds = float(_env_int("SWEEP_BUDGET_SECONDS", 0))
    bounded = False
    if only is None and not full_scan and hasattr(store, "iter_due_case_models"):
        bounded = True
        cases = store.iter_due_case_models(pathway, calendar, now, **({"limit": max_cases + 1} if max_cases else {}), **kwargs)
        if hasattr(store, "count_open_cases"):
            result.cases_skipped = store.count_open_cases(pathway, **kwargs)  # the evaluated ones are taken off below
    else:
        cases = store.iter_open_case_models(pathway, only=only, **kwargs)
    skipped_base = result.cases_skipped
    # Several cases' planned events go to the store in one transaction (A20): `append_batches`, per-case locking and
    # checks unchanged, a refused case costs only itself. Without it (the in-memory store, a wrapper that does not
    # offer it) or with `batch_cases` < 2 every case is appended on its own, as before.
    batching = batch_cases > 1 and hasattr(store, "append_batches")
    pending: list[tuple[UUID, CaseModel, list[Event]]] = []
    fkw = {"fence": fence} if fence is not None else {}

    def flush() -> None:
        batch, pending[:] = list(pending), []
        if not batch:
            return
        try:
            outcomes = store.append_batches([planned for _, _, planned in batch], pathway, **fkw)
        except StaleLeader:
            raise  # fatal for the sweep: never turned into the per-case retries below
        except Exception as exc:  # noqa: BLE001 - nothing of the batch was written; do each case on its own
            log.warning("batched append of %d cases failed (%s: %s); appending them one by one", len(batch),
                        type(exc).__name__, exc)
            outcomes = [None] * len(batch)
        for (case_id, model, planned), outcome in zip(batch, outcomes):
            if isinstance(outcome, str):
                _count(result, planned)
            else:  # refused in the batch (or the batch failed): the per-case path classifies and reports it
                _emit_case(store, pathway, calendar, now, result, case_id, model, planned, fence)

    import time

    timer = timer or time.perf_counter  # a bound on the work done, never a decision about a case
    t0 = timer()
    try:
        for case_id, model, error in cases:
            if bounded and result.cases_evaluated and (
                (max_cases and result.cases_evaluated >= max_cases) or (budget_seconds and timer() - t0 >= budget_seconds)
            ):
                result.budget_exhausted = True
                break
            result.cases_evaluated += 1
            if model is None:
                result.errors.append(f"{case_id}: {error}")
                log.error("case %s does not replay: %s", case_id, error)
                continue
            planned = plan_case(case_id, model, pathway, calendar, now)
            if not planned:
                continue
            _check_planned(planned, pathway)
            if batching and not any(e.type == "handoff_rejected" for e in planned) and _valid_payloads(planned):
                pending.append((case_id, model, planned))
                if len(pending) >= batch_cases:
                    flush()
                continue
            _emit_case(store, pathway, calendar, now, result, case_id, model, planned, fence)
        flush()
        if result.budget_exhausted and hasattr(store, "count_due_cases"):
            result.due_remaining = max(store.count_due_cases(pathway, calendar, now, **kwargs), 0)
    except StaleLeader as exc:  # F10: deposed. Whatever is still pending is dropped unwritten; the next leader re-plans it.
        pending.clear()
        result.fenced = True
        result.errors.append(str(exc))
        log.warning("sweep stopped: %s", exc)
    if skipped_base:
        result.cases_skipped = max(skipped_base - result.cases_evaluated, 0)
    return result


class _RecordingStore:
    """Delegates to a store and notes the (event_id, case_id) of every event it appended."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.refs: list[tuple[str, str]] = []
        if hasattr(inner, "append_many"):  # the sweep probes for it with hasattr
            self.append_many = self._append_many
        if hasattr(inner, "append_batches"):
            self.append_batches = self._append_batches

    def append(self, event, pathway, *, within=None, guard=None, fence=None):
        state = self._inner.append(event, pathway, within=within, guard=guard, fence=fence)
        self.refs.append((str(event.event_id), str(event.case_id)))
        return state

    def _append_many(self, events, pathway, *, within=None, guard=None, fence=None):
        state = self._inner.append_many(events, pathway, within=within, guard=guard, fence=fence)
        self.refs.extend((str(e.event_id), str(e.case_id)) for e in events)
        return state

    def _append_batches(self, batches, pathway, *, fence=None):
        outcomes = self._inner.append_batches(batches, pathway, fence=fence)
        for events, outcome in zip(batches, outcomes):
            if isinstance(outcome, str):  # committed; a refused case wrote nothing
                self.refs.extend((str(e.event_id), str(e.case_id)) for e in events)
        return outcomes

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _sweep_shard(args: tuple) -> dict:
    """Worker entry point (top level so it can be pickled): sweep one slice with its own pool."""
    database_url, pathway, calendar, now, index, count, record, fence, max_cases, budget_seconds = args
    from psycopg_pool import ConnectionPool

    from app.store_pg import PostgresEventStore

    pool = ConnectionPool(database_url, min_size=1, max_size=2, open=True)
    try:
        pool.wait(timeout=30)
        store = PostgresEventStore(pool, calendar=calendar)
        if record:
            store = _RecordingStore(store)
        result = run_sweep(store, pathway, calendar, now, shard=(index, count), fence=fence,
                           max_cases=max_cases, budget_seconds=budget_seconds)
        out = result.to_json()
        out["fenced"] = result.fenced  # F10: each worker checks the lease in its own write transactions
        if record:
            out["emitted_refs"] = store.refs
        return out
    finally:
        pool.close()


def _noop(_: int) -> int:
    return _


def make_sweep_executor(workers: int):
    """A reusable pool of `workers` spawned processes for `run_sweep_parallel`. A long-running
    scheduler creates it once and keeps it, so each sweep pays no process start-up (about a
    second); `warm=True` semantics: the workers import the sweep modules before the first use."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    list(executor.map(_noop, range(workers * 4)))
    return executor


def run_sweep_parallel(
    database_url: str,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    workers: int = 4,
    executor=None,
    record: bool = False,
    fence=None,
    max_cases: int | None = None,
    budget_seconds: float | None = None,
) -> SweepResult:
    """The same sweep, split into `workers` disjoint slices of the cases swept by separate
    processes (each with its own connection pool). Replaying every open case is CPU-bound
    Python, so slices scale with cores. The result is identical to `run_sweep`: the slices
    cover every case exactly once, event ids are deterministic, and every append still goes
    through the transactional store, so a slice that overlaps another or is run twice can
    only produce no-ops.

    Processes are started with `spawn` (never `fork`) so this is safe to call from a threaded
    server process. Pass an `executor` from `make_sweep_executor` to reuse warm workers;
    otherwise a pool is created and shut down for this one sweep. `record=True` also collects
    `SweepResult.emitted_refs` (what the workers appended).

    `fence` (F10), a `leader.Fence`, goes to every worker (it is a small frozen dataclass, so it pickles) and is checked in
    each worker's own write transactions; a worker whose write is refused stops its slice, and `SweepResult.fenced` is set
    when any did. Workers deposed together write nothing after the takeover.
    """
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if max_cases is None:
        max_cases = _env_int("SWEEP_MAX_CASES", 0)
    per_shard = -(-max_cases // workers) if max_cases else 0  # each worker's share of the per-loop bound (ceiling)
    if budget_seconds is None:
        budget_seconds = float(_env_int("SWEEP_BUDGET_SECONDS", 0))
    jobs = [(database_url, pathway, calendar, now, k, workers, record, fence, per_shard, budget_seconds)
            for k in range(workers)]
    total = SweepResult(sim_time=_utc(now).isoformat())
    own = executor is None
    if own:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    try:
        for part in executor.map(_sweep_shard, jobs):
            total.cases_evaluated += part["cases_evaluated"]
            total.cases_skipped += part.get("cases_skipped", 0)
            total.mark_overdue += part["mark_overdue"]
            total.escalate += part["escalate"]
            total.handoff_bounced += part["handoff_bounced"]
            total.rejected += part["rejected"]
            total.errors += part["errors"]
            total.fenced = total.fenced or bool(part.get("fenced"))
            total.budget_exhausted = total.budget_exhausted or bool(part.get("budget_exhausted"))
            total.due_remaining += part.get("due_remaining", 0)
            total.emitted_refs += [tuple(r) for r in part.get("emitted_refs", ())]
    finally:
        if own:
            executor.shutdown()
    return total


# ---- the scheduler route's long-lived pool (A17) ---------------------------------------------------

DEFAULT_MAX_WORKERS = 4


def default_sweep_workers() -> int:
    return max(1, min(DEFAULT_MAX_WORKERS, os.cpu_count() or 1))


def sweep_workers_from_env(environ=None) -> int:
    """`SWEEP_WORKERS` (an integer >= 1), default min(4, cpus). 1 means the single-process sweep.
    An unusable value (not a number, or below 1) falls back to the default and is logged."""
    raw = (os.environ if environ is None else environ).get("SWEEP_WORKERS", "").strip()
    if not raw:
        return default_sweep_workers()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        log.warning("SWEEP_WORKERS=%r is not an integer >= 1; using %d", raw, default_sweep_workers())
        return default_sweep_workers()
    return value


class SweepPool:
    """The scheduler route's sweep runner: `workers` spawned processes started once and kept (so a
    sweep pays no process start-up), created lazily on the first sweep. Sweeps are serialised by a
    lock, so overlapping calls never fight over the same connections; the sweep itself is
    idempotent, so serialising only costs time. A pool whose worker died is replaced and the sweep
    retried once. `workers == 1` never creates a pool: the caller runs `run_sweep` in-process."""

    def __init__(self, workers: int) -> None:
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self.workers = workers
        self._executor = None
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self._executor is not None

    def warm(self) -> None:
        """Start the worker processes now (they import the sweep modules before the first use), so
        the first sweep does not pay for it. The API does not call this at start-up; the load test does."""
        with self._lock:
            if self._executor is None:
                self._executor = make_sweep_executor(self.workers)

    def run(self, database_url: str, pathway: Pathway, calendar: WorkingCalendar, now: datetime,
            record: bool = False, fence=None, max_cases: int | None = None,
            budget_seconds: float | None = None) -> SweepResult:
        from concurrent.futures.process import BrokenProcessPool

        with self._lock:
            for attempt in (1, 2):
                if self._executor is None:
                    self._executor = make_sweep_executor(self.workers)
                try:
                    return run_sweep_parallel(database_url, pathway, calendar, now, workers=self.workers,
                                              executor=self._executor, record=record, fence=fence,
                                              max_cases=max_cases, budget_seconds=budget_seconds)
                except BrokenProcessPool:
                    log.error("sweep worker pool broke (attempt %d); replacing it", attempt)
                    self._discard()
                    if attempt == 2:
                        raise
        raise AssertionError("unreachable")

    def _discard(self) -> None:
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def shutdown(self) -> None:
        with self._lock:
            self._discard()
