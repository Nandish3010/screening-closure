"""T-A37 against the running HAPI server (`make test-db` with the stack up)."""

from uuid import uuid4

import pytest

import fhirtest
from api_helpers import login_headers, make_client, user_id_of
from app import evidence

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not fhirtest.reachable(), reason=f"HAPI not reachable at {fhirtest.FHIR_URL}"),
]

client = make_client()


@pytest.fixture(autouse=True)
def hapi_verifier():
    saved = evidence.verifier
    evidence.verifier = evidence.FhirVerifier(fhirtest.FHIR_URL, timeout=10)
    yield
    evidence.verifier = saved


def test_a_closure_resolves_against_a_real_diagnosticreport_and_a_ghost_does_not():
    report_id = f"a15-{uuid4().hex[:12]}"
    status, _ = fhirtest.put_tagged({
        "resourceType": "DiagnosticReport", "id": report_id, "status": "final",
        "code": {"text": "Colonoscopy report (test)"}})
    assert status in (200, 201)
    assert evidence.verifier.exists(f"DiagnosticReport/{report_id}") is True
    assert evidence.verifier.exists(f"DiagnosticReport/a15-ghost-{uuid4().hex[:8]}") is False
    assert evidence.verifier.exists(f"Procedure/a15-ghost-{uuid4().hex[:8]}") is False

    # end to end through the API on the in-memory store, with the real verifier
    from test_evidence import reviewed_case, post, count, state  # reuses the walk to clinician_reviewed

    cid, spec = reviewed_case("Dr Hapi Closer")
    signer = f"Practitioner/{user_id_of('specialist_owner', 'Dr Hapi Closer')}"
    n = count(cid)
    r = post(cid, "close", spec, {"evidence_ref": f"DiagnosticReport/a15-nope-{uuid4().hex[:8]}", "signed_by": signer})
    assert r.status_code == 422 and count(cid) == n and state(cid) == "clinician_reviewed"
    r = post(cid, "close", spec, {"evidence_ref": f"DiagnosticReport/{report_id}", "signed_by": signer})
    assert r.status_code == 201 and r.json()["state"] == "closed"
