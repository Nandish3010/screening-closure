"""Human-effort accounting (A21, ADR-032): how much of a case's work a person did, and how much the system did.

The product principle is that humans act only at final decisions (accept an escalation, approve a drafted message, sign a
closure, handle an exhausted reroute list); every other step is automated, and the system has to show that it cuts human
labour by more than `TARGET_REDUCTION_PCT`. This module is the accounting, and nothing else: pure functions over a case's
events, no clock, no store, no I/O, so the API (`GET /metrics/effort`, the case page's `effort`) and the behaviour
simulator (B17 imports `HUMAN_ACTION_MINUTES`) read the same numbers.

`actor_kind(actor)` classifies who wrote an event: `human` (a person acting in one of the six user roles), `automation`
(`system:automation`), `model` (`model:*`, a drafting model) or `service` (the scheduler, watchdog, notifier, consent
service and intake: infrastructure, neither a person nor a substitute for one).

Minutes. `HUMAN_ACTION_MINUTES` is a plain dict of the minutes one human action takes. EVERY VALUE IS AN ASSUMPTION
("assumed; to source"): none is a measurement, none has a citation yet, and the 40% result stands or falls with them. The
first six rows are the owner's figures (coordinator phone call 6, review 3, referral handling 8, sign-off 4, escalation
handling 5, message approval 2); the rest price the steps the automation replaces and the owner did not list, so that
"before" can be compared with "after". `docs/assumptions-limitations.md` and the pilot plan say how to replace them with a
time-and-motion study (to source).

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

# The roles a person can act in (equals app.auth.USER_ROLES; tests/test_effort.py keeps the two in step).
HUMAN_ROLES = ("lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor")
STAFF_ROLES = tuple(r for r in HUMAN_ROLES if r != "patient")  # a patient's own action is not staff labour
AUTOMATION_ACTOR = "system:automation"

ACTOR_KINDS = ("human", "automation", "model", "service")

# Minutes of one human action. ALL ASSUMED (ASSUMED above): to source.
HUMAN_ACTION_MINUTES: dict[str, float] = {
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
    """What the page must say next to the percentage: the minutes are assumed, and which they are."""
    return {
        "status": ASSUMED,
        "note": "Every minute value is an assumption with no source yet; the reduction is only as good as these.",
        "minutes": dict(HUMAN_ACTION_MINUTES),
    }
