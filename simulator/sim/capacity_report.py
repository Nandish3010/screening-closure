"""The "Under finite capacity" section of results.md, rendered from capacity.json (ticket B19).

`make evaluate` writes results.md with this section taken from the committed docs/results/capacity.json (it does not rerun the
capacity sweep: about 3 minutes, see the Makefile); `make evaluate-capacity` rewrites capacity.json and splices the section
into results.md in place, between two marker comments, leaving every other byte as it was.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .capacity_eval import CAPACITY_LEVELS, STAFFING_LEVELS, _key
from .engine import BASELINE, ORCHESTRATED
from .evaluate import LITERATURE, UPPER

BEGIN = "<!-- capacity-section:begin (written by make evaluate-capacity; make evaluate copies it from capacity.json) -->"
END = "<!-- capacity-section:end -->"
TITLE = {UPPER: "upper range", LITERATURE: "literature anchored"}
STUB = ("## Under finite capacity\n\nNot generated yet: `docs/results/capacity.json` does not exist. Run `make evaluate-capacity` "
        "(about 3 minutes) and then `make evaluate`.")


# ---- small formatting helpers ------------------------------------------------------------------------------------------

def _f(v, kind: str, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    if kind == "pct":
        return (f"{100 * v:+.1f}pp" if signed else f"{100 * v:.1f}%")
    if kind == "days":
        return f"{v:+.1f}" if signed else f"{v:.1f}"
    if kind == "int":
        return f"{v:.0f}"
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def _pv(cap: dict, key: str):
    return next(p["value"] for p in cap["parameters"] if p["key"] == "capacity." + key)


def _m(block: dict, key: str):
    s = block.get(key)
    return None if not s else s["mean"]


def _cell(s: dict | None, kind: str, signed: bool = False, ci: bool = True) -> str:
    if not s:
        return "n/a"
    if not ci or s.get("ci_low") is None:
        return _f(s["mean"], kind, signed)
    return f"{_f(s['mean'], kind, signed)} ({_f(s['ci_low'], kind, signed)} to {_f(s['ci_high'], kind, signed)})"


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _arm(block: dict, arm: str) -> dict:
    return block["arms"][arm]


# ---- headline at base capacity ---------------------------------------------------------------------------------------------

HEAD_ROWS = [
    ("completion_90d", "Colonoscopy completed within 90 days", "pct"),
    ("completion_180d", "Colonoscopy completed within 180 days", "pct"),
    ("completion_360d", "Colonoscopy completed within 360 days", "pct"),
    ("time_to_colonoscopy_median", "Time to colonoscopy, median among completers (days)", "days"),
    ("time_to_colonoscopy_km_median", "Time to colonoscopy, Kaplan-Meier median (days)", "days"),
    ("open_90d", "Cases still open at 90 days", "pct"),
    ("closed_share", "Cases closed by end of follow-up", "pct"),
    ("lost_to_follow_up_rate", "Lost to follow-up", "pct"),
    ("delay_harm_share", "No colonoscopy by 270 days", "pct"),
]


def headline_tables(cap: dict) -> list[str]:
    out = []
    for scen in cap["meta"]["scenarios"]:
        sc = cap["scenarios"][scen]
        u, b = sc["unconstrained"], sc["base"]
        rows = []
        for key, label, kind in HEAD_ROWS:
            rows.append([
                label,
                _cell(_arm(u, BASELINE).get(key), kind, ci=False), _cell(_arm(u, ORCHESTRATED).get(key), kind, ci=False),
                _cell(u["difference"].get(key), kind, signed=True),
                _cell(_arm(b, BASELINE).get(key), kind, ci=False), _cell(_arm(b, ORCHESTRATED).get(key), kind, ci=False),
                _cell(b["difference"].get(key), kind, signed=True),
            ])
        out += [
            f"**{TITLE[scen].capitalize()}{' (the one to quote)' if scen == LITERATURE else ' (mechanism ceiling)'}.** "
            "Means over replications; the difference column is orchestrated minus baseline with its 95% interval.",
            "",
            _table(["Metric", "Baseline, no limit", "Orchestrated, no limit", "Difference, no limit",
                    "Baseline, base capacity", "Orchestrated, base capacity", "Difference, base capacity"], rows),
            "",
        ]
    return out


CAP_ROWS = [
    ("slot_utilisation", "Slots booked / offered, days 30 to 150", "pct"),
    ("slot_used_share", "Slots used (attended or refilled) / offered", "pct"),
    ("bookings_per_100_cases", "Appointments booked per 100 cases", "num"),
    ("request_to_slot_wait_median", "Request to appointment, median (days)", "days"),
    ("request_to_slot_wait_p90", "Request to appointment, p90 (days)", "days"),
    ("capacity_delay_mean", "Of which waiting for a free slot, mean (days)", "days"),
    ("booked_late_share", "Appointments pushed back a day or more by a full calendar", "pct"),
]
Q_ROWS = [
    ("esc_jobs_per_100_cases", "Escalations and watchdog flags to work, per 100 cases", "num"),
    ("esc_supervisor_utilisation", "Supervisor budget used, days 30 to 150", "pct"),
    ("esc_coordinator_utilisation", "Coordinator budget used, days 30 to 150", "pct"),
    ("esc_delay_mean", "Wait before an escalation is worked, mean (days)", "days"),
    ("esc_delay_p90", "Wait before an escalation is worked, p90 (days)", "days"),
    ("esc_delayed_share", "Escalations that waited half a day or more", "pct"),
    ("esc_queue_mean", "Escalations waiting at the end of a working day, mean", "int"),
    ("esc_queue_max", "Escalations waiting at the end of a working day, worst day", "int"),
    ("esc_stale_share", "Escalations that were stale when their turn came (the case had moved on)", "pct"),
]


def capacity_tables(cap: dict) -> list[str]:
    scens = cap["meta"]["scenarios"]
    rows = []
    for key, label, kind in CAP_ROWS:
        row = [label]
        for s in scens:
            b = cap["scenarios"][s]["base"]
            row += [_cell(_arm(b, BASELINE).get(key), kind, ci=False) if s == scens[0] else "",
                    _cell(_arm(b, ORCHESTRATED).get(key), kind, ci=False)]
        rows.append(row)
    # baseline is the same in both scenarios: one baseline column, then one orchestrated column per scenario
    header = ["Slots and waiting at base capacity", "Baseline"] + [f"Orchestrated, {TITLE[s]}" for s in scens]
    rows = []
    for key, label, kind in CAP_ROWS:
        b0 = cap["scenarios"][scens[0]]["base"]
        rows.append([label, _cell(_arm(b0, BASELINE).get(key), kind, ci=False)]
                    + [_cell(_arm(cap["scenarios"][s]["base"], ORCHESTRATED).get(key), kind, ci=False) for s in scens])
    t1 = _table(header, rows)
    rows2 = [[label] + [_cell(_arm(cap["scenarios"][s]["base"], ORCHESTRATED).get(key), kind, ci=False) for s in scens]
             for key, label, kind in Q_ROWS]
    t2 = _table(["Escalation handling at base staffing (orchestrated arm)"] + [TITLE[s].capitalize() for s in scens], rows2)
    return [t1, "", t2, ""]


def capacity_sensitivity(cap: dict) -> list[str]:
    out = []
    for scen in cap["meta"]["scenarios"]:
        sc = cap["scenarios"][scen]
        rows = []
        for lv in (None,) + tuple(CAPACITY_LEVELS):
            blk = sc["unconstrained"] if lv is None else sc["capacity_sensitivity"][_key(lv)]
            name = "no limit" if lv is None else f"{round(100 * lv)}%" + (" (base)" if lv == 1.0 else "")
            rows.append([
                name,
                _cell(_arm(blk, BASELINE).get("slot_utilisation"), "pct", ci=False) if lv else "n/a",
                _cell(_arm(blk, ORCHESTRATED).get("slot_utilisation"), "pct", ci=False) if lv else "n/a",
                _cell(_arm(blk, BASELINE).get("request_to_slot_wait_median"), "days", ci=False) if lv else "n/a",
                _cell(_arm(blk, ORCHESTRATED).get("request_to_slot_wait_median"), "days", ci=False) if lv else "n/a",
                _cell(blk["difference"].get("completion_90d"), "pct", signed=True),
                _cell(blk["difference"].get("completion_180d"), "pct", signed=True),
                _cell(blk["difference"].get("completion_360d"), "pct", signed=True),
                _cell(_arm(blk, BASELINE).get("time_to_colonoscopy_median"), "days", ci=False),
                _cell(_arm(blk, ORCHESTRATED).get("time_to_colonoscopy_median"), "days", ci=False),
            ])
        out += [
            f"**{TITLE[scen].capitalize()}.** Slot supply moved to 70, 85, 100 and 115% of base; escalation staffing held at base. "
            "Differences are orchestrated minus baseline (the gain from the orchestration), with 95% intervals.",
            "",
            _table(["Slot supply", "Utilisation, baseline", "Utilisation, orchestrated", "Request to appointment, baseline (median days)",
                    "Request to appointment, orchestrated (median days)", "Gain in 90-day completion", "Gain in 180-day completion",
                    "Gain in 360-day completion", "Time to colonoscopy, baseline (median days)", "Time to colonoscopy, orchestrated (median days)"],
                   rows),
            "",
        ]
    return out


def staffing_sensitivity(cap: dict) -> list[str]:
    out = []
    for scen in cap["meta"]["scenarios"]:
        sc = cap["scenarios"][scen]
        rows = []
        for lv in STAFFING_LEVELS:
            v = sc["staffing_sensitivity"][_key(lv)]
            a = v["arm"]
            rows.append([
                f"{round(100 * lv)}%" + (" (base)" if lv == 1.0 else ""),
                _cell(a.get("esc_supervisor_utilisation"), "pct", ci=False), _cell(a.get("esc_coordinator_utilisation"), "pct", ci=False),
                _cell(a.get("esc_delay_mean"), "days", ci=False), _cell(a.get("esc_delay_p90"), "days", ci=False),
                _cell(a.get("esc_queue_mean"), "int", ci=False), _cell(a.get("esc_stale_share"), "pct", ci=False),
                _cell(a.get("completion_180d"), "pct", ci=False),
                _cell(v["difference"].get("completion_90d"), "pct", signed=True),
                _cell(v["difference"].get("completion_180d"), "pct", signed=True),
                _cell(a.get("time_to_colonoscopy_median"), "days", ci=False),
            ])
        out += [
            f"**{TITLE[scen].capitalize()}.** Supervisor and coordinator budgets moved together to 50, 75, 100, 150, 200 and 300% of base; "
            "slot supply held at base. Gains are against the baseline arm at base capacity (the baseline raises no escalations, so staffing does not touch it).",
            "",
            _table(["Staffing", "Supervisor budget used", "Coordinator budget used", "Wait to be worked, mean (days)",
                    "Wait to be worked, p90 (days)", "Escalations waiting, mean", "Stale when worked", "180-day completion, orchestrated",
                    "Gain in 90-day completion", "Gain in 180-day completion", "Time to colonoscopy, orchestrated (median days)"],
                   rows),
            "",
        ]
    return out


# ---- the plain-English reading ---------------------------------------------------------------------------------------------

def _gain(blk: dict, key: str):
    return _m(blk["difference"], key)


def _shrink(unc: float | None, cap: float | None) -> str:
    """How the gain changed, in words that are true for the numbers (no CI logic: the tables have the intervals)."""
    if unc is None or cap is None:
        return "cannot be compared"
    if unc <= 0:
        return "was not positive to begin with"
    r = cap / unc
    if r >= 1.1:
        return f"is larger ({100 * r:.0f}% of the unconstrained gain)"
    if r >= 0.9:
        return f"holds ({100 * r:.0f}% of the unconstrained gain)"
    if r >= 0.15:
        return f"shrinks to {100 * r:.0f}% of the unconstrained gain"
    if r > -0.15:
        return f"is almost gone ({100 * r:.0f}% of the unconstrained gain is left)"
    return "reverses sign"


def decomposition_table(cap: dict) -> list[str]:
    out = []
    for scen in cap["meta"]["scenarios"]:
        sc = cap["scenarios"][scen]
        rows = []
        for label, blk_arm, blk_diff in (
            ("No limit (reference)", sc["unconstrained"]["arms"][ORCHESTRATED], sc["unconstrained"]["difference"]),
            ("Slots limited, staff unlimited", sc["decomposition"]["slots_only"]["arm"], sc["decomposition"]["slots_only"]["difference"]),
            ("Staff limited, slots unlimited", sc["decomposition"]["staff_only"]["arm"], sc["decomposition"]["staff_only"]["difference"]),
            ("Both limited (base capacity)", sc["base"]["arms"][ORCHESTRATED], sc["base"]["difference"]),
        ):
            rows.append([label, _cell(blk_arm.get("completion_90d"), "pct", ci=False), _cell(blk_arm.get("completion_180d"), "pct", ci=False),
                         _cell(blk_arm.get("completion_360d"), "pct", ci=False),
                         _cell(blk_diff.get("completion_90d"), "pct", signed=True), _cell(blk_diff.get("completion_180d"), "pct", signed=True),
                         _cell(blk_diff.get("completion_360d"), "pct", signed=True),
                         _cell(blk_diff.get("time_to_colonoscopy_km_median"), "days", signed=True)])
        out += [
            f"**{TITLE[scen].capitalize()}.** Orchestrated arm with one limit at a time. The gain is against the baseline arm under the same slot limit "
            "(the baseline raises no escalations, so staff limits never touch it).",
            "",
            _table(["Which limit binds", "90-day completion", "180-day completion", "360-day completion", "Gain at 90 days", "Gain at 180 days",
                    "Gain at 360 days", "Change in Kaplan-Meier median time to colonoscopy (days)"], rows),
            "",
        ]
    return out


def reading(cap: dict) -> list[str]:
    lit = LITERATURE if LITERATURE in cap["scenarios"] else cap["meta"]["scenarios"][0]
    sc = cap["scenarios"][lit]
    u, b = sc["unconstrained"], sc["base"]
    g = lambda blk, k: _gain(blk, k)  # noqa: E731
    pp = lambda x: f"{100 * x:+.1f} percentage points"  # noqa: E731
    d = lambda x: f"{x:+.0f} days"  # noqa: E731
    c70, c85, c115 = (sc["capacity_sensitivity"][k] for k in ("70", "85", "115"))
    st = sc["staffing_sensitivity"]
    so, fo = sc["decomposition"]["slots_only"], sc["decomposition"]["staff_only"]
    up = cap["scenarios"].get(UPPER)
    util_b = _m(_arm(b, BASELINE), "slot_utilisation")
    util_o = _m(_arm(b, ORCHESTRATED), "slot_utilisation")
    book_b = _m(_arm(b, BASELINE), "bookings_per_100_cases")
    book_o = _m(_arm(b, ORCHESTRATED), "bookings_per_100_cases")
    wait_b = _m(_arm(b, BASELINE), "request_to_slot_wait_median")
    wait_o = _m(_arm(b, ORCHESTRATED), "request_to_slot_wait_median")
    esc_d = _m(_arm(b, ORCHESTRATED), "esc_delay_mean")
    esc_q = _m(_arm(b, ORCHESTRATED), "esc_queue_mean")
    paras = [
        "**In plain English, for the literature-anchored scenario.** Give the programme a finite number of colonoscopy slots and a finite number of staff minutes "
        "and the benefit in *how soon* patients get their colonoscopy shrinks sharply (the 90-day advantage is almost gone and the advantage in median time to colonoscopy roughly halves), "
        "while the benefit in *whether* they get one within six to twelve months mostly survives at base capacity and then falls away quickly if slots are scarcer than that.",
        "",
        f"- **Completion within 90 days.** Without a limit, orchestration completes {pp(g(u, 'completion_90d'))} more patients within 90 days than today's process. "
        f"At base capacity the gain {_shrink(g(u, 'completion_90d'), g(b, 'completion_90d'))}: {pp(g(b, 'completion_90d'))}. The share of cases still open at 90 days improves by "
        f"{abs(g(b, 'open_90d')) * 100:.1f} points against {abs(g(u, 'open_90d')) * 100:.1f} without a limit. The calendar is {100 * util_b:.0f}% full under today's process and {100 * util_o:.0f}% full with the orchestration, "
        f"which books {book_o:.0f} appointments per 100 cases against {book_b:.0f}: the patients it brings in queue behind patients already booked, and the median wait from request to appointment is "
        f"{wait_o:.0f} days against {wait_b:.0f}.",
        f"- **Time to colonoscopy.** The censoring-aware (Kaplan-Meier) median time to colonoscopy is shortened by {abs(g(u, 'time_to_colonoscopy_km_median')):.0f} days without a limit and by "
        f"{abs(g(b, 'time_to_colonoscopy_km_median')):.0f} days at base capacity (the gain {_shrink(-g(u, 'time_to_colonoscopy_km_median'), -g(b, 'time_to_colonoscopy_km_median'))}). "
        f"The plain median among patients who had a colonoscopy goes the other way ({d(g(u, 'time_to_colonoscopy_median'))} without a limit, {d(g(b, 'time_to_colonoscopy_median'))} at base capacity, orchestrated minus baseline), "
        "because the orchestration brings in extra patients who are, on average, later than the ones who would have come anyway; the Kaplan-Meier figure is the fair one.",
        f"- **Completion within 180 and 360 days.** The 180-day gain {_shrink(g(u, 'completion_180d'), g(b, 'completion_180d'))} "
        f"({pp(g(b, 'completion_180d'))} against {pp(g(u, 'completion_180d'))} without a limit) and the 360-day gain "
        f"{_shrink(g(u, 'completion_360d'), g(b, 'completion_360d'))} ({pp(g(b, 'completion_360d'))}). A full calendar delays a colonoscopy; "
        f"at base capacity it does not stop most of the extra patients from getting one within six months. Part of why the gain is not smaller is a model effect explained under *Which limit does it*.",
        f"- **If slots are scarcer, the 180-day gain goes quickly.** It is {pp(g(c115, 'completion_180d'))} at 115% of base slot supply, {pp(g(b, 'completion_180d'))} at 100%, "
        f"{pp(g(c85, 'completion_180d'))} at 85% and {pp(g(c70, 'completion_180d'))} at 70%. The orchestration needs room for roughly "
        f"{100 * (book_o / book_b - 1):.0f}% more appointments than today's process; once the calendar cannot take them the extra patients wait past the 180-day mark, and no escalation can make a slot appear. "
        f"The 360-day gain stays at {pp(g(c70, 'completion_360d'))} even at 70%, because the demand is a single wave and the queue always clears in the end; with a steady inflow of new positives it would not "
        "(see the limits below).",
        f"- **Staff minutes.** At base staffing an escalation waits a mean of {esc_d:.1f} days to be worked, with {esc_q:.0f} waiting on an average day. "
        f"Halving the staff takes the wait to {_m(st['50']['arm'], 'esc_delay_mean'):.1f} days; doubling them takes it to {_m(st['200']['arm'], 'esc_delay_mean'):.1f} and tripling them to {_m(st['300']['arm'], 'esc_delay_mean'):.1f}: "
        "a team sized to 85% of the average workload needs roughly twice that size to keep escalations from queueing. "
        f"Completion hardly moves with it: the 180-day gain is {pp(_m(st['50']['difference'], 'completion_180d'))} at 50% staffing and {pp(_m(st['300']['difference'], 'completion_180d'))} at 300%.",
        f"- **Which limit does it.** The slots do the damage to speed. Limiting only the slots leaves a 90-day gain of {pp(_m(so['difference'], 'completion_90d'))}, a 180-day gain of {pp(_m(so['difference'], 'completion_180d'))} and a 360-day gain of "
        f"{pp(_m(so['difference'], 'completion_360d'))}. Limiting only the staff does not lower completion at all in this model: it leaves {pp(_m(fo['difference'], 'completion_90d'))}, {pp(_m(fo['difference'], 'completion_180d'))} and "
        f"{pp(_m(fo['difference'], 'completion_360d'))}, which is *more* than with no limit. That is a property of the model's ladder, not a finding about staffing: an escalation worked days late is more often moot (the case moved on, stale), "
        "so fewer chases and referral bounces go out, and each chase that goes unanswered or bounce that is rejected can push a patient further from care (`patients.dropout_prob_per_lapse`, `handoff.max_attempts`). "
        "The both-limits gain at 180 and 360 days therefore sits about a point above the slots-only gain for that reason, and the staffing rows must be read as statements about waiting and workload, not as evidence that understaffing is harmless.",
        "",
    ]
    if up is not None:
        ub, uu = up["base"], up["unconstrained"]
        paras += [
            f"For the upper-range scenario (the mechanism ceiling) the 90-day gain goes from {pp(g(uu, 'completion_90d'))} to {pp(g(ub, 'completion_90d'))} and the 180-day gain "
            f"from {pp(g(uu, 'completion_180d'))} to {pp(g(ub, 'completion_180d'))}; the orchestration there books {_m(_arm(ub, ORCHESTRATED), 'bookings_per_100_cases'):.0f} appointments per 100 cases "
            f"and the calendar is {100 * _m(_arm(ub, ORCHESTRATED), 'slot_utilisation'):.0f}% full. Its escalation queue is far longer ({_m(_arm(ub, ORCHESTRATED), 'esc_delay_mean'):.0f} days mean wait at base staffing), "
            "because it raises far more escalations than the staff were sized for.",
            "",
        ]
    paras += [
        "**How far to trust it.** The size of these effects follows from two sizing choices that are assumptions, not measurements, and the tables above show what moves when they change:",
        "",
        "1. *The base calendar is sized to today's demand.* Slot supply is set so that the baseline arm fills about 85% of the offered slots between day 30 and day 150, because the ticket asked for 80 to 90% and "
        "no public source says how many slots a catchment of positive-FIT patients has. A real unit with more slack than that would show smaller losses; a unit already full with symptomatic and surveillance patients would show larger ones. "
        "The 70 to 115% rows answer that question.",
        "2. *The base staffing is sized to the literature-anchored workload.* Supervisor and coordinator budgets are set so that the unconstrained workload is about 85% of the budget over the same window. "
        "No public staffing ratio exists. The work arrives as one wave (every result arrives within 30 days), so even a team sized to 85% of the average has a queue while the wave passes.",
        "",
        "**Things the model does not do.** The demand is a single wave: all 2,000 results arrive within 30 days and nothing else uses the calendar apart from the refill of no-shows, so a calendar that is too small only postpones colonoscopies, "
        "and a team that is too small only postpones escalations; with a steady inflow of positives the queue would not clear and the 360-day figures would be worse. The calendar is flat (the same slots every working day, equal providers). "
        "Priorities (urgent and symptomatic patients, the cancer pathway) are not modelled, and neither is overbooking, which units use against no-shows. A stale escalation still costs the reviewer its minutes. "
        "The slot supply and the staff budgets are sized to the workload, not measured, so the absolute waits are properties of that sizing; the sweeps above show how they move.",
        "",
        "**Why the headline at the top is unchanged.** The headline of this document remains the unconstrained model, which is what the earlier results, the sensitivity sweep and the risk score were built on. "
        "Quote the figures in this section whenever the question is what a unit with finite slots and finite staff would see.",
        "",
    ]
    return paras


# ---- assembly --------------------------------------------------------------------------------------------------------------------

def parameter_lines(cap: dict) -> list[str]:
    rows = [[f"`{p['key']}`", str(p["value"]), p["status"]] for p in cap["parameters"] if p["key"] != "capacity.enabled"]
    return [_table(["Parameter (simulator/params.capacity.yaml)", "Value", "Status"], rows), ""]


def render_section(cap: dict) -> str:
    m = cap["meta"]
    lit = LITERATURE if LITERATURE in cap["scenarios"] else m["scenarios"][0]
    b = cap["scenarios"][lit]["base"]
    sec = [
        "## Under finite capacity",
        "",
        f"Generated by `make evaluate-capacity` (seed {m['seed']}, {m['replications']} replications x {m['cases_per_replication']:,} cases x {m['follow_up_days']} days, "
        f"same cohorts and random numbers as the rest of this file; `docs/results/capacity.json`, commit `{m['git_commit'][:12]}`). "
        "Everything above this section is the unconstrained model: cases independent, any number of colonoscopies on any day, escalations free.",
        "",
        f"**What changes.** Every case in a run now shares (1) a booking calendar with a fixed number of colonoscopy slots per working day at each of three providers: a booking takes the earliest free slot after its lead time, "
        f"a no-show frees the slot only if the unit refills it (probability {_pv(cap, 'dna_backfill_prob'):g}), a patient who withdraws consent and declines care cancels and frees it; and (2) a daily minutes budget for the supervisor and the programme coordinator, "
        f"spent on escalations and watchdog flags ({_pv(cap, 'staff.accept_minutes'):g} minutes to accept, plus {_pv(cap, 'staff.contact_minutes'):g} to phone the patient when the case is waiting for them), served first come first served, the overflow waiting for the next working day. "
        "Nothing else changes: same patients, same random numbers, same baseline logic. With so much capacity that nothing ever waits, the event log is identical to the unconstrained one (a test pins it).",
        "",
        f"**How big the base capacity is.** Slot supply is {_pv(cap, 'sessions_per_week_per_1000_cases'):g} sessions a week per 1,000 cases at {_pv(cap, 'slots_per_session'):g} colonoscopies a session "
        f"(a four-hour list is 12 points, a colonoscopy 2 points; NHS Improving Quality), over {_pv(cap, 'providers'):g} providers, which fills "
        f"{100 * _m(_arm(b, BASELINE), 'slot_utilisation'):.0f}% of the slots under today's process between day {m['utilisation_window_days'][0]} and day {m['utilisation_window_days'][1]}. "
        f"Staffing is {_pv(cap, 'staff.supervisor_minutes_per_day_per_1000_cases'):g} supervisor and {_pv(cap, 'staff.coordinator_minutes_per_day_per_1000_cases'):g} coordinator minutes a day per 1,000 cases. "
        "Sources and grades: [`docs/evidence.md`](../evidence.md) section 12.",
        "",
        "### Headline outcomes at base capacity, both scenarios",
        "",
        *headline_tables(cap),
        "### Slots, waiting and the escalation queue at base capacity",
        "",
        *capacity_tables(cap),
        "### Capacity sensitivity: 70, 85, 100 and 115% of base slot supply",
        "",
        *capacity_sensitivity(cap),
        "### Which limit does what: slots only, staff only, both",
        "",
        *decomposition_table(cap),
        "### Staffing sensitivity: escalation budgets at 50 to 300% of base",
        "",
        *staffing_sensitivity(cap),
        "### How much the benefit shrinks when capacity binds",
        "",
        *reading(cap),
        "### Capacity parameters",
        "",
        *parameter_lines(cap),
        "Definitions of every capacity metric: [`metrics.md`](metrics.md) section 12.",
    ]
    return "\n".join(sec)


def section_block(cap: dict | None) -> str:
    return BEGIN + "\n" + (render_section(cap) if cap else STUB) + "\n" + END


def load_capacity(out_dir: Path) -> dict | None:
    p = Path(out_dir) / "capacity.json"
    return json.loads(p.read_text()) if p.exists() else None


def splice_into_results(out_dir: Path) -> bool:
    """Replace the capacity block of results.md in place (or insert it before '## Summary'). False if there is no results.md."""
    md = Path(out_dir) / "results.md"
    if not md.exists():
        return False
    text = md.read_text()
    block = section_block(load_capacity(Path(out_dir)))
    if BEGIN in text and END in text:
        i, j = text.index(BEGIN), text.index(END) + len(END)
        text = text[:i] + block + text[j:]
    else:
        k = text.index("## Summary")
        text = text[:k] + block + "\n\n" + text[k:]
    md.write_text(text)
    return True
