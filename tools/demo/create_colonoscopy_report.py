#!/usr/bin/env python3
"""Create the colonoscopy Procedure (and its DiagnosticReport) for a case directly in HAPI.

The API route `POST /cases/{id}/confirmatory-result` does the same thing as the case owner; this helper is
for scripts and for repairing a demo by hand, without a login:

    PYTHONPATH=api python3 tools/demo/create_colonoscopy_report.py --case-id <uuid> --patient-ref Patient/<id>

It shares `app.confirmatory` with the API, so the ids are the same (`Procedure/colonoscopy-<case_id>`,
`DiagnosticReport/colonoscopy-report-<case_id>`) and running it twice is harmless (PUT). It prints the
Procedure reference, which is what `close` takes as `evidence_ref`.

  FHIR_BASE_URL  default http://localhost:8080/fhir (the compose port on the host)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from app.confirmatory import FhirRecorder, RecorderUnavailable  # noqa: E402
from app.fhir_projection import HttpFhirClient  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case-id", required=True, type=UUID)
    ap.add_argument("--patient-ref", help="Patient/<id> the colonoscopy belongs to")
    ap.add_argument("--at", help="ISO-8601 time the colonoscopy was performed (default: now, UTC)")
    ap.add_argument("--fhir", default=os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir"))
    args = ap.parse_args(argv)
    performed = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else datetime.now(UTC)  # wall-clock-ok: CLI default
    try:
        refs = FhirRecorder(HttpFhirClient(args.fhir)).record(args.case_id, args.patient_ref, performed)
    except RecorderUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(refs["procedure_ref"])
    print(refs["report_ref"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
