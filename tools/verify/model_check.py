#!/usr/bin/env python3
"""Explicit-state model checker for the closure engine (ticket A27, ADR-038).

It drives the REAL `app.state_machine.step` (the machine is not re-implemented): from the genesis model of the shipped
pathway it applies every event of a finite alphabet (event type x actor x representative payload, valid and invalid) to
one concrete representative of every abstract state, breadth-first, until a level adds no new abstract state. On every
accepted transition it checks the safety invariants I1 to I4; on the saturated graph it checks I5 (a person can always
act), I6 (replay determinism) and I7 (a person can always reach `closed`); I8 (erasure is final, A32) on every transition. On a violation it prints a shortest
counterexample (BFS order) and exits non-zero.

The search runs in three feature SLICES (messaging and consent, ownership and handoff, SLA ladders). Each slice has the
whole main path and every state change, plus the events of its feature, and is saturated on its own; the full product of
the three is too large for the three-minute budget and adds nothing for these properties, because the features do not read
each other's fields. That claim is checked, not assumed: the non-interference check flips every other slice's fields on
the representatives and requires the slice's events to behave the same. docs/governance/verification.md has the argument.

    PYTHONPATH=api python tools/verify/model_check.py [--slice NAME] [--all] [--json] [-v]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "api") not in sys.path:  # `python tools/verify/model_check.py` without PYTHONPATH
    sys.path.insert(0, str(ROOT / "api"))

from fastapi import HTTPException  # noqa: E402

from app import auth, evidence, payloads, policy  # noqa: E402
from app import state_machine as sm  # noqa: E402
from app.events import Event  # noqa: E402
from app.pathway import Pathway, load_pathway  # noqa: E402

PATHWAY_PATH = ROOT / "config/pathways/colorectal_fit.yaml"
OUTREACH_POLICY_PATH = ROOT / "config/outreach_policy.yaml"

NS = UUID("6d1c3a52-0a27-4c38-9b8e-5e3f2b7a9a27")
CASE = uuid5(NS, "case")
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # every event is at T0: never backdated; due dates are relative to it
PAST = (T0 - timedelta(days=1)).isoformat()
FUTURE = (T0 + timedelta(days=1)).isoformat()

# ---- actors -----------------------------------------------------------------------------------------------------------
# People are built the way the API builds them (auth.Identity), so the route's own gates can be run on them. The
# specialist acts for `prov-b`, the provider the handoff payloads name, so they may answer a referral (owner scope, A5).
IDENTITIES = {
    "lab": auth.Identity(kind="user", role="lab", name="Lab One"),
    "primary_care_owner": auth.Identity(kind="user", role="primary_care_owner", name="Dr Primary", provider="prov-a"),
    "specialist_owner": auth.Identity(kind="user", role="specialist_owner", name="Dr Spec", provider="prov-b"),
    "coordinator": auth.Identity(kind="user", role="coordinator", name="Coord One"),
    "supervisor": auth.Identity(kind="user", role="supervisor", name="Super One"),
    "patient": auth.Identity(kind="user", role="patient", name="Pat One", patient_ref="Patient/p1"),
}
HUMAN_BY_ACTOR = {i.actor: i for i in IDENTITIES.values()}
PCO = IDENTITIES["primary_care_owner"].actor
SPEC = IDENTITIES["specialist_owner"].actor
COORD = IDENTITIES["coordinator"].actor
SUP = IDENTITIES["supervisor"].actor
# The people who act in the search. The machine reads a person only by identity (equality: owner, requester) and by role
# in `escalation_accepted` (the rung's accepted_by): these four cover every rung's role and every route a step needs.
# `lab` and `patient` have the same row of the actor table as these; they are in the table probe (`TABLE_PROBE`) only.
CAST = [PCO, SPEC, COORD, SUP]
FOREIGN_SIGNER = f"specialist_owner:{auth.opaque_user_id('specialist_owner', 'Dr Someone Else')}"
SERVICES = [
    "system:scheduler", "system:watchdog", "system:notifier", "system:consent", "system:automation",
    "model:drafter", "system:triage", "system:agency:outreach", "system:hl7", sm.ERASURE_ACTOR,
]
STRANGERS = ["intruder:x1", "scheduler", "system:intake"]  # match no row of the table: may emit nothing
ACTORS = [*CAST, *SERVICES, *STRANGERS]
TABLE_PROBE = [*HUMAN_BY_ACTOR, *SERVICES, *STRANGERS]  # every actor, for the per-state table probe
# The checker's OWN notion of "automated" (never sm.is_automated, so a mutation of that function cannot hide itself).
AUTOMATED_PREFIXES = ("system:", "model:", "scheduler", "watchdog", "agency")
# I2: what no automated actor may ever get accepted.
HUMAN_DECISIONS = frozenset({
    "close", "reopen", "clinician_review", "message_approved", "escalation_accepted", "consent_updated",
    "accept_ownership", "record_informed_refusal",
})
# A33 (ADR-042): the counselling record a refusal cites and a declined closure must cite back.
COUNSELLING = "DocumentReference/counsel-1"
PHYSICIANS = ("primary_care_owner", "specialist_owner")  # who may sign a declined closure
# I4: outreach (contacting the patient, or handing them to someone new).
OUTREACH = frozenset({"contact_patient", "message_drafted", "message_approved", "message_sent", "handoff_requested"})
# I7: states a case may stay in for ever without a human path to `closed` (documented terminal holds). None is needed:
# every reachable open state has one. Kept explicit, so adding one is a reviewed change to this file.
DOCUMENTED_TERMINAL_HOLDS: frozenset[str] = frozenset()


def is_automated(actor: str) -> bool:
    return actor.startswith(AUTOMATED_PREFIXES) or ":" not in actor


# ---- the payload alphabet ---------------------------------------------------------------------------------------------
# Each entry: (type, label, builder(actor, model) -> payload). A builder reads the CURRENT model only to name what the
# payload must name (the current state, the escalation to attach to), so the alphabet stays finite per abstract state.

Builder = Callable[[str, sm.CaseModel], dict]
# A builder that reads the model reads only `state` and `escalation_event_id` (the event cache is keyed on them).
REF = "DiagnosticReport/colo-1"
RUNGS = ("reminder", "supervisor", "coordinator")
SENT = {"channel": "sms", "language": "en", "purpose": "reminder"}
AUTO_TEMPLATE = {"auto": True, "template_id": "reminder.sms", "template_version": 1, "purpose": "reminder",
                 "channel": "sms", "language": "en", "notification_id": "n-auto"}


def _practitioner(actor: str) -> str:
    ident = HUMAN_BY_ACTOR.get(actor)
    return f"Practitioner/{ident.user_id}" if ident else "Practitioner/unknown"


def alphabet(pathway: Pathway) -> list[tuple[str, str, Builder, bool]]:
    """(type, label, builder, reads_model) for every representative event."""
    milestones = list(pathway.milestones)
    a: list[tuple[str, str, Builder, bool]] = []

    def add(type_: str, label: str, b, reads_model: bool = False) -> None:
        a.append((type_, label, b if callable(b) else (lambda actor, m, p=b: dict(p)), reads_model))

    add("acknowledge", "plain", {})
    add("acknowledge", "auto", {"auto": True})
    add("accept_ownership", "plain", {})
    add("accept_ownership", "provider", {"provider": "prov-a"})
    add("assign_ownership", "auto", {"owner": PCO, "provider": "prov-a", "auto": True})
    add("assign_ownership", "no-owner", {"auto": True})
    add("assign_ownership", "no-auto-flag", {"owner": PCO})
    for t in ("contact_patient", "patient_acknowledge", "book_confirmatory"):
        add(t, "plain", {})
    add("complete_confirmatory", "plain", {})
    add("complete_confirmatory", "auto", {"auto": True})
    add("clinician_review", "plain", {})
    add("clinician_review", "signed-self", lambda actor, m: {"signed_by": _practitioner(actor)})
    # close: evidence present / absent / incomplete (empty, malformed); signer self / Practitioner-self / other / none
    add("close", "ok-self", lambda actor, m: {"evidence_ref": REF, "signed_by": actor})
    add("close", "ok-practitioner", lambda actor, m: {"evidence_ref": REF, "signed_by": _practitioner(actor)})
    add("close", "signer-other", {"evidence_ref": REF, "signed_by": FOREIGN_SIGNER})
    add("close", "no-signer", {"evidence_ref": REF})
    add("close", "empty-signer", {"evidence_ref": REF, "signed_by": ""})
    add("close", "no-evidence", lambda actor, m: {"signed_by": actor})
    add("close", "empty-evidence", lambda actor, m: {"evidence_ref": "", "signed_by": actor})
    add("close", "malformed-evidence", lambda actor, m: {"evidence_ref": "Procedure/.", "signed_by": actor})
    add("close", "auto", lambda actor, m: {"evidence_ref": REF, "signed_by": actor, "auto": True})
    # A33: a declined closure cites the counselling record and says so; the signer is a physician who did not counsel
    add("close", "declined-ok", lambda actor, m: {"outcome": "declined", "evidence_ref": COUNSELLING, "signed_by": actor})
    add("close", "declined-practitioner", lambda actor, m: {"outcome": "declined", "evidence_ref": COUNSELLING,
                                                          "signed_by": _practitioner(actor)})
    add("close", "declined-other-ref", lambda actor, m: {"outcome": "declined", "evidence_ref": "DocumentReference/other",
                                                       "signed_by": actor})
    add("close", "declined-colonoscopy-ref", lambda actor, m: {"outcome": "declined", "evidence_ref": REF, "signed_by": actor})
    add("close", "declined-no-signer", {"outcome": "declined", "evidence_ref": COUNSELLING})
    add("close", "counselling-no-outcome", lambda actor, m: {"evidence_ref": COUNSELLING, "signed_by": actor})
    add("close", "declined-auto", lambda actor, m: {"outcome": "declined", "evidence_ref": COUNSELLING, "signed_by": actor,
                                                  "auto": True})
    add("record_informed_refusal", "by-spec", {"counselling_ref": COUNSELLING, "counselled_by": SPEC, "reason": "personal_choice"})
    add("record_informed_refusal", "by-pco", {"counselling_ref": COUNSELLING, "counselled_by": PCO, "reason": "fear_of_procedure"})
    add("record_informed_refusal", "by-spec-practitioner", {"counselling_ref": COUNSELLING,
                                                          "counselled_by": _practitioner(SPEC), "reason": "alternative_test_requested"})
    add("record_informed_refusal", "other-needs-note", {"counselling_ref": COUNSELLING, "counselled_by": SPEC,
                                                      "reason": "other_with_note", "note": "n"})
    add("record_informed_refusal", "bad-reason", {"counselling_ref": COUNSELLING, "counselled_by": SPEC, "reason": "because"})
    add("record_informed_refusal", "no-counselling", {"counselled_by": SPEC, "reason": "personal_choice"})
    add("record_informed_refusal", "no-counsellor", {"counselling_ref": COUNSELLING, "reason": "personal_choice"})
    add("record_informed_refusal", "coordinator-counsellor", {"counselling_ref": COUNSELLING, "counselled_by": COORD,
                                                            "reason": "personal_choice"})
    add("appointment_cancelled", "by-patient", {"reason": "patient_request", "by_patient": True})
    add("appointment_cancelled", "by-provider", {"reason": "provider_cancelled", "by_patient": False})
    add("appointment_cancelled", "bad-reason", {"reason": "because", "by_patient": False})
    add("appointment_cancelled", "no-by-patient", {"reason": "other"})
    add("reopen", "reason", {"reason": "new_finding"})
    add("reopen", "no-reason", {})
    add("mark_overdue", "state", lambda actor, m: {"state": m.state, "due_at": PAST}, True)
    add("mark_overdue", "state-not-due", lambda actor, m: {"state": m.state, "due_at": FUTURE}, True)
    add("mark_overdue", "wrong-state", {"state": "nowhere", "due_at": PAST})
    add("mark_overdue", "bad-date", lambda actor, m: {"state": m.state, "due_at": "yesterday"}, True)
    for ms in milestones:
        add("mark_overdue", f"ms:{ms}", {"milestone": ms, "due_at": PAST})
    add("mark_overdue", "ms:unknown", {"milestone": "nope", "due_at": PAST})
    for rung in RUNGS:
        add("escalate", f"state:{rung}", lambda actor, m, r=rung: {"state": m.state, "rung": r}, True)
        for ms in milestones:
            add("escalate", f"ms:{ms}:{rung}", {"milestone": ms, "rung": rung})
            # attached to the state ladder's current escalation (A19)
            add("escalate", f"ms:{ms}:{rung}:attached", lambda actor, m, r=rung, ms=ms: {
                "milestone": ms, "rung": r, "attached_to": str(m.escalation_event_id)}, True)
        # one escalation that also covers every milestone ladder at the same rung (A19)
        add("escalate", f"state:{rung}:also_for", lambda actor, m, r=rung: {
            "state": m.state, "rung": r, "also_for": [{"milestone": ms, "rung": r} for ms in milestones]}, True)
    add("escalate", "state:bogus-rung", lambda actor, m: {"state": m.state, "rung": "bogus"}, True)
    add("escalation_accepted", "plain", {})
    for ms in milestones:
        add("escalation_accepted", f"ms:{ms}", {"milestone": ms})
    add("handoff_requested", "plain", {"to_provider": "prov-b"})
    add("handoff_requested", "auto", {"to_provider": "prov-b", "auto": True})
    add("handoff_requested", "empty-provider", {"to_provider": " "})
    add("handoff_accepted", "plain", {})
    add("handoff_accepted", "empty-provider", {"provider": ""})
    add("handoff_rejected", "capacity", {"reason": "capacity"})
    add("handoff_rejected", "bounce", {"reason": pathway.handoff_bounce_reason, "bounced": True})
    add("handoff_rejected", "bogus", {"reason": "because"})
    add("reassign", "to-spec", {"owner": SPEC})
    add("reassign", "to-pco", {"owner": PCO})
    add("reassign", "no-owner", {})
    add("consent_updated", "withdraw", {"withdrawn": True})
    add("consent_updated", "restore", {"withdrawn": False})
    add("consent_updated", "sms-off", {"channel_sms": False})
    add("consent_updated", "sms-on", {"channel_sms": True})
    add("consent_updated", "empty", {})
    add("consent_updated", "not-bool", {"withdrawn": "yes"})
    add("contact_updated", "valid", {"contact_valid": True})
    add("contact_updated", "invalid", {"contact_valid": False})
    add("mark_lost_contact", "3", {"failed_attempts": 3})
    add("mark_lost_contact", "2", {"failed_attempts": 2})
    add("message_drafted", "d1", {"draft_id": "d1"})
    add("message_approved", "d1", {"draft_id": "d1"})
    add("message_approved", "d2", {"draft_id": "d2"})
    add("message_rejected", "d1", {"draft_id": "d1"})
    for d in ("d1", "d2", "d3"):  # d3 is never approved
        add("message_sent", f"draft:{d}", {**SENT, "draft_id": d, "notification_id": f"n-{d}"})
    add("message_sent", "draft:d1:bad-channel", {**SENT, "channel": "pigeon", "draft_id": "d1", "notification_id": "n"})
    add("message_sent", "no-draft", {**SENT, "notification_id": "n-none"})
    add("message_sent", "auto:listed", AUTO_TEMPLATE)
    add("message_sent", "auto:unlisted", {**AUTO_TEMPLATE, "template_id": "lottery.sms", "purpose": "lottery"})
    add("message_sent", "auto:with-draft", {**AUTO_TEMPLATE, "draft_id": "d1"})
    add("message_sent", "auto:no-template", {k: v for k, v in AUTO_TEMPLATE.items() if k != "template_id"})
    add("callback_requested", "plain", {})
    add("callback_requested", "relay", {"reason": "reschedule", "source_event_id": "e-1"})
    add("callback_requested", "relay-bogus", {"reason": "bored", "source_event_id": "e-1"})
    for reason in sm.REVIEW_REASONS:
        p = {"reason": reason, "source_event_id": "e-1"}
        if reason == "incomplete_procedure":
            p["evidence_ref"] = REF
        add("review_requested", reason, p)
    add("review_requested", "bogus", {"reason": "bogus"})
    add("watchdog_flagged", "idle", {"reason": "idle", "idle_days": 5})
    add("closure_proposed", "auto", {"evidence_ref": REF, "auto": True})
    add("closure_proposed", "no-evidence", {"auto": True})
    add("reply_received", "plain", {"channel": "sms", "text_ref": "t-1"})
    add("reply_classified", "plain", {"intent": "question", "confidence": 0.9})
    # A32 (ADR-025): the erasure record, written only by `system:erasure` on a closed case; after it nothing is accepted (I8)
    add(sm.ERASURE_EVENT, "plain", {"erasure_id": str(uuid5(NS, "erasure")), "categories": ["contact_registry"]})
    add("bogus_event", "plain", {})
    return a


# ---- slices -----------------------------------------------------------------------------------------------------------

ALL = "*"
# Every slice has the main path, closure and reopening, consent withdrawal and restoration (a state change that every
# feature must survive), lost contact, ownership, and the annotations that touch no abstract field.
BASE = {
    "acknowledge": ALL, "accept_ownership": ALL, "assign_ownership": ALL, "contact_patient": ALL,
    "patient_acknowledge": ALL, "book_confirmatory": ALL, "complete_confirmatory": ALL, "clinician_review": ALL,
    "close": ALL, "reopen": ALL, "mark_lost_contact": ALL, "record_informed_refusal": ALL, "appointment_cancelled": ALL, "watchdog_flagged": ALL, "closure_proposed": ALL,
    "reply_received": ALL, "reply_classified": ALL, "bogus_event": ALL, sm.ERASURE_EVENT: ALL,
    "consent_updated": {"withdraw", "restore", "empty", "not-bool"},
}


@dataclass(frozen=True)
class Slice:
    name: str
    doc: str
    moves: dict  # event type -> ALL or a set of labels
    restrict: dict = field(default_factory=dict)  # event type -> the people who may try it in this slice (else CAST)
    perturb: tuple = ()  # the other slices' field groups flipped by the non-interference check
    # I5/I7 with the route's owner scope (A5). Only the ownership slice, which tries every ownership event with every
    # person, can say who may come to own the case; the other slices check that THEIR fields never block a person, and
    # take "the person who must own the case can come to own it" from the ownership slice (verification.md, I7).
    owner_scope: bool = True


SLICES = {
    "messaging": Slice(
        "messaging", "consent ledger, outreach holds, drafts, approvals and sends, triage and review flags",
        {**BASE, "consent_updated": ALL, "contact_updated": ALL, "review_requested": ALL, "message_drafted": ALL,
         "message_approved": ALL, "message_rejected": ALL, "message_sent": ALL, "callback_requested": ALL},
        restrict={"accept_ownership": (PCO,)},
        perturb=("ownership", "ladders"),
        owner_scope=False,
    ),
    "ownership": Slice(
        "ownership", "owner, referral handoff (request, accept, reject, bounce), reassignment, re-route flags",
        {**BASE, "handoff_requested": ALL, "handoff_accepted": ALL, "handoff_rejected": ALL, "reassign": ALL,
         "review_requested": {"reroute_exhausted", "consent_withdrawn", "incomplete_procedure", "triage_uncertain",
                              "already_done", "bogus"}},
        perturb=("messaging", "ladders"),
    ),
    "ladders": Slice(
        "ladders", "state SLA and milestone breaches, the escalation ladders, coalescing (also_for, attached_to)",
        {**BASE, "record_informed_refusal": {"by-spec"},  # one counsellor form here: the ladders do not read it (keeps the slice small)
         "mark_overdue": ALL, "escalate": ALL, "escalation_accepted": ALL,
         "handoff_requested": {"plain", "auto"}, "handoff_accepted": {"plain"},
         "handoff_rejected": {"capacity", "bounce"}, "reassign": {"to-spec"}},
        restrict={"accept_ownership": (PCO,), "handoff_requested": (PCO,), "handoff_accepted": (SPEC,),
                  "handoff_rejected": (SPEC,), "reassign": (COORD,)},
        perturb=("messaging",),
        owner_scope=False,
    ),
}


# ---- the abstraction --------------------------------------------------------------------------------------------------
GROUPS = ("core", "messaging", "ownership", "ladders")


def signature(m: sm.CaseModel) -> tuple:
    """The finite abstract state: everything `step` READS when it decides whether to accept an event and what comes next,
    grouped by feature. Timestamps, event ids, counters, histories and the flags `step` only writes for people to read
    (review reasons, closure proposal, incomplete procedure, watchdog flag, last handoff) are left out."""
    ladders = [m.escalation_event_id, *(ms.escalation_event_id for ms in m.milestones.values())]
    pending = m.pending_handoff
    return (
        # core: the state, consent freeze and where it resumes, and each milestone's clock (started / met / void)
        (m.state, m.erased, m.consent_frozen, bool(m.consent.get("withdrawn")), m.resume_state,
         # the refusal on record: a declined closure reads who counselled and which record (A33)
         None if m.refusal is None else (m.refusal["counselling_ref"], m.refusal["counselled_by"]),
         tuple((ms.started_at is not None, ms.met_at is not None, ms.void) for ms in m.milestones.values())),
        # messaging: the channel consent, the open holds, the approved-unsent drafts (at most 2: two approvable ids)
        (bool(m.consent.get("channel_sms")), tuple(sorted(m.outreach_holds)), tuple(sorted(m.approved_unsent))),
        # ownership: who owns the case and the pending referral request
        (m.owner, None if pending is None else (pending["to_provider"], pending["requested_by"], pending["from_owner"])),
        # ladders: the state's and each milestone's breach and rung, and which ladders share one escalation event
        (m.overdue, m.escalation_level, m.escalation_accepted,
         tuple((ms.overdue, ms.escalation_level, ms.escalation_accepted) for ms in m.milestones.values()),
         tuple(ladders.index(x) if x is not None else -1 for x in ladders)),
    )


def project(sig: tuple | None, drop: tuple) -> tuple | None:
    if sig is None:
        return None
    return tuple(None if g in drop else part for g, part in zip(GROUPS, sig))


def perturb(m: sm.CaseModel, group: str) -> sm.CaseModel:
    """Flip every field of one group (the non-interference check). The result need not be reachable: the question is
    only whether an event of another feature READS these fields."""
    if group == "messaging":
        return replace(m, consent={**m.consent, "channel_sms": not m.consent.get("channel_sms")},
                       outreach_holds=() if m.outreach_holds else ("opt_out", "wrong_number"),
                       approved_unsent=() if m.approved_unsent else ("d1", "d2"))
    if group == "ownership":  # the pending request (the owner is read by assign_ownership, so it is in every slice)
        pending = None if m.pending_handoff else {
            "to_provider": "prov-b", "requested_by": SUP, "from_owner": SUP, "requested_at": T0.isoformat(),
            "requested_event_id": str(uuid5(NS, "perturbed-request"))}
        return replace(m, pending_handoff=pending)
    if group == "ladders":
        flip = uuid5(NS, "perturbed-escalation")
        milestones = {n: replace(ms, overdue=not ms.overdue, escalation_level=0 if ms.escalation_level else 2,
                                 escalation_accepted=not ms.escalation_accepted, escalation_event_id=flip)
                      for n, ms in m.milestones.items()}
        return replace(m, overdue=not m.overdue, escalation_level=0 if m.escalation_level else 2,
                       escalation_accepted=not m.escalation_accepted, escalation_event_id=flip, milestones=milestones)
    raise ValueError(group)


# ---- the gates in front of the machine --------------------------------------------------------------------------------


def human_admitted(identity: auth.Identity, type_: str, payload: dict, model: sm.CaseModel,
                   owner_scope: bool = True) -> bool:
    """Would the events route let this person TRY this event: role policy, owner scope, payload schema, signer and
    evidence shape (the HAPI lookup aside)? Route-only events (message_approved / message_rejected via the drafts route)
    count as not admitted, so liveness is checked with fewer human moves than people have: the safe direction."""
    if identity.role not in policy.EVENT_POLICY.get(type_, frozenset()):
        return False
    try:
        if owner_scope:
            policy.authorize_owner_scope(identity, type_, model)
        payloads.validate(type_, payload)
    except (HTTPException, payloads.PayloadSchemaError):
        return False
    signed = payload.get("signed_by")
    if type_ in ("clinician_review", "close") and signed is not None and not evidence.signed_by_matches(identity, signed):
        return False
    return not (type_ == "close" and not evidence.well_formed_ref(payload["evidence_ref"]))


def physician_not_counsellor(pre: sm.CaseModel, actor: str, p: dict) -> list:
    """I1 extended (A33): a declined closure needs the counselling reference on record, a physician signer, and a signer who
    is not the counsellor. The counsellor is compared by user id, so `specialist_owner:u1` and `Practitioner/u1` are one."""
    bad = []
    refusal = pre.refusal
    if refusal is None or not refusal.get("counselling_ref"):
        return [("I1", "a declined closure without a counselling reference on record")]
    if p.get("evidence_ref") != refusal["counselling_ref"]:
        bad.append(("I1", f"declined closure cites {p.get('evidence_ref')!r}, not the counselling record"))
    if actor.split(":", 1)[0] not in PHYSICIANS:
        bad.append(("I1", f"declined closure by {actor!r}, who is not a physician role"))

    def uid(x: str) -> str:
        return x.split("/", 1)[1] if x.startswith("Practitioner/") else x.split(":", 1)[-1]

    counsellor = uid(refusal["counselled_by"])
    for who in (actor, str(p.get("signed_by"))):
        if uid(who) == counsellor:
            bad.append(("I1", f"declined closure signed by {who!r}, who is the counsellor"))
    return bad


def route_gate_refusal(actor: str, payload: dict) -> str | None:
    """Run the REAL route gate (`routers.cases._check_signature_and_evidence`) on a close the machine accepted: the HTTP
    status it refuses with, or None if it lets the close through to the evidence lookup."""
    from app.routers import cases
    identity = HUMAN_BY_ACTOR.get(actor)
    if identity is None:
        return "no person (services are refused by the route policy)"
    try:
        cases._check_signature_and_evidence(identity, "close", dict(payload), CASE)
    except HTTPException as exc:
        return str(exc.status_code)
    except Exception:  # noqa: BLE001 - got past the signer and shape checks, to the evidence lookup
        return None
    return None


def listed_templates() -> set[tuple]:
    from app import outreach_policy
    pol = outreach_policy.load_policy(OUTREACH_POLICY_PATH)
    return {(t.id, t.version, t.purpose, t.channel) for t in pol.templates.values()}


def auto_guard_admits(model: sm.CaseModel, ev: Event) -> bool:
    """The REAL append-time guard for an automatic message (outreach_policy.auto_message_guard), as if the policy were
    signed (the shipped one is not, which would refuse everything and prove nothing)."""
    from unittest import mock

    from app import outreach_policy
    with mock.patch.object(outreach_policy, "signed_by", lambda pol: "model-check"):
        try:
            outreach_policy.auto_message_guard(ev, str(OUTREACH_POLICY_PATH))(model)
        except sm.InvalidTransition:
            return False
    return True


# ---- results ----------------------------------------------------------------------------------------------------------


@dataclass
class Violation:
    invariant: str
    message: str
    trace: list  # [(type, actor, label, payload)]
    slice: str = ""

    def render(self) -> str:
        lines = [f"VIOLATION {self.invariant} [{self.slice}]: {self.message}",
                 "  counterexample (shortest in BFS order, after case_created):"]
        for n, (t, actor, label, p) in enumerate(self.trace, 1):
            lines.append(f"    {n:2}. {t:<22} by {actor:<32} [{label}] {json.dumps(p, sort_keys=True)}")
        return "\n".join(lines)


@dataclass
class SliceResult:
    name: str
    states: int = 0
    transitions: int = 0
    attempts: int = 0
    depth: int = 0
    levels: list = field(default_factory=list)
    saturated: bool = False
    seconds: float = 0.0
    abstraction_pairs: int = 0
    interference_pairs: int = 0
    table_probes: int = 0
    replayed: int = 0
    checked: dict = field(default_factory=dict)
    delegated: dict = field(default_factory=dict)
    states_by_name: dict = field(default_factory=dict)
    violations: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.saturated and not self.violations


@dataclass
class Result:
    slices: list = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.slices) and all(s.ok for s in self.slices)

    @property
    def violations(self) -> list:
        return [v for s in self.slices for v in s.violations]

    @property
    def states(self) -> int:
        return sum(s.states for s in self.slices)


def _bump(d: dict, key: str) -> None:
    d[key] = d.get(key, 0) + 1


# ---- the search -------------------------------------------------------------------------------------------------------


class Checker:
    def __init__(self, slice_: Slice, pathway: Pathway | None = None, *, max_depth: int = 200,
                 abstraction_samples: int = 1000, interference_samples: int = 400, stop_at_first: bool = True,
                 verbose: bool = False):
        self.sl = slice_
        self.pw = pathway or load_pathway(PATHWAY_PATH)
        self.max_depth = max_depth
        self.abstraction_samples = abstraction_samples
        self.interference_samples = interference_samples
        self.stop_at_first = stop_at_first
        self.verbose = verbose
        self.alpha = [(t, lbl, b, dyn) for t, lbl, b, dyn in alphabet(self.pw)
                      if t in slice_.moves and (slice_.moves[t] == ALL or lbl in slice_.moves[t])]
        self.templates = listed_templates()
        self._events: dict[tuple, Event] = {}
        self._admitted: dict[tuple, bool] = {}
        self.r = SliceResult(slice_.name)
        # Which actors try which event type in the search: those whose row of the actor table holds it (the table is read
        # NOW, so a mutated table is searched as mutated). Every other pair (the agency, the strangers, a service and a
        # type outside its row) is refused by the table before `step` reads the model: the table probe tries every such
        # pair, with every payload, on the first representatives of every state name, and any acceptance is a violation.
        self.actors_for = {}
        for t, _, _, _ in self.alpha:
            humans = self.sl.restrict.get(t, CAST)
            self.actors_for[t] = [a for a in ACTORS if (a in humans or a not in HUMAN_BY_ACTOR)
                                  and t in sm.allowed_events(a)]
        self._static = {(t, lbl, a): self.event(t, a, b(a, None)) for t, lbl, b, dyn in self.alpha if not dyn
                        for a in TABLE_PROBE}
        self._dynamic: dict[tuple, Event] = {}

    def event(self, type_: str, actor: str, payload: dict) -> Event:
        key = (type_, actor, json.dumps(payload, sort_keys=True, default=str))
        ev = self._events.get(key)
        if ev is None:
            ev = Event(CASE, type_, T0, actor, payload, uuid5(NS, repr(key)))
            self._events[key] = ev
        return ev

    def make(self, type_: str, label: str, build, dyn: bool, actor: str, m: sm.CaseModel) -> Event:
        if not dyn:
            return self._static[(type_, label, actor)]
        key = (type_, label, actor, m.state, m.escalation_event_id)
        ev = self._dynamic.get(key)
        if ev is None:
            ev = self._dynamic[key] = self.event(type_, actor, build(actor, m))
        return ev

    def moves(self, m: sm.CaseModel):
        for type_, label, build, dyn in self.alpha:
            for actor in self.actors_for[type_]:
                yield label, self.make(type_, label, build, dyn, actor, m)

    def genesis(self) -> sm.CaseModel:
        self.created = Event(CASE, sm.CASE_CREATED, T0, "system:intake", {"patient_ref": "Patient/p1"},
                             uuid5(NS, "created"))
        return sm.genesis(sm.initial_model(self.pw), self.created, self.pw)

    def try_step(self, m: sm.CaseModel, ev: Event) -> sm.CaseModel | None:
        try:
            return sm.step(m, ev, self.pw)
        except sm.InvalidTransition:
            return None

    def admitted(self, ev: Event, m: sm.CaseModel) -> bool:
        ident = HUMAN_BY_ACTOR.get(ev.actor)
        if ident is None:
            return False
        pending = m.pending_handoff["to_provider"] if m.pending_handoff else None
        key = (ev.event_id, m.owner, pending)
        if key not in self._admitted:
            self._admitted[key] = human_admitted(ident, ev.type, ev.payload, m, self.sl.owner_scope)
        return self._admitted[key]

    # -- I1 to I4, on every accepted transition --
    def check_transition(self, pre: sm.CaseModel, ev: Event, post: sm.CaseModel) -> list:
        out = []
        t, actor, p = ev.type, ev.actor, ev.payload
        c, terminal = self.r.checked, self.pw.terminal_state
        # I1: `closed` only via `close`, from the state before it, by a person, with evidence and a signer
        if post.state == terminal and pre.state != terminal:
            _bump(c, "I1 closures")
            if t != "close":
                out.append(("I1", f"{t!r} entered {terminal!r}"))
            if is_automated(actor) or actor not in HUMAN_BY_ACTOR:
                out.append(("I1", f"{actor!r} is not a person and closed the case"))
            declined = p.get("outcome") == "declined"
            if pre.state != (self.pw.refusal_state if declined else self.pw.states[-2]):
                out.append(("I1", f"closed from {pre.state!r} with outcome {p.get('outcome')!r}"))
            if declined:
                _bump(c, "I1 declined closures")
                out.extend(physician_not_counsellor(pre, actor, p))
            elif pre.state == self.pw.refusal_state:
                out.append(("I1", "a recorded refusal was closed without outcome 'declined'"))
            if not (isinstance(p.get("evidence_ref"), str) and p["evidence_ref"]):
                out.append(("I1", "closed without evidence_ref"))
            if not (isinstance(p.get("signed_by"), str) and p["signed_by"]):
                out.append(("I1", "closed without signed_by"))
            # signer = actor and a well-formed reference: the route's job (ADR-021), not the machine's. Every close the
            # machine accepts without them must be refused by the REAL route gate.
            ident = HUMAN_BY_ACTOR.get(actor)
            signer_ok = ident is not None and p.get("signed_by") in (ident.actor, f"Practitioner/{ident.user_id}")
            ref = p.get("evidence_ref")
            ref_ok = isinstance(ref, str) and ref.partition("/")[2].strip(".") != ""
            if not (signer_ok and ref_ok):
                _bump(self.r.delegated, "I1: foreign signer or malformed evidence -> refused by the route gate (ADR-021)")
                if route_gate_refusal(actor, p) is None:
                    out.append(("I1", f"close signed {p.get('signed_by')!r} with evidence {ref!r} passes the machine "
                                      "AND the route gate"))
        if pre.state == terminal and t not in ("reopen", sm.ERASURE_EVENT):
            out.append(("I1", f"{t!r} accepted on a closed case"))
        # I8 (A32, ADR-025): erasure only on a closed case, only by `system:erasure`, moves no state, and is final
        if pre.erased:
            out.append(("I8", f"{t!r} accepted on an erased case"))
        if t == sm.ERASURE_EVENT:
            _bump(c, "I8 erasures")
            if pre.state != terminal:
                out.append(("I8", f"erasure accepted in {pre.state!r}, not {terminal!r}"))
            if actor != sm.ERASURE_ACTOR:
                out.append(("I8", f"erasure written by {actor!r}"))
            if post.state != pre.state or not post.erased:
                out.append(("I8", "erasure moved the state or did not mark the case erased"))
        # I2: no automated actor gets a human decision accepted; the agency and unknown actors get nothing accepted
        if t in HUMAN_DECISIONS:
            _bump(c, "I2 human decisions")
            if is_automated(actor):
                out.append(("I2", f"automated actor {actor!r} got {t!r} accepted"))
        if actor.startswith(sm.AGENCY_ACTOR_PREFIX) or actor in STRANGERS:
            out.append(("I2", f"{actor!r} has no authority and got {t!r} accepted"))
        # I3: message_sent only for an approved, unsent draft (the notifier) or a well-formed allow-listed template
        # (the automation), while consent holds and no opt-out / wrong-number hold is open
        if t == "message_sent":
            _bump(c, "I3 sends")
            channel = p.get("channel")
            if pre.consent_frozen or pre.consent.get("withdrawn"):
                out.append(("I3", "message sent while consent is withdrawn"))
            if not pre.consent.get(f"channel_{channel}", False):
                out.append(("I3", f"message sent on {channel!r}, which the patient has not consented to"))
            if pre.outreach_holds:
                out.append(("I3", f"message sent while outreach hold(s) {list(pre.outreach_holds)} are open"))
            if actor == sm.NOTIFIER_ACTOR:
                if p.get("draft_id") not in pre.approved_unsent:
                    out.append(("I3", f"draft {p.get('draft_id')!r} sent without an unsent approval"))
            elif actor == sm.AUTOMATION_ACTOR:
                if p.get("auto") is not True or p.get("draft_id") is not None or not p.get("template_id"):
                    out.append(("I3", "automatic message without the auto flag or a template, or with a draft"))
                elif p["template_id"] != f"{p.get('purpose')}.{channel}":
                    out.append(("I3", "automatic message whose template id is not <purpose>.<channel>"))
                elif (p["template_id"], p.get("template_version"), p.get("purpose"), channel) not in self.templates:
                    # the allow-list is the append-time guard's (ADR-032): it must refuse what the machine let through
                    _bump(self.r.delegated, "I3: unlisted template -> refused by the append-time guard (ADR-032)")
                    if auto_guard_admits(pre, ev):
                        out.append(("I3", f"unlisted template {p['template_id']!r} passes machine AND guard"))
            else:
                out.append(("I3", f"message_sent by {actor!r}"))
        # I4: consent frozen => no outreach accepted
        if pre.consent_frozen:
            _bump(c, "I4 frozen transitions")
            if t in OUTREACH:
                out.append(("I4", f"{t!r} accepted while consent is frozen"))
        if (post.state == sm.CONSENT_WITHDRAWN) != post.consent_frozen:
            out.append(("I4", "consent_frozen out of step with the consent_withdrawn state"))
        return out

    # -- the search --
    def run(self) -> SliceResult:
        t0 = time.perf_counter()
        r = self.r
        start = self.genesis()
        sigs = {signature(start): 0}
        reps = [start]
        parent: list = [None]  # (parent index, move)
        human_edges: list = [set()]
        human_enabled = [False]
        behaviours: dict = {}
        pairs: list = []
        frontier, depth = [0], 0
        r.levels = [1]

        def trace_of(i: int, extra=None) -> list:
            out = []
            while parent[i] is not None:
                i, move = parent[i]
                out.append(move)
            out.reverse()
            return out + ([extra] if extra else [])

        def stop(violations) -> bool:
            for inv, msg, trace in violations:
                r.violations.append(Violation(inv, msg, trace, self.sl.name))
            return bool(violations) and self.stop_at_first

        def finish():
            r.seconds, r.states, r.depth = time.perf_counter() - t0, len(reps), depth
            for m in reps:
                _bump(r.states_by_name, m.state)
            return r

        while frontier:
            if depth >= self.max_depth:
                r.saturated = False
                return finish()
            nxt = []
            for i in frontier:
                m = reps[i]
                beh = {}
                for label, ev in self.moves(m):
                    r.attempts += 1
                    key = (ev.type, ev.actor, label)
                    try:
                        post = self.try_step(m, ev)
                    except Exception as exc:  # noqa: BLE001 - step must refuse with InvalidTransition, never crash
                        if stop([("CRASH", f"step raised {type(exc).__name__}: {exc}",
                                  trace_of(i, (ev.type, ev.actor, label, ev.payload)))]):
                            return finish()
                        post = None
                    if post is None:
                        beh[key] = None
                        continue
                    r.transitions += 1
                    move = (ev.type, ev.actor, label, ev.payload)
                    if stop([(inv, msg, trace_of(i, move)) for inv, msg in self.check_transition(m, ev, post)]):
                        return finish()
                    s = signature(post)
                    beh[key] = s
                    j = sigs.get(s)
                    if j is None:
                        j = len(reps)
                        sigs[s] = j
                        reps.append(post)
                        parent.append((i, move))
                        human_edges.append(set())
                        human_enabled.append(False)
                        nxt.append(j)
                    elif len(pairs) < self.abstraction_samples and post != reps[j]:
                        pairs.append((j, post, trace_of(i, move)))
                    if self.admitted(ev, m):
                        human_enabled[i] = True
                        human_edges[i].add(j)
                behaviours[i] = beh
            frontier = nxt
            depth += 1
            r.levels.append(len(nxt))
            if self.verbose:
                print(f"  [{self.sl.name}] level {depth:3}: +{len(nxt):6} states (total {len(reps)}), "
                      f"{r.attempts} step() calls, {time.perf_counter() - t0:.1f} s", file=sys.stderr, flush=True)
        r.saturated = True
        r.levels.pop()  # the final, empty level is the evidence of saturation; `depth` counts the levels that added
        for check in (lambda: self._abstraction(behaviours, pairs),
                      lambda: self._interference(reps, behaviours, trace_of),
                      lambda: self._table_probe(reps, trace_of),
                      lambda: self._liveness(reps, human_edges, human_enabled, trace_of),
                      lambda: self._replay(reps, trace_of)):
            if stop(check()):
                break
        return finish()

    def _behaviour(self, m: sm.CaseModel, drop: tuple = ()) -> dict:
        out = {}
        for label, ev in self.moves(m):
            post = self.try_step(m, ev)
            out[(ev.type, ev.actor, label)] = None if post is None else project(signature(post), drop)
        return out

    def _abstraction(self, behaviours, pairs) -> list:
        """Two concrete models with the same signature (reached by different paths) must behave the same on the whole
        alphabet: same accept/refuse, same successor signature. Evidence that the signature holds what step reads."""
        for j, other, trace in pairs:
            self.r.abstraction_pairs += 1
            mine, theirs = behaviours[j], self._behaviour(other)
            if mine != theirs:
                k = next(k for k in mine if mine[k] != theirs.get(k))
                return [("ABSTRACTION", f"same signature, different behaviour on {k}: representative -> {mine[k]}, "
                                        f"other -> {theirs.get(k)}", trace)]
        return []

    def _interference(self, reps, behaviours, trace_of) -> list:
        """The slice's events must not read the other slices' fields: flip those fields on representatives and require
        the same accept/refuse and the same successor (the flipped group itself left out of the comparison)."""
        step_ = max(1, len(reps) // max(1, self.interference_samples))
        for i in range(0, len(reps), step_):
            for group in self.sl.perturb:
                self.r.interference_pairs += 1
                mine = {k: project(v, (group,)) for k, v in behaviours[i].items()}
                theirs = self._behaviour(perturb(reps[i], group), (group,))
                if mine != theirs:
                    k = next(k for k in mine if mine[k] != theirs.get(k))
                    return [("INTERFERENCE", f"{k} reads the {group!r} fields: {mine[k]} vs {theirs.get(k)} when "
                                             "they are flipped", trace_of(i))]
        return []

    def _table_probe(self, reps, trace_of) -> list:
        """Every (type, actor) pair outside the actor table must be refused whatever the payload and the model: checked
        with every actor of TABLE_PROBE on the first three representatives of every state name."""
        seen: dict = {}
        for i, m in enumerate(reps):
            if seen.get(m.state, 0) >= 3:
                continue
            seen[m.state] = seen.get(m.state, 0) + 1
            for type_, label, build, dyn in self.alpha:
                for actor in TABLE_PROBE:
                    if type_ in sm.allowed_events(actor):
                        continue
                    self.r.table_probes += 1
                    ev = self.make(type_, label, build, dyn, actor, m)
                    if self.try_step(m, ev) is not None:
                        return [("TABLE", f"{actor!r} got {type_!r} accepted although its row does not hold it",
                                 trace_of(i, (type_, actor, label, ev.payload)))]
        return []

    def _liveness(self, reps, human_edges, human_enabled, trace_of) -> list:
        terminal = self.pw.terminal_state
        # I5: every reachable non-closed state has an event some person may try (route gates) that the machine accepts
        for i, m in enumerate(reps):
            if m.state != terminal and not human_enabled[i]:
                return [("I5", f"no person can act in {m.state!r}: {signature(m)}", trace_of(i))]
        self.r.checked["I5 open states"] = sum(1 for m in reps if m.state != terminal)
        # I7: from every reachable state some sequence of people's events reaches `closed` (or a documented hold)
        rev: list = [[] for _ in reps]
        for i, succ in enumerate(human_edges):
            for j in succ:
                rev[j].append(i)
        good = {i for i, m in enumerate(reps) if m.state == terminal or m.state in DOCUMENTED_TERMINAL_HOLDS}
        queue = deque(good)
        while queue:
            for i in rev[queue.popleft()]:
                if i not in good:
                    good.add(i)
                    queue.append(i)
        self.r.checked["I7 states"] = len(reps)
        stuck = [i for i in range(len(reps)) if i not in good]
        if stuck:
            i = min(stuck, key=lambda k: len(trace_of(k)))
            return [("I7", f"no human path to {terminal!r} from {reps[i].state!r}: {signature(reps[i])} "
                           f"({len(stuck)} such states)", trace_of(i))]
        return []

    def _replay(self, reps, trace_of) -> list:
        """I6: replaying each representative's trace as a log (`state_machine.replay_model`) gives exactly the model the
        search holds, every field and not only the signature, and a second replay gives the same."""
        for i, m in enumerate(reps):
            events = [self.created] + [self.event(t, a, p) for t, a, _, p in trace_of(i)]
            once = sm.replay_model(events, self.pw)
            self.r.replayed += 1
            if once != m or sm.replay_model(events, self.pw) != once:
                return [("I6", "replaying the trace gives a different model from the one the search reached",
                         trace_of(i))]
        self.r.checked["I6 replays"] = self.r.replayed
        return []


def check(slices=None, pathway: Pathway | None = None, **kw) -> Result:
    t0 = time.perf_counter()
    out = Result()
    for name in slices or SLICES:
        res = Checker(SLICES[name], pathway, **kw).run()
        out.slices.append(res)
        if res.violations and kw.get("stop_at_first", True):
            break
    out.seconds = time.perf_counter() - t0
    return out


def render(r: Result) -> str:
    lines = [f"model-check: {'PASS' if r.ok else 'FAIL'}  ({r.states} abstract states in {len(r.slices)} slices, "
             f"{r.seconds:.1f} s)"]
    for s in r.slices:
        lines += [
            f"  slice {s.name}: {SLICES[s.name].doc}",
            f"    abstract states      {s.states}  {json.dumps(s.states_by_name, sort_keys=True)}",
            f"    transitions          {s.transitions} accepted of {s.attempts} step() calls",
            f"    depth                {s.depth - 1} (longest shortest trace, events after case_created); "
            f"new states per BFS level {s.levels}",
            f"    saturated            " + (f"True: level {s.depth} added no new state" if s.saturated else
                                           "FALSE: --max-depth reached with states still being found"),
            f"    abstraction check    {s.abstraction_pairs} same-signature pairs, full alphabet",
            f"    non-interference     {s.interference_pairs} flipped models ({', '.join(SLICES[s.name].perturb)})",
            f"    table probe          {s.table_probes} left-out (type, actor) pairs, all refused",
            f"    replayed (I6)        {s.replayed} traces",
            f"    checked              {json.dumps(s.checked, sort_keys=True)}",
            f"    delegated to gates   {json.dumps(s.delegated, sort_keys=True)}",
            f"    time                 {s.seconds:.1f} s",
        ]
    lines += [v.render() for v in r.violations]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slice", action="append", choices=list(SLICES), help="run only this slice (repeatable)")
    ap.add_argument("--max-depth", type=int, default=200)
    ap.add_argument("--abstraction-samples", type=int, default=1000)
    ap.add_argument("--interference-samples", type=int, default=400)
    ap.add_argument("--all", action="store_true", help="report the first violation of every slice, not only the first")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print each BFS level as it finishes")
    args = ap.parse_args(argv)
    r = check(args.slice, max_depth=args.max_depth, abstraction_samples=args.abstraction_samples,
              interference_samples=args.interference_samples, stop_at_first=not args.all, verbose=args.verbose)
    if args.json:
        print(json.dumps({"ok": r.ok, "seconds": round(r.seconds, 1), "slices": [
            {k: v for k, v in vars(s).items() if k != "violations"} | {"violations": [v.render() for v in s.violations]}
            for s in r.slices]}, indent=2, default=str))
    else:
        print(render(r))
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
