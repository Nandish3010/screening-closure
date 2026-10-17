"""API tests against the default in-memory store (no database needed)."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)  # no lifespan: cases.store stays the in-memory default
SIGNED = {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}


def test_case_lifecycle_and_response_shape():
    created = client.post("/cases", json={"patient_ref": "Patient/1"}).json()
    case_id = created["case_id"]
    assert created["events"][0]["seq"] == 1
    assert created["events"][0]["type"] == "case_created"
    assert created["events"][0]["event_id"]

    resp = client.post(f"/cases/{case_id}/events", json={"type": "close", "actor": "clinician:x"})
    assert resp.status_code == 409

    first = client.get("/pathways").json()["transitions"][0]["event"]
    resp = client.post(f"/cases/{case_id}/events", json={"type": first, "actor": "coordinator:b"})
    assert resp.status_code == 201
    assert [e["seq"] for e in resp.json()["events"]] == [1, 2]
    assert client.get(f"/cases/{case_id}").json()["state"] == resp.json()["state"]


def test_event_id_makes_retries_idempotent():
    case_id = client.post("/cases", json={}).json()["case_id"]
    first = client.get("/pathways").json()["transitions"][0]["event"]
    body = {"type": first, "actor": "coordinator:b", "event_id": str(uuid4())}
    one = client.post(f"/cases/{case_id}/events", json=body)
    two = client.post(f"/cases/{case_id}/events", json=body)
    assert one.status_code == two.status_code == 201
    assert len(two.json()["events"]) == 2


def test_list_cases_filters_by_state_and_unknown_case_404():
    case_id = client.post("/cases", json={}).json()["case_id"]
    initial = client.get(f"/cases/{case_id}").json()["state"]
    listed = client.get("/cases", params={"state": initial}).json()
    assert case_id in [c["case_id"] for c in listed]
    assert client.get("/cases", params={"state": "no_such_state"}).json() == []
    assert client.get(f"/cases/{uuid4()}").status_code == 404


def test_client_supplied_case_id_is_idempotent_and_conflicts_on_reuse():
    case_id = str(uuid4())
    body = {"case_id": case_id, "patient_ref": "Patient/1", "result_ref": "DiagnosticReport/2"}
    first = client.post("/cases", json=body)
    assert first.status_code == 201
    assert first.json()["case_id"] == case_id
    genesis = first.json()["events"][0]["payload"]
    assert genesis == {"patient_ref": "Patient/1", "result_ref": "DiagnosticReport/2"}
    second = client.post("/cases", json=body)
    assert second.status_code == 409
    assert "CaseAlreadyExists" in second.json()["detail"]
    assert len(client.get("/cases").json()) >= 1
