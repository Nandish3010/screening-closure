"""Recording the confirmatory colonoscopy as FHIR evidence (E8).

`POST /cases/{id}/confirmatory-result` (the case's owner only) writes a synthetic colonoscopy
`Procedure` and its `DiagnosticReport` to HAPI and returns their references. The owner then
posts `complete_confirmatory {result_ref}` and, later, `close {evidence_ref}` with the returned
`Procedure/<id>`; `close` is checked against HAPI by `app.evidence` (A15), so the evidence the
clinician signs against is a resource that really exists.

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

from app.fhir_projection import DEFAULT_TAG, FhirClient, FhirError, HttpFhirClient

SYSTEM_PATIENT_REF = "urn:screening-closure:patient-ref"
COLONOSCOPY_SNOMED = {"system": "http://snomed.info/sct", "code": "73761001", "display": "Colonoscopy"}
COLONOSCOPY_LOINC = {"system": "http://loinc.org", "code": "18746-8", "display": "Colonoscopy study"}


class RecorderUnavailable(Exception):
    """HAPI could not be asked or refused the resources: the result is not recorded (503)."""


def procedure_ref(case_id: UUID) -> str:
    return f"Procedure/colonoscopy-{case_id}"


def report_ref(case_id: UUID) -> str:
    return f"DiagnosticReport/colonoscopy-report-{case_id}"


def build_resources(case_id: UUID, patient: dict | None, performed_at: datetime) -> tuple[dict, dict]:
    """The Procedure and the DiagnosticReport for the case's colonoscopy (pure)."""
    when = performed_at.isoformat()
    tagged = {"tag": [DEFAULT_TAG]}
    procedure = {
        "resourceType": "Procedure",
        "id": procedure_ref(case_id).split("/", 1)[1],
        "meta": tagged,
        "status": "completed",
        "code": {"coding": [COLONOSCOPY_SNOMED], "text": "Colonoscopy"},
        "performedDateTime": when,
        "identifier": [{"system": "urn:screening-closure:case", "value": str(case_id)}],
    }
    report = {
        "resourceType": "DiagnosticReport",
        "id": report_ref(case_id).split("/", 1)[1],
        "meta": tagged,
        "status": "final",
        "code": {"coding": [COLONOSCOPY_LOINC], "text": "Colonoscopy report"},
        "effectiveDateTime": when,
        "conclusion": "Colonoscopy completed. Synthetic demonstration record; no clinical finding is encoded.",
        "identifier": [{"system": "urn:screening-closure:case", "value": str(case_id)}],
    }
    if patient is not None:
        procedure["subject"] = patient
        report["subject"] = patient
    return procedure, report


class Recorder(Protocol):
    def record(self, case_id: UUID, patient_ref: str | None, performed_at: datetime) -> dict:
        """Create the resources and return {"procedure_ref", "report_ref"}. Raises RecorderUnavailable."""
        ...


class LocalRecorder:
    """No HAPI: returns the references the resources would have, writes nothing."""

    def record(self, case_id, patient_ref, performed_at):
        return {"procedure_ref": procedure_ref(case_id), "report_ref": report_ref(case_id), "written": False}


class StaticRecorder:
    """Records what it was asked (unit tests)."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[tuple[UUID, str | None, datetime]] = []
        self.fail = fail

    def record(self, case_id, patient_ref, performed_at):
        if self.fail:
            raise RecorderUnavailable(self.fail)
        self.calls.append((case_id, patient_ref, performed_at))
        return {"procedure_ref": procedure_ref(case_id), "report_ref": report_ref(case_id), "written": True}


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
            if self.client.exists(patient_ref):
                return {"reference": patient_ref}
        except FhirError:
            pass
        return {"identifier": {"system": SYSTEM_PATIENT_REF, "value": patient_ref}, "display": patient_ref}

    def record(self, case_id, patient_ref, performed_at):
        procedure, report = build_resources(case_id, self._patient(patient_ref), performed_at)
        bundle = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {"resource": r, "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}}
                for r in (procedure, report)
            ],
        }
        try:
            self.client.transaction(bundle)
        except FhirError as exc:
            raise RecorderUnavailable(str(exc)) from exc
        return {"procedure_ref": procedure_ref(case_id), "report_ref": report_ref(case_id), "written": True}


# Bound by app.main at start-up; tests replace it.
recorder: Recorder = LocalRecorder()


def configure(app_settings) -> Recorder:
    global recorder
    if app_settings.fhir_verify_evidence or app_settings.fhir_projection:
        recorder = FhirRecorder(HttpFhirClient(app_settings.fhir_base_url, app_settings.fhir_timeout_seconds))
    else:
        recorder = LocalRecorder()
    return recorder
