"""The Integrations page's feed (`GET /integrations/feed`): bounded, staff-only, metadata only, labelled simulated and synthetic."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_helpers import ROLES, login_headers
from app import confirmatory, integration_feed
from app.config import settings
from app.events import InMemoryEventStore
from app.hl7 import contacts
from app.main import app
from app.routers import cases
from test_hl7_handler import fit


@pytest.fixture(autouse=True)
def world(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()
    integration_feed.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_staff_who_may_read_it_are_exactly_coordinator_and_supervisor(client):
    allowed = {"coordinator", "supervisor"}
    for role in ROLES:
        status = client.get("/integrations/feed", headers=login_headers(client, role)).status_code
        assert (status == 200) == (role in allowed), (role, status)
    assert client.get("/integrations/feed").status_code == 401


def test_the_service_tokens_cannot_read_it(client):
    for token in (settings.hl7_service_token, settings.scheduler_service_token):
        resp = client.get("/integrations/feed", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403


def test_an_hl7_message_leaves_one_metadata_entry_and_no_content(client):
    msg = fit("FEED1")
    hl7 = {"Authorization": f"Bearer {settings.hl7_service_token}", "Content-Type": "application/hl7-v2"}
    assert client.post("/hl7", content=msg.encode(), headers=hl7).status_code == 200
    body = client.get("/integrations/feed", headers=login_headers(client, "coordinator")).json()
    assert body["simulated"] is True and body["synthetic"] is True
    entries = [e for e in body["entries"] if e["kind"] == "hl7"]
    assert len(entries) == 1
    e = entries[0]
    assert e["direction"] == "in" and e["status"] == "ACK AA" and "ORU" in e["summary"] and len(e["ref"]) == 12
    text = str(body)
    for piece in msg.replace("\r", "\n").split("\n"):  # no segment of the message appears in the feed
        if len(piece) > 12:
            assert piece not in text
    assert "FEED1" not in text  # nor the control id


def test_the_feed_is_bounded_newest_first_and_the_limit_is_capped(client):
    for i in range(integration_feed.MAX_ENTRIES + 25):
        integration_feed.record("fhir", "out", f"entry {i}", "ok")
    headers = login_headers(client, "supervisor")
    body = client.get("/integrations/feed", params={"limit": 5}, headers=headers).json()
    assert [e["summary"] for e in body["entries"]] == [f"entry {i}" for i in range(integration_feed.MAX_ENTRIES + 24, integration_feed.MAX_ENTRIES + 19, -1)]
    assert len(integration_feed._ring) == integration_feed.MAX_ENTRIES
    assert client.get("/integrations/feed", params={"limit": integration_feed.MAX_LIMIT + 1}, headers=headers).status_code == 422
    assert client.get("/integrations/feed", params={"limit": 0}, headers=headers).status_code == 422
    assert len(client.get("/integrations/feed", params={"limit": integration_feed.MAX_LIMIT}, headers=headers).json()["entries"]) == integration_feed.MAX_LIMIT


def test_record_never_raises_and_ignores_unknown_kinds():
    integration_feed.record("nope", "in", "x", "ok")
    integration_feed.record("hl7", "sideways", "x", "ok")
    integration_feed.record("hl7", "in", None, "ok")  # type: ignore[arg-type]
    assert integration_feed.latest() == []


def test_a_notification_is_recorded_without_its_body(client):
    from datetime import datetime
    from uuid import UUID

    from app import notify

    case_id = client.post("/cases", json={}, headers=login_headers(client, "coordinator")).json()["case_id"]
    row = notify.Notification(UUID(int=1), UUID(case_id), "sms", "en", "result_ready", "SECRET-BODY-TEXT", "blocked_consent",
                              "x", None, datetime(2026, 1, 1))
    notify._note_feed(row)  # the helper `notify.send` calls on every return path
    entries = integration_feed.latest()
    assert entries and entries[0].kind == "notification" and entries[0].status == "blocked_consent"
    assert "SECRET-BODY-TEXT" not in str(entries[0]) and entries[0].ref == case_id.replace("-", "")[:8]


def _projecting_stack(monkeypatch):
    from app import fhir_projection as fp
    from app.calendar import default_calendar
    from app.clock import clock
    from app.config import settings as app_settings
    from app.pathway import load_pathway
    from fhirtest import FakeFhirClient

    pathway = load_pathway(app_settings.pathway_path)
    fake = FakeFhirClient(existing={"Patient/p-1"})
    inner = InMemoryEventStore()
    projector = fp.Projector(fake, inner, pathway, default_calendar(), fp.InMemoryProjectionQueue(), tag={"system": "https://example.test/x", "code": "unit"})
    monkeypatch.setattr(cases, "store", fp.ProjectingStore(inner, projector))
    clock.reset()
    return fake


def test_a_fhir_write_and_a_queued_write_each_leave_a_metadata_entry(client, monkeypatch):
    fake = _projecting_stack(monkeypatch)
    coord = login_headers(client, "coordinator")
    client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=coord)
    fhir = [e for e in integration_feed.latest(50) if e.kind == "fhir"]
    assert fhir and all(e.direction == "out" and e.status == "ok" and "resources" in e.summary for e in fhir)
    integration_feed.clear()
    fake.down = True
    client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=coord)
    queued = [e for e in integration_feed.latest(50) if e.kind == "fhir"]
    assert queued and all(e.status == "queued" for e in queued)
    assert "Patient/p-1" not in str(queued)


def test_an_evidence_lookup_records_the_outcome_but_not_the_reference(monkeypatch):
    from app import evidence

    v = evidence.FhirVerifier("http://fhir.invalid")
    monkeypatch.setattr(v, "_fetch", lambda ref: {"resourceType": "DiagnosticReport", "id": "r-1"})
    assert v._get("DiagnosticReport/r-1") is not None
    monkeypatch.setattr(v, "_fetch", lambda ref: None)
    assert v._get("DiagnosticReport/r-2") is None

    def boom(ref):
        raise evidence.EvidenceUnavailable("down")

    monkeypatch.setattr(v, "_fetch", boom)
    with pytest.raises(evidence.EvidenceUnavailable):
        v._get("Procedure/p-9")
    got = [(e.summary, e.status) for e in reversed(integration_feed.latest(10))]
    assert got == [("FHIR evidence lookup: DiagnosticReport", "found"), ("FHIR evidence lookup: DiagnosticReport", "not_found"),
                   ("FHIR evidence lookup: Procedure", "unavailable")]
    assert "r-1" not in str(got) and "p-9" not in str(got)
