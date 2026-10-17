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

from app.auth import SCHEDULER_ROLE, USER_ROLES, Identity, optional_identity

PUBLIC = "public"
ANY_IDENTITY = "any_identity"  # any valid user or service token

LAB, PCO, SPEC, COORD, PATIENT, SUPER = USER_ROLES
CLINICIANS = frozenset({PCO, SPEC})  # roles that may sign off a clinical decision
STAFF = frozenset(USER_ROLES) - {PATIENT}
ALL_USERS = frozenset(USER_ROLES)
SCHEDULER = frozenset({SCHEDULER_ROLE})

# (method, path template) -> PUBLIC | ANY_IDENTITY | frozenset of roles
ROUTE_POLICY: dict[tuple[str, str], object] = {
    ("GET", "/health"): PUBLIC,
    ("POST", "/auth/dev-login"): PUBLIC,
    ("GET", "/me"): ANY_IDENTITY,
    ("GET", "/pathways"): ANY_IDENTITY,
    ("POST", "/cases"): frozenset({LAB, COORD, SUPER}),
    ("GET", "/cases"): STAFF,
    # Patients may read their own case only (checked in the handler).
    ("GET", "/cases/{case_id}"): ALL_USERS,
    # Every role may try; EVENT_POLICY decides per event type.
    ("POST", "/cases/{case_id}/events"): ALL_USERS | SCHEDULER,
}

# event type -> roles allowed to emit it via POST /cases/{id}/events
EVENT_POLICY: dict[str, frozenset[str]] = {
    "acknowledge": frozenset({LAB, COORD, SUPER}),
    "accept_ownership": frozenset({PCO, SPEC, SUPER}),
    "contact_patient": frozenset({PCO, SPEC, COORD, SUPER}),
    "patient_acknowledge": frozenset({PATIENT, COORD}),
    "book_confirmatory": frozenset({PCO, SPEC, COORD}),
    "complete_confirmatory": frozenset({SPEC}),
    "clinician_review": CLINICIANS,
    "close": CLINICIANS,
    "reopen": CLINICIANS,
    "mark_overdue": SCHEDULER,
    "escalate": frozenset({SCHEDULER_ROLE, COORD, SUPER}),
    "escalation_accepted": frozenset({PCO, SPEC, SUPER, COORD}),
    "handoff_requested": frozenset({PCO, SPEC, SUPER}),
    "handoff_accepted": CLINICIANS,
    "handoff_rejected": CLINICIANS,
    "reassign": frozenset({SUPER, COORD}),
    "consent_updated": frozenset({PATIENT, COORD}),
    "mark_lost_contact": frozenset({PCO, SPEC, COORD, SUPER}),
    "message_drafted": frozenset({PCO, SPEC, COORD}),
    "message_approved": CLINICIANS,
    # Only the approval-gated send path (D5) writes message_sent; nobody may post it.
    "message_sent": frozenset(),
    "callback_requested": frozenset({PATIENT, COORD}),
    "watchdog_flagged": SCHEDULER,
}

# Roles that act only on their own case.
OWN_CASE_ONLY = frozenset({PATIENT})


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
