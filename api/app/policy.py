"""Authorisation policy: who may call which route, and emit which event type (ADR-012).

Two tables, both default-deny:

* `ROUTE_POLICY` maps (HTTP method, route path template) to the roles allowed in. A route
  that is not in the table is refused for everyone (fail closed), so adding a route without
  deciding its policy cannot expose it; `tests/test_rbac.py` also fails the build.
* `EVENT_POLICY` maps an event type to the roles that may emit it through
  `POST /cases/{id}/events`. An event type that is not listed cannot be posted by anyone.

The state machine remains the authority on whether an event is *valid*; this module only
decides who is *allowed to try*. Patients are additionally limited to their own case.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.auth import AGENCY_ROLE, AUTOMATION_ROLE, HL7_ROLE, SCHEDULER_ROLE, USER_ROLES, WATCHDOG_ROLE, Identity, optional_identity

PUBLIC = "public"
ANY_IDENTITY = "any_identity"  # any valid user or service token

LAB, PCO, SPEC, COORD, PATIENT, SUPER = USER_ROLES
CLINICIANS = frozenset({PCO, SPEC})  # roles that may sign off a clinical decision
STAFF = frozenset(USER_ROLES) - {PATIENT}
ALL_USERS = frozenset(USER_ROLES)
SCHEDULER = frozenset({SCHEDULER_ROLE})
WATCHDOG = frozenset({WATCHDOG_ROLE})
AUTOMATION = frozenset({AUTOMATION_ROLE})  # A21, ADR-032: system:automation
AGENCY = frozenset({AGENCY_ROLE})
HL7 = frozenset({HL7_ROLE})  # A28, ADR-039: system:hl7

# (method, path template) -> PUBLIC | ANY_IDENTITY | frozenset of roles
ROUTE_POLICY: dict[tuple[str, str], object] = {
    ("GET", "/health"): PUBLIC,
    ("POST", "/auth/dev-login"): PUBLIC,
    ("GET", "/me"): ANY_IDENTITY,
    ("GET", "/pathways"): ANY_IDENTITY,
    ("POST", "/cases"): frozenset({LAB, COORD, SUPER}),
    # The HL7 v2 adapter's transport (A28, ADR-039): the message in the body, the ACK in the answer. The `system:hl7` service
    # token reaches this route and nothing else: it is not in any other row of this table or of EVENT_POLICY. What the
    # handler may then DO is HL7_ACTIONS below (and, for the one event it writes, its row of the state machine's actor table).
    ("POST", "/hl7"): HL7,
    # Staff see every case; a patient sees only their own (filtered in the handler, A9).
    ("GET", "/cases"): ALL_USERS,
    # The audit view of a case: same access as the case itself (patients: their own only).
    ("GET", "/cases/{case_id}/timeline"): ALL_USERS,
    # Patients may read their own case only (checked in the handler).
    ("GET", "/cases/{case_id}"): ALL_USERS,
    # The notification log of a case: owners (of that case), coordinators, supervisors (A7).
    ("GET", "/cases/{case_id}/notifications"): frozenset({PCO, SPEC, COORD, SUPER}),
    # The FHIR resources projected for a case (A12): owners (own case), coordinators, supervisors.
    ("GET", "/cases/{case_id}/fhir"): frozenset({PCO, SPEC, COORD, SUPER}),
    # Every role may try; EVENT_POLICY decides per event type. The automation token is not among them: the automation
    # scans write their events in process (A21, ADR-032), so there is no route by which a token can be used to emit one.
    ("POST", "/cases/{case_id}/events"): ALL_USERS | SCHEDULER | WATCHDOG,
    # Closure metrics for the dashboard (A10).
    ("GET", "/metrics/closure"): frozenset({COORD, SUPER}),
    # Human effort versus automation (A21): per-case counts of human and automated actions, assumed minutes.
    ("GET", "/metrics/effort"): frozenset({COORD, SUPER}),
    # Record the confirmatory colonoscopy as FHIR evidence (E8): the case's owner (checked in the handler).
    ("POST", "/cases/{case_id}/confirmatory-result"): frozenset({PCO, SPEC}),
    # Demo clock: supervisor only. The router is registered only when APP_ENV=dev (app.main); outside dev
    # these paths do not exist, so these entries are never consulted.
    ("POST", "/clock/advance"): frozenset({SUPER}),
    ("POST", "/clock/reset"): frozenset({SUPER}),
    # The scheduler service's periodic sweep and its heartbeat.
    ("POST", "/scheduler/sweep"): SCHEDULER,
    ("POST", "/scheduler/heartbeat"): SCHEDULER,
    # The send outbox (ADR-030): the scheduler loop drains approved drafts that have not gone out yet.
    ("POST", "/scheduler/outbox/drain"): SCHEDULER,
    # The automation scans (A21, ADR-032): intake heal (acknowledge, assign owner), auto-reroute, auto-outreach with the
    # pre-approved templates, and the evidence scan in HAPI. The scheduler loop calls them; the automation token may too.
    # They are how the automation acts: the events are appended inside the API process, never posted by a token.
    ("POST", "/scheduler/automation-scan"): SCHEDULER | AUTOMATION,
    ("POST", "/scheduler/evidence-scan"): SCHEDULER | AUTOMATION,
    # The watchdog service's scan (idle cases, scheduler liveness); its own token, nobody else.
    ("POST", "/watchdog/scan"): WATCHDOG,
    # Liveness for the dashboard (A11): coordinator and supervisor; the watchdog may read it too.
    ("GET", "/heartbeats"): frozenset({COORD, SUPER}) | WATCHDOG,
    ("GET", "/alerts"): frozenset({COORD, SUPER}) | WATCHDOG,
    # An inbound patient reply (D13): the coordinator who received it records it here; the text is stored, classified and
    # at most a callback or a human review is requested. A `system:inbound` service token for the message gateway is not
    # built (docs/governance/reply-triage-card.md). Not the patient, the lab, a clinician or any service.
    ("POST", "/cases/{case_id}/replies"): frozenset({COORD}),
    # Reading them (the text is there): whoever has to deal with a flagged reply. Owners are not scoped here; they see
    # the flag on their case and the coordinator reads the text.
    ("GET", "/cases/{case_id}/replies"): frozenset({COORD, SUPER}),
    # The agency (A22, ADR-033). Starting a run: a supervisor, or the agency supervisor's own service token (the
    # token reaches this route and nothing else). Reading the ledger and the org chart: coordinator and supervisor.
    # The inbox of tasks that need a person: owners (items on cases they own, filtered in the handler), coordinator
    # and supervisor. A case's agency summary: owners on their own case, coordinator, supervisor.
    ("POST", "/agency/run"): frozenset({SUPER}) | AGENCY,
    ("GET", "/agency/org"): frozenset({COORD, SUPER}),
    ("GET", "/agency/runs"): frozenset({COORD, SUPER}),
    ("GET", "/agency/tasks"): frozenset({COORD, SUPER}),
    ("GET", "/agency/inbox"): frozenset({PCO, SPEC, COORD, SUPER}),
    ("GET", "/cases/{case_id}/agency"): frozenset({PCO, SPEC, COORD, SUPER}),
}

# event type -> roles allowed to emit it via POST /cases/{id}/events
EVENT_POLICY: dict[str, frozenset[str]] = {
    "acknowledge": frozenset({LAB, COORD, SUPER}),
    "accept_ownership": frozenset({PCO, SPEC, SUPER}),
    # A21: the automation sets the owner from config/providers.yaml without acceptance; the owner still accepts. In
    # process only (the scans, the ingest hook): nobody can post it.
    "assign_ownership": frozenset(),
    "contact_patient": frozenset({PCO, SPEC, COORD, SUPER}),
    "patient_acknowledge": frozenset({PATIENT, COORD}),
    "book_confirmatory": frozenset({PCO, SPEC, COORD}),
    "complete_confirmatory": frozenset({SPEC}),  # the automation writes it in process, on evidence found in HAPI (A21)
    # A21: the evidence scan proposes a closure (one-click sign-off, evidence prefilled). Closure itself stays human. In
    # process only: nobody can post it.
    "closure_proposed": frozenset(),
    "clinician_review": CLINICIANS,
    "close": CLINICIANS,
    "reopen": CLINICIANS,
    "mark_overdue": SCHEDULER,
    "escalate": frozenset({SCHEDULER_ROLE, COORD, SUPER}),
    "escalation_accepted": frozenset({PCO, SPEC, SUPER, COORD}),
    "handoff_requested": frozenset({PCO, SPEC, SUPER}),  # the automation re-routes in process (A21)
    "handoff_accepted": CLINICIANS,
    # Humans reject with a reason from the taxonomy; the scheduler may emit exactly the bounce
    # (reason `timeout`, bounced: true); the state machine enforces both (A5, ADR-016).
    "handoff_rejected": CLINICIANS | {SCHEDULER_ROLE},
    "reassign": frozenset({SUPER, COORD}),
    "consent_updated": frozenset({PATIENT, COORD}),
    # A person confirms the patient's contact details (the wrong number was put right): clears a wrong_number hold on
    # automatic messages and lifts a contact_valid: false on the case. Owner scoping applies to clinicians.
    "contact_updated": frozenset({PCO, SPEC, COORD, SUPER}),
    "mark_lost_contact": frozenset({PCO, SPEC, COORD, SUPER}),
    # A33 (ADR-042): a person records the patient's informed refusal after counselling; `counselled_by` must be a physician
    # (payload schema). The coordinator may record a conversation a physician had; the physician who signs the closure must
    # be someone else (state machine). Owner scoping applies to clinicians.
    "record_informed_refusal": frozenset({PCO, SPEC, COORD}),
    # A34: a person cancels the booked appointment (the patient asked, or the provider did); `system:hl7` does so in process
    # for an SIU S15 (HL7_EVENTS), never through this route.
    "appointment_cancelled": frozenset({PCO, SPEC, COORD}),
    "message_drafted": frozenset({PCO, SPEC, COORD}),
    # Only the draft approval route (D5) writes message_approved, because it must come with the
    # approval of a stored draft; posting it here would fake an approval. Nobody may post it.
    "message_approved": frozenset(),
    # Only the draft rejection route (A16) writes message_rejected, in the same transaction as the
    # rejection of the stored draft; posting it here would record a rejection of nothing.
    "message_rejected": frozenset(),
    # Only the approval-gated send path (D5) writes message_sent; nobody may post it.
    "message_sent": frozenset(),
    "callback_requested": frozenset({PATIENT, COORD}),
    # Written only in process by the reply route as `system:triage` (D13); nobody can post them, and the state
    # machine accepts them from that actor alone (ACTOR_EVENTS).
    "reply_received": frozenset(),
    "reply_classified": frozenset(),
    # Emitted in process: by the API itself as `system:consent` when consent is withdrawn (A6), by the automation when the
    # reroute list is used up (A21) and by `system:triage` when a patient's reply needs a person (D13: opt_out,
    # wrong_number, triage_uncertain, already_done); nobody can post it.
    "review_requested": frozenset(),
    # Only the watchdog service (A11); the scheduler no longer holds it.
    "watchdog_flagged": WATCHDOG,
}

# ---- the agency (A22, ADR-033): agents are labour, not authority ----------------------------------------------
# No agency identity is in EVENT_POLICY: the events route refuses `agency` for every event type, and the state machine
# refuses any event whose actor is `system:agency:*`. The agency is preparation only. The one thing an agent may
# CAUSE is listed here, by the event that the path it uses writes under that path's own actor and rules:
#   message_drafted    the drafting path of `POST /cases/{id}/drafts` (actor `model:<provider>`); the draft is validated,
#                      stored as Provenance and waits for a human to approve it (the approval gate is unchanged)
# Every other agent causes nothing: it reads, and writes only its own ledger rows (a recommendation for a person).
# An agent not listed, or a cause not in its set, is refused (fail closed): `authorize_agency`.
AGENCY_ACTOR_PREFIX = "system:agency:"
AGENCY_CAUSES: dict[str, frozenset[str]] = {
    "system:agency:supervisor": frozenset(),
    "system:agency:outreach": frozenset({"message_drafted"}),
    "system:agency:referral_scheduling": frozenset(),
    "system:agency:evidence_closure": frozenset(),
    "system:agency:patient_support": frozenset(),
    "system:agency:quality_ops": frozenset(),
}
# Decisions that stay with people whatever an agent's allow-list says (checked again by `authorize_agency`).
AGENCY_NEVER = frozenset({
    "close", "clinician_review", "reopen", "message_approved", "message_rejected", "message_sent",
    "escalation_accepted", "handoff_accepted", "handoff_rejected", "reassign", "consent_updated",
    "accept_ownership", "callback_requested", "handoff_requested", "complete_confirmatory", "reply_classified",
    "record_informed_refusal", "appointment_cancelled",
})


def authorize_agency(identity: Identity, causes: str) -> None:
    """May this agency identity cause `causes` (an event type written by another actor's path)? Raises 403."""
    allowed = AGENCY_CAUSES.get(identity.actor) if identity.role == AGENCY_ROLE else None
    if allowed is None:
        raise HTTPException(403, f"{identity.actor!r} is not an agency agent: denied")
    if causes in AGENCY_NEVER or causes not in allowed:
        raise HTTPException(403, f"{identity.actor!r} may not cause {causes!r}")


# ---- the HL7 v2 adapter (A28, ADR-039): what `system:hl7` may cause, default deny ---------------------------------------
# The adapter acts in process, through paths that already exist, never through POST /cases/{id}/events (it has no entry
# there). Three actions, and a message type that would need a fourth is refused (ACK AE):
#   create_case                 a positive FIT result creates a case by the POST /cases path (genesis by `system:intake`)
#   book_confirmatory           the confirmatory appointment of a case, as the event `book_confirmatory` by `system:hl7`
#   record_confirmatory_result  the colonoscopy report is recorded as FHIR evidence by the POST /cases/{id}/confirmatory-result
#                               path (appends no event; closing still needs a clinician, a signature and complete evidence)
# It can never close, sign, reassign, accept ownership, change consent or confirm contact details.
HL7_ACTIONS = frozenset({"create_case", "book_confirmatory", "cancel_appointment", "record_confirmatory_result"})
HL7_EVENTS = frozenset({"book_confirmatory", "appointment_cancelled"})  # equals ACTOR_EVENTS["system:hl7"] (tests/test_actor_table.py)


def authorize_hl7(action: str) -> None:
    """May the HL7 adapter do `action`? Raises 403 for anything not in HL7_ACTIONS."""
    if action not in HL7_ACTIONS:
        raise HTTPException(403, f"{HL7_ROLE!r} may not {action!r}: denied")


# Roles that act only on their own case.
OWN_CASE_ONLY = frozenset({PATIENT})

# Owner scoping (A5): a primary_care_owner or specialist_owner acts only on cases they own,
# except for accepting ownership of an unowned case and answering a handoff addressed to their
# provider. Supervisors and coordinators (and the other roles) are not owner-scoped.
OWNER_SCOPED = frozenset({PCO, SPEC})
HANDOFF_ANSWERS = frozenset({"handoff_accepted", "handoff_rejected"})


def enforce_route_policy(
    request: Request, identity: Identity | None = Depends(optional_identity)
) -> None:
    """App-wide dependency: look the route up, then check authentication and role."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    entry = ROUTE_POLICY.get((request.method, path)) if path else None
    if entry is None:
        raise HTTPException(403, "no access policy for this route: denied")
    if entry == PUBLIC:
        return
    if identity is None:
        raise HTTPException(
            401, "authentication required", headers={"WWW-Authenticate": "Bearer"}
        )
    if entry == ANY_IDENTITY:
        return
    if identity.role not in entry:
        raise HTTPException(403, f"role {identity.role!r} may not call {request.method} {path}")


def authorize_event(identity: Identity, event_type: str) -> None:
    allowed = EVENT_POLICY.get(event_type)
    if allowed is None:
        raise HTTPException(403, f"no policy for event type {event_type!r}: denied")
    if identity.role not in allowed:
        raise HTTPException(403, f"role {identity.role!r} may not emit {event_type!r}")


def authorize_case_access(identity: Identity, patient_ref: str | None) -> None:
    """Patients see and act on their own case only; unknown and foreign cases look the same."""
    if identity.role in OWN_CASE_ONLY and (
        identity.patient_ref is None or identity.patient_ref != patient_ref
    ):
        raise HTTPException(404, "case not found")


def owner_guard(identity: Identity, event_type: str):
    """An `append` guard (SEC-2): re-evaluates owner scope on the replayed model INSIDE the store's
    transaction, under the case's row lock, before the state machine sees the event. A check made on
    an earlier read can be stale by the time the write happens (the case may have been handed to
    someone else in between); this one cannot. Raises the same 403 as `authorize_owner_scope`, and
    the store then writes nothing."""
    def guard(model) -> None:
        authorize_owner_scope(identity, event_type, model)

    return guard


def authorize_owner_scope(identity: Identity, event_type: str, model) -> None:
    """Per-owner scoping for clinicians. `model` is the case's read model (replayed)."""
    if identity.role not in OWNER_SCOPED:
        return
    if model.owner == identity.actor:
        return
    if event_type == "accept_ownership" and model.owner is None:
        return
    pending = model.pending_handoff
    if (
        event_type in HANDOFF_ANSWERS
        and pending is not None
        and pending["to_provider"] == identity.effective_provider
    ):
        return
    raise HTTPException(
        403,
        f"{identity.role} {identity.name!r} may only act on cases they own "
        f"(this case's owner is {model.owner or 'not yet assigned'})",
    )
