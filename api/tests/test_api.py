"""API tests against the default in-memory store (no database needed)."""

from uuid import uuid4

from api_helpers import actor_of, login_headers, make_client

client = make_client()
COORD = login_headers(client, "coordinator", "bea")
LAB = login_headers(client, "lab", "lab one")
OWNER = login_headers(client, "primary_care_owner", "dr k")
SIGNED = {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}


def post(case_id, type_, headers, payload=None, **extra):
    body = {"type": type_, "payload": payload or {}, **extra}
    return client.post(f"/cases/{case_id}/events", json=body, headers=headers)


def new_case(**body):
    return client.post("/cases", json=body, headers=COORD).json()["case_id"]


def test_case_lifecycle_and_response_shape():
    created = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=COORD).json()
    case_id = created["case_id"]
    assert created["events"][0]["seq"] == 1
    assert created["events"][0]["type"] == "case_created"
    assert created["events"][0]["event_id"]

    resp = post(case_id, "book_confirmatory", COORD)  # allowed role, wrong state
    assert resp.status_code == 409

    first = client.get("/pathways", headers=COORD).json()["transitions"][0]["event"]
    resp = post(case_id, first, LAB)
    assert resp.status_code == 201
    assert [e["seq"] for e in resp.json()["events"]] == [1, 2]
    assert resp.json()["events"][1]["actor"] == actor_of("lab", "lab one")  # derived from the identity (opaque id, A14)
    got = client.get(f"/cases/{case_id}", headers=COORD).json()
    assert got["state"] == resp.json()["state"]


def test_event_id_makes_retries_idempotent():
    case_id = new_case()
    first = client.get("/pathways", headers=COORD).json()["transitions"][0]["event"]
    kwargs = {"event_id": str(uuid4())}
    one = post(case_id, first, LAB, **kwargs)
    two = post(case_id, first, LAB, **kwargs)
    assert one.status_code == two.status_code == 201
    assert len(two.json()["events"]) == 2


def test_list_cases_filters_by_state_and_unknown_case_404():
    case_id = new_case()
    initial = client.get(f"/cases/{case_id}", headers=COORD).json()["state"]
    listed = client.get("/cases", params={"state": initial}, headers=COORD).json()
    assert case_id in [c["case_id"] for c in listed]
    assert client.get("/cases", params={"state": "no_such_state"}, headers=COORD).json() == []
    assert client.get(f"/cases/{uuid4()}", headers=COORD).status_code == 404


def test_client_supplied_case_id_is_idempotent_and_conflicts_on_reuse():
    case_id = str(uuid4())
    body = {"case_id": case_id, "patient_ref": "Patient/1", "result_ref": "DiagnosticReport/2"}
    first = client.post("/cases", json=body, headers=COORD)
    assert first.status_code == 201
    assert first.json()["case_id"] == case_id
    genesis = first.json()["events"][0]["payload"]
    assert genesis == {"patient_ref": "Patient/1", "result_ref": "DiagnosticReport/2"}
    second = client.post("/cases", json=body, headers=COORD)
    assert second.status_code == 409
    assert "CaseAlreadyExists" in second.json()["detail"]
    assert len(client.get("/cases", headers=COORD).json()) >= 1


def test_case_json_carries_the_read_model():
    created = client.post("/cases", json={}, headers=COORD).json()
    model = created["read_model"]
    assert set(model) >= {
        "state", "overdue", "escalation_level", "owner", "consent_frozen", "last_event_at"
    }
    assert model["state"] == created["state"] == "result_received"
    assert model["overdue"] is False and model["escalation_level"] == 0
    assert model["owner"] is None and model["consent_frozen"] is False
    assert model["last_event_at"] == created["events"][0]["at"]

    case_id = created["case_id"]
    post(case_id, "acknowledge", LAB)
    out = post(case_id, "accept_ownership", OWNER).json()
    assert out["read_model"]["owner"] == actor_of("primary_care_owner", "dr k")
    assert out["read_model"]["state"] == out["state"] == "owner_accepted"


def test_side_state_events_over_http():
    case_id = new_case(patient_ref="Patient/77")
    patient = login_headers(client, "patient", "pat", patient_ref="Patient/77")
    out = post(case_id, "consent_updated", patient, {"withdrawn": True}).json()
    assert out["state"] == "consent_withdrawn"
    assert post(case_id, "acknowledge", LAB).status_code == 409  # frozen: only consent moves it
    assert post(case_id, "callback_requested", patient).status_code == 201  # annotations land
    back = post(case_id, "consent_updated", patient, {"withdrawn": False}).json()
    assert back["state"] == "result_received" and back["read_model"]["consent_frozen"] is False
