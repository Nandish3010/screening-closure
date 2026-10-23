"""Human effort per case (ticket B17): minutes of staff time in the baseline and the orchestrated arm.

The effort model is a pure function of the event log. It never touches the engine, so no outcome and no event-log hash changes.

Each case follows one of two PROCESSES, whatever its arm:

  manual         today's process. Every step is done by a person: the coordinator looks the result up and acknowledges it, phones
                 or writes to the patient, sends the referral, books the appointment, records that the patient called back; the
                 clinician reviews the report and files the closure. The baseline arm is all manual, and so are the cases of the
                 orchestrated arm that the programme does not reach (`orchestration.programme_reach` below 1).
  orchestrated   the engine does everything except the final decisions (the split of ticket A21):
                   human      accept an escalation, approve a model-drafted message, sign the closure, handle an exhausted
                              reroute list, plus the manual steps that cannot be automated: record that the patient called back,
                              review a consent withdrawal, decide on a transfer, rebook, recover a contact, review a reopened case,
                              the specialist's accept/reject of a referral;
                   automated  acknowledge on ingest, owner auto-assignment, pre-approved template outreach and reminders,
                              evidence detection and closure proposal, reroute down the provider list, bounce, overdue, escalation
                              emission, consent ledger, de-duplication.

A human action costs minutes (`ACTIONS`, each with a range, a status sourced / sourced-adjacent / assumed, and a source in
docs/evidence.md section 11). An automated step costs no human time and is only counted. The baseline also spends time on
"chasing" that leaves no event (calling the patient who has not answered, calling the specialist's office that has not replied,
checking whether the colonoscopy happened); it is costed from the waiting times in the log, capped at N attempts (`CHASE_DEFAULT`),
and it buys the baseline no outcome in the simulator (its response and staff-delay parameters already describe today's chasing).

Reduction = 1 - orchestrated / baseline of human minutes per case. The effort model is a post-processing step: counts of
actions are computed per replication in the worker (`arm_effort`) and minutes are applied afterwards (`aggregate_effort`), so the
sensitivity of the result to every minutes figure is exact and free.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Callable, Iterable, Sequence

MANUAL, ORCH = "manual", "orchestrated"
PROCESSES = (MANUAL, ORCH)
COORDINATOR, CLINICIAN, SPECIALIST = "coordinator", "owner_clinician", "specialist"
ROLES = (COORDINATOR, CLINICIAN, SPECIALIST)
STATUSES = ("sourced", "sourced-adjacent", "assumed")
DAY_S = 86400.0


# ---------------------------------------------------------------------------------------------------------------------
# sources (full citations and URLs are in docs/evidence.md section 11; the ids here are the E11-n row ids)
# ---------------------------------------------------------------------------------------------------------------------

SOURCES = {
    "E11-1": "Heslop, Power, Cranwell, Hum Resour Health 2014;12:52, care coordinators' two-day activity log, 3,520 functions (Western Health, Melbourne)",
    "E11-2": "O'Leary et al., Prev Med Rep 2023;34:102211, centralized navigation after abnormal mailed FIT, North Carolina FQHCs, navigator call logs (26 patients)",
    "E11-3": "Rohan et al., JNCCN 2016;14(11):1371-8, navigators at Boston Medical Center, 404 patients, time per patient and per activity",
    "E11-4": "Robinson et al., J Ambul Care Manage 2010;33(2):143-54, prevention care managers' timed phone support, NYC community health centers (38 women)",
    "E11-5": "Hashim, Franks, Fiscella, J Am Board Fam Pract 2001;14:193-6, reminder calls (1 minute per call is the authors' assumption)",
    "E11-6": "UCSF Pediatric Access Center referral time-and-motion, about 12 minutes per manual referral (secondary report only, primary not located)",
}


# ---------------------------------------------------------------------------------------------------------------------
# human actions
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    id: str
    label: str
    role: str
    process: str  # manual | orchestrated | both
    minutes: float
    low: float
    high: float
    status: str
    source: str  # "E11-n ..." for sourced rows; "assumed: reason" otherwise
    chase: bool = False  # costed from waiting times, not from an event
    short: str = ""  # a plural noun phrase for running text

    def source_ids(self) -> list[str]:
        return [s for s in SOURCES if s in self.source]


def _a(id, label, role, process, minutes, low, high, status, source, chase=False):
    return Action(id, label, role, process, float(minutes), float(low), float(high), status, source, chase)


ACTIONS: tuple[Action, ...] = (
    # ---- today's process (baseline arm, and cases the programme does not reach) -----------------------------------------
    _a("M_RESULT_LOOKUP", "Look up the positive result, review the chart, open a work item", COORDINATOR, MANUAL, 5.0, 3.0, 14.0, "assumed",
       "assumed: 5 minutes; E11-1 'reading medical history/report' (14.0 minutes, complex-care coordinators) is the upper bound"),
    _a("M_RESULT_ACK", "Acknowledge the result in the record", COORDINATOR, MANUAL, 2.0, 1.0, 4.0, "assumed",
       "assumed: no time study of result acknowledgement found"),
    _a("M_HANDOVER", "Decide who follows the case up and hand it over (no named owner today)", COORDINATOR, MANUAL, 9.5, 4.75, 10.3, "sourced-adjacent",
       "E11-1 'handover' 9.5 minutes (SD 4.1), 'prioritising patients' 10.3; care coordinators in a hospital-community service"),
    _a("M_PHONE_ATTEMPT", "Phone the patient (one attempt, with documentation)", COORDINATOR, MANUAL, 7.2, 3.9, 12.1, "sourced-adjacent",
       "E11-1 'follow-up call after discharge' 7.2 minutes (SD 3.5); E11-2: 15.5 minutes on calls over a typical 4 calls = 3.9 (low), 48.5 total per patient / 4 = 12.1 (high)"),
    _a("M_SMS_ATTEMPT", "Send the patient an SMS or WhatsApp message by hand", COORDINATOR, MANUAL, 1.5, 1.0, 3.0, "assumed",
       "assumed: 1.5 minutes; E11-5 assumed 1 minute per reminder call (the floor)"),
    _a("M_LETTER", "Prepare and post a letter", COORDINATOR, MANUAL, 4.0, 2.0, 8.0, "assumed",
       "assumed: 4 minutes; E11-4 reports 17.5 minutes to compile and mail materials per woman over 18 months (number of mailings not reported)"),
    _a("M_REFERRAL_SEND", "Send or fax the referral to the endoscopy service", COORDINATOR, MANUAL, 12.4, 6.2, 18.6, "sourced-adjacent",
       "E11-1 'referral - external' 12.4 minutes (SD 9.1, n=102); E11-6 about 12 minutes per manual referral corroborates (secondary report)"),
    _a("M_COMPLETION_RECORD", "Record that the colonoscopy happened (find and file the report)", COORDINATOR, MANUAL, 5.0, 2.5, 10.0, "assumed",
       "assumed: 5 minutes"),
    _a("M_CHASE_PATIENT", "Chase a patient who has not answered (phone call, up to N attempts)", COORDINATOR, MANUAL, 7.2, 3.9, 12.1, "sourced-adjacent",
       "as M_PHONE_ATTEMPT: E11-1 follow-up call 7.2 minutes; E11-2 3.9 to 12.1 per call", chase=True),
    _a("M_CHASE_SPECIALIST", "Chase the specialist's office that has not replied (up to N attempts)", COORDINATOR, MANUAL, 8.1, 4.0, 12.2, "sourced-adjacent",
       "E11-1 'contracting/liaising with provider - external' 8.1 minutes (SD 11.7, n=192)", chase=True),
    _a("M_CHASE_COMPLETION", "Check whether an overdue colonoscopy happened (up to N attempts)", COORDINATOR, MANUAL, 5.0, 2.5, 10.0, "assumed",
       "assumed: 5 minutes", chase=True),
    _a("M_DUPLICATE_ITEM", "Process a duplicate result from a second lab as a new work item", COORDINATOR, MANUAL, 5.0, 3.0, 14.0, "assumed",
       "assumed: same as M_RESULT_LOOKUP"),
    _a("M_CLINICIAN_REVIEW", "Clinician reads the colonoscopy report and documents the finding", CLINICIAN, MANUAL, 5.0, 3.0, 10.0, "assumed",
       "assumed: 5 minutes"),
    _a("M_CLOSE_FILE", "Clinician files the closure", CLINICIAN, MANUAL, 3.0, 2.0, 6.0, "assumed",
       "assumed: 3 minutes"),
    # ---- shared: a person does it in both processes -----------------------------------------------------------------------
    _a("X_RECORD_CONTACT", "Record that the patient called back or walked in", COORDINATOR, "both", 2.0, 1.0, 4.0, "assumed",
       "assumed: 2 minutes"),
    _a("X_BOOK_APPT", "Book or rebook the colonoscopy appointment", COORDINATOR, "both", 8.0, 4.0, 15.1, "sourced-adjacent",
       "E11-4 'scheduling appointments and sending reminders' 8.0 minutes per woman; E11-3 'scheduling or rescheduling appointments' 15.1 minutes (high)"),
    _a("X_SPECIALIST_REVIEW", "Specialist reviews a referral and accepts or rejects it", SPECIALIST, "both", 9.5, 4.75, 15.7, "sourced-adjacent",
       "E11-1 'accepting/discussing referral' 9.5 minutes (SD 6.2, n=230); range is the mean plus or minus 1 SD (low halved)"),
    _a("X_REOPEN_REVIEW", "Clinician reviews a late finding that reopens a closed case", CLINICIAN, "both", 5.0, 3.0, 10.0, "assumed",
       "assumed: 5 minutes"),
    # ---- orchestrated process: the human steps that remain -------------------------------------------------------------------
    _a("O_ACCEPT_ESC_SUPERVISOR", "Supervisor accepts an escalation", CLINICIAN, ORCH, 2.0, 1.0, 4.0, "assumed",
       "assumed: 2 minutes to open the case, read the context and accept"),
    _a("O_ACCEPT_ESC_COORDINATOR", "Coordinator accepts an escalation or a watchdog flag", COORDINATOR, ORCH, 2.0, 1.0, 4.0, "assumed",
       "assumed: 2 minutes"),
    _a("O_SUPERVISOR_CALL", "Supervisor phones the patient after an escalation", CLINICIAN, ORCH, 7.2, 3.9, 12.1, "sourced-adjacent",
       "as M_PHONE_ATTEMPT: E11-1 follow-up call 7.2 minutes; E11-2 3.9 to 12.1 per call"),
    _a("O_COORDINATOR_CALL", "Coordinator contacts the patient by the alternate route", COORDINATOR, ORCH, 7.2, 3.9, 12.1, "sourced-adjacent",
       "as M_PHONE_ATTEMPT: E11-1 follow-up call 7.2 minutes; E11-2 3.9 to 12.1 per call"),
    _a("O_APPROVE_DRAFT", "Approve a model-drafted message before it is sent", CLINICIAN, ORCH, 1.5, 1.0, 3.0, "assumed",
       "assumed: 1.5 minutes. The simulator has no model drafts (every message is a pre-approved template), so one approval is charged on every human-contact message, the only non-template outreach"),
    _a("O_SIGN_CLOSURE", "Review the machine-prepared evidence and sign the closure", CLINICIAN, ORCH, 5.0, 3.0, 8.0, "assumed",
       "assumed: 5 minutes (the clinician still reads the evidence the engine attached; 8 minutes = today's review plus filing)"),
    _a("O_EXHAUSTED_REROUTE", "Handle an exhausted reroute list (find another service by hand)", COORDINATOR, ORCH, 15.0, 8.0, 30.0, "assumed",
       "assumed: 15 minutes, about one external referral plus follow-up calls (E11-1 12.4 and 8.1)"),
    _a("O_LOST_REVIEW", "Review a case flagged lost (unreachable, declined, repeated no-show) and record the outcome", COORDINATOR, ORCH, 8.0, 4.0, 15.0, "assumed",
       "assumed: 8 minutes"),
    _a("O_RECORD_REFUSAL", "Counsel a patient who declines and record the informed refusal (A33)", CLINICIAN, ORCH, 15.0, 8.0, 25.0, "assumed",
       "assumed: 15 minutes for the conversation and the counselling record (the sign-off is charged as O_SIGN_CLOSURE)"),
    _a("O_CONSENT_REVIEW", "Review a consent withdrawal", CLINICIAN, ORCH, 5.0, 3.0, 10.0, "assumed",
       "assumed: 5 minutes"),
    _a("O_CONTACT_RECOVERED", "Find and record a corrected contact for an unreachable patient", COORDINATOR, ORCH, 5.0, 3.0, 10.0, "assumed",
       "assumed: 5 minutes"),
    _a("O_TRANSFER_DECISION", "Receiving clinician decides on a transfer of the case after a provider switch", CLINICIAN, ORCH, 5.0, 3.0, 9.5, "assumed",
       "assumed: 5 minutes; E11-1 'accepting/discussing referral' (9.5) is the upper bound"),
)
SHORT = {
    "M_RESULT_LOOKUP": "result look-up and chart review", "M_RESULT_ACK": "result acknowledgement", "M_HANDOVER": "hand-over to a clinician",
    "M_PHONE_ATTEMPT": "phone attempts", "M_SMS_ATTEMPT": "manual SMS messages", "M_LETTER": "letters", "M_REFERRAL_SEND": "sending referrals",
    "M_COMPLETION_RECORD": "recording completion", "M_CHASE_PATIENT": "chasing patients", "M_CHASE_SPECIALIST": "chasing specialists",
    "M_CHASE_COMPLETION": "chasing overdue colonoscopies", "M_DUPLICATE_ITEM": "duplicate results", "M_CLINICIAN_REVIEW": "clinician review of reports",
    "M_CLOSE_FILE": "filing closures", "X_RECORD_CONTACT": "recording call-backs", "X_BOOK_APPT": "booking and rebooking",
    "X_SPECIALIST_REVIEW": "specialist review of referrals", "X_REOPEN_REVIEW": "reopened cases",
    "O_ACCEPT_ESC_SUPERVISOR": "supervisors accepting escalations", "O_ACCEPT_ESC_COORDINATOR": "coordinators accepting escalations and flags",
    "O_SUPERVISOR_CALL": "supervisor phone calls", "O_COORDINATOR_CALL": "coordinator alternate-route contacts", "O_APPROVE_DRAFT": "draft approvals",
    "O_SIGN_CLOSURE": "signing closures", "O_EXHAUSTED_REROUTE": "exhausted reroute lists", "O_LOST_REVIEW": "lost-case reviews", "O_RECORD_REFUSAL": "informed-refusal counselling",
    "O_CONSENT_REVIEW": "consent reviews", "O_CONTACT_RECOVERED": "corrected contacts", "O_TRANSFER_DECISION": "transfer decisions",
}
ACTIONS = tuple(replace(a, short=SHORT[a.id]) for a in ACTIONS)
ACTION = {a.id: a for a in ACTIONS}
MINUTES = {a.id: a.minutes for a in ACTIONS}


@dataclass(frozen=True)
class AutoStep:
    id: str
    label: str
    ticket: str  # the engine step that does it (A21 split) or the existing engine mechanism


AUTOMATED_STEPS: tuple[AutoStep, ...] = (
    AutoStep("A_INGEST", "Case created from the result on ingest", "acknowledge on ingest"),
    AutoStep("A_ACK", "Result acknowledged on ingest", "acknowledge on ingest"),
    AutoStep("A_ASSIGN_OWNER", "Owner assigned automatically", "owner auto-assignment"),
    AutoStep("A_TEMPLATE_OUTREACH", "Pre-approved template outreach, reminder and appointment reminder sent", "pre-approved template outreach and reminders"),
    AutoStep("A_REMINDER_EMIT", "Reminder rung emitted to the owner", "pre-approved template outreach and reminders"),
    AutoStep("A_EVIDENCE_DETECT", "Colonoscopy completion and its evidence detected", "evidence detection"),
    AutoStep("A_CLOSURE_PROPOSAL", "Closure proposed to the clinician", "closure proposal"),
    AutoStep("A_BOOKING_CONFIRMED", "Appointment recorded from the unit's acceptance", "referral acceptance carries the appointment"),
    AutoStep("A_REFERRAL_REROUTE", "Referral sent and rerouted down the provider list", "reroute down a provider list"),
    AutoStep("A_BOUNCE", "Unanswered referral bounced", "bounce"),
    AutoStep("A_OVERDUE", "Overdue raised at a missed SLA or appointment", "overdue"),
    AutoStep("A_ESCALATION_EMIT", "Escalation, watchdog flag or lost-contact flag emitted", "escalation emission"),
    AutoStep("A_REPLY_DETECT", "Patient's reply to a message detected", "reply handling"),
    AutoStep("A_CONSENT_LEDGER", "Consent withdrawal recorded, outreach blocked", "consent ledger"),
    AutoStep("A_DELIVERY_FAIL", "Delivery failure detected", "delivery tracking"),
    AutoStep("A_DEDUP", "Duplicate result merged", "de-duplication"),
    AutoStep("A_CLAIM_GUARD", "Conflicting ownership claim rejected by the state machine", "state machine"),
)
AUTO = {s.id: s for s in AUTOMATED_STEPS}

# What today's process never does (it is not costed in the baseline, and the orchestrated arm does it by machine).
NEVER_DONE_IN_BASELINE: tuple[tuple[str, str], ...] = (
    ("Appointment reminders before the colonoscopy", "A_TEMPLATE_OUTREACH"),
    ("Noticing a missed appointment and rebooking it", "A_OVERDUE"),
    ("Overdue tracking against working-day SLAs and the escalation ladder", "A_ESCALATION_EMIT"),
    ("Noticing a rejected referral and rerouting it (only a share of rejections is noticed today)", "A_REFERRAL_REROUTE"),
    ("Recording a consent withdrawal and stopping outreach", "A_CONSENT_LEDGER"),
    ("Merging duplicate results and resolving conflicting ownership claims", "A_DEDUP"),
    ("Surfacing a lost case to a human", "A_ESCALATION_EMIT"),
)


# ---------------------------------------------------------------------------------------------------------------------
# event -> action map
# ---------------------------------------------------------------------------------------------------------------------
# A token is a human action id, "auto:<id>" for an automated step, or nothing. Values are a tuple of tokens or a function of the
# event returning one. `NONE` = no staff or system work (a patient or third-party event, or recorded together with another action).

NONE: tuple = ()


def _manual_message(ev) -> tuple:
    ch = ev.payload.get("channel")
    return ("M_PHONE_ATTEMPT",) if ch == "phone" else ("M_LETTER",) if ch == "letter" else ("M_SMS_ATTEMPT",)


def _orch_message(ev) -> tuple:
    kind = ev.payload.get("kind")
    if kind == "human_call":
        return ("O_SUPERVISOR_CALL", "O_APPROVE_DRAFT")
    if kind == "coordinator_route":
        return ("O_COORDINATOR_CALL", "O_APPROVE_DRAFT")
    return ("auto:A_TEMPLATE_OUTREACH",)


def _orch_escalated(ev) -> tuple:
    notify = ev.payload.get("notify")
    if notify is None:
        notify = "programme_coordinator" if ev.payload.get("rung") == 2 else "owner_supervisor"
    return ("auto:A_ESCALATION_EMIT", "O_ACCEPT_ESC_COORDINATOR" if notify == "programme_coordinator" else "O_ACCEPT_ESC_SUPERVISOR")


def _orch_ack(ev) -> tuple:
    return ("X_RECORD_CONTACT",) if ev.payload.get("via") == "self_presented" else ("auto:A_REPLY_DETECT",)


def _handoff_response(ev) -> tuple:
    return ("X_SPECIALIST_REVIEW",) if ev.actor.startswith("endoscopy") else ("O_TRANSFER_DECISION",)


def _orch_lost(ev) -> tuple:
    exhausted = ev.payload.get("reason") == "referral_attempts_exhausted"
    return ("auto:A_ESCALATION_EMIT", "O_EXHAUSTED_REROUTE" if exhausted else "O_LOST_REVIEW")


def _orch_overdue(ev) -> tuple:
    return ("auto:A_BOUNCE",) if ev.payload.get("reason") == "handoff_unanswered" else ("auto:A_OVERDUE",)


EVENT_MAP: dict[str, dict[str, tuple | Callable]] = {
    # pathway events (config/pathways/colorectal_fit.yaml)
    "case_created": {MANUAL: ("M_RESULT_LOOKUP",), ORCH: ("auto:A_INGEST",)},
    "acknowledge": {MANUAL: ("M_RESULT_ACK",), ORCH: ("auto:A_ACK",)},
    "accept_ownership": {MANUAL: ("M_HANDOVER",), ORCH: ("auto:A_ASSIGN_OWNER",)},
    "contact_patient": {MANUAL: NONE, ORCH: NONE},  # the work is the message_sent that carries it
    "patient_acknowledge": {MANUAL: ("X_RECORD_CONTACT",), ORCH: _orch_ack},
    "book_confirmatory": {MANUAL: ("X_BOOK_APPT",), ORCH: ("auto:A_BOOKING_CONFIRMED",)},
    "complete_confirmatory": {MANUAL: ("M_COMPLETION_RECORD",), ORCH: ("auto:A_EVIDENCE_DETECT",)},
    "clinician_review": {MANUAL: ("M_CLINICIAN_REVIEW",), ORCH: ("auto:A_CLOSURE_PROPOSAL",)},
    "close": {MANUAL: ("M_CLOSE_FILE",), ORCH: ("O_SIGN_CLOSURE",)},
    "record_informed_refusal": {MANUAL: NONE, ORCH: ("O_RECORD_REFUSAL",)},  # only the orchestrated arm logs it (A33)
    # overlay events (engine.OVERLAY_EVENTS)
    "overdue": {MANUAL: NONE, ORCH: _orch_overdue},
    "escalated": {MANUAL: NONE, ORCH: _orch_escalated},
    "reminder_sent": {MANUAL: NONE, ORCH: ("auto:A_REMINDER_EMIT",)},
    "message_sent": {MANUAL: _manual_message, ORCH: _orch_message},
    "message_blocked": {MANUAL: NONE, ORCH: ("auto:A_CONSENT_LEDGER",)},
    "delivery_failed": {MANUAL: NONE, ORCH: ("auto:A_DELIVERY_FAIL",)},
    "contact_recovered": {MANUAL: NONE, ORCH: ("O_CONTACT_RECOVERED",)},
    "handoff_requested": {MANUAL: ("M_REFERRAL_SEND",), ORCH: ("auto:A_REFERRAL_REROUTE",)},
    "handoff_accepted": {MANUAL: _handoff_response, ORCH: _handoff_response},
    "handoff_rejected": {MANUAL: _handoff_response, ORCH: _handoff_response},
    "consent_updated": {MANUAL: NONE, ORCH: ("auto:A_CONSENT_LEDGER",)},  # not recorded at all today
    "consent_review_completed": {MANUAL: NONE, ORCH: ("O_CONSENT_REVIEW",)},
    "watchdog_flagged": {MANUAL: NONE, ORCH: ("auto:A_ESCALATION_EMIT", "O_ACCEPT_ESC_COORDINATOR")},
    "lost_contact": {MANUAL: NONE, ORCH: _orch_lost},  # a flag nobody reads today: not costed
    "reopened": {MANUAL: ("X_REOPEN_REVIEW",), ORCH: ("X_REOPEN_REVIEW",)},
    "appointment_missed": {MANUAL: NONE, ORCH: NONE},  # recorded by the endoscopy unit
    "appointment_rebooked": {MANUAL: NONE, ORCH: ("X_BOOK_APPT",)},  # today the patient rebooks directly with the unit
    "provider_switched": {MANUAL: NONE, ORCH: NONE},  # a patient event; the follow-on work has its own events
    "duplicate_result_received": {MANUAL: ("M_DUPLICATE_ITEM",), ORCH: NONE},
    "duplicate_result_merged": {MANUAL: NONE, ORCH: ("auto:A_DEDUP",)},
    "conflicting_ack_rejected": {MANUAL: NONE, ORCH: ("auto:A_CLAIM_GUARD",)},
    "conflicting_ack_unresolved": {MANUAL: NONE, ORCH: NONE},  # nobody resolves it today: not costed
}


def tokens(process: str, ev) -> tuple:
    rule = EVENT_MAP[ev.type][process]
    return rule(ev) if callable(rule) else rule


# ---------------------------------------------------------------------------------------------------------------------
# chasing (manual process): work that leaves no event
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChaseCfg:
    max_attempts: int  # N: attempts per waiting episode
    interval_days: float  # one attempt per full interval of waiting


CHASE_DEFAULT = ChaseCfg(3, 14.0)  # assumed: no published figure for how often a coordinator chases
CHASE_VARIANTS = {"default": CHASE_DEFAULT, "none": ChaseCfg(0, 14.0), "intensive": ChaseCfg(5, 7.0)}


def _attempts(wait_days: float, cfg: ChaseCfg) -> int:
    if cfg.max_attempts <= 0 or wait_days <= 0:
        return 0
    return min(cfg.max_attempts, int(math.floor(wait_days / cfg.interval_days + 1e-9)))


def chase_counts(events: Sequence, follow_up_days: float, variants: dict[str, ChaseCfg]) -> dict[str, Counter]:
    """Chasing attempts a coordinator makes in the manual process, per variant. An attempt is made for each full interval
    that a patient has not answered since an outreach, a referral has not been answered since it was sent, or a colonoscopy has
    not been recorded since the appointment date, up to N attempts per episode."""
    first = events[0].at
    day0 = first.date()
    rel = [((e.at - first).total_seconds() / DAY_S, e) for e in events]
    waits: list[tuple[str, float]] = []
    acks = [r for r, e in rel if e.type == "patient_acknowledge"]
    for r, e in rel:
        if e.type == "message_sent":
            later = [a for a in acks if a >= r]
            waits.append(("M_CHASE_PATIENT", (later[0] if later else follow_up_days) - r))
    reqs = [(r, e) for r, e in rel if e.type == "handoff_requested"]
    resp = [r for r, e in rel if e.type in ("handoff_accepted", "handoff_rejected") and e.actor.startswith("endoscopy")]
    for i, (r, _) in enumerate(reqs):
        nxt = reqs[i + 1][0] if i + 1 < len(reqs) else follow_up_days
        got = [x for x in resp if r <= x <= nxt]
        waits.append(("M_CHASE_SPECIALIST", (got[0] if got else nxt) - r))
    done = [r for r, e in rel if e.type == "complete_confirmatory"]
    for r, e in rel:
        if e.type == "book_confirmatory":
            on = e.payload.get("appointment_on")
            appt = (date.fromisoformat(on) - day0).days if on else r
            later = [d for d in done if d >= r]
            waits.append(("M_CHASE_COMPLETION", (later[0] if later else follow_up_days) - appt))
    out: dict[str, Counter] = {}
    for name, cfg in variants.items():
        c: Counter = Counter()
        for aid, w in waits:
            n = _attempts(w, cfg)
            if n:
                c[aid] += n
        out[name] = c
    return out


# ---------------------------------------------------------------------------------------------------------------------
# per case and per arm
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class CaseEffort:
    human: Counter  # action id -> count, events only
    auto: Counter  # automated step id -> count
    chase: dict[str, Counter]  # chase variant -> action id -> count (manual process only)


def case_effort(events: Sequence, process: str, follow_up_days: float,
                variants: dict[str, ChaseCfg] | None = None) -> CaseEffort:
    variants = CHASE_VARIANTS if variants is None else variants
    human: Counter = Counter()
    auto: Counter = Counter()
    for ev in events:
        for tok in tokens(process, ev):
            if tok.startswith("auto:"):
                auto[tok[5:]] += 1
            else:
                human[tok] += 1
    chase = chase_counts(events, follow_up_days, variants) if process == MANUAL and events else {v: Counter() for v in variants}
    return CaseEffort(human, auto, chase)


def minutes_of(counts: dict[str, float], minutes: dict[str, float] | None = None) -> dict:
    """Total minutes and minutes per role for a dict of action counts."""
    m = MINUTES if minutes is None else minutes
    by_role = {r: 0.0 for r in ROLES}
    for aid, n in counts.items():
        by_role[ACTION[aid].role] += n * m[aid]
    return {"total": sum(by_role.values()), "by_role": by_role}


def arm_effort(cases: Iterable, reach_cut: float, follow_up_days: float, variants: dict[str, ChaseCfg] | None = None) -> dict:
    """Action counts for one arm of one replication, over all cases and over the in-reach cases.

    `cases` are engine `Case` objects: `.orch` (the case got the orchestrated process), `.log`, `.lat.u_reach`.
    The result is plain dicts (picklable, JSON-able) of integer counts; minutes are applied later.
    """
    variants = CHASE_VARIANTS if variants is None else variants

    def blank() -> dict:
        return {"n_cases": 0, "n_closed": 0, "human": Counter(), "auto": Counter(), "chase": {v: Counter() for v in variants}}

    acc = {"all": blank(), "in_reach": blank()}
    for c in cases:
        ce = case_effort(c.log, ORCH if c.orch else MANUAL, follow_up_days, variants)
        # A declined closure (A33) is not a closed loop in the programme's sense: it is reported apart and not in this denominator
        closed = any(e.type == "close" and e.payload.get("outcome") != "declined" for e in c.log)
        for key in ("all", "in_reach") if c.lat.u_reach < reach_cut else ("all",):
            a = acc[key]
            a["n_cases"] += 1
            a["n_closed"] += closed
            a["human"].update(ce.human)
            a["auto"].update(ce.auto)
            for v in variants:
                a["chase"][v].update(ce.chase[v])
    return {k: {"n_cases": v["n_cases"], "n_closed": v["n_closed"], "human": dict(sorted(v["human"].items())),
                "auto": dict(sorted(v["auto"].items())),
                "chase": {n: dict(sorted(c.items())) for n, c in v["chase"].items()}} for k, v in acc.items()}


# ---------------------------------------------------------------------------------------------------------------------
# aggregation across replications
# ---------------------------------------------------------------------------------------------------------------------

def _counts(block: dict, variant: str, extra: dict | None = None) -> dict[str, float]:
    out: dict[str, float] = dict(block["human"])
    for aid, n in block["chase"][variant].items():
        out[aid] = out.get(aid, 0) + n
    for aid, n in (extra or {}).items():
        out[aid] = out.get(aid, 0) + n
    return out


def rep_metrics(block: dict, minutes: dict[str, float] | None = None, variant: str = "default", extra: dict | None = None) -> dict:
    """The effort metrics of one arm in one replication (one subset: all cases or in-reach)."""
    n = block["n_cases"]
    counts = _counts(block, variant, extra)
    mt = minutes_of(counts, minutes)
    n_human = sum(counts.values())
    n_auto = sum(block["auto"].values())
    closed = block["n_closed"]
    by_status = {s: 0.0 for s in STATUSES}
    m = MINUTES if minutes is None else minutes
    for aid, k in counts.items():
        by_status[ACTION[aid].status] += k * m[aid]
    return {
        "minutes_per_case": mt["total"] / n if n else None,
        "minutes_per_closed_case": mt["total"] / closed if closed else None,
        "human_actions_per_case": n_human / n if n else None,
        "automated_actions_per_case": n_auto / n if n else None,
        "automation_share": n_auto / (n_auto + n_human) if (n_auto + n_human) else None,
        **{f"minutes_per_case.{r}": mt["by_role"][r] / n if n else None for r in ROLES},
        **{f"minutes_per_case.status.{s}": by_status[s] / n if n else None for s in STATUSES},
    }


def _reduction(base: float | None, orch: float | None) -> float | None:
    return None if not base or orch is None else 1.0 - orch / base


def rep_reduction(b: dict, o: dict, minutes: dict[str, float] | None = None, variant: str = "default",
                  o_extra: dict | None = None) -> dict:
    mb, mo = rep_metrics(b, minutes, variant), rep_metrics(o, minutes, variant, o_extra)
    return {
        "labour_reduction": _reduction(mb["minutes_per_case"], mo["minutes_per_case"]),
        "labour_reduction_per_closed_case": _reduction(mb["minutes_per_closed_case"], mo["minutes_per_closed_case"]),
        "human_actions_reduction": _reduction(mb["human_actions_per_case"], mo["human_actions_per_case"]),
        **{f"labour_reduction.{r}": _reduction(mb[f"minutes_per_case.{r}"], mo[f"minutes_per_case.{r}"]) for r in ROLES},
    }


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def least_favourable(shared: str) -> dict[str, float]:
    """Baseline-only actions at their low end, orchestrated-only actions at their high end, shared actions at `shared` ("low"|"high")."""
    out = {}
    for a in ACTIONS:
        if a.process == MANUAL:
            out[a.id] = a.low
        elif a.process == ORCH:
            out[a.id] = a.high
        else:
            out[a.id] = a.low if shared == "low" else a.high
    return out


def aggregate_effort(reps: list[dict], base_seed: int, resamples: int, summ: Callable, scenario: str) -> dict:
    """`reps`: per replication {"baseline": arm_effort, "orchestrated": arm_effort}. Returns the `effort` block of one scenario."""
    n = len(reps)

    def cell(vals, *parts):
        return summ([v for v in vals if v is not None], (base_seed, "bootstrap", "effort", scenario, *parts), resamples, n)

    out: dict = {"n_reps": n}
    for subset in ("all", "in_reach"):
        sub: dict = {"arms": {}, "reduction": {}}
        for arm in ("baseline", "orchestrated"):
            per = [rep_metrics(r[arm][subset]) for r in reps]
            sub["arms"][arm] = {k: cell([p[k] for p in per], subset, arm, k) for k in per[0]}
        red = [rep_reduction(r["baseline"][subset], r["orchestrated"][subset]) for r in reps]
        sub["reduction"] = {k: cell([x[k] for x in red], subset, "reduction", k) for k in red[0]}
        # per case counts of every action, for the table of what people actually do
        counts = {}
        for arm in ("baseline", "orchestrated"):
            tot: Counter = Counter()
            ncase = 0
            for r in reps:
                b = r[arm][subset]
                tot.update(_counts(b, "default"))
                tot.update({"auto:" + k: v for k, v in b["auto"].items()})
                ncase += b["n_cases"]
            counts[arm] = {k: v / ncase for k, v in sorted(tot.items())} if ncase else {}
        sub["actions_per_case"] = counts
        out[subset] = sub

    # ---- robustness of the programme-level reduction (point estimates: mean over replications) ------------------------------
    def red_of(minutes=None, variant="default", extra_booking=False, roles=None) -> float | None:
        vals = []
        for r in reps:
            ob = r["orchestrated"]["all"]
            extra = {"X_BOOK_APPT": ob["auto"].get("A_BOOKING_CONFIRMED", 0)} if extra_booking else None
            vals.append(rep_reduction(r["baseline"]["all"], ob, minutes, variant, extra)["labour_reduction"])
        return _mean(vals)

    def red_roles(roles: tuple) -> float | None:
        vals = []
        for r in reps:
            def tot(arm):
                b = r[arm]["all"]
                mt = minutes_of(_counts(b, "default"))
                return sum(mt["by_role"][x] for x in roles) / b["n_cases"]
            vals.append(_reduction(tot("baseline"), tot("orchestrated")))
        return _mean(vals)

    lf_lo, lf_hi = red_of(least_favourable("low")), red_of(least_favourable("high"))
    out["robustness"] = {
        "headline": red_of(),
        "baseline_chasing_not_costed": red_of(variant="none"),
        "baseline_chasing_intensive": red_of(variant="intensive"),
        "orchestrated_bookings_keyed_by_hand": red_of(extra_booking=True),
        "no_draft_approval_charge": red_of({**MINUTES, "O_APPROVE_DRAFT": 0.0}),
        "escalation_outreach_is_an_approved_draft_not_a_call": red_of({**MINUTES, "O_SUPERVISOR_CALL": 0.0, "O_COORDINATOR_CALL": 0.0}),
        "coordinator_and_clinician_only": red_roles((COORDINATOR, CLINICIAN)),
        "least_favourable_minutes": min(v for v in (lf_lo, lf_hi) if v is not None) if lf_lo is not None else None,
        "all_minutes_low": red_of({a.id: a.low for a in ACTIONS}),
        "all_minutes_high": red_of({a.id: a.high for a in ACTIONS}),
    }
    # ---- one-at-a-time tornado over the minutes figures ---------------------------------------------------------------------
    base = out["robustness"]["headline"]
    torn = []
    for a in ACTIONS:
        lo = red_of({**MINUTES, a.id: a.low})
        hi = red_of({**MINUTES, a.id: a.high})
        if lo is None or hi is None:
            continue
        torn.append({"action": a.id, "status": a.status, "low_minutes": a.low, "high_minutes": a.high,
                     "reduction_at_low": lo, "reduction_at_high": hi, "swing": abs(hi - lo)})
    torn.sort(key=lambda t: (-t["swing"], t["action"]))
    out["tornado"] = torn
    out["tornado_base"] = base
    return out


# ---------------------------------------------------------------------------------------------------------------------
# model description for results.json
# ---------------------------------------------------------------------------------------------------------------------

def model_description() -> dict:
    actions = [
        {"id": a.id, "label": a.label, "role": a.role, "process": a.process, "minutes": a.minutes, "low": a.low, "high": a.high,
         "status": a.status, "source": a.source, "chase": a.chase, "short": a.short}
        for a in ACTIONS
    ]
    desc = {
        "actions": actions,
        "automated_steps": [{"id": s.id, "label": s.label, "step": s.ticket} for s in AUTOMATED_STEPS],
        "never_done_in_baseline": [{"work": w, "automated_by": s} for w, s in NEVER_DONE_IN_BASELINE],
        "chase": {name: {"max_attempts": c.max_attempts, "interval_days": c.interval_days} for name, c in CHASE_VARIANTS.items()},
        "chase_default": "default",
        "sources": dict(SOURCES),
        "event_map": {
            et: {p: (list(r) if not callable(r) else "by payload") for p, r in rules.items()} for et, rules in sorted(EVENT_MAP.items())
        },
    }
    desc["effort_hash"] = hashlib.sha256(json.dumps(desc, sort_keys=True).encode()).hexdigest()
    return desc


def effort_hash() -> str:
    return model_description()["effort_hash"]
