"""Human-effort accounting (A21, ADR-032), ILLUSTRATIVE: how much of one case's work a person did, and how much the system did.

This module is an illustration of the accounting for one case in the running application (`GET /metrics/effort`, the case
page's `effort`). It is NOT where the submission's number comes from. The headline reduction in human effort is computed by
the behaviour simulator over a simulated cohort, with ranges, sources and a sensitivity analysis
(`simulator/sim/effort.py`, ticket B17; `docs/evidence.md` section 11; `docs/results/`). Quote that number, not the
percentage this module shows for a handful of demo cases. Coordinator labour is the scaling limit of a screening-closure
programme, so the system aims to cut human labour by more than `TARGET_REDUCTION_PCT`, and this module shows, case by
case, what that accounting looks like; it is pure functions over a case's events, no clock, no store, no I/O.

`actor_kind(actor)` classifies who wrote an event: `human` (a person acting in one of the six user roles), `automation`
(`system:automation`), `model` (`model:*`, a drafting model) or `service` (the scheduler, watchdog, notifier, consent
service and intake: infrastructure, neither a person nor a substitute for one).

Minutes. `HUMAN_ACTION_MINUTES` is a plain dict of the minutes one human action takes. Where the simulator's table is
importable at runtime (`simulator.sim.effort`, present in a checkout of the repository, absent from the API image) the
minutes are read from it, action by action (`SIMULATOR_ACTION`); anything the simulator does not price, and every value when
it is not importable, comes from this module's own copy, `ASSUMED_MINUTES`, and EVERY VALUE OF THAT COPY IS AN ASSUMPTION
("assumed; to source"): none is a measurement, none has a citation, and the reduction stands or falls with them.
`MINUTES_SOURCE` says which it is and `assumptions()` reports it next to the percentage. The first six rows of the own copy
are the owner's figures (coordinator phone call 6, review 3, referral handling 8, sign-off 4, escalation handling 5, message
approval 2); the rest price the steps the automation replaces and the owner did not list, so that "before" can be compared
with "after".

The comparison. `baseline_minutes` is what the same case would have cost a person with no automation: the minutes of the
human actions that happened, plus the minutes of the manual action each automated event replaced
(`AUTOMATION_REPLACES`). `reduction_pct` is the replaced minutes as a share of the baseline. A person's action that still
happens (the phone call, the review, the sign-off, accepting an escalation) is in both, so the percentage cannot exceed
what the automation really took over. A drafting model's work is not credited (conservative): a model-drafted message
costs a human the approval minutes and saves nothing in this accounting.
"""

from __future__ import annotations

ASSUMED = "assumed; to source"
TARGET_REDUCTION_PCT = 40.0

# The roles a person can act in (equals app.auth.USER_ROLES; tests/test_effort_api.py keeps the two in step).
HUMAN_ROLES = ("lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor")
STAFF_ROLES = tuple(r for r in HUMAN_ROLES if r != "patient")  # a patient's own action is not staff labour
AUTOMATION_ACTOR = "system:automation"

ACTOR_KINDS = ("human", "automation", "model", "service")

# Minutes of one human action: this module's OWN COPY. ALL ASSUMED (ASSUMED above): to source. Used for every action the
# simulator's table does not price, and for all of them when the simulator is not importable.
ASSUMED_MINUTES: dict[str, float] = {
    # the owner's figures (A21)
    "coordinator_phone_call": 6,   # contact_patient: phoning the patient about the result and the next step
    "clinician_review": 3,         # clinician_review: reading the colonoscopy outcome
    "referral_handling": 8,        # handoff_requested / reassign: choosing, sending and chasing a referral
    "sign_off": 4,                 # close: checking the evidence and signing the closure
    "escalation_handling": 5,      # escalation_accepted: reading an escalation and acting on it
    "message_approval": 2,         # message_approved / message_rejected: reading a drafted message and deciding
    # priced here so that the replaced steps have a cost (also assumed)
    "result_acknowledgement": 2,   # acknowledge: logging that the lab result reached the programme
    "owner_assignment": 2,         # finding the registered provider's owner and routing the case to them
    "accept_ownership": 1,         # accept_ownership: one click by the assigned owner
    "manual_message": 3,           # writing and sending one patient message or reminder by hand
    "evidence_chase": 5,           # finding the colonoscopy result and recording it on the case
    "closure_preparation": 2,      # pulling the evidence reference together for the sign-off
    "appointment_booking": 5,      # book_confirmatory: booking the confirmatory appointment
}

# Which row of the simulator's table (simulator/sim/effort.py, `MINUTES`, keyed by action id) prices each action here. An action
# with no counterpart there (accept_ownership, closure_preparation) keeps its own copy.
SIMULATOR_ACTION: dict[str, str] = {
    "coordinator_phone_call": "M_PHONE_ATTEMPT",
    "clinician_review": "M_CLINICIAN_REVIEW",
    "referral_handling": "M_REFERRAL_SEND",
    "sign_off": "O_SIGN_CLOSURE",
    "escalation_handling": "O_ACCEPT_ESC_COORDINATOR",
    "message_approval": "O_APPROVE_DRAFT",
    "result_acknowledgement": "M_RESULT_ACK",
    "owner_assignment": "M_HANDOVER",
    "manual_message": "M_SMS_ATTEMPT",
    "evidence_chase": "M_COMPLETION_RECORD",
    "appointment_booking": "X_BOOK_APPT",
}
SIMULATOR_MODULE = "simulator.sim.effort"
OWN_COPY = "api/app/effort.py (own copy, assumed)"


def load_minutes(module=None) -> tuple[dict[str, float], str, tuple[str, ...]]:
    """(minutes, source, actions priced by the simulator). Reads the simulator's table when `simulator.sim.effort` can be
    imported (or `module` is given) and has a `MINUTES` mapping; any failure, or a missing row, falls back to the own copy,
    never to an error: the API must start without the simulator."""
    minutes = dict(ASSUMED_MINUTES)
    try:
        if module is None:
            import importlib

            module = importlib.import_module(SIMULATOR_MODULE)
        table = module.MINUTES
        taken = {}
        for action, sim_id in SIMULATOR_ACTION.items():
            value = table.get(sim_id)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                taken[action] = float(value)
    except Exception:  # noqa: BLE001 - not importable (the API image), or a table of another shape: use the own copy
        return minutes, OWN_COPY, ()
    if not taken:
        return minutes, OWN_COPY, ()
    minutes.update(taken)
    return minutes, "simulator/sim/effort.py", tuple(sorted(taken))


# What the accounting uses. Tests that need the figures in the own copy patch this with `dict(ASSUMED_MINUTES)`.
HUMAN_ACTION_MINUTES: dict[str, float]
MINUTES_SOURCE: str
FROM_SIMULATOR: tuple[str, ...]
HUMAN_ACTION_MINUTES, MINUTES_SOURCE, FROM_SIMULATOR = load_minutes()

# Event type -> the human action it counts as, when a PERSON wrote it. An event with no row is a human action that is
# counted but has no minutes in the table (the report says how many).
HUMAN_EVENT_ACTION: dict[str, str] = {
    "acknowledge": "result_acknowledgement",
    "accept_ownership": "accept_ownership",
    "reassign": "referral_handling",
    "handoff_requested": "referral_handling",
    "contact_patient": "coordinator_phone_call",
    "book_confirmatory": "appointment_booking",
    "complete_confirmatory": "evidence_chase",
    "clinician_review": "clinician_review",
    "close": "sign_off",
    "escalation_accepted": "escalation_handling",
    "message_approved": "message_approval",
    "message_rejected": "message_approval",
}

# Event type -> the manual action it replaced, when `system:automation` wrote it. None: it replaces nothing (it hands a
# decision to a person), so it adds no saving.
AUTOMATION_REPLACES: dict[str, str | None] = {
    "acknowledge": "result_acknowledgement",
    "assign_ownership": "owner_assignment",
    "message_sent": "manual_message",
    "complete_confirmatory": "evidence_chase",
    "closure_proposed": "closure_preparation",
    "handoff_requested": "referral_handling",
    "review_requested": None,
}


def actor_kind(actor: str | None) -> str:
    """`human`, `automation`, `model` or `service` for an event's actor string."""
    if not actor:
        return "service"
    if actor == AUTOMATION_ACTOR:
        return "automation"
    if actor.startswith("model:"):
        return "model"
    return "human" if actor.split(":", 1)[0] in HUMAN_ROLES else "service"


def _field(event, name):
    return event.get(name) if isinstance(event, dict) else getattr(event, name)


def _is_staff(actor: str) -> bool:
    return actor_kind(actor) == "human" and actor.split(":", 1)[0] in STAFF_ROLES


def minutes_for(action: str | None) -> float:
    return float(HUMAN_ACTION_MINUTES[action]) if action else 0.0


def case_effort(events) -> dict:
    """The effort accounting of one case from its events (objects with `type` and `actor`, or dicts).

    `human_actions` counts staff (a person in any role but patient); `patient_actions`, `automated_actions`,
    `model_actions` and `service_actions` count the rest (the genesis marker `case_created` is none of them).
    `automation_share` is automated / (automated + human staff), None before either has acted."""
    counts = {"human": 0, "patient": 0, "automation": 0, "model": 0, "service": 0}
    human_minutes = saved_minutes = 0.0
    unpriced = 0
    for e in events:
        kind_, actor = _field(e, "type"), _field(e, "actor")
        if kind_ == "case_created":
            continue
        kind = actor_kind(actor)
        if kind == "human":
            if _is_staff(actor):
                counts["human"] += 1
                action = HUMAN_EVENT_ACTION.get(kind_)
                if action is None:
                    unpriced += 1
                human_minutes += minutes_for(action)
            else:
                counts["patient"] += 1
        else:
            counts[kind] += 1
            if kind == "automation":
                saved_minutes += minutes_for(AUTOMATION_REPLACES.get(kind_))
    acted = counts["automation"] + counts["human"]
    baseline = human_minutes + saved_minutes
    return {
        "human_actions": counts["human"],
        "automated_actions": counts["automation"],
        "patient_actions": counts["patient"],
        "model_actions": counts["model"],
        "service_actions": counts["service"],
        "automation_share": round(counts["automation"] / acted, 4) if acted else None,
        "human_minutes": round(human_minutes, 2),
        "baseline_minutes": round(baseline, 2),
        "minutes_saved": round(saved_minutes, 2),
        "reduction_pct": round(100 * saved_minutes / baseline, 1) if baseline else None,
        "unpriced_human_actions": unpriced,
    }


def human_minutes_per_case(events) -> float:
    """Minutes of human time the case has cost so far, from the assumed table (B17 uses this per simulated case)."""
    return case_effort(events)["human_minutes"]


def effort_report(per_case: dict) -> dict:
    """Totals over `{case_id: events}`: the sums of each case's accounting, the automation share and the reduction
    against the target. `meets_target` is True only when the reduction is above `TARGET_REDUCTION_PCT`."""
    rows = {cid: case_effort(events) for cid, events in per_case.items()}
    keys = ("human_actions", "automated_actions", "patient_actions", "model_actions", "service_actions",
            "human_minutes", "baseline_minutes", "minutes_saved", "unpriced_human_actions")
    total = {k: round(sum(r[k] for r in rows.values()), 2) for k in keys}
    for k in ("human_actions", "automated_actions", "patient_actions", "model_actions", "service_actions",
              "unpriced_human_actions"):
        total[k] = int(total[k])
    acted = total["automated_actions"] + total["human_actions"]
    total["automation_share"] = round(total["automated_actions"] / acted, 4) if acted else None
    total["reduction_pct"] = (round(100 * total["minutes_saved"] / total["baseline_minutes"], 1)
                              if total["baseline_minutes"] else None)
    total["target_pct"] = TARGET_REDUCTION_PCT
    total["meets_target"] = bool(total["reduction_pct"] is not None and total["reduction_pct"] > TARGET_REDUCTION_PCT)
    return {"rows": rows, "totals": total}


def assumptions() -> dict:
    """What the page must say next to the percentage: where the minutes come from and that they are not measurements."""
    from_sim = bool(FROM_SIMULATOR)
    return {
        "status": ASSUMED if not from_sim else "from the simulator's table; rows without a source there are assumed",
        "source": MINUTES_SOURCE,
        "illustrative": True,
        "note": ("Illustrative, per case. The number for the submission comes from the behaviour simulator "
                 "(simulator/sim/effort.py, docs/results). "
                 + ("Every minute value here is an assumption with no source yet; the reduction is only as good as these."
                    if not from_sim else
                    "Minutes for " + ", ".join(FROM_SIMULATOR) + " are the simulator's; the rest are this module's assumptions.")),
        "minutes": dict(HUMAN_ACTION_MINUTES),
    }
