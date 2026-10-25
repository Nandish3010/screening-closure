"""A33 and A34 (ADR-042): the informed-refusal closure path and appointment cancel / rebook.

A33: `record_informed_refusal` (human only) moves a case from owner_accepted, patient_acknowledged or confirmatory_booked to
`declined_pending_signoff`; `close {outcome: declined, evidence_ref: <counselling_ref>, signed_by}` by a physician who is not the
counsellor closes it; metrics report declined closures apart; the re-offer date is the refusal plus `re_offer_months`.
A34: `appointment_cancelled` returns confirmatory_booked to patient_acknowledged without moving the milestone clocks (A25)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import yaml

from api_helpers import actor_of, login_headers, make_client, user_id_of
from app import payloads
from app.config import settings as app_settings
from app.events import Event, replay_model
from app.pathway import PathwayError, load_pathway
from app.routers import cases
from app.state_machine import (
    ACTOR_EVENTS, HUMAN_ROLES, InvalidTransition, PHYSICIAN_ROLES, add_months, allowed_events, step,
)

client = make_client()
PATHWAY = load_pathway(app_settings.pathway_path)
REF = "DocumentReference/counselling-1"


def H(role, name=None):
    return login_headers(client, role, name)


def post(cid, type_, headers, payload=None):
    return client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def case_at(upto: str, owner="Dr Owner"):
    """A case whose owner is the primary care owner `owner`, advanced to `upto`."""
    cid = client.post("/cases", json={"patient_ref": "Patient/r1"}, headers=H("coordinator")).json()["case_id"]
    pco = H("primary_care_owner", owner)
    order = ["acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge", "book_confirmatory"]
    heads = {"acknowledge": H("lab"), "accept_ownership": pco, "contact_patient": pco,
             "patient_acknowledge": login_headers(client, "patient", "pat", "Patient/r1"), "book_confirmatory": pco}
    target = {"owner_accepted": 2, "patient_acknowledged": 4, "confirmatory_booked": 5}[upto]
    for type_ in order[:target]:
        assert post(cid, type_, heads[type_]).status_code == 201, type_
    return cid, pco


def state(cid):
    return cases.store.current_state(UUID(cid))


def refusal(by, reason="personal_choice", **extra):
    return {"counselling_ref": REF, "counselled_by": by, "reason": reason, **extra}


# ---- the vocabulary: one source ----------------------------------------------------------------------------------------


def test_the_payload_taxonomies_equal_the_pathway_yaml():
    assert tuple(PATHWAY.refusal_reasons) == payloads.REFUSAL_REASONS
    assert tuple(PATHWAY.appointment_cancel_reasons) == payloads.APPOINTMENT_CANCEL_REASONS
    assert PATHWAY.refusal_state == "declined_pending_signoff" and PATHWAY.re_offer_months == 12


def test_the_actor_table_rows():
    for role in HUMAN_ROLES:
        assert {"record_informed_refusal", "appointment_cancelled"} <= allowed_events(f"{role}:x")  # the machine lets people try
    # no service may write the human-only refusal; only the HL7 adapter may cancel an appointment (SIU S15)
    for actor, row in ACTOR_EVENTS.items():
        assert "record_informed_refusal" not in row or actor.rstrip(":") in HUMAN_ROLES
        assert ("appointment_cancelled" in row) == (actor.rstrip(":") in HUMAN_ROLES or actor == "system:hl7")
    assert "record_informed_refusal" in PATHWAY.human_only_events


def test_the_loader_refuses_a_pathway_that_lets_another_side_state_close(tmp_path):
    raw = yaml.safe_load(open(app_settings.pathway_path))
    raw["transitions"].append({"from": "lost_contact", "event": "close", "to": "closed", "requires": ["evidence_ref", "signed_by"]})
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(PathwayError, match="may transition to"):
        load_pathway(path)
    raw["transitions"].pop()
    raw["transitions"] = [t for t in raw["transitions"] if not (t.get("from") == "declined_pending_signoff" and t["event"] == "close")]
    raw["transitions"].append({"from": "declined_pending_signoff", "event": "close", "to": "closed", "requires": ["evidence_ref", "signed_by"]})
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(PathwayError, match="outcome"):
        load_pathway(path)  # the refusal state may close only for a declined outcome


# ---- A33: the strict payload -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {"counselling_ref": "Procedure/x", "counselled_by": "specialist_owner:a", "reason": "personal_choice"},
    {"counselling_ref": REF, "counselled_by": "coordinator:a", "reason": "personal_choice"},          # a coordinator cannot counsel
    {"counselling_ref": REF, "counselled_by": "specialist_owner:a", "reason": "because"},
    {"counselling_ref": REF, "counselled_by": "specialist_owner:a", "reason": "other_with_note"},    # needs a note
    {"counselling_ref": REF, "counselled_by": "specialist_owner:a", "reason": "personal_choice", "note": "x"},  # a note only with other
    {"counselling_ref": REF, "counselled_by": "specialist_owner:a", "reason": "personal_choice", "extra": 1},
    {"counselled_by": "specialist_owner:a", "reason": "personal_choice"},
    {"counselling_ref": "DocumentReference/..", "counselled_by": "specialist_owner:a", "reason": "personal_choice"},
])
def test_a_malformed_refusal_is_a_422_and_writes_nothing(payload):
    cid, pco = case_at("patient_acknowledged")
    n = len(cases.store.events_for(UUID(cid)))
    assert post(cid, "record_informed_refusal", pco, payload).status_code == 422
    assert len(cases.store.events_for(UUID(cid))) == n and state(cid) == "patient_acknowledged"


def test_the_declined_close_schema_ties_the_outcome_to_the_counselling_record():
    ok = {"outcome": "declined", "evidence_ref": REF, "signed_by": "specialist_owner:a"}
    payloads.validate("close", ok)
    for bad in ({**ok, "evidence_ref": "Procedure/p1"}, {**ok, "outcome": "investigated"},
                {"evidence_ref": REF, "signed_by": "specialist_owner:a"}):
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("close", bad)
    payloads.validate("close", {"evidence_ref": "Procedure/p1", "signed_by": "specialist_owner:a"})


# ---- A33: the path, through the API ------------------------------------------------------------------------------------


@pytest.mark.parametrize("upto", ["owner_accepted", "patient_acknowledged", "confirmatory_booked"])
def test_a_refusal_is_recorded_from_the_three_states_and_the_case_waits_for_a_signature(upto):
    cid, pco = case_at(upto)
    me = actor_of("primary_care_owner", "Dr Owner")
    r = post(cid, "record_informed_refusal", pco, refusal(me, "fear_of_procedure"))
    assert r.status_code == 201 and r.json()["state"] == "declined_pending_signoff"
    rm = r.json()["read_model"]
    assert rm["refusal"]["counselling_ref"] == REF and rm["refusal"]["reason"] == "fear_of_procedure"
    at = datetime.fromisoformat(rm["refusal"]["recorded_at"])
    assert rm["re_offer_due_at"] == add_months(at, 12).isoformat()
    assert r.json()["due_at"]  # the close SLA runs on it: a refusal that nobody signs is not forgotten


def test_a_refusal_is_not_possible_from_the_other_states():
    cid, pco = case_at("owner_accepted")
    post(cid, "contact_patient", pco)  # now patient_contacted
    me = actor_of("primary_care_owner", "Dr Owner")
    assert post(cid, "record_informed_refusal", pco, refusal(me)).status_code == 409


def test_roles_that_may_not_record_a_refusal_are_refused():
    cid, pco = case_at("patient_acknowledged")
    me = actor_of("primary_care_owner", "Dr Owner")
    for role in ("lab", "patient", "supervisor"):
        assert post(cid, "record_informed_refusal", H(role), refusal(me)).status_code == 403, role
    # a clinician who does not own the case
    assert post(cid, "record_informed_refusal", H("specialist_owner", "Dr Other"), refusal(me)).status_code == 403
    # a coordinator can record the conversation a physician had
    assert post(cid, "record_informed_refusal", H("coordinator"), refusal(me)).status_code == 201


def test_a_declined_closure_needs_a_physician_who_is_not_the_counsellor_and_is_reported_apart():
    cid, pco = case_at("confirmatory_booked")
    counsellor = actor_of("primary_care_owner", "Dr Owner")
    assert post(cid, "record_informed_refusal", pco, refusal(counsellor)).status_code == 201

    def close(headers, signed_by, **over):
        return post(cid, "close", headers, {"outcome": "declined", "evidence_ref": REF, "signed_by": signed_by, **over})

    # the counsellor signs their own counselling: refused, in either form of the identity
    assert close(pco, counsellor).status_code == 409
    assert close(pco, f"Practitioner/{user_id_of('primary_care_owner', 'Dr Owner')}").status_code == 409
    # a coordinator and a supervisor never close
    assert close(H("coordinator"), actor_of("coordinator", "test coordinator")).status_code == 403
    assert close(H("supervisor"), actor_of("supervisor", "test supervisor")).status_code == 403
    # the owner is the counsellor, so a coordinator reassigns the sign-off to another physician
    spec_actor = actor_of("specialist_owner", "Dr Signer")
    assert post(cid, "reassign", H("coordinator"), {"owner": spec_actor}).status_code == 201
    assert state(cid) == "declined_pending_signoff"
    spec = H("specialist_owner", "Dr Signer")
    # the evidence must be the counselling record; a colonoscopy closure shape is refused; the signature must be the signer's own
    assert close(spec, spec_actor, evidence_ref="DocumentReference/other").status_code == 409
    assert post(cid, "close", spec, {"evidence_ref": "Procedure/p1", "signed_by": spec_actor}).status_code == 409
    assert close(spec, counsellor).status_code == 403
    done = close(spec, spec_actor)
    assert done.status_code == 201 and done.json()["state"] == "closed"
    rm = done.json()["read_model"]
    assert rm["closure_outcome"] == "declined" and rm["re_offer_due_at"] == rm["refusal"]["re_offer_due_at"]

    m = client.get("/metrics/closure", headers=H("coordinator")).json()
    assert m["declined_closures"] >= 1 and m["declined_pending_signoff"] >= 0
    assert m["cases_closed"] == m["cases_closed_investigated"] + m["declined_closures"]


def test_a_declined_closure_is_not_a_completed_colonoscopy_and_the_refuser_is_not_lost():
    from app import metrics
    cid, pco = case_at("confirmatory_booked")
    counsellor = actor_of("primary_care_owner", "Dr Owner")
    post(cid, "record_informed_refusal", pco, refusal(counsellor))
    events = cases.store.events_for(UUID(cid))
    rec = metrics.build_record(events, PATHWAY)
    assert rec.refusal_pending and rec.refusal_on_record and not rec.completed_confirmatory
    later = events[-1].at.replace(year=events[-1].at.year + 1)
    assert metrics.lost_cases([rec], later) == {"lost_to_follow_up": 0, "lost_never_surfaced": 0}  # the patient decided
    spec_actor = actor_of("specialist_owner", "Dr Signer")
    post(cid, "reassign", H("coordinator"), {"owner": spec_actor})
    assert post(cid, "close", H("specialist_owner", "Dr Signer"), {"outcome": "declined", "evidence_ref": REF, "signed_by": spec_actor}).status_code == 201
    rec = metrics.build_record(cases.store.events_for(UUID(cid)), PATHWAY)
    assert rec.declined_closure and rec.first_safe_close_declined and not rec.completed_confirmatory
    from app.calendar import default_calendar
    out = metrics.closure_metrics([rec], later, default_calendar())
    assert out["declined_closures"] == 1 and out["closures_measured"] == 0 and out["p50_days"] is None
    assert out["declined_p50_days"] is not None and out["lost_to_follow_up"] == 0


def test_a_reopen_clears_the_refusal_and_a_colonoscopy_close_then_works():
    cid, pco = case_at("patient_acknowledged")
    counsellor = actor_of("primary_care_owner", "Dr Owner")
    post(cid, "record_informed_refusal", pco, refusal(counsellor))
    spec_actor = actor_of("specialist_owner", "Dr Signer")
    post(cid, "reassign", H("coordinator"), {"owner": spec_actor})
    spec = H("specialist_owner", "Dr Signer")
    post(cid, "close", spec, {"outcome": "declined", "evidence_ref": REF, "signed_by": spec_actor})
    r = post(cid, "reopen", spec, {"reason": "patient_request"})
    assert r.status_code == 201 and r.json()["read_model"]["refusal"] is None and r.json()["read_model"]["closure_outcome"] is None


# ---- A33: the machine itself, without the route ------------------------------------------------------------------------


def _fold(events):
    return replay_model(events, PATHWAY)


def _declined_model():
    cid = uuid4()
    at = datetime(2026, 1, 5, 8, tzinfo=UTC)
    spec = "specialist_owner:sp1"
    pco = "primary_care_owner:pc1"
    log = [Event(cid, "case_created", at, "system:intake", {})]
    for t, a, p in [("acknowledge", "lab:l", {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
                    ("patient_acknowledge", "coordinator:c", {}),
                    ("record_informed_refusal", pco, {"counselling_ref": REF, "counselled_by": spec, "reason": "personal_choice"})]:
        log.append(Event(cid, t, at, a, p))
    return cid, at, spec, pco, _fold(log)


def test_the_machine_refuses_a_declined_close_that_skips_a_rule():
    cid, at, spec, pco, model = _declined_model()
    assert model.state == "declined_pending_signoff"

    def tryclose(actor, **payload):
        payload = {"outcome": "declined", "evidence_ref": REF, "signed_by": actor, **payload}
        return step(model, Event(cid, "close", at, actor, payload), PATHWAY)

    assert tryclose(pco).state == "closed"                                    # a physician who is not the counsellor
    with pytest.raises(InvalidTransition, match="counselled"):
        tryclose(spec)                                                          # the counsellor
    with pytest.raises(InvalidTransition, match="signed_by must be the signer's own identity"):
        tryclose(pco, signed_by="Practitioner/sp1")                            # in the counsellor's name: not the closer's (A36)
    with pytest.raises(InvalidTransition, match="physician"):
        tryclose("coordinator:c")
    with pytest.raises(InvalidTransition, match="counselling record"):
        tryclose(pco, evidence_ref="DocumentReference/other")
    with pytest.raises(InvalidTransition):
        tryclose(pco, outcome="investigated")                                  # the row demands outcome declined
    for automated in ("system:automation", "system:hl7", "system:scheduler", "model:x"):
        with pytest.raises(InvalidTransition):
            step(model, Event(cid, "close", at, automated, {"outcome": "declined", "evidence_ref": REF, "signed_by": pco}), PATHWAY)
        with pytest.raises(InvalidTransition):
            step(model, Event(cid, "record_informed_refusal", at, automated, refusal(spec)), PATHWAY)


def test_a_colonoscopy_close_cannot_borrow_the_declined_outcome():
    cid = uuid4()
    at = datetime(2026, 1, 5, 8, tzinfo=UTC)
    log = [Event(cid, "case_created", at, "system:intake", {})]
    for t, a, p in [("acknowledge", "lab:l", {}), ("accept_ownership", "primary_care_owner:p", {}),
                    ("contact_patient", "primary_care_owner:p", {}), ("patient_acknowledge", "coordinator:c", {}),
                    ("book_confirmatory", "coordinator:c", {}), ("complete_confirmatory", "specialist_owner:s", {}),
                    ("clinician_review", "specialist_owner:s", {})]:
        log.append(Event(cid, t, at, a, p))
    model = _fold(log)
    assert model.state == "clinician_reviewed"
    with pytest.raises(InvalidTransition, match="declined"):
        step(model, Event(cid, "close", at, "specialist_owner:s", {"outcome": "declined", "evidence_ref": REF, "signed_by": "specialist_owner:s"}), PATHWAY)
    assert PHYSICIAN_ROLES == ("primary_care_owner", "specialist_owner")


def test_the_re_offer_month_arithmetic_and_its_config():
    assert add_months(datetime(2027, 1, 31, tzinfo=UTC), 1) == datetime(2027, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2028, 2, 29, tzinfo=UTC), 12) == datetime(2029, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 30, tzinfo=UTC), 3) == datetime(2027, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 12, 15, tzinfo=UTC), 12) == datetime(2027, 12, 15, tzinfo=UTC)


def test_the_re_offer_interval_is_configurable(tmp_path):
    raw = yaml.safe_load(open(app_settings.pathway_path))
    raw["informed_refusal"]["re_offer_months"] = 3
    path = tmp_path / "three.yaml"
    path.write_text(yaml.safe_dump(raw))
    pw = load_pathway(path)
    cid = uuid4()
    at = datetime(2026, 1, 5, 8, tzinfo=UTC)
    log = [Event(cid, "case_created", at, "system:intake", {})]
    for t, a, p in [("acknowledge", "lab:l", {}), ("accept_ownership", "primary_care_owner:p", {}),
                    ("record_informed_refusal", "primary_care_owner:p", {"counselling_ref": REF, "counselled_by": "specialist_owner:s", "reason": "personal_choice"})]:
        log.append(Event(cid, t, at, a, p))
    # owner_accepted is a source state of the refusal, so no contact is needed first
    assert replay_model(log, pw).refusal["re_offer_due_at"] == datetime(2026, 4, 5, 8, tzinfo=UTC).isoformat()
    raw["informed_refusal"]["re_offer_months"] = 0
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(PathwayError, match="re_offer_months"):
        load_pathway(path)


# ---- A34: cancel and rebook ----------------------------------------------------------------------------------------------


def test_a_cancelled_appointment_returns_the_case_and_a_booking_rebooks_it_with_the_clocks_unchanged():
    cid, pco = case_at("patient_acknowledged")
    assert post(cid, "handoff_requested", pco, {"to_provider": "endo-a15"}).status_code == 201
    assert post(cid, "handoff_accepted", client_spec := _spec(), {}).status_code == 201
    booked = post(cid, "book_confirmatory", client_spec, {"appointment_ref": "APPT-1", "appointment_date": "2026-02-02"})
    assert booked.status_code == 201 and booked.json()["state"] == "confirmatory_booked"
    clock_before = booked.json()["milestones"]["confirmatory_completed"]
    cancelled = post(cid, "appointment_cancelled", client_spec, {"reason": "patient_request", "by_patient": True})
    assert cancelled.status_code == 201 and cancelled.json()["state"] == "patient_acknowledged"
    assert cancelled.json()["milestones"]["confirmatory_completed"] == clock_before        # A25: nothing restarted
    again = post(cid, "book_confirmatory", client_spec, {"appointment_ref": "APPT-2", "appointment_date": "2026-02-09"})
    assert again.status_code == 201 and again.json()["state"] == "confirmatory_booked"
    assert again.json()["milestones"]["confirmatory_completed"]["due_at"] == clock_before["due_at"]


def _spec():
    token = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": "Dr Endo", "provider": "endo-a15"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("payload", [
    {"reason": "bored", "by_patient": True}, {"reason": "other"}, {"reason": "other", "by_patient": "yes"},
    {"reason": "other", "by_patient": True, "extra": 1},
])
def test_a_malformed_cancellation_is_a_422(payload):
    cid, pco = case_at("confirmatory_booked")
    assert post(cid, "appointment_cancelled", pco, payload).status_code == 422
    assert state(cid) == "confirmatory_booked"


def test_a_cancellation_needs_a_booked_case_and_the_right_roles():
    cid, pco = case_at("patient_acknowledged")
    assert post(cid, "appointment_cancelled", pco, {"reason": "other", "by_patient": False}).status_code == 409
    cid, pco = case_at("confirmatory_booked")
    for role in ("lab", "patient", "supervisor"):
        assert post(cid, "appointment_cancelled", H(role), {"reason": "other", "by_patient": False}).status_code == 403, role
    assert post(cid, "appointment_cancelled", H("coordinator"), {"reason": "provider_cancelled", "by_patient": False}).status_code == 201


def test_only_the_hl7_adapter_among_services_may_cancel():
    cid = uuid4()
    at = datetime(2026, 1, 5, 8, tzinfo=UTC)
    log = [Event(cid, "case_created", at, "system:intake", {})]
    for t, a, p in [("acknowledge", "lab:l", {}), ("accept_ownership", "primary_care_owner:p", {}),
                    ("contact_patient", "primary_care_owner:p", {}), ("patient_acknowledge", "coordinator:c", {}),
                    ("book_confirmatory", "coordinator:c", {})]:
        log.append(Event(cid, t, at, a, p))
    model = _fold(log)
    payload = {"reason": "other", "by_patient": False}
    assert step(model, Event(cid, "appointment_cancelled", at, "system:hl7", payload), PATHWAY).state == "patient_acknowledged"
    for other in ("system:automation", "system:scheduler", "system:triage", "system:notifier", "model:x", "system:agency:outreach"):
        with pytest.raises(InvalidTransition):
            step(model, Event(cid, "appointment_cancelled", at, other, payload), PATHWAY)
