"""E8: POST /cases/{id}/confirmatory-result records the colonoscopy as FHIR evidence (owner only)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from api_helpers import login_headers, make_client
from app import confirmatory, evidence
from app.fhir_projection import FhirError

client = make_client()


@pytest.fixture(autouse=True)
def swap_recorder_and_verifier():
    saved = (confirmatory.recorder, evidence.verifier)
    confirmatory.recorder = confirmatory.StaticRecorder()
    yield confirmatory.recorder
    confirmatory.recorder, evidence.verifier = saved


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post(case_id, type_, headers, payload=None):
    return client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def booked_case(owner="Dr Endo"):
    """A case at confirmatory_booked owned by the specialist `owner`."""
    cid = client.post("/cases", json={"patient_ref": "Patient/p-e8"}, headers=H("coordinator")).json()["case_id"]
    pco = H("primary_care_owner", "Dr Referrer")
    token = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": owner, "provider": "endo-e8"}).json()["token"]
    spec = {"Authorization": f"Bearer {token}"}
    for type_, headers, payload in [
        ("acknowledge", H("lab"), {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
        ("patient_acknowledge", H("patient", "pat", "Patient/p-e8"), {}),
        ("handoff_requested", pco, {"to_provider": "endo-e8"}), ("handoff_accepted", spec, {}),
        ("book_confirmatory", spec, {}),
    ]:
        assert post(cid, type_, headers, payload).status_code == 201, type_
    return cid, spec


def test_the_owner_records_the_result_and_gets_a_procedure_reference(swap_recorder_and_verifier):
    cid, spec = booked_case()
    r = client.post(f"/cases/{cid}/confirmatory-result", headers=spec)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["evidence_ref"] == f"Procedure/colonoscopy-{cid}" == body["procedure_ref"]
    assert body["report_ref"] == f"DiagnosticReport/colonoscopy-report-{cid}"
    (call,) = swap_recorder_and_verifier.calls
    assert call[0] == UUID(cid) and call[1] == "Patient/p-e8"
    # the reference is a valid closure evidence shape
    from app import payloads

    payloads.validate("complete_confirmatory", {"result_ref": body["evidence_ref"]})
    payloads.validate("close", {"evidence_ref": body["evidence_ref"], "signed_by": "Practitioner/abc"})


def test_it_is_idempotent_and_writes_no_event():
    cid, spec = booked_case("Dr Twice")
    n = len(client.get(f"/cases/{cid}", headers=spec).json()["events"])
    a = client.post(f"/cases/{cid}/confirmatory-result", headers=spec).json()
    b = client.post(f"/cases/{cid}/confirmatory-result", headers=spec).json()
    assert a["evidence_ref"] == b["evidence_ref"]
    assert len(client.get(f"/cases/{cid}", headers=spec).json()["events"]) == n


@pytest.mark.parametrize("role", ["coordinator", "supervisor", "lab", "patient"])
def test_other_roles_are_refused(role, swap_recorder_and_verifier):
    cid, _ = booked_case("Dr Guarded")
    r = client.post(f"/cases/{cid}/confirmatory-result", headers=H(role))
    assert r.status_code == 403
    assert swap_recorder_and_verifier.calls == []


def test_a_clinician_who_does_not_own_the_case_is_refused(swap_recorder_and_verifier):
    cid, _ = booked_case("Dr Owner")
    r = client.post(f"/cases/{cid}/confirmatory-result", headers=H("specialist_owner", "Dr Stranger"))
    assert r.status_code == 403 and "may only act on cases they own" in r.json()["detail"]
    assert swap_recorder_and_verifier.calls == []


def test_a_case_before_the_booking_has_no_colonoscopy_to_record():
    cid = client.post("/cases", json={"patient_ref": "Patient/p-early"}, headers=H("coordinator")).json()["case_id"]
    pco = H("primary_care_owner", "Dr Early")
    post(cid, "acknowledge", H("lab"))
    post(cid, "accept_ownership", pco)
    r = client.post(f"/cases/{cid}/confirmatory-result", headers=pco)
    assert r.status_code == 409


def test_unknown_case_is_404_and_no_token_is_401():
    assert client.post(f"/cases/{UUID(int=7)}/confirmatory-result", headers=H("specialist_owner")).status_code == 404
    assert client.post(f"/cases/{UUID(int=7)}/confirmatory-result").status_code == 401


def test_hapi_down_is_a_503_and_records_nothing():
    confirmatory.recorder = confirmatory.StaticRecorder(fail="HAPI unreachable")
    cid, spec = booked_case("Dr Down")
    r = client.post(f"/cases/{cid}/confirmatory-result", headers=spec)
    assert r.status_code == 503 and "HAPI unreachable" in r.json()["detail"]


def test_the_returned_reference_closes_the_case_when_it_exists():
    """End to end with the closure guard: evidence from this route is what close accepts."""
    cid, spec = booked_case("Dr Closer E8")
    ref = client.post(f"/cases/{cid}/confirmatory-result", headers=spec).json()["evidence_ref"]
    evidence.verifier = evidence.StaticVerifier({ref})
    me = client.get("/me", headers=spec).json()
    assert post(cid, "complete_confirmatory", spec, {"result_ref": ref}).status_code == 201
    assert post(cid, "clinician_review", spec, {"signed_by": f"Practitioner/{me['user_id']}"}).status_code == 201
    done = post(cid, "close", spec, {"evidence_ref": ref, "signed_by": f"Practitioner/{me['user_id']}"})
    assert done.status_code == 201 and done.json()["state"] == "closed"


class _FakeFhir:
    def __init__(self, patient_exists=True, fail=False):
        self.bundles, self.patient_exists, self.fail = [], patient_exists, fail

    def transaction(self, bundle):
        if self.fail:
            raise FhirError("HAPI answered HTTP 500")
        self.bundles.append(bundle)

    def exists(self, ref):
        return self.patient_exists


def test_fhir_recorder_puts_a_colonoscopy_procedure_and_report_in_one_transaction():
    fake = _FakeFhir()
    case_id = UUID(int=42)
    out = confirmatory.FhirRecorder(fake).record(case_id, "Patient/p1", datetime(2026, 3, 2, 9, tzinfo=UTC))
    (bundle,) = fake.bundles
    assert bundle["type"] == "transaction"
    procedure, report = [e["resource"] for e in bundle["entry"]]
    assert [e["request"] for e in bundle["entry"]] == [
        {"method": "PUT", "url": f"Procedure/colonoscopy-{case_id}"},
        {"method": "PUT", "url": f"DiagnosticReport/colonoscopy-report-{case_id}"},
    ]
    assert procedure["resourceType"] == "Procedure" and procedure["status"] == "completed"
    assert procedure["code"]["coding"][0]["code"] == "73761001"
    assert procedure["subject"] == {"reference": "Patient/p1"} == report["subject"]
    assert procedure["performedDateTime"].startswith("2026-03-02")
    assert out["procedure_ref"] == f"Procedure/colonoscopy-{case_id}"


def test_fhir_recorder_uses_an_identifier_reference_for_a_patient_hapi_does_not_have():
    fake = _FakeFhir(patient_exists=False)
    confirmatory.FhirRecorder(fake).record(UUID(int=1), "Patient/ghost", datetime(2026, 3, 2, tzinfo=UTC))
    subject = fake.bundles[0]["entry"][0]["resource"]["subject"]
    assert "reference" not in subject and subject["identifier"]["value"] == "Patient/ghost"


def test_fhir_recorder_maps_a_hapi_error_to_unavailable():
    with pytest.raises(confirmatory.RecorderUnavailable):
        confirmatory.FhirRecorder(_FakeFhir(fail=True)).record(UUID(int=1), None, datetime(2026, 3, 2, tzinfo=UTC))


def test_cors_preflight_allows_the_local_web_origin_with_the_authorization_header():
    r = client.options(
        "/cases", headers={"Origin": "http://localhost:4175", "Access-Control-Request-Method": "GET",
                           "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:4175"
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_cors_does_not_allow_an_unknown_origin():
    r = client.options("/cases", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers
