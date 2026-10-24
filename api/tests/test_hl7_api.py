"""A28 (ADR-039): POST /hl7 and the `system:hl7` identity. The token reaches the HL7 route and nothing else; no user role and
no other service token reaches it; the adapter's actor row holds one event; the transport answers every message that reached
the handler with an ACK."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_helpers import ROLES, login_headers
from app import auth, confirmatory, policy
from app.config import settings
from app.events import InMemoryEventStore
from app.hl7 import contacts, handler
from app.hl7 import parser as p
from app.main import app
from app.routers import cases
from app.state_machine import ACTOR_EVENTS, HL7_ACTOR, CaseModel, InvalidTransition, allowed_events, step
from test_hl7_handler import PATIENT, adt, colonoscopy, fit, make_case, siu


@pytest.fixture(autouse=True)
def world(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def hl7():
    return {"Authorization": f"Bearer {settings.hl7_service_token}", "Content-Type": "application/hl7-v2"}


def post(client, headers, text):
    return client.post("/hl7", content=text.encode("utf-8"), headers=headers)


def test_the_token_is_the_system_hl7_identity():
    identity = auth.authenticate(settings.hl7_service_token)
    assert (identity.kind, identity.role, identity.actor) == ("service", "hl7", "system:hl7") == ("service", auth.HL7_ROLE, HL7_ACTOR)
    assert identity.role not in auth.USER_ROLES and "hl7" in auth.AUTOMATED_ROLE_NAMES
    with pytest.raises(auth.AuthError):
        auth.validate_user("hl7", "x", None)  # nobody can log in as the adapter


def test_a_message_is_acked_over_http(client, hl7):
    resp = post(client, hl7, fit("H1"))
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/hl7-v2")
    ack = p.parse(resp.text)
    assert ack.first("MSA").get(1) == "AA" and ack.first("MSA").get(2) == "H1"
    assert len(cases.store.load_cases(cases.pathway)) == 1
    again = p.parse(post(client, hl7, fit("H1")).text)
    assert again.first("MSA").get(1) == "AA" and len(cases.store.load_cases(cases.pathway)) == 1


def test_every_message_that_reaches_the_handler_gets_an_ack_with_status_200(client, hl7):
    for body, code in [("", "AR"), ("not hl7", "AR"), (fit("H2", status="P"), "AE"), (siu("S15", "H3"), "AE"),
                       (fit("H4", flag="N", value="negative"), "AA")]:
        resp = post(client, hl7, body)
        assert resp.status_code == 200 and p.parse(resp.text).first("MSA").get(1) == code, body[:20]


def test_an_oversize_body_is_a_413_with_an_ar(client, hl7):
    resp = post(client, hl7, "MSH|" + "x" * (p.MAX_MESSAGE_BYTES + 10))
    assert resp.status_code == 413 and p.parse(resp.text).first("MSA").get(1) == "AR"


def test_a_body_that_is_not_utf8_is_an_ar(client, hl7):
    resp = client.post("/hl7", content=b"MSH|^~\\&|A|B|C|D|2026||ORU^R01|X|P|2.5\r\xff\xfe", headers=hl7)
    assert resp.status_code == 200 and p.parse(resp.text).first("MSA").get(1) == "AR"


def test_no_token_is_a_401_and_a_bad_token_a_401(client):
    assert client.post("/hl7", content=fit("N1")).status_code == 401
    assert client.post("/hl7", content=fit("N1"), headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert cases.store.load_cases(cases.pathway) == []


@pytest.mark.parametrize("role", ROLES)
def test_no_user_role_may_post_to_the_adapter(client, role):
    headers = login_headers(client, role, patient_ref="Patient/p1" if role == "patient" else None)
    assert post(client, headers, fit("U1")).status_code == 403
    assert cases.store.load_cases(cases.pathway) == []


@pytest.mark.parametrize("token", ["scheduler_service_token", "watchdog_service_token", "automation_service_token", "agency_service_token"])
def test_no_other_service_token_may_post_to_the_adapter(client, token):
    headers = {"Authorization": f"Bearer {getattr(settings, token)}"}
    assert post(client, headers, fit("S1")).status_code == 403


def test_the_adapter_token_can_call_the_hl7_route_and_nothing_else(client, hl7):
    allowed = {("POST", "/hl7")}
    # routes any valid identity may read (they carry no case data and no write)
    open_to_any_identity = {k for k, v in policy.ROUTE_POLICY.items() if v == policy.ANY_IDENTITY}
    public = {k for k, v in policy.ROUTE_POLICY.items() if v == policy.PUBLIC}
    for (method, path), rule in policy.ROUTE_POLICY.items():
        if rule in (policy.PUBLIC, policy.ANY_IDENTITY):
            continue
        assert (auth.HL7_ROLE in rule) == ((method, path) in allowed), (method, path)
    assert open_to_any_identity == {("GET", "/me"), ("GET", "/pathways")} and ("GET", "/health") in public
    case_id = make_case(upto="patient_acknowledged")
    for method, path in [("GET", "/cases"), ("POST", "/cases"), ("GET", f"/cases/{case_id}"), ("GET", f"/cases/{case_id}/timeline"),
                         ("GET", f"/cases/{case_id}/fhir"), ("POST", f"/cases/{case_id}/confirmatory-result"),
                         ("POST", "/scheduler/sweep"), ("POST", "/watchdog/scan"), ("POST", "/agency/run"),
                         ("GET", "/metrics/closure"), ("GET", "/alerts"), ("POST", f"/cases/{case_id}/replies")]:
        resp = client.request(method, path, headers={"Authorization": hl7["Authorization"]}, json={})
        assert resp.status_code == 403, (method, path, resp.status_code)


@pytest.mark.parametrize("event_type", sorted(policy.EVENT_POLICY) + ["book_confirmatory", "close", "bogus"])
def test_the_adapter_token_cannot_post_any_event(client, hl7, event_type):
    case_id = make_case(upto="patient_acknowledged")
    resp = client.post(f"/cases/{case_id}/events", json={"type": event_type, "payload": {}}, headers={"Authorization": hl7["Authorization"]})
    assert resp.status_code == 403
    assert all(policy.HL7 and auth.HL7_ROLE not in roles for roles in policy.EVENT_POLICY.values())


def test_the_actor_row_holds_book_confirmatory_and_appointment_cancelled_and_the_state_machine_refuses_all_else():
    row = {"book_confirmatory", "appointment_cancelled"}  # A34, ADR-042: the adapter books and cancels the appointment
    assert ACTOR_EVENTS[HL7_ACTOR] == row == allowed_events("system:hl7")
    assert allowed_events("system:hl7:x") == frozenset() and allowed_events("system:HL7") == frozenset()
    from app.main import pathway

    for state in [*pathway.states, *pathway.side_states]:
        for event_type in sorted({*pathway.event_types, *policy.EVENT_POLICY, "close", "reopen", "bogus"} - row):
            with pytest.raises(InvalidTransition, match="actor table"):
                step(CaseModel(state=state), __event(event_type), pathway)


def __event(event_type):
    import uuid
    from datetime import UTC, datetime

    from app.events import Event

    return Event(uuid.uuid4(), event_type, datetime(2026, 1, 5, tzinfo=UTC), HL7_ACTOR, {"reason": "x", "evidence_ref": "Procedure/x", "signed_by": "x"})


def test_a_whole_flow_over_http_and_the_case_page_shows_the_adapter_by_name(client, hl7):
    assert post(client, hl7, fit("W1", ), ).status_code == 200
    (lc,) = cases.store.load_cases(cases.pathway)
    for etype, role, payload in [("acknowledge", "lab", {}), ("accept_ownership", "primary_care_owner", {}),
                                 ("contact_patient", "primary_care_owner", {"channel": "sms"}), ("patient_acknowledge", "coordinator", {})]:
        r = client.post(f"/cases/{lc.case_id}/events", json={"type": etype, "payload": payload}, headers=login_headers(client, role))
        assert r.status_code == 201, r.text
    assert p.parse(post(client, hl7, siu("S12", "W2")).text).first("MSA").get(1) == "AA"
    page = client.get(f"/cases/{lc.case_id}", headers=login_headers(client, "coordinator")).json()
    assert page["state"] == "confirmatory_booked"
    booking = page["events"][-1]
    assert booking["type"] == "book_confirmatory" and booking["actor"] == "system:hl7"
    assert page["actors"]["system:hl7"]["role"] == "system"
    assert p.parse(post(client, hl7, colonoscopy("W3", "complete")).text).first("MSA").get(1) == "AA"
    # nothing the adapter did closed or signed the case
    assert page["state"] != "closed" and not any(e["type"] in ("close", "clinician_review") for e in page["events"])
    assert p.parse(post(client, hl7, adt("A08", "W4")).text).first("MSA").get(1) == "AA"
    assert PATIENT in page["patient_ref"]


def test_outside_dev_the_published_default_token_opens_nothing_and_needs_no_configuration(monkeypatch):
    from app.config import HL7_TOKEN_DEFAULT

    assert settings.hl7_service_token == HL7_TOKEN_DEFAULT and auth.service_identity(HL7_TOKEN_DEFAULT).actor == "system:hl7"
    monkeypatch.setattr(settings, "app_env", "prod")
    assert auth.service_identity(HL7_TOKEN_DEFAULT) is None                 # the adapter is off in production until a token is set
    assert not [p_ for p_ in auth.secure_config_problems() if "HL7" in p_]  # a deployment that does not use HL7 configures nothing
    monkeypatch.setattr(settings, "hl7_service_token", "short")
    assert any("HL7_SERVICE_TOKEN is shorter" in p_ for p_ in auth.secure_config_problems())
    monkeypatch.setattr(settings, "hl7_service_token", "h" * 40)
    assert auth.service_identity("h" * 40).actor == "system:hl7" and not [p_ for p_ in auth.secure_config_problems() if "HL7" in p_]
    monkeypatch.setattr(settings, "scheduler_service_token", "h" * 40)
    assert any("HL7_SERVICE_TOKEN are the same value" in p_ for p_ in auth.secure_config_problems())
    monkeypatch.setattr(settings, "hl7_service_token", "")
    assert auth.service_identity("") is None
