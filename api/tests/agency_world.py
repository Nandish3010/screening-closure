"""A 22-case cohort for the agency tests (A22): the seeded cohort's size and ids, walked to a spread of states.

The ids are the ones `simulator/create_cases.py` gives (uuid5 of a report id) so the cohort looks like the demo's
22 cases, but the cases are built straight on the store (no HTTP) and walked through ordinary events by people,
so every state an agent routes on is present: no owner yet, contact phase, lost contact, acknowledged, a rejected
referral, booked with the appointment passed, completed, reviewed, closed, consent withdrawn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import app.main  # noqa: F401 - loads the pathway into the cases router
from app import evidence, notify, ops, users
from app.agency import runner
from app.ai.draft_store import InMemoryDraftStore
from app.auth import opaque_user_id
from app.clock import clock
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.routers import cases, drafts

CASE_NAMESPACE = uuid.UUID("0b8e6d5a-3c1f-4a52-8a77-9d4e2f6b1c33")  # simulator/create_cases.py
START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # the sim clock start, a Monday

PCO = f"primary_care_owner:{opaque_user_id('primary_care_owner', 'Dr Layla Haddad')}"
SPEC = f"specialist_owner:{opaque_user_id('specialist_owner', 'Dr Karim Nasser')}"
LAB = f"lab:{opaque_user_id('lab', 'lab one')}"
COORD = f"coordinator:{opaque_user_id('coordinator', 'Coordinator Sara')}"
PATIENT = "patient:p"

# index -> target state, in the order the cohort is built
TARGETS = (
    ["result_received"] * 5 + ["acknowledged"] * 2 + ["owner_accepted"] * 3 + ["patient_contacted"] * 2
    + ["patient_contacted_twice", "lost_contact"] + ["patient_acknowledged"] * 2
    + ["referral_rejected", "confirmatory_booked", "confirmatory_completed", "clinician_reviewed", "closed",
       "consent_withdrawn"]
)
assert len(TARGETS) == 22


def case_ids() -> list[uuid.UUID]:
    return [uuid.uuid5(CASE_NAMESPACE, f"DiagnosticReport/agency-{i:02d}") for i in range(22)]


def fresh_world() -> None:
    """In-memory stores, a fresh ledger and the clock at its start."""
    cases.store = InMemoryEventStore()
    drafts.store = InMemoryDraftStore()
    notify.log = notify.InMemoryNotificationLog()
    ops.store = ops.InMemoryOpsStore()
    users.directory = users.InMemoryUserDirectory()
    evidence.verifier = evidence.AcceptAllVerifier()
    clock.reset()
    runner.reset_memory_ledger()
    runner.reset_sweep_counter()
    for role, name in (("primary_care_owner", "Dr Layla Haddad"), ("specialist_owner", "Dr Karim Nasser")):
        from app.auth import Identity

        users.directory.ensure(Identity("user", role, name))


def _add(case_id, kind, actor, payload=None, at=None):
    cases.store.append(Event(case_id, kind, at or clock.now(), actor, payload or {}), cases.pathway)


def build_cohort() -> list[uuid.UUID]:
    """Create the 22 cases at the clock's start and walk each to its target state."""
    ids = case_ids()
    at = clock.now()
    for i, (case_id, target) in enumerate(zip(ids, TARGETS, strict=True)):
        cases.store.create_case(case_id, cases.pathway.name,
                                Event(case_id, CASE_CREATED, at, "system:intake",
                                      {"patient_ref": f"Patient/agency-{i:02d}"}, uuid.uuid5(case_id, "genesis")),
                                cases.pathway.initial_state)
        walk(case_id, target)
    return ids


def walk(case_id, target: str) -> None:
    if target == "result_received":
        return
    _add(case_id, "acknowledge", LAB)
    if target == "acknowledged":
        return
    owner = SPEC if target in ("confirmatory_booked", "confirmatory_completed", "clinician_reviewed", "closed") else PCO
    _add(case_id, "accept_ownership", owner)
    if target == "consent_withdrawn":
        _add(case_id, "consent_updated", COORD, {"withdrawn": True})
        return
    if target == "owner_accepted":
        return
    _add(case_id, "contact_patient", owner, {"channel": "phone"})
    if target == "patient_contacted":
        return
    if target == "patient_contacted_twice":
        _add(case_id, "mark_lost_contact", owner, {"failed_attempts": 3})
        _add(case_id, "contact_patient", owner, {"channel": "phone"})
        return
    if target == "lost_contact":
        _add(case_id, "mark_lost_contact", owner, {"failed_attempts": 3})
        return
    _add(case_id, "patient_acknowledge", PATIENT, {"lang": "ar"})
    if target == "patient_acknowledged":
        return
    if target == "referral_rejected":
        _add(case_id, "handoff_requested", owner, {"to_provider": "Al Noor Endoscopy Centre"})
        _add(case_id, "handoff_rejected", SPEC, {"reason": "capacity"})
        return
    _add(case_id, "book_confirmatory", owner, {"appointment_date": (clock.now() - timedelta(days=1)).date().isoformat()})
    if target == "confirmatory_booked":
        return
    _add(case_id, "complete_confirmatory", owner, {"result_ref": f"Procedure/colonoscopy-{case_id}"})
    if target == "confirmatory_completed":
        return
    _add(case_id, "clinician_review", owner, {"signed_by": owner})
    if target == "clinician_reviewed":
        return
    _add(case_id, "close", owner, {"evidence_ref": f"Procedure/colonoscopy-{case_id}", "signed_by": owner})
