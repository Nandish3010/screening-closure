"""T-A37 against the running HAPI server (`make test-db` with the stack up)."""

from uuid import UUID, uuid4

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
    # end to end through the API on the in-memory store, with the real verifier
    from test_evidence import reviewed_case, post, count, state  # reuses the walk to clinician_reviewed

    cid, spec = reviewed_case("Dr Hapi Closer")
    report_id = f"a15-{uuid4().hex[:12]}"
    status, _ = fhirtest.put_tagged({
        "resourceType": "DiagnosticReport", "id": report_id, "status": "final",
        "code": {"text": "Colonoscopy report (test)"},
        "conclusionCode": [{"coding": [{"system": evidence.COMPLETENESS_SYSTEM, "code": "complete"}]}],  # A24
        # SEC-2: the evidence names the case it belongs to (what app.confirmatory writes)
        "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": cid}]})
    assert status in (200, 201)
    case = {"case_id": UUID(cid), "patient_ref": "Patient/1"}
    assert evidence.verifier.exists(f"DiagnosticReport/{report_id}", **case) is True
    assert evidence.verifier.exists(f"DiagnosticReport/{report_id}", case_id=UUID(int=9), patient_ref="Patient/9") is False
    assert evidence.verifier.exists(f"DiagnosticReport/a15-ghost-{uuid4().hex[:8]}", **case) is False
    assert evidence.verifier.exists(f"Procedure/a15-ghost-{uuid4().hex[:8]}", **case) is False
    # the real server answers `Procedure/.` with a Bundle (a search), which must not count as evidence
    assert evidence.verifier.exists("Procedure/.", **case) is False

    signer = f"Practitioner/{user_id_of('specialist_owner', 'Dr Hapi Closer')}"
    n = count(cid)
    r = post(cid, "close", spec, {"evidence_ref": f"DiagnosticReport/a15-nope-{uuid4().hex[:8]}", "signed_by": signer})
    assert r.status_code == 422 and count(cid) == n and state(cid) == "clinician_reviewed"
    r = post(cid, "close", spec, {"evidence_ref": "Procedure/..", "signed_by": signer})
    assert r.status_code == 422 and count(cid) == n
    r = post(cid, "close", spec, {"evidence_ref": f"DiagnosticReport/{report_id}", "signed_by": signer})
    assert r.status_code == 201 and r.json()["state"] == "closed"


def test_a24_the_recorders_resources_are_judged_by_a_real_hapi_round_trip():
    """A24 (ADR-036): what `app.confirmatory` writes is what `FhirVerifier.assess` reads back from a real server,
    including the `Procedure.report` link; a stopped colonoscopy is refused and a CT colonography is accepted."""
    from datetime import UTC, datetime

    from app import confirmatory

    verdicts = {}
    for completeness in ("complete", "incomplete", "ct_colonography"):
        case_id = uuid4()
        procedure, report = confirmatory.build_resources(case_id, {"identifier": {"system": confirmatory.SYSTEM_PATIENT_REF,
                                                                                  "value": "Patient/1"}},
                                                         datetime.now(UTC), completeness)
        assert fhirtest.put_tagged(report)[0] in (200, 201) and fhirtest.put_tagged(procedure)[0] in (200, 201)
        case = {"case_id": case_id, "patient_ref": "Patient/1"}
        verdicts[completeness] = (evidence.verifier.assess(f"Procedure/{procedure['id']}", **case),
                                  evidence.verifier.assess(f"DiagnosticReport/{report['id']}", **case))
    for completeness, ok in (("complete", True), ("incomplete", False), ("ct_colonography", True)):
        via_procedure, via_report = verdicts[completeness]
        assert via_procedure.resolves and via_report.resolves, completeness
        assert via_procedure.acceptable is ok and via_report.acceptable is ok, completeness
        assert via_report.completeness == completeness
    # a Procedure alone (no link) is not closure evidence, even though it exists and is the case's
    case_id = uuid4()
    alone = {"resourceType": "Procedure", "id": f"a24-{uuid4().hex[:12]}", "status": "completed",
             "code": {"text": "Colonoscopy"}, "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}]}
    assert fhirtest.put_tagged(alone)[0] in (200, 201)
    got = evidence.verifier.assess(f"Procedure/{alone['id']}", case_id=case_id, patient_ref="Patient/1")
    assert got.resolves and not got.acceptable and "procedure alone" in got.reason

