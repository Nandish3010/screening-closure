#!/usr/bin/env python3
"""Create the counselling DocumentReference of an informed refusal for a case directly in HAPI (ADR-059).

`record_informed_refusal` and the declined `close` resolve the refusal's `counselling_ref` in FHIR, the way a colonoscopy's
evidence is resolved (`FHIR_VERIFY_EVIDENCE`): a made-up reference is refused. The record is made by the clinical record in a
real deployment; for the demo stack this helper writes a synthetic one the checks accept:

    PYTHONPATH=api python3 tools/demo/create_counselling_record.py --case-id <uuid> --patient-ref Patient/<id> [--author <user id>]

The id is deterministic (`DocumentReference/counselling-<case_id>`) and the write is a PUT, so running it twice is harmless. It
carries the case identifier and the patient, which is what ties it to THIS case (a record of another case does not resolve).
It prints the reference: that is the `counselling_ref` the refusal form takes.

  FHIR_BASE_URL  default http://localhost:8080/fhir (the compose port on the host)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from app.evidence import CASE_IDENTIFIER_SYSTEM  # noqa: E402
from app.fhir_projection import DEFAULT_TAG, FhirError, HttpFhirClient  # noqa: E402

_USER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def counselling_ref(case_id: UUID) -> str:
    return f"DocumentReference/counselling-{case_id}"


def build_resource(case_id: UUID, patient_ref: str | None, author: str | None, at: datetime) -> dict:
    """The synthetic DocumentReference (pure). No clinical content: a status, a type and who it is about."""
    resource = {
        "resourceType": "DocumentReference",
        "id": counselling_ref(case_id).split("/", 1)[1],
        "meta": {"tag": [DEFAULT_TAG]},
        "status": "current",
        "type": {"text": "Informed refusal counselling note"},
        "date": at.isoformat(),
        "description": "Synthetic demonstration record; no clinical content.",
        "identifier": [{"system": CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}],
    }
    if patient_ref:
        resource["subject"] = {"reference": patient_ref}
    if author:
        resource["author"] = [{"reference": f"Practitioner/{author}"}]
    return resource


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case-id", required=True, type=UUID)
    ap.add_argument("--patient-ref", help="Patient/<id> the note is about (it must equal the case's patient)")
    ap.add_argument("--author", help="the counselling physician's user id (recorded as Practitioner/<id>)")
    ap.add_argument("--fhir", default=os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir"))
    args = ap.parse_args(argv)
    if args.author and not _USER_ID.match(args.author):
        print("error: --author is a user id (letters, digits, - and _)", file=sys.stderr)
        return 2
    now = datetime.now(UTC)  # wall-clock-ok: CLI default
    resource = build_resource(args.case_id, args.patient_ref, args.author, now)
    bundle = {"resourceType": "Bundle", "type": "transaction",
              "entry": [{"resource": resource, "request": {"method": "PUT", "url": f"DocumentReference/{resource['id']}"}}]}
    try:
        HttpFhirClient(args.fhir).transaction(bundle)
    except FhirError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(counselling_ref(args.case_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
