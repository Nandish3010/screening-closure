"""The privacy centre's "who looked at my record" (ADR-053): staff reads are written to the access audit, bounded, and a patient
reads them as role, name and time and nothing more. Also the PHI/RBAC proof for the patient app: what a patient is sent never
holds staff-only data (priority, model ids, risk, other patients, other staff)."""

import json
from datetime import timedelta
from uuid import UUID

import pytest

from api_helpers import actor_of, make_client
from app import access_audit, notify, readmodel
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.events import InMemoryEventStore
from app.pathway import PathwayRegistry, load_pathway
from app.config import settings as app_settings
from app.routers import cases, drafts

client = make_client()
NAMES = {"coord": "Coordinator Wibble", "pco": "Dr Quillon Marsh", "spec": "Dr Fenwick Ostrander", "sup": "Supervisor Hobart"}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    monkeypatch.setattr(access_audit, "log_store", access_audit.InMemoryAccessLog())
    clock.reset()
    yield
    clock.reset()


def login(role, name, patient_ref=None):
    body = {"role": role, "name": name, **({"patient_ref": patient_ref} if patient_ref else {})}
    return {"Authorization": f"Bearer {client.post('/auth/dev-login', json=body).json()['token']}"}


def post(cid, type_, headers, payload=None):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == 201, (type_, r.text)


def make_case(ref="Patient/aa-1", **extra):
    h = {"coord": login("coordinator", NAMES["coord"]), "pco": login("primary_care_owner", NAMES["pco"]),
         "spec": login("specialist_owner", NAMES["spec"]), "sup": login("supervisor", NAMES["sup"]),
         "lab": login("lab", "Lab Tech"), "pat": login("patient", "Patient Person", ref)}
    cid = client.post("/cases", json={"patient_ref": ref, **extra}, headers=h["coord"]).json()["case_id"]
    post(cid, "acknowledge", h["lab"])
    post(cid, "accept_ownership", h["pco"])
    return cid, h


def log_of(cid, headers):
    r = client.get(f"/cases/{cid}/access-log", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_staff_read_is_logged_and_the_patient_sees_role_name_and_time_only():
    cid, h = make_case()
    assert log_of(cid, h["pat"])["entries"] == []  # nobody has looked yet (posting events is not reading)
    assert client.get(f"/cases/{cid}", headers=h["coord"]).status_code == 200
    body = log_of(cid, h["pat"])
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry == {"at": entry["at"], "role": "coordinator", "name": NAMES["coord"]}  # no actor id, no kind of read
    assert entry["at"] == clock.now().isoformat()
    assert body["window_minutes"] == 15 and body["limit"] == access_audit.PATIENT_LIMIT
    blob = json.dumps(body)
    assert actor_of("coordinator", NAMES["coord"]).split(":")[1] not in blob


def test_each_kind_of_read_is_logged_for_staff_and_a_staff_view_names_the_actor_and_the_kind():
    cid, h = make_case()
    client.get(f"/cases/{cid}", headers=h["coord"])
    client.get(f"/cases/{cid}/timeline", headers=h["coord"])
    client.get(f"/cases/{cid}/notifications", headers=h["coord"])
    client.get(f"/cases/{cid}/fhir", headers=h["coord"])
    client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": "ok"}, headers=h["coord"])
    client.get(f"/cases/{cid}/replies", headers=h["coord"])
    rows = log_of(cid, h["sup"])["entries"]
    assert sorted(r["kind"] for r in rows) == ["case", "fhir", "messages", "replies", "timeline"]
    assert {r["actor"] for r in rows} == {actor_of("coordinator", NAMES["coord"])}
    assert len(log_of(cid, h["pat"])["entries"]) == 5


def test_the_audit_is_bounded_one_row_per_person_and_kind_per_window():
    cid, h = make_case()
    for _ in range(60):  # a page left open, polled
        client.get(f"/cases/{cid}", headers=h["coord"])
    assert len(log_of(cid, h["pat"])["entries"]) == 1
    client.get(f"/cases/{cid}", headers=h["pco"])  # another person: their own row
    clock.advance(hours=1)  # past the window: the same person is logged again
    client.get(f"/cases/{cid}", headers=h["coord"])
    names = [e["name"] for e in log_of(cid, h["pat"])["entries"]]
    assert names == [NAMES["coord"], NAMES["pco"], NAMES["coord"]]  # newest first
    assert access_audit.WINDOW == timedelta(minutes=15)


def test_the_patients_own_reads_and_the_services_are_not_logged():
    cid, h = make_case()
    for path in ("", "/timeline", "/access-log"):
        assert client.get(f"/cases/{cid}{path}", headers=h["pat"]).status_code == 200
    assert client.get("/cases", headers=h["coord"]).status_code == 200  # the worklist lists many cases: not a per-record read
    assert log_of(cid, h["sup"])["entries"] == []  # reading the log is not logged either
    service = type("Svc", (), {"kind": "service", "role": "scheduler", "actor": "system:scheduler"})()
    access_audit.note_read(service, UUID(cid), "case", clock.now())
    assert access_audit.log_store.for_case(UUID(cid), 10) == []


def test_a_failed_or_refused_read_writes_nothing_and_an_audit_failure_does_not_stop_a_read(monkeypatch):
    cid, h = make_case()
    assert client.get(f"/cases/{cid}", headers=login("patient", "Other", "Patient/zz")).status_code == 404
    assert client.get(f"/cases/{cid}/fhir", headers=h["lab"]).status_code == 403
    assert log_of(cid, h["sup"])["entries"] == []

    class Broken:
        def record(self, *a, **k):
            raise RuntimeError("audit store down")

        def for_case(self, *a, **k):
            return []

    monkeypatch.setattr(access_audit, "log_store", Broken())
    assert client.get(f"/cases/{cid}", headers=h["coord"]).status_code == 200


def test_who_may_read_the_access_log():
    cid, h = make_case()
    assert client.get(f"/cases/{cid}/access-log").status_code == 401
    assert client.get(f"/cases/{cid}/access-log", headers=h["lab"]).status_code == 403
    other = login("patient", "Someone Else", "Patient/not-this-one")
    assert client.get(f"/cases/{cid}/access-log", headers=other).status_code == 404  # unknown and foreign look the same
    assert client.get(f"/cases/{cid}/access-log", headers=h["pco"]).status_code == 200  # the owner
    assert client.get(f"/cases/{cid}/access-log", headers=h["spec"]).status_code == 403  # a clinician who does not own it
    assert client.get(f"/cases/{cid}/access-log", headers=h["coord"]).status_code == 200
    assert client.get(f"/cases/{cid}/access-log", headers=h["sup"]).status_code == 200
    assert client.get("/cases/00000000-0000-0000-0000-000000000000/access-log", headers=h["sup"]).status_code == 404


# ---- the patient app never receives staff-only data ---------------------------------------------------------------------


def test_the_patient_app_payloads_hold_no_staff_only_data(monkeypatch):
    chronic = load_pathway("config/pathways/chronic_conditions.yaml")
    reg = PathwayRegistry([load_pathway(app_settings.pathway_path), chronic])
    monkeypatch.setattr(cases, "registry", reg)
    monkeypatch.setattr(cases, "pathway", reg.default)
    cid, h = make_case("Patient/aa-2", pathway="chronic_conditions",
                       results=[{"kind": "blood_pressure", "values": {"systolic_mmhg": 182, "diastolic_mmhg": 104}}])
    staff = client.get(f"/cases/{cid}", headers=h["coord"]).json()
    assert staff["priority"]["tier"] == "urgent"  # the tier exists, for staff
    client.get(f"/cases/{cid}", headers=h["coord"])
    other_cid, _ = make_case("Patient/aa-3")  # another patient's case
    pat = h["pat"]
    answers = [client.get(f"/cases/{cid}", headers=pat).json(), client.get(f"/cases/{cid}/timeline", headers=pat).json(),
               client.get(f"/cases/{cid}/access-log", headers=pat).json(), client.get("/cases", headers=pat).json()]
    for body in answers:
        blob = json.dumps(body).lower()
        for needle in ("priority", "urgent", "bp_very_high", "tier", "model_id", "prompt_hash", "risk_score\": 0", "top_factors",
                       "patient/aa-3", other_cid, "supervisor hobart", "dr fenwick", "wibble_", "triage", "evidence_ref", "signed_by"):
            assert needle not in blob, f"{needle!r} reached the patient app: {blob[:300]}"
    case = answers[0]
    assert case["risk"] is None and case["risk_score"] is None
    assert case["care_contact"] == readmodel.care_contact(NAMES["pco"], "primary_care_owner")
    assert [r["case_id"] for r in answers[3]] == [cid]  # only their own case in the list
    assert "synthetic" in case["care_contact"]  # the number is labelled as a placeholder
