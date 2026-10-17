#!/usr/bin/env python3
"""Generate synthetic positive FIT results for the patients loaded in HAPI.

Pipeline step 2 of 3 (load_cohort -> generate_results -> create_cases).

For every patient aged 40-75 on SIM_CLOCK_START, a seeded draw decides whether their FIT is
positive (rate from simulator/params.yaml: fit_positivity_rate). For each POSITIVE it writes an
Observation and a DiagnosticReport to HAPI and records them in data/results/manifest.json.

Everything is a pure function of (seed, patient identity, params): the same seed over the same
cohort always selects the same patients with the same dates, and resource ids are deterministic
(uuid5), so a rerun updates nothing and duplicates nothing.

  FHIR_BASE_URL     default http://localhost:8080/fhir
  SIM_CLOCK_START   default: value in .env, else 2026-01-05T08:00:00Z
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("generate_results")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FHIR = "http://localhost:8080/fhir"
DEFAULT_SEED = 20261017
DEFAULT_CLOCK = "2026-01-05T08:00:00Z"
DEFAULT_RATE = 0.07
# Abu Dhabi policy: FIT screening for adults aged 40-75 (docs/evidence.md section 8: IFHAS V10, ADPHC 2019).
AGE_MIN, AGE_MAX = 40, 75
WINDOW_DAYS = 14
MANIFEST = REPO_ROOT / "data" / "results" / "manifest.json"
PARAMS = Path(__file__).resolve().parent / "params.yaml"

# LOINC code for the FIT (faecal immunochemical test) result. Display string verified against the
# HL7 terminology server (tx.fhir.org, LOINC 2.82): the current display is the one below; older
# LOINC releases called it "Hemoglobin.gastrointestinal [Presence] in Stool by Immunoassay".
FIT_LOINC = {
    "system": "http://loinc.org",
    "code": "29771-3",
    "display": "Hemoglobin [Presence] in Stool from gastrointestinal lower by Immunoassay",
}
SYNTHEA_ID_SYSTEM = "https://github.com/synthetichealth/synthea"
# Fixed namespace for deterministic resource ids (never change: it would orphan existing data).
ID_NAMESPACE = uuid.UUID("5c0e7a4e-0b1f-4d8e-9f53-2f6d1a9b7c10")


# ---------------------------------------------------------------- pure selection logic

@dataclass(frozen=True)
class Positive:
    patient_id: str
    patient_key: str
    effective: datetime
    observation_id: str
    report_id: str


def parse_clock(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def age_on(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def patient_key(patient: dict) -> str:
    """Stable identity for seeding: the Synthea UUID if present (survives HAPI id reassignment)."""
    for ident in patient.get("identifier", []):
        if ident.get("system") == SYNTHEA_ID_SYSTEM and ident.get("value"):
            return ident["value"]
    return str(patient["id"])


def is_eligible(patient: dict, clock: datetime) -> bool:
    """Aged 40-75 (inclusive, completed years) on the clock date, and not deceased by then."""
    birth = patient.get("birthDate")
    if not birth:
        return False
    on = clock.date()
    try:
        birth_date = date.fromisoformat(birth[:10])
    except ValueError:
        return False
    if not AGE_MIN <= age_on(birth_date, on) <= AGE_MAX:
        return False
    if patient.get("deceasedBoolean") is True:
        return False
    deceased = patient.get("deceasedDateTime")
    if deceased and parse_clock(deceased) <= clock:
        return False
    return True


def eligible_patients(patients: list[dict], clock: datetime) -> list[dict]:
    return [p for p in patients if is_eligible(p, clock)]


def select_positives(
    patients: list[dict], seed: int, rate: float, clock: datetime, cap: int | None = None
) -> list[Positive]:
    """Seeded FIT outcome per eligible patient; returns the positives, ordered by patient key.

    Each patient has its own RNG stream (seed + stable key), so the outcome does not depend on
    list order or on who else is in the cohort. `cap` keeps the first N positives by key.
    """
    out: list[Positive] = []
    for p in sorted(eligible_patients(patients, clock), key=patient_key):
        key = patient_key(p)
        rng = random.Random(f"{seed}|{key}")
        if rng.random() >= rate:
            continue
        offset = timedelta(seconds=rng.randrange(WINDOW_DAYS * 86400))
        effective = (clock - offset).replace(microsecond=0)
        out.append(Positive(
            patient_id=str(p["id"]),
            patient_key=key,
            effective=effective,
            observation_id=str(uuid.uuid5(ID_NAMESPACE, f"{seed}|{key}|Observation")),
            report_id=str(uuid.uuid5(ID_NAMESPACE, f"{seed}|{key}|DiagnosticReport")),
        ))
    return out[:cap] if cap is not None else out


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_resources(pos: Positive) -> tuple[dict, dict]:
    subject = {"reference": f"Patient/{pos.patient_id}"}
    when = _iso(pos.effective)
    obs = {
        "resourceType": "Observation",
        "id": pos.observation_id,
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory"}]}],
        "code": {"coding": [FIT_LOINC], "text": "Faecal immunochemical test (FIT)"},
        "subject": subject,
        "effectiveDateTime": when,
        # Plain text concept on purpose: avoids SNOMED CT licensing for a coded "Positive".
        "valueCodeableConcept": {"text": "Positive"},
    }
    report = {
        "resourceType": "DiagnosticReport",
        "id": pos.report_id,
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "LAB"}]}],
        "code": {"coding": [FIT_LOINC], "text": "Faecal immunochemical test (FIT)"},
        "subject": subject,
        "effectiveDateTime": when,
        "issued": when,
        "result": [{"reference": f"Observation/{pos.observation_id}"}],
        "conclusion": "Positive FIT. Confirmatory colonoscopy recommended.",
    }
    return obs, report


# ---------------------------------------------------------------- config helpers

def load_positivity_rate(path: Path = PARAMS) -> float:
    text = path.read_text()
    try:
        import yaml  # present in the project venv; fall back to a regex if run with bare python3

        return float(yaml.safe_load(text)["fit_positivity_rate"]["value"])
    except ImportError:
        m = re.search(r"^fit_positivity_rate:\s*\n\s+value:\s*([0-9.]+)", text, re.M)
        return float(m.group(1)) if m else DEFAULT_RATE


def sim_clock_start() -> str:
    if os.environ.get("SIM_CLOCK_START"):
        return os.environ["SIM_CLOCK_START"]
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        m = re.search(r"^SIM_CLOCK_START=(\S+)", env_file.read_text(), re.M)
        if m:
            return m.group(1)
    return DEFAULT_CLOCK


# ---------------------------------------------------------------- HAPI I/O

def fetch_patients(client: httpx.Client, base: str) -> list[dict]:
    """GET Patient?_count=200 and follow `next` links (re-rooted onto `base`)."""
    patients: list[dict] = []
    url: str | None = f"{base}/Patient?_count=200"
    origin = urlsplit(base)
    while url:
        resp = client.get(url, headers={"Accept": "application/fhir+json"})
        resp.raise_for_status()
        bundle = resp.json()
        patients += [e["resource"] for e in bundle.get("entry", []) if e["resource"]["resourceType"] == "Patient"]
        nxt = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
        if nxt:
            parts = urlsplit(nxt)
            nxt = parts._replace(scheme=origin.scheme, netloc=origin.netloc).geturl()
        url = nxt
    return patients


def put_resource(client: httpx.Client, base: str, resource: dict) -> str:
    """Create-or-skip by deterministic id. Returns 'created' or 'exists'."""
    url = f"{base}/{resource['resourceType']}/{resource['id']}"
    if client.get(url).status_code == 200:
        return "exists"
    resp = client.put(url, json=resource, headers={"Content-Type": "application/fhir+json"})
    resp.raise_for_status()
    return "created"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--cap", type=int, default=None, help="limit the number of positives (default: no cap)")
    ap.add_argument("--rate", type=float, default=None, help="override fit_positivity_rate from params.yaml")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = os.environ.get("FHIR_BASE_URL", DEFAULT_FHIR).rstrip("/")
    clock = parse_clock(sim_clock_start())
    rate = args.rate if args.rate is not None else load_positivity_rate()

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        patients = fetch_patients(client, base)
        eligible = eligible_patients(patients, clock)
        positives = select_positives(patients, args.seed, rate, clock, args.cap)
        log.info("patients=%d eligible(40-75 at %s)=%d positives=%d (rate=%s seed=%d cap=%s)",
                 len(patients), clock.date(), len(eligible), len(positives), rate, args.seed, args.cap)

        created = existing = 0
        entries = []
        for pos in positives:
            obs, report = build_resources(pos)
            states = [put_resource(client, base, obs), put_resource(client, base, report)]
            created += states.count("created")
            existing += states.count("exists")
            entries.append({
                "patient_id": pos.patient_id,
                "report_id": pos.report_id,
                "observation_id": pos.observation_id,
                "effective_date": _iso(pos.effective),
                "seed": args.seed,
            })

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({
        "seed": args.seed,
        "sim_clock_start": _iso(clock),
        "fit_positivity_rate": rate,
        "patients_in_hapi": len(patients),
        "eligible_40_75": len(eligible),
        "results": entries,
    }, indent=2) + "\n")
    print(f"SUMMARY patients={len(patients)} eligible={len(eligible)} positives={len(positives)} "
          f"resources_created={created} resources_already_present={existing} manifest={args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
