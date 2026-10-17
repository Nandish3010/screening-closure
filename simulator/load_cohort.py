#!/usr/bin/env python3
"""Load the Synthea FHIR output into HAPI as transaction bundles.

Order: hospitalInformation*.json, practitionerInformation*.json, then every patient bundle
(patient bundles reference practitioners/organisations by conditional reference, so those must
exist first). Only httpx is needed beyond the standard library.

Synthea bundles use urn:uuid references, so loading twice would create duplicate patients.
The loader therefore refuses to run when HAPI already holds Patients unless --force is given.

  FHIR_BASE_URL   default http://localhost:8080/fhir
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger("load_cohort")

DEFAULT_FHIR = "http://localhost:8080/fhir"
DEFAULT_DIR = "data/synthea/out/fhir"
PROGRESS_EVERY = 25
MAX_ATTEMPTS = 5


def patient_count(client: httpx.Client, base: str) -> int:
    resp = client.get(f"{base}/Patient", params={"_summary": "count"})
    resp.raise_for_status()
    return int(resp.json().get("total", 0))


def ordered_bundles(directory: Path) -> tuple[list[Path], list[Path]]:
    """Return (infrastructure bundles, patient bundles); infrastructure goes first."""
    files = sorted(directory.glob("*.json"))
    infra = [f for f in files if f.name.startswith(("hospitalInformation", "practitionerInformation"))]
    # hospitals before practitioners (practitioner roles reference organisations)
    infra.sort(key=lambda f: (not f.name.startswith("hospitalInformation"), f.name))
    patients = [f for f in files if f not in infra]
    return infra, patients


def post_bundle(client: httpx.Client, base: str, path: Path) -> None:
    """POST one transaction bundle; retry with backoff on 5xx and connection errors."""
    body = path.read_bytes()
    delay = 1.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.post(base, content=body, headers={"Content-Type": "application/fhir+json"})
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            err = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code < 300:
                return
            if resp.status_code < 500:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(f"gave up after {MAX_ATTEMPTS} attempts: {err}")
        log.warning("%s attempt %d failed (%s); retrying in %.0fs", path.name, attempt, err, delay)
        time.sleep(delay)
        delay *= 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"Synthea FHIR output directory (default {DEFAULT_DIR})")
    ap.add_argument("--force", action="store_true", help="load even if HAPI already has Patients (creates duplicates)")
    ap.add_argument("--skip-if-loaded", action="store_true",
                    help="exit 0 without loading when HAPI already has Patients (used by `make demo-seed`)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = os.environ.get("FHIR_BASE_URL", DEFAULT_FHIR).rstrip("/")
    directory = Path(args.dir)
    if not directory.is_dir() or not any(directory.glob("*.json")):
        log.error("no FHIR bundles in %s (run `make synthea` first)", directory)
        return 2

    infra, patients = ordered_bundles(directory)
    total = len(infra) + len(patients)
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        try:
            existing = patient_count(client, base)
        except httpx.HTTPError as exc:
            log.error("cannot reach HAPI at %s: %s", base, exc)
            return 2
        if existing > 0 and args.skip_if_loaded and not args.force:
            print(f"SUMMARY bundles_loaded=0 bundles_failed=0 skipped=already_loaded patients_in_hapi={existing} elapsed_s=0.0")
            return 0
        if existing > 0 and not args.force:
            log.error("HAPI already has %d Patient resources; refusing to load again "
                      "(Synthea bundles would create duplicates). Use --force to override, "
                      "or `make reset` for a clean slate.", existing)
            return 3

        log.info("loading %d bundles (%d infrastructure + %d patients) into %s", total, len(infra), len(patients), base)
        started = time.monotonic()
        loaded = failed = 0
        for i, path in enumerate(infra + patients, 1):
            try:
                post_bundle(client, base, path)
                loaded += 1
            except RuntimeError as exc:
                failed += 1
                log.error("FAILED %s: %s", path.name, exc)
            if i % PROGRESS_EVERY == 0 or i == total:
                log.info("progress: %d/%d bundles (%d failed)", i, total, failed)
        elapsed = time.monotonic() - started
        try:
            final = patient_count(client, base)
        except httpx.HTTPError:
            final = -1

    print(f"SUMMARY bundles_loaded={loaded} bundles_failed={failed} patients_in_hapi={final} elapsed_s={elapsed:.1f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
