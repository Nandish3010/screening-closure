"""SEC-2 fix 1: closure evidence is tied to the case it closes (ADR-021 amended).

A fake HAPI (a local HTTP server) answers the verifier's GET. The verifier must accept only a body that
is the resource asked for AND names this case (case identifier, or the case's patient as subject); a
Bundle, another resource, a resource of another case, and ids made only of dots are all "not found"."""

import http.server
import json
import threading
from datetime import UTC, datetime
from uuid import UUID

import pytest

from api_helpers import actor_of, make_client, user_id_of
from app import confirmatory, evidence
from app.routers import cases
from test_evidence import count, post, reviewed_case, state

client = make_client()


class FakeHapi:
    """Serves `resources` ({"Type/id": body}); any path it does not know is a 404, except the
    search endpoints (`/Procedure/`, `/Procedure/.`...), which answer 200 with a Bundle, as HAPI does."""

    def __init__(self):
        self.resources: dict[str, dict] = {}
        self.requests: list[str] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                key = self.path.removeprefix("/fhir/")
                body = owner.resources.get(key)
                if body is None and key.rstrip("/").split("/")[-1] in ("", ".", ".."):
                    body = {"resourceType": "Bundle", "type": "searchset", "total": 1,
                            "entry": [{"resource": {"resourceType": "Procedure", "id": "someone-elses"}}]}
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Type", "application/fhir+json")
                self.end_headers()
                self.wfile.write(json.dumps(body if body is not None else {"resourceType": "OperationOutcome"}).encode())

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/fhir"

    def put(self, resource: dict) -> str:
        ref = f"{resource['resourceType']}/{resource['id']}"
        self.resources[ref] = resource
        return ref


@pytest.fixture
def hapi():
    fake = FakeHapi()
    saved = evidence.verifier
    evidence.verifier = evidence.FhirVerifier(fake.url, timeout=3)
    yield fake
    evidence.verifier = saved
    fake.server.shutdown()


def procedure(ident, *, case_id=None, patient=None, resource_type="Procedure"):
    out = {"resourceType": resource_type, "id": ident, "status": "completed"}
    if case_id is not None:
        out["identifier"] = [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}]
    if patient is not None:
        out["subject"] = {"reference": patient}
    return out


def close(cid, spec, ref, name="Dr Closer"):
    return post(cid, "close", spec, {"evidence_ref": ref, "signed_by": actor_of("specialist_owner", name)})


def test_case_b_cannot_be_closed_with_case_as_procedure(hapi):
    a, _ = reviewed_case("Dr A", patient_ref="Patient/a")
    b, spec_b = reviewed_case("Dr B", patient_ref="Patient/b")
    ref = hapi.put(procedure("colonoscopy-a", case_id=a, patient="Patient/a"))
    n = count(b)
    r = close(b, spec_b, ref, "Dr B")
    assert r.status_code == 422 and "does not resolve" in r.json()["detail"]
    assert count(b) == n and state(b) == "clinician_reviewed"


def test_another_case_of_the_same_patient_is_not_this_cases_evidence(hapi):
    a, _ = reviewed_case("Dr A2", patient_ref="Patient/same")
    b, spec_b = reviewed_case("Dr B2", patient_ref="Patient/same")
    ref = hapi.put(procedure("colonoscopy-a2", case_id=a, patient="Patient/same"))
    assert close(b, spec_b, ref, "Dr B2").status_code == 422
    assert state(b) == "clinician_reviewed"


def test_a_resource_with_the_case_identifier_or_the_patient_subject_closes_its_own_case(hapi):
    own, spec = reviewed_case("Dr Own", patient_ref="Patient/own")
    ref = hapi.put(procedure("by-identifier", case_id=own))  # no subject at all
    assert close(own, spec, ref, "Dr Own").status_code == 201
    other, spec2 = reviewed_case("Dr Own2", patient_ref="Patient/own2")
    ref2 = hapi.put(procedure("by-subject", patient="Patient/own2"))  # names no case: the patient decides
    assert close(other, spec2, ref2, "Dr Own2").status_code == 201


def test_a_resource_naming_neither_the_case_nor_its_patient_is_refused(hapi):
    cid, spec = reviewed_case("Dr Nobody", patient_ref="Patient/nobody")
    ref = hapi.put(procedure("orphan"))
    assert close(cid, spec, ref, "Dr Nobody").status_code == 422
    ref = hapi.put(procedure("wrong-patient", patient="Patient/somebody-else"))
    assert close(cid, spec, ref, "Dr Nobody").status_code == 422


@pytest.mark.parametrize("ident", [".", "..", "...", "-."])
def test_dot_only_ids_are_refused_before_any_request(hapi, ident):
    # `Procedure/.` and `Procedure/..` collapse to the type's search endpoint, whose Bundle is not evidence
    cid, spec = reviewed_case(f"Dr Dots{len(ident)}{ord(ident[0])}", patient_ref="Patient/dots")
    n = count(cid)
    r = close(cid, spec, f"Procedure/{ident}", f"Dr Dots{len(ident)}{ord(ident[0])}")
    if ident == "-.":  # not dots only: a real (missing) id, so the server is asked and says 404
        assert r.status_code == 422 and hapi.requests
    else:
        assert r.status_code == 422 and hapi.requests == []
    assert count(cid) == n


def test_dot_only_ids_are_refused_even_when_verification_is_off():
    assert isinstance(evidence.verifier, evidence.AcceptAllVerifier)
    cid, spec = reviewed_case("Dr Off", patient_ref="Patient/off")
    for ref in ("Procedure/.", "Procedure/..", "DiagnosticReport/..."):
        assert close(cid, spec, ref, "Dr Off").status_code == 422
    assert state(cid) == "clinician_reviewed"


def test_a_200_with_a_bundle_is_not_found(hapi):
    cid, spec = reviewed_case("Dr Bundle", patient_ref="Patient/bundle")
    ref = hapi.put({"resourceType": "Bundle", "id": "colonoscopy-x", "type": "searchset",
                    "entry": [{"resource": procedure("p", case_id=cid, patient="Patient/bundle")}]})
    hapi.resources["Procedure/colonoscopy-x"] = hapi.resources.pop(ref)  # served at the Procedure path
    r = close(cid, spec, "Procedure/colonoscopy-x", "Dr Bundle")
    assert r.status_code == 422 and state(cid) == "clinician_reviewed"


def test_a_body_that_is_another_resource_or_id_is_not_found(hapi):
    cid, spec = reviewed_case("Dr Mismatch", patient_ref="Patient/mm")
    hapi.resources["Procedure/asked"] = procedure("different", case_id=cid)  # id differs
    hapi.resources["DiagnosticReport/asked"] = procedure("asked", case_id=cid)  # resourceType differs
    for ref in ("Procedure/asked", "DiagnosticReport/asked"):
        assert close(cid, spec, ref, "Dr Mismatch").status_code == 422
    assert state(cid) == "clinician_reviewed"


def test_an_unreachable_server_is_unavailable_not_not_found():
    v = evidence.FhirVerifier("http://127.0.0.1:1/fhir", timeout=0.2)
    with pytest.raises(evidence.EvidenceUnavailable):
        v.exists("Procedure/x", case_id=UUID(int=1), patient_ref="Patient/1")


@pytest.mark.parametrize("ref,ok", [("Procedure/abc", True), ("Procedure/.", False), ("Procedure/..", False),
                                    ("Procedure/", False), ("Procedure/a.b", True), ("DiagnosticReport/1-2", True)])
def test_well_formed_ref(ref, ok):
    assert evidence.well_formed_ref(ref) is ok


def test_the_recorded_resources_carry_the_case_identifier_and_the_patient():
    case_id = UUID(int=42)
    procedure_, report = confirmatory.build_resources(case_id, {"reference": "Patient/9"}, datetime(2026, 1, 5, tzinfo=UTC))
    for resource in (procedure_, report):
        assert {"system": "urn:screening-closure:case", "value": str(case_id)} in resource["identifier"]
        assert resource["subject"] == {"reference": "Patient/9"}
        assert evidence.belongs_to_case(resource, case_id, "Patient/9")
        assert not evidence.belongs_to_case(resource, UUID(int=43), "Patient/9")


def test_the_evidence_a_case_records_closes_that_case_only(hapi):
    a, spec_a = reviewed_case("Dr Rec A", patient_ref="Patient/rec")
    b, spec_b = reviewed_case("Dr Rec B", patient_ref="Patient/rec")
    proc, _ = confirmatory.build_resources(UUID(a), {"reference": "Patient/rec"}, datetime(2026, 1, 5, tzinfo=UTC))
    ref = hapi.put(proc)
    assert close(b, spec_b, ref, "Dr Rec B").status_code == 422
    assert close(a, spec_a, ref, "Dr Rec A").status_code == 201
