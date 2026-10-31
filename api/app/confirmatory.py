"""Recording the confirmatory colonoscopy as FHIR evidence (E8).

`POST /cases/{id}/confirmatory-result` (the case's owner only) writes a synthetic colonoscopy
`Procedure` and its `DiagnosticReport` to HAPI and returns their references. The owner then
posts `complete_confirmatory {result_ref}` and, later, `close {evidence_ref}` with the returned
`Procedure/<id>`; `close` is checked against HAPI by `app.evidence` (A15), so the evidence the
clinician signs against is a resource that really exists.

Each resource carries the identifier `urn:screening-closure:case|<case_id>` (and the patient as its
subject): that is what ties the evidence to this case, and `app.evidence.FhirVerifier` refuses a
closure whose evidence belongs to another case (SEC-2).

Completeness (A24, ADR-036). The recorder says how the colonoscopy ended: `complete` (default), `incomplete`, or
`ct_colonography` (a CT colonography done instead). The report is `final`, its `code` is LOINC 18746-8 (a colonoscopy
report) or, for a CT colonography, plain text, and its `conclusionCode` carries `completeness` in our own code system
(`urn:screening-closure:completeness`); the Procedure is `completed` and links to the report (`Procedure.report`), or
`stopped` when the colonoscopy was incomplete. `app.evidence` reads exactly this back, so a case closes only on a
complete colonoscopy (or a CT colonography) and an incomplete one cannot be closed by citing it.

Other pathways. The recorder writes the confirmatory procedure of the case's pathway (the pack's `evidence.procedure`, e.g.
`colposcopy`): the same two resources, named by that procedure in their ids and plain text (`Colposcopy report`, `Colposcopy
complete`), with NO terminology code (none is asserted without a source), and no CT-colonography alternative. `app.evidence`
reads exactly this back, and a pack that requires the procedure to be named refuses a record that does not name it.

The ids are deterministic (`colonoscopy-<case_id>`, `colonoscopy-report-<case_id>`) and written
with PUT inside one transaction, so recording twice is idempotent and a demo that is reset and
replayed produces the same references. Nothing here decides anything about the case: it writes
two resources and an audit trail is still the event log.

The recorder is swappable like `app.evidence.verifier`. Unit tests use `StaticRecorder`. When
neither FHIR evidence verification nor the FHIR projection is on (no HAPI to talk to) the
default `LocalRecorder` only computes the references, which is harmless because nothing then
checks that they exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.evidence import CASE_IDENTIFIER_SYSTEM, COMPLETENESS_SYSTEM, COMPLETENESS_VALUES
from app.pathway import DEFAULT_PROCEDURE
from app.fhir_projection import DEFAULT_TAG, FhirClient, FhirError, HttpFhirClient, is_safe_ref

SYSTEM_PATIENT_REF = "urn:screening-closure:patient-ref"
COLONOSCOPY_SNOMED = {"system": "http://snomed.info/sct", "code": "73761001", "display": "Colonoscopy"}
COLONOSCOPY_LOINC = {"system": "http://loinc.org", "code": "18746-8", "display": "Colonoscopy study"}
# What each completeness writes: the report's plain text and conclusion, and the Procedure's status.
_COMPLETENESS_TEXT = {
    "complete": ("Colonoscopy report", "Colonoscopy complete",
                 "Colonoscopy completed. Synthetic demonstration record; no clinical finding is encoded."),
    "incomplete": ("Colonoscopy report", "Colonoscopy incomplete",
                   "Colonoscopy incomplete. Synthetic demonstration record; no clinical finding is encoded."),
    "ct_colonography": ("CT colonography report", "CT colonography complete",
                        "CT colonography completed in place of colonoscopy. Synthetic demonstration record; "
                        "no clinical finding is encoded."),
}
_PROCEDURE_STATUS = {"complete": "completed", "incomplete": "stopped", "ct_colonography": "completed"}


class RecorderUnavailable(Exception):
    """HAPI could not be asked or refused the resources: the result is not recorded (503)."""


def procedure_ref(case_id: UUID, procedure: str = DEFAULT_PROCEDURE) -> str:
    return f"Procedure/{procedure}-{case_id}"


def report_ref(case_id: UUID, procedure: str = DEFAULT_PROCEDURE) -> str:
    return f"DiagnosticReport/{procedure}-report-{case_id}"


def _texts(procedure: str, completeness: str) -> tuple[str, str, str]:
    """(report text, conclusion text, conclusion) for the pathway's procedure. The colonoscopy's are the table above."""
    if procedure == DEFAULT_PROCEDURE:
        return _COMPLETENESS_TEXT[completeness]
    name = procedure.capitalize()
    state = "complete" if completeness == "complete" else "incomplete"
    return (f"{name} report", f"{name} {state}",
            f"{name} {'completed' if state == 'complete' else 'incomplete'}. Synthetic demonstration record; "
            "no clinical finding is encoded.")


def build_resources(case_id: UUID, patient: dict | None, performed_at: datetime,
                    completeness: str = "complete", procedure: str = DEFAULT_PROCEDURE) -> tuple[dict, dict]:
    """The Procedure and the DiagnosticReport for the case's confirmatory procedure (pure). `completeness` is how it ended
    (A24): `complete`, `incomplete` or, for the colonoscopy only, `ct_colonography`."""
    if completeness not in COMPLETENESS_VALUES:
        raise ValueError(f"completeness must be one of {list(COMPLETENESS_VALUES)}")
    if completeness == "ct_colonography" and procedure != DEFAULT_PROCEDURE:
        raise ValueError(f"a {procedure} has no CT-colonography alternative")
    report_text, conclusion_text, conclusion = _texts(procedure, completeness)
    when = performed_at.isoformat()
    tagged = {"tag": [DEFAULT_TAG]}
    is_ct = completeness == "ct_colonography"
    coded = procedure == DEFAULT_PROCEDURE  # a terminology code is asserted only where the repository cites one
    proc_res = {
        "resourceType": "Procedure",
        "id": procedure_ref(case_id, procedure).split("/", 1)[1],
        "meta": tagged,
        "status": _PROCEDURE_STATUS[completeness],
        "code": {"coding": [] if (is_ct or not coded) else [COLONOSCOPY_SNOMED],
                 "text": "CT colonography" if is_ct else procedure.capitalize()},
        "performedDateTime": when,
        "identifier": [{"system": CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}],
        "report": [{"reference": report_ref(case_id, procedure)}],
    }
    report = {
        "resourceType": "DiagnosticReport",
        "id": report_ref(case_id, procedure).split("/", 1)[1],
        "meta": tagged,
        "status": "final",
        "code": {"coding": [] if (is_ct or not coded) else [COLONOSCOPY_LOINC], "text": report_text},
        "effectiveDateTime": when,
        "conclusion": conclusion,
        "conclusionCode": [{"coding": [{"system": COMPLETENESS_SYSTEM, "code": completeness}], "text": conclusion_text}],
        "identifier": [{"system": CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}],
    }
    if not proc_res["code"]["coding"]:
        del proc_res["code"]["coding"]
    if not report["code"]["coding"]:
        del report["code"]["coding"]
    if patient is not None:
        proc_res["subject"] = patient
        report["subject"] = patient
    return proc_res, report


class Recorder(Protocol):
    def record(self, case_id: UUID, patient_ref: str | None, performed_at: datetime,
               completeness: str = "complete", procedure: str = DEFAULT_PROCEDURE) -> dict:
        """Create the resources and return {"procedure_ref", "report_ref", "completeness"}. Raises RecorderUnavailable."""
        ...


class LocalRecorder:
    """No HAPI: returns the references the resources would have, writes nothing."""

    def record(self, case_id, patient_ref, performed_at, completeness="complete", procedure=DEFAULT_PROCEDURE):
        return {"procedure_ref": procedure_ref(case_id, procedure), "report_ref": report_ref(case_id, procedure),
                "completeness": completeness, "written": False}


class StaticRecorder:
    """Records what it was asked (unit tests)."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[tuple[UUID, str | None, datetime]] = []
        self.completeness: list[str] = []
        self.fail = fail

    def record(self, case_id, patient_ref, performed_at, completeness="complete", procedure=DEFAULT_PROCEDURE):
        if self.fail:
            raise RecorderUnavailable(self.fail)
        self.calls.append((case_id, patient_ref, performed_at))
        self.completeness.append(completeness)
        return {"procedure_ref": procedure_ref(case_id, procedure), "report_ref": report_ref(case_id, procedure),
                "completeness": completeness, "written": True}


class FhirRecorder:
    """PUTs both resources in one transaction to HAPI."""

    def __init__(self, client: FhirClient) -> None:
        self.client = client

    def _patient(self, patient_ref: str | None) -> dict | None:
        """A real reference when the Patient exists in HAPI (referential integrity), else an
        identifier-only one, as the projection does."""
        if not patient_ref:
            return None
        try:
            # A reference that is not a clean `Patient/<id>` is never looked up (SEC-1): it would be put in a URL.
            if is_safe_ref(patient_ref) and patient_ref.startswith("Patient/") and self.client.exists(patient_ref):
                return {"reference": patient_ref}
        except FhirError:
            pass
        return {"identifier": {"system": SYSTEM_PATIENT_REF, "value": patient_ref}, "display": patient_ref}

    def record(self, case_id, patient_ref, performed_at, completeness="complete", procedure=DEFAULT_PROCEDURE):
        proc_resource, report = build_resources(case_id, self._patient(patient_ref), performed_at, completeness, procedure)
        bundle = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {"resource": r, "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}}
                for r in (proc_resource, report)
            ],
        }
        try:
            self.client.transaction(bundle)
        except FhirError as exc:
            raise RecorderUnavailable(str(exc)) from exc
        return {"procedure_ref": procedure_ref(case_id, procedure), "report_ref": report_ref(case_id, procedure),
                "completeness": completeness, "written": True}


# Bound by app.main at start-up; tests replace it.
recorder: Recorder = LocalRecorder()


def configure(app_settings) -> Recorder:
    global recorder
    if app_settings.fhir_verify_evidence or app_settings.fhir_projection:
        recorder = FhirRecorder(HttpFhirClient(app_settings.fhir_base_url, app_settings.fhir_timeout_seconds))
    else:
        recorder = LocalRecorder()
    return recorder
