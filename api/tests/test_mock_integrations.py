"""ADR-057: the MOCK Malaffi result replay and Sahatna booking callback. Conformance: the message each mock builds parses as
HL7 v2 of the shape our adapter reads and is applied by the existing handler, nothing else. Gates: coordinator and supervisor
only, development or a declared demo deployment only. Honesty: every response says it is a mock."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_helpers import ROLES, login_headers
from app import confirmatory, integration_feed, mock_integrations as mocks
from app.clock import clock
from app.config import settings
from app.events import InMemoryEventStore
from app.hl7 import contacts, handler
from app.hl7 import parser as p
from app.main import app
from app.routers import cases
from test_hl7_handler import make_case

STAFF = {"coordinator", "supervisor"}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()
    integration_feed.clear()
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def coord(client):
    return login_headers(client, "coordinator")


def replay(client, headers, **body):
    return client.post("/integrations/mock/malaffi/replay", json=body, headers=headers)


def callback(client, headers, case_id, **body):
    return client.post("/integrations/mock/sahatna/booking-callback", json={"case_id": str(case_id), **body}, headers=headers)


# ---- Malaffi ------------------------------------------------------------------------------------------------------


def test_the_message_is_hl7_v2_of_the_shape_the_adapter_reads():
    raw = mocks.oru_message("MOCK-MALAFFI-ORU-1", clock.now())
    msg = p.parse(raw)
    assert msg.message_type == ("ORU", "R01") and msg.control_id == "MOCK-MALAFFI-ORU-1"
    assert msg.sender in handler.current().allowed_senders
    assert [s.name for s in (msg.first("PID"), msg.first("OBR"), msg.first("OBX"))] == ["PID", "OBR", "OBX"]
    assert msg.first("OBX").get(3, 1) == "29771-3" and msg.first("OBX").get(8) == "A" and msg.first("OBX").get(11) == "F"
    assert handler.patient_ref_of(msg.first("PID"), handler.current()) == f"Patient/{mocks.MOCK_PATIENT_ID}"


def test_a_replay_creates_a_case_through_the_hl7_adapter_and_says_it_is_a_mock(client, coord):
    resp = replay(client, coord)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mock"] is True and body["label"].startswith("MOCK:") and "Not a connection to Malaffi" in body["label"]
    assert "specification" in body["note"]
    assert body["message_type"] == "ORU^R01" and body["ack"] == {"code": "AA", "reason": None} and body["outcome"] == "case_created"
    (lc,) = cases.store.load_cases(cases.pathway)
    assert body["case_id"] == str(lc.case_id) and lc.patient_ref == f"Patient/{mocks.MOCK_PATIENT_ID}"
    assert body["state"] in ("result_received", "acknowledged")
    assert body["replays_used"] == 1 and body["replays_max"] == mocks.MAX_REPLAYS


def test_redelivery_is_a_duplicate_and_creates_no_second_case(client, coord):
    first = replay(client, coord).json()
    again = replay(client, coord, redeliver=True).json()
    assert again["outcome"] == "duplicate" and again["ack"]["code"] == "AA" and again["case_id"] == first["case_id"]
    assert len(cases.store.load_cases(cases.pathway)) == 1


def test_redelivery_with_nothing_replayed_is_refused(client, coord):
    assert replay(client, coord, redeliver=True).status_code == 409


def test_a_new_replay_is_a_new_message_and_the_mock_is_bounded(client, coord):
    for n in range(mocks.MAX_REPLAYS):
        assert replay(client, coord).json()["replays_used"] == n + 1
    assert len(cases.store.load_cases(cases.pathway)) == mocks.MAX_REPLAYS
    assert replay(client, coord).status_code == 409


def test_a_sender_that_is_not_allowed_is_refused_by_the_real_adapter_not_by_the_mock(client, coord, monkeypatch):
    monkeypatch.setenv("HL7_ALLOWED_SENDERS", "OTHER|OTHER-FAC")
    body = replay(client, coord).json()
    assert body["outcome"] == "rejected" and body["ack"]["code"] == "AR" and body["case_id"] is None
    assert cases.store.load_cases(cases.pathway) == []


def test_the_feed_marks_the_replay_as_a_mock_and_holds_no_message_content(client, coord):
    replay(client, coord)
    entries = client.get("/integrations/feed", headers=coord).json()["entries"]
    assert any("MOCK Malaffi" in e["summary"] for e in entries)
    assert mocks.MOCK_PATIENT_ID not in str(entries)


# ---- Sahatna ------------------------------------------------------------------------------------------------------


def test_the_callback_is_translated_to_an_siu_s12_of_the_shape_the_adapter_reads():
    slot = mocks.default_slot(clock.now())
    assert slot.weekday() < 5 and (slot.hour, slot.minute) == (9, 30)
    msg = p.parse(mocks.siu_message("MOCK-SAHATNA-x", "10242", "SAH-1", slot, clock.now()))
    assert msg.message_type == ("SIU", "S12") and msg.sender in handler.current().allowed_senders
    assert msg.first("SCH").get(2, 1) == "SAH-1" and msg.first("AIS").get(4, 1) == slot.strftime("%Y%m%d%H%M%S")


def test_a_booking_callback_moves_the_case_to_booked_by_the_existing_event_as_system_hl7(client, coord):
    case_id = make_case("20001", upto="patient_acknowledged")
    resp = callback(client, coord, case_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mock"] is True and "Not a connection to Sahatna" in body["label"] and body["translated_to"] == "HL7 SIU^S12"
    assert body["outcome"] == "booked" and body["event"] == "book_confirmatory"
    assert (body["state_before"], body["state"]) == ("patient_acknowledged", "confirmatory_booked")
    booked = [e for e in cases.store.events_for(case_id) if e.type == "book_confirmatory"]
    assert len(booked) == 1 and booked[0].actor == "system:hl7" and booked[0].payload["appointment_ref"].startswith("SAH-")


def test_the_same_callback_twice_is_a_duplicate(client, coord):
    case_id = make_case("20002", upto="patient_acknowledged")
    assert callback(client, coord, case_id).json()["outcome"] == "booked"
    again = callback(client, coord, case_id).json()
    assert again["outcome"] == "duplicate" and again["ack"]["code"] == "AA" and again["event"] is None
    assert len([e for e in cases.store.events_for(case_id) if e.type == "book_confirmatory"]) == 1


def test_a_callback_for_a_case_that_cannot_be_booked_is_refused_by_the_state_rules_not_applied(client, coord):
    case_id = make_case("20003", upto="acknowledged")
    body = callback(client, coord, case_id).json()
    assert body["outcome"] == "rejected" and body["ack"]["code"] == "AE" and body["event"] is None
    assert body["state"] == "acknowledged"


def test_an_unknown_case_is_404(client, coord):
    assert callback(client, coord, "00000000-0000-4000-8000-000000000000").status_code == 404


def test_the_status_lists_only_cases_that_can_be_booked_and_are_unambiguous(client, coord):
    ok = make_case("20010", upto="patient_acknowledged")
    make_case("20011", upto="acknowledged")
    make_case("20012", upto="patient_acknowledged")
    make_case("20012", upto="patient_acknowledged")  # the same patient twice: the adapter would refuse, so it is not offered
    body = client.get("/integrations/mock", headers=coord).json()
    assert body["mock"] is True and body["enabled"] is True
    assert [r["case_id"] for r in body["sahatna"]["eligible_cases"]] == [str(ok)]
    assert body["malaffi"] == {"label": mocks.MALAFFI_LABEL, "replays_used": 0, "replays_max": mocks.MAX_REPLAYS}


# ---- the gates ----------------------------------------------------------------------------------------------------


def test_only_coordinator_and_supervisor_may_call_the_mocks(client):
    case_id = make_case("20020", upto="patient_acknowledged")
    for role in ROLES:
        h = login_headers(client, role)
        want = 200 if role in STAFF else 403
        assert client.get("/integrations/mock", headers=h).status_code == want, role
        assert replay(client, h).status_code == (200 if role in STAFF else 403), role
        assert callback(client, h, case_id).status_code in ((200,) if role in STAFF else (403,)), role
    assert client.get("/integrations/mock").status_code == 401


def test_the_hl7_service_token_cannot_call_the_mocks(client):
    h = {"Authorization": f"Bearer {settings.hl7_service_token}"}
    assert client.get("/integrations/mock", headers=h).status_code == 403
    assert replay(client, h).status_code == 403


def test_outside_development_and_outside_a_declared_demo_the_routes_do_not_exist(client, coord, monkeypatch):
    case_id = make_case("20030", upto="patient_acknowledged")
    monkeypatch.setattr(mocks, "enabled", lambda: False)  # a dev login token is refused outside dev, so the gate itself is switched here
    assert client.get("/integrations/mock", headers=coord).status_code == 404
    assert replay(client, coord).status_code == 404
    assert callback(client, coord, case_id).status_code == 404
    assert cases.store.load_cases(cases.pathway, "patient_acknowledged") != [] and len(cases.store.load_cases(cases.pathway)) == 1


@pytest.mark.parametrize("env,demo,want", [("dev", False, True), ("dev", True, True), ("production", True, True),
                                            ("production", False, False), ("staging", False, False)])
def test_the_gate_is_development_or_a_declared_demo_deployment(monkeypatch, env, demo, want):
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "demo_deployment", demo)
    assert mocks.enabled() is want
