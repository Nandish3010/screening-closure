#!/usr/bin/env python3
"""Create one case per positive FIT report listed in data/results/manifest.json.

Pipeline step 3 of 3. The case id is uuid5(report id), so reruns are idempotent: the API
answers 409 CaseAlreadyExists for a case that is already there, which is reported as
"already created" rather than as an error.

  API_URL   default http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

log = logging.getLogger("create_cases")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API = "http://localhost:8000"
MANIFEST = REPO_ROOT / "data" / "results" / "manifest.json"
# Fixed namespace for deterministic case ids (never change: it would break idempotency).
CASE_NAMESPACE = uuid.UUID("0b8e6d5a-3c1f-4a52-8a77-9d4e2f6b1c33")


def case_id_for(report_id: str) -> str:
    return str(uuid.uuid5(CASE_NAMESPACE, f"DiagnosticReport/{report_id}"))


def create_case(client: httpx.Client, api: str, entry: dict) -> str:
    """Returns 'created' or 'exists'. Retries connection errors and 5xx with backoff."""
    body = {
        "case_id": case_id_for(entry["report_id"]),
        "patient_ref": f"Patient/{entry['patient_id']}",
        "result_ref": f"DiagnosticReport/{entry['report_id']}",
    }
    delay = 1.0
    for attempt in range(1, 6):
        try:
            resp = client.post(f"{api}/cases", json=body)
        except httpx.TransportError as exc:
            err = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 201:
                return "created"
            if resp.status_code == 409 and "CaseAlreadyExists" in resp.text:
                return "exists"
            if resp.status_code < 500:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if attempt == 5:
            raise RuntimeError(f"gave up after 5 attempts: {err}")
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api = os.environ.get("API_URL", DEFAULT_API).rstrip("/")
    if not args.manifest.exists():
        log.error("manifest %s not found (run generate_results.py first)", args.manifest)
        return 2
    results = json.loads(args.manifest.read_text())["results"]

    created = exists = failed = 0
    with httpx.Client(timeout=30.0) as client:
        for entry in results:
            try:
                if create_case(client, api, entry) == "created":
                    created += 1
                else:
                    exists += 1
            except RuntimeError as exc:
                failed += 1
                log.error("FAILED report %s: %s", entry["report_id"], exc)
    print(f"SUMMARY results={len(results)} cases_created={created} cases_already_created={exists} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
