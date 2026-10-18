"""T-A37 (A15, ADR-021): closure evidence must resolve, and the signer must be the closer."""

import http.server
import json
import threading
from uuid import UUID

import pytest

from api_helpers import actor_of, login_headers, make_client, user_id_of
from app import evidence
from app.config import Settings
from app.routers import cases

client = make_client()


@pytest.fixture(autouse=True)
def restore_verifier():
    saved = evidence.verifier
    yield
    evidence.verifier = saved


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post(case_id, type_, headers, payload=None):
    return client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def reviewed_case(spec_name="Dr Closer", review=True, patient_ref="Patient/1"):
    """A case at clinician_reviewed (or confirmatory_completed) whose owner is the specialist
    `spec_name`, who acts for provider endo-a15."""
    cid = client.post("/cases", json={"patient_ref": patient_ref}, headers=H("coordinator")).json()["case_id"]
    pco = H("primary_care_owner", "Dr Referrer")
    token = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": spec_name,
                                                 "provider": "endo-a15"}).json()["token"]
    spec = {"Authorization": f"Bearer {token}"}
    steps = [
        ("acknowledge", H("lab"), {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
        ("patient_acknowledge", H("patient", "pat", patient_ref), {}),
        ("handoff_requested", pco, {"to_provider": "endo-a15"}), ("handoff_accepted", spec, {}),
        ("book_confirmatory", spec, {}), ("complete_confirmatory", spec, {}),
    ]
    if review:
        steps.append(("clinician_review", spec, {}))
    for type_, headers, payload in steps:
        assert post(cid, type_, headers, payload).status_code == 201, type_
    return cid, spec


def count(cid):
    return len(cases.store.events_for(UUID(cid)))


def state(cid):
    return cases.store.current_state(UUID(cid))


def me(role, name):
    return {"actor": actor_of(role, name), "practitioner": f"Practitioner/{user_id_of(role, name)}"}


# ---- evidence ---------------------------------------------------------------------------------


def test_t_a37_evidence_that_does_not_exist_is_refused_and_nothing_is_written():
    evidence.verifier = evidence.StaticVerifier({"DiagnosticReport/real-1"})
    cid, spec = reviewed_case()
    who = me("specialist_owner", "Dr Closer")
    n = count(cid)
    r = post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/ghost", "signed_by": who["practitioner"]})
    assert r.status_code == 422 and "does not resolve" in r.json()["detail"]
    assert count(cid) == n and state(cid) == "clinician_reviewed"
    ok = post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/real-1", "signed_by": who["practitioner"]})
    assert ok.status_code == 201 and ok.json()["state"] == "closed"


def test_t_a37_a_procedure_resolves_too():
    evidence.verifier = evidence.StaticVerifier({"Procedure/colonoscopy-9"})
    cid, spec = reviewed_case()
    r = post(cid, "close", spec, {"evidence_ref": "Procedure/colonoscopy-9", "signed_by": me("specialist_owner", "Dr Closer")["actor"]})
    assert r.status_code == 201 and r.json()["state"] == "closed"


@pytest.mark.parametrize("ref", ["Patient/1", "report-1", "DiagnosticReport/", "DiagnosticReport/has space",
                                 "Observation/1", "DiagnosticReport/1/_history", ""])
def test_t_a37_only_a_diagnosticreport_or_procedure_reference_is_accepted(ref):
    evidence.verifier = evidence.StaticVerifier({ref})  # even if "found", the shape is wrong
    cid, spec = reviewed_case()
    n = count(cid)
    r = post(cid, "close", spec, {"evidence_ref": ref, "signed_by": me("specialist_owner", "Dr Closer")["actor"]})
    assert r.status_code == 422, r.text
    assert count(cid) == n


def test_t_a37_when_the_fhir_server_cannot_answer_the_closure_is_refused_not_assumed():
    class Down:
        def exists(self, ref, *, case_id=None, patient_ref=None):
            raise evidence.EvidenceUnavailable("connection refused")

        assess = exists

    evidence.verifier = Down()
    cid, spec = reviewed_case()
    n = count(cid)
    r = post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/x", "signed_by": me("specialist_owner", "Dr Closer")["actor"]})
    assert r.status_code == 503 and "cannot verify" in r.json()["detail"]
    assert count(cid) == n


def test_t_a37_the_verifier_is_not_asked_when_the_state_would_refuse_anyway_order_is_payload_signer_evidence():
    v = evidence.StaticVerifier({"DiagnosticReport/real-1"})
    evidence.verifier = v
    cid, spec = reviewed_case()
    # wrong signer: refused before HAPI is asked
    r = post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/real-1", "signed_by": "primary_care_owner:abc123def456"})
    assert r.status_code == 403 and v.calls == []


def test_t_a37_verification_off_by_default_accepts_a_well_formed_reference():
    assert isinstance(evidence.verifier, evidence.AcceptAllVerifier)
    cid, spec = reviewed_case()
    r = post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/not-checked", "signed_by": me("specialist_owner", "Dr Closer")["actor"]})
    assert r.status_code == 201


def test_t_a37_settings_choose_the_verifier():
    assert isinstance(evidence.configure(Settings(fhir_verify_evidence=False)), evidence.AcceptAllVerifier)
    v = evidence.configure(Settings(fhir_verify_evidence=True, fhir_base_url="http://fhir:8080/fhir/"))
    assert isinstance(v, evidence.FhirVerifier) and v.base_url == "http://fhir:8080/fhir"
    assert Settings().fhir_verify_evidence is False  # unit tests and local runs do not need HAPI


# ---- the signer ---------------------------------------------------------------------------------


def test_t_a37_signed_by_must_be_the_closers_own_identity():
    cid, spec = reviewed_case()
    who, other = me("specialist_owner", "Dr Closer"), me("specialist_owner", "Dr Someone Else")
    ref = "DiagnosticReport/r1"
    n = count(cid)
    for bad in (other["actor"], other["practitioner"], "Practitioner/9", "primary_care_owner:" + who["actor"].split(":")[1],
                "supervisor:abc123def456"):
        r = post(cid, "close", spec, {"evidence_ref": ref, "signed_by": bad})
        assert r.status_code == 403 and "own identity" in r.json()["detail"], (bad, r.text)
    assert count(cid) == n and state(cid) == "clinician_reviewed"
    # a name is not an identity: the schema refuses it before the signer check
    assert post(cid, "close", spec, {"evidence_ref": ref, "signed_by": "Dr Closer"}).status_code == 422
    # both of the caller's own forms work
    assert post(cid, "close", spec, {"evidence_ref": ref, "signed_by": who["practitioner"]}).status_code == 201


def test_t_a37_the_actor_string_form_also_closes():
    cid, spec = reviewed_case()
    r = post(cid, "close", spec, {"evidence_ref": "Procedure/p1", "signed_by": me("specialist_owner", "Dr Closer")["actor"]})
    assert r.status_code == 201 and r.json()["events"][-1]["payload"]["signed_by"] == r.json()["events"][-1]["actor"]


def test_t_a37_a_clinician_review_signature_is_checked_the_same_way():
    cid, spec = reviewed_case(review=False)
    assert state(cid) == "confirmatory_completed"
    n = count(cid)
    r = post(cid, "clinician_review", spec, {"signed_by": me("specialist_owner", "Other")["actor"]})
    assert r.status_code == 403 and count(cid) == n
    ok = post(cid, "clinician_review", spec, {"signed_by": me("specialist_owner", "Dr Closer")["practitioner"]})
    assert ok.status_code == 201 and ok.json()["state"] == "clinician_reviewed"


# ---- the HAPI verifier against a local HTTP server -----------------------------------------------


class _Fhir(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = {"/fhir/DiagnosticReport/here": 200, "/fhir/DiagnosticReport/gone": 410,
                "/fhir/DiagnosticReport/boom": 500}.get(self.path, 404)
        body = b"{}"
        if code == 200:  # the body is the resource asked for, and it is Patient/1's
            ident = self.path.rsplit("/", 1)[1]
            body = json.dumps({"resourceType": "DiagnosticReport", "id": ident,
                               "subject": {"reference": "Patient/1"}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/fhir+json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def fhir_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Fhir)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/fhir"
    server.shutdown()


def test_t_a37_the_fhir_verifier_maps_http_status_to_resolves_or_not(fhir_server):
    v = evidence.FhirVerifier(fhir_server, timeout=2)
    case = {"case_id": UUID(int=1), "patient_ref": "Patient/1"}
    assert v.exists("DiagnosticReport/here", **case) is True
    assert v.exists("DiagnosticReport/nope", **case) is False  # 404
    assert v.exists("DiagnosticReport/gone", **case) is False  # 410 deleted
    with pytest.raises(evidence.EvidenceUnavailable, match="HTTP 500"):
        v.exists("DiagnosticReport/boom", **case)


def test_t_a37_an_unreachable_fhir_server_is_unavailable_not_false():
    v = evidence.FhirVerifier("http://127.0.0.1:9/fhir", timeout=1)
    with pytest.raises(evidence.EvidenceUnavailable, match="unreachable"):
        v.exists("DiagnosticReport/x")
