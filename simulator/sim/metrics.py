"""Per-case records built from event logs, and the per-arm metrics computed from them.

`build_record` is the single path from an event log to a `CaseRecord`; both the engine and the
hand-built scenario (scenario.py) use it, so metric arithmetic can be verified on known logs.
All times in a record are days since that case's own result arrived ("relative days").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Sequence

from .calendar import WorkingCalendar
from .messy import MESSY_TAGS

DAY = timedelta(days=1)

# Events that count as "somebody acted" for escalation precision and progress.
MAIN_CHAIN_PROGRESS = (
    "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge", "book_confirmatory",
    "complete_confirmatory", "clinician_review", "close",
)
EXTRA_ACTIONS = ("handoff_accepted", "contact_recovered", "appointment_rebooked", "consent_review_completed")
VISIBILITY_EVENTS = ("overdue", "escalated", "watchdog_flagged", "lost_contact")

AbstentionHook = Callable[[Sequence["CaseRecord"]], "float | None"]


@dataclass(frozen=True, slots=True)
class CaseRecord:
    idx: int
    language: str
    channel: str
    messy: tuple[str, ...]  # messy tags that were injected into this case
    follow_up_days: float
    t0: float  # absolute simulation day on which the result arrived
    t_owner: float | None  # first accept_ownership with a named owner
    t_contact: float | None  # first contact_patient
    t_complete: float | None  # first complete_confirmatory
    closes: tuple[float, ...]
    reopens: tuple[float, ...]
    escalations: tuple[float, ...]
    actions: tuple[float, ...]
    last_progress: float | None
    lost_contact: bool
    visible: bool  # the system ever surfaced the case to a human (overdue/escalated/watchdog/lost)
    consent_breach: bool  # outreach sent after consent was withdrawn
    handled: tuple[str, ...]  # messy tags that the system explicitly handled
    n_events: int = 0


def build_record(
    idx: int, language: str, channel: str, messy: Iterable[str], follow_up_days: float,
    t0: float, events: Sequence, start_dt: datetime,
) -> CaseRecord:
    first_at = events[0].at
    t_owner = t_contact = t_complete = None
    closes: list[float] = []
    reopens: list[float] = []
    escalations: list[float] = []
    actions: list[float] = []
    last_progress = None
    lost = visible = breach = False
    withdrawn_at = None
    handled: list[str] = []
    for ev in events:
        rel = (ev.at - first_at) / DAY
        et = ev.type
        if et in MAIN_CHAIN_PROGRESS:
            actions.append(rel)
            last_progress = rel
            if et == "accept_ownership":
                if t_owner is None and ev.payload.get("owner"):
                    t_owner = rel
            elif et == "contact_patient":
                if t_contact is None:
                    t_contact = rel
            elif et == "complete_confirmatory":
                if t_complete is None:
                    t_complete = rel
            elif et == "close":
                closes.append(rel)
        elif et in EXTRA_ACTIONS:
            actions.append(rel)
            if et == "contact_recovered":
                handled.append("wrong_contact")
            elif et == "handoff_accepted" and ev.payload.get("kind") == "transfer":
                handled.append("provider_switch")
            elif et == "consent_review_completed":
                handled.append("consent_after_booking")
        elif et == "reopened":
            reopens.append(rel)
        elif et == "escalated":
            escalations.append(rel)
        elif et == "duplicate_result_merged":
            handled.append("duplicate_result")
        elif et == "conflicting_ack_rejected":
            handled.append("conflicting_ack")
        elif et == "consent_updated" and ev.payload.get("status") == "withdrawn":
            if withdrawn_at is None:
                withdrawn_at = rel
        elif et == "message_sent" and withdrawn_at is not None and rel > withdrawn_at:
            breach = True
        if et == "lost_contact":
            lost = True
        if et in VISIBILITY_EVENTS:
            visible = True
    return CaseRecord(
        idx=idx, language=language, channel=channel, messy=tuple(messy), follow_up_days=follow_up_days,
        t0=t0, t_owner=t_owner, t_contact=t_contact, t_complete=t_complete,
        closes=tuple(closes), reopens=tuple(reopens), escalations=tuple(escalations),
        actions=tuple(actions), last_progress=last_progress, lost_contact=lost, visible=visible,
        consent_breach=breach, handled=tuple(dict.fromkeys(handled)), n_events=len(events),
    )


# ---------------------------------------------------------------------------------------------
# statistics helpers
# ---------------------------------------------------------------------------------------------

def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (q in [0, 1]); None for an empty sample."""
    if not values:
        return None
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (pos - lo) * (s[hi] - s[lo])


def _share(flags: Sequence[bool]) -> float | None:
    return sum(1 for f in flags if f) / len(flags) if flags else None


def is_open_at(rec: CaseRecord, day: float) -> bool:
    """Open = not currently closed. A close that was later reopened counts as open again."""
    for i, c in enumerate(rec.closes):
        if c <= day:
            r = rec.reopens[i] if i < len(rec.reopens) else None
            if r is None or r > day:
                return False
    return True


def _acted_within(rec: CaseRecord, cal: WorkingCalendar, esc: float, window_wd: int) -> bool:
    deadline_abs = cal.add_working_days_t(rec.t0 + esc, window_wd)
    deadline = deadline_abs - rec.t0
    return any(esc < a <= deadline for a in rec.actions)


def is_ltfu(rec: CaseRecord, idle_days: float) -> bool:
    """Lost to follow-up: no confirmatory completion by the end of follow-up and no progress for
    `idle_days` (or an explicit lost_contact flag)."""
    if rec.t_complete is not None:
        return False
    if rec.lost_contact:
        return True
    last = rec.last_progress if rec.last_progress is not None else 0.0
    return rec.follow_up_days - last >= idle_days


# ---------------------------------------------------------------------------------------------
# metric computation
# ---------------------------------------------------------------------------------------------

def _closure_time(rec: CaseRecord) -> float | None:
    return rec.closes[0] if rec.closes else None


def _median(vals: Sequence[float]) -> float | None:
    return percentile(vals, 0.5)


def _group_stats(recs: Sequence[CaseRecord]) -> dict[str, float | None]:
    ct = [c for c in map(_closure_time, recs) if c is not None]
    return {
        "n": float(len(recs)),
        "completion_final": _share([r.t_complete is not None for r in recs]),
        "closure_median": _median(ct),
    }


def _gap(values: Sequence[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return (max(vs) - min(vs)) if len(vs) >= 2 else None


def compute_metrics(
    records: Sequence[CaseRecord],
    *,
    cal: WorkingCalendar,
    ltfu_idle_days: float,
    escalation_window_wd: int,
    min_group_size: int,
    delay_harm_days: float | None = None,
    abstention_hook: AbstentionHook | None = None,
) -> dict[str, float | None]:
    n = len(records)
    out: dict[str, float | None] = {"n_cases": float(n)}
    if n == 0:
        return out
    horizon = min(r.follow_up_days for r in records)

    owners = [r.t_owner for r in records if r.t_owner is not None]
    out["owned_share"] = len(owners) / n
    out["time_to_ownership_median"] = _median(owners)
    out["time_to_ownership_p90"] = percentile(owners, 0.9)
    contacts = [r.t_contact for r in records if r.t_contact is not None]
    out["time_to_first_contact_median"] = _median(contacts)
    out["time_to_first_contact_p90"] = percentile(contacts, 0.9)

    closure = [c for c in map(_closure_time, records) if c is not None]
    out["closed_share"] = len(closure) / n
    out["time_to_closure_median"] = _median(closure)
    out["time_to_closure_p90"] = percentile(closure, 0.9)

    for d in (30, 60, 90):
        out[f"open_{d}d"] = _share([is_open_at(r, d) for r in records]) if horizon >= d else None
    comp_times = [r.t_complete for r in records if r.t_complete is not None]
    out["time_to_colonoscopy_median"] = _median(comp_times)
    out["time_to_colonoscopy_p25"] = percentile(comp_times, 0.25)
    out["time_to_colonoscopy_p75"] = percentile(comp_times, 0.75)
    for d in (90, 180, 360):
        out[f"completion_{d}d"] = (
            _share([r.t_complete is not None and r.t_complete <= d for r in records]) if horizon >= d else None
        )
    out["completion_final"] = _share([r.t_complete is not None for r in records])
    if delay_harm_days is not None and horizon >= delay_harm_days:
        # share with no colonoscopy by the delay-harm threshold (never, or later)
        out["delay_harm_share"] = _share([r.t_complete is None or r.t_complete > delay_harm_days for r in records])
    else:
        out["delay_harm_share"] = None

    closed = [r for r in records if r.closes]
    out["reopen_rate"] = (sum(1 for r in closed if r.reopens) / len(closed)) if closed else None

    esc_total = sum(len(r.escalations) for r in records)
    out["escalations_per_100_cases"] = 100.0 * esc_total / n
    if esc_total:
        good = sum(1 for r in records for e in r.escalations if _acted_within(r, cal, e, escalation_window_wd))
        out["escalation_precision"] = good / esc_total
    else:
        out["escalation_precision"] = None

    out["automation_abstention_rate"] = abstention_hook(records) if abstention_hook else None

    ltfu = [is_ltfu(r, ltfu_idle_days) for r in records]
    out["lost_to_follow_up_rate"] = _share(ltfu)
    out["silent_loss_rate"] = _share([lf and not r.visible for lf, r in zip(ltfu, records)])
    out["consent_breach_rate"] = _share([r.consent_breach for r in records])

    # fairness: completion and time to closure by language and by preferred channel
    for dim in ("language", "channel"):
        groups: dict[str, list[CaseRecord]] = {}
        for r in records:
            groups.setdefault(getattr(r, dim), []).append(r)
        comp_vals, clos_vals = [], []
        for g in sorted(groups):
            st = _group_stats(groups[g])
            for k, v in st.items():
                out[f"fairness.{dim}.{g}.{k}"] = v
            if len(groups[g]) >= min_group_size:
                comp_vals.append(st["completion_final"])
                clos_vals.append(st["closure_median"])
        out[f"fairness.{dim}.completion_gap_max"] = _gap(comp_vals)
        out[f"fairness.{dim}.closure_median_gap_max_days"] = _gap(clos_vals)

    # messy-case handling
    for tag in MESSY_TAGS:
        aff = [r for r in records if tag in r.messy]
        un = [r for r in records if tag not in r.messy]
        out[f"messy.{tag}.n"] = float(len(aff))
        out[f"messy.{tag}.handled_rate"] = _share([tag in r.handled for r in aff])
        out[f"messy.{tag}.completion_final"] = _share([r.t_complete is not None for r in aff])
        out[f"messy.{tag}.completion_final_unaffected"] = _share([r.t_complete is not None for r in un])
        out[f"messy.{tag}.closed_share"] = _share([bool(r.closes) for r in aff])
        out[f"messy.{tag}.closure_median"] = _median([c for c in map(_closure_time, aff) if c is not None])
    return out
