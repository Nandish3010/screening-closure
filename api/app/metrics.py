"""Closure metrics (A10): pure functions over a case's event log. No store, no clock, no I/O.

`GET /metrics/closure` is a thin route over these, and the simulator's evaluation can call the
same functions, so the dashboard and the evaluation cannot disagree. The definitions follow
docs/results/metrics.md wherever the two overlap:

* Clock zero is the case's own `case_created` event (the day the result arrived).
* Time to closure is measured to the FIRST `close` (a later re-close does not count) and only
  over cases that closed, so read it with `cases_closed`. "Safe" closure means the closure rule
  held on that event: a human actor, an `evidence_ref` and a `signed_by`; a close without them
  is counted in `unsafe_closures` and left out of the percentiles.
* Percentiles use linear interpolation at position q x (n - 1).
* Open means "not currently closed": a reopened case is open again. Age is whole elapsed
  calendar days since the result arrived (0-30, 31-60, 61-90, then 90+ meaning more than 90).
* Lost to follow-up: no `complete_confirmatory` and either an explicit lost-contact flag or no
  pathway progress for 30 days or more at `now`. "Surfaced" means the log holds at least one
  `mark_overdue`, `escalate`, `watchdog_flagged` or `mark_lost_contact` event; a lost case that
  was never surfaced is a silent loss (`lost_never_surfaced`).
* Calendar days are elapsed days; working days count the working days after the start day
  (the calendar module's `working_days_between`).

The engine names the surfacing events `mark_overdue` and `escalate`, where the simulator logs
`overdue` and `escalated`; pass `surfacing_events` to `build_record` to map them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from app.calendar import WorkingCalendar
from app.pathway import Pathway
from app.state_machine import CASE_CREATED, genesis, initial_model, is_automated, step

AGE_BUCKETS = ("0-30", "31-60", "61-90", "90+")
LTFU_IDLE_DAYS = 30  # metrics.md: metrics.ltfu_idle_days
SURFACING_EVENTS = frozenset({"mark_overdue", "escalate", "watchdog_flagged", "mark_lost_contact"})
LOST_CONTACT_EVENT = "mark_lost_contact"
UNASSIGNED = "unassigned"
_DAY = 86400.0


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class CaseRecord:
    """Everything the metrics need from one case's log."""

    case_id: object
    t0: datetime                       # case_created: clock zero
    state: str                         # state at the end of the log
    first_close_at: datetime | None    # the first `close`, safe or not
    first_safe_close_at: datetime | None
    unsafe_closure: bool               # a close without a human, evidence or signature
    completed_confirmatory: bool
    last_progress_at: datetime | None  # last pathway-progress event (None: none yet)
    lost_contact_flag: bool
    surfaced: bool
    flagged: bool                      # an open watchdog flag at the end of the log
    breach_providers: tuple            # provider (or None) at each `mark_overdue`
    dwells: tuple                      # (from_state, to_state, entered_at, left_at), completed visits


def build_record(
    events,
    pathway: Pathway,
    *,
    surfacing_events=SURFACING_EVENTS,
    lost_contact_event: str = LOST_CONTACT_EVENT,
) -> CaseRecord:
    """Fold one case's log (replay through the state machine) into a `CaseRecord`."""
    progress_events = {t.event for t in pathway.main_transitions}
    model = initial_model(pathway)
    t0 = None
    entered_at = None
    first_close = first_safe = None
    unsafe = completed = lost_flag = surfaced = False
    last_progress = None
    breach_providers: list = []
    dwells: list = []
    for ev in events:
        at = _utc(ev.at)
        if ev.type == CASE_CREATED:
            model = genesis(model, ev, pathway)
            t0 = entered_at = at
            continue
        before = model.state
        model = step(model, ev, pathway)
        if ev.type in surfacing_events and not (ev.payload or {}).get("attached_to"):
            # an `escalate` attached to another ladder's escalation of the same day is no new escalation (A19)
            surfaced = True
        if ev.type == lost_contact_event:
            lost_flag = True
        if ev.type == "mark_overdue":
            breach_providers.append(model.provider)
        if ev.type in progress_events:
            last_progress = at
        if ev.type == "complete_confirmatory":
            completed = True
        if ev.type == "close":
            if first_close is None:
                first_close = at
            safe = (not is_automated(ev.actor) and bool(ev.payload.get("evidence_ref"))
                    and bool(ev.payload.get("signed_by")))
            if safe and first_safe is None:
                first_safe = at
            unsafe = unsafe or not safe
        if model.state != before:
            dwells.append((before, model.state, entered_at, at))
            entered_at = at
    return CaseRecord(
        case_id=events[0].case_id if events else None,
        t0=t0 if t0 is not None else _utc(events[0].at),
        state=model.state,
        first_close_at=first_close,
        first_safe_close_at=first_safe,
        unsafe_closure=unsafe,
        completed_confirmatory=completed,
        last_progress_at=last_progress,
        lost_contact_flag=lost_flag,
        surfaced=surfaced,
        flagged=model.watchdog_flag is not None,
        breach_providers=tuple(breach_providers),
        dwells=tuple(dwells),
    )


def percentile(values, q: float) -> float | None:
    """Linear interpolation at position q x (n - 1), q in [0, 1]. None for no data."""
    xs = sorted(values)
    if not xs:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (pos - lo) * (xs[hi] - xs[lo])


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / _DAY


def time_to_closure(records, calendar: WorkingCalendar, terminal_state: str = "closed") -> dict:
    """P50 / P90 days from result to the first safe close, in calendar and working days."""
    safe = [r for r in records if r.first_safe_close_at is not None]
    cal = [_days(r.t0, r.first_safe_close_at) for r in safe]
    wd = [calendar.working_days_between(r.t0, r.first_safe_close_at) for r in safe]
    return {
        "n": len(safe),
        "p50_days": percentile(cal, 0.5), "p90_days": percentile(cal, 0.9),
        "p50_working_days": percentile(wd, 0.5), "p90_working_days": percentile(wd, 0.9),
    }


def age_bucket(age_days: float) -> str:
    whole = int(age_days)
    if whole <= 30:
        return "0-30"
    if whole <= 60:
        return "31-60"
    if whole <= 90:
        return "61-90"
    return "90+"


def open_by_age_bucket(records, now: datetime, terminal_state: str = "closed") -> dict:
    """Cases not currently closed, by whole days since the result arrived."""
    out = {b: 0 for b in AGE_BUCKETS}
    for r in records:
        if r.state != terminal_state:
            out[age_bucket(_days(r.t0, _utc(now)))] += 1
    return out


def breaches_by_provider(records) -> list[dict]:
    """Every `mark_overdue` counted against the provider the case was with at the time
    (`unassigned` when none), most breaches first, ties by provider name."""
    counts: dict[str, int] = {}
    for r in records:
        for provider in r.breach_providers:
            key = provider or UNASSIGNED
            counts[key] = counts.get(key, 0) + 1
    return [{"provider": p, "count": c} for p, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def bottleneck_by_transition(records, calendar: WorkingCalendar) -> list[dict]:
    """Median days a case spent in a state before leaving it for the next, per transition,
    slowest first. Completed visits only (a case still waiting is not in the sample, so this
    understates the slowest steps)."""
    groups: dict[tuple, list] = {}
    for r in records:
        for frm, to, start, end in r.dwells:
            groups.setdefault((frm, to), []).append((start, end))
    rows = []
    for (frm, to), spans in groups.items():
        rows.append({
            "transition": f"{frm} -> {to}",
            "median_days": median(_days(a, b) for a, b in spans),
            "median_working_days": median(calendar.working_days_between(a, b) for a, b in spans),
            "n": len(spans),
        })
    return sorted(rows, key=lambda row: (-row["median_days"], row["transition"]))


def lost_cases(records, now: datetime) -> dict:
    """Lost to follow-up, and the part of it that nobody was ever told about."""
    lost = never = 0
    for r in records:
        if r.completed_confirmatory:
            continue
        last = r.last_progress_at or r.t0
        idle = _days(last, _utc(now))
        if r.lost_contact_flag or idle >= LTFU_IDLE_DAYS:
            lost += 1
            if not r.surfaced:
                never += 1
    return {"lost_to_follow_up": lost, "lost_never_surfaced": never}


def closure_metrics(records, now: datetime, calendar: WorkingCalendar, *, terminal_state: str = "closed") -> dict:
    """All the closure metrics in one dict (the body of `GET /metrics/closure`, minus the
    pieces that need a database: `sim_time` and the abstention counts)."""
    records = list(records)
    ttc = time_to_closure(records, calendar, terminal_state)
    closed = sum(1 for r in records if r.state == terminal_state)
    return {
        "cases_total": len(records),
        "cases_open": len(records) - closed,
        "cases_closed": closed,
        "closures_measured": ttc["n"],
        "unsafe_closures": sum(1 for r in records if r.unsafe_closure),
        "p50_days": ttc["p50_days"],
        "p90_days": ttc["p90_days"],
        "p50_working_days": ttc["p50_working_days"],
        "p90_working_days": ttc["p90_working_days"],
        "open_by_age_bucket": open_by_age_bucket(records, now, terminal_state),
        "breaches_by_provider": breaches_by_provider(records),
        "bottleneck_by_transition": bottleneck_by_transition(records, calendar),
        **lost_cases(records, now),
        "cases_flagged": sum(1 for r in records if r.flagged and r.state != terminal_state),
    }


def abstention(attempts: int, abstentions: int) -> dict:
    return {"attempts": attempts, "abstentions": abstentions,
            "rate": (abstentions / attempts) if attempts else None}
