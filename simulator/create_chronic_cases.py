#!/usr/bin/env python3
"""Create the long-term-conditions demo cases (the third pathway, `config/pathways/chronic_conditions.yaml`) on the running stack.

Part of the demo seed and of the demo snapshot: `make demo-snapshot` runs it, `deploy/gcp/vm/seed-on-vm.sh` runs it before the
hosted demo's snapshot is taken, and `make demo-seed-chronic` (or `make demo-seed DEMO_THIRD_PATHWAY=1`) runs it by itself.
Rerunning is safe (deterministic ids, 409 = exists). The point is that a judge who opens the Today screen of the hosted demo
sees the cardiometabolic half of the challenge brief (blood pressure, HbA1c, BMI) and not only bowel screening.
Cases of this pathway never sit in `owner_accepted` or `confirmatory_completed`, the two states the video recorder looks for
(tools/video/record.ts `findCases`).

  API_URL         default http://localhost:8000
  FHIR_BASE_URL   default http://localhost:8080/fhir

What it does, all through the real API, as the demo logins:

  * writes `DEMO_PATIENTS` to HAPI if they are not there: four SYNTHETIC patients with Emirati-style names (like the staff of the
    demo cast), a gender and a birth date, tagged SYNTHETIC. They are not in the Synthea cohort and not in the colorectal
    manifest (the Synthea names are US-shaped). Fixed ids (`demo-chronic-1` ...), so a rerun writes nothing twice;
  * creates one case each with `pathway: chronic_conditions`, a deterministic id (uuid5, so a rerun is idempotent: 409 = exists)
    and a synthetic `results` list, from which the server derives the case's priority tier: two very high blood pressures
    (urgent, so both are on the coordinator's Today screen), a high HbA1c (soon) and a raised BMI (routine);
  * walks them to different stages so the worklist shows the pathway at work. The urgent ones stay at `acknowledged` (no person
    has acted yet: that is what puts an urgent case on Today); the others are at `patient_contacted` and
    `patient_acknowledged` (the last is ready for the mock Sahatna booking callback on the Integrations page).

The result values are SYNTHETIC: made-up numbers chosen only to fall in the placeholder rules of the pack, which are themselves
pending clinician sign-off (H1). They appear only in the POST body of the intake. The server keeps only the tier and the id of
the rule that decided it, never the values (ADR-051), and this script prints no value anywhere: its summary line gives counts
of states and of tiers. The web shows the tier, a plain-language reason and the placeholder flag.

No clinical content beyond that: the patients are synthetic, nothing is diagnosed, no impact number is produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import httpx

from insurance_mix import insurance_scheme_for

log = logging.getLogger("create_chronic_cases")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API = "http://localhost:8000"
DEFAULT_FHIR = "http://localhost:8080/fhir"
PATHWAY = "chronic_conditions"
# Fixed namespace for deterministic case ids (never change: it would break idempotency).
CASE_NAMESPACE = uuid.UUID("753efb6d-8d82-4d5b-92b8-3dde584651a1")
SYNTHETIC_TAG = {"system": "https://screening-closure.example/tags", "code": "SYNTHETIC", "display": "Synthetic demo patient, not a real person"}
PATIENT_ID_SYSTEM = "https://screening-closure.example/demo-patient"

# The demo patients: SYNTHETIC, with Emirati-style names like the demo's staff (Sara Al Mansoori, Maryam Al Suwaidi ...). Fixed ids.
# Each carries the result set and the stage of its case. The numbers inside a result set are invented, sit in the pack's PLACEHOLDER
# bands (H1) and mean nothing clinically; they are sent once, in the intake body (simulator/tests/test_create_chronic_cases.py runs
# app.priority.derive over the real pack and asserts each tier).
RESULT_SETS = {
    "very_high_blood_pressure": {"tier": "urgent", "results": [{"kind": "blood_pressure", "values": {"systolic_mmhg": 186, "diastolic_mmhg": 122}}]},
    "very_high_blood_pressure_b": {"tier": "urgent", "results": [{"kind": "blood_pressure", "values": {"systolic_mmhg": 182, "diastolic_mmhg": 108}}]},
    "high_hba1c": {"tier": "soon", "results": [{"kind": "hba1c", "values": {"mmol_per_mol": 80}}]},
    "raised_bmi": {"tier": "routine", "results": [{"kind": "bmi", "values": {"kg_per_m2": 33}}]},
}
DEMO_PATIENTS = (
    {"id": "demo-chronic-1", "given": "Fatima", "family": "Al Dhaheri", "gender": "female", "birth": "1971-03-14", "result_set": "very_high_blood_pressure", "stage": "acknowledged"},
    {"id": "demo-chronic-2", "given": "Khalid", "family": "Al Mazrouei", "gender": "male", "birth": "1966-09-02", "result_set": "very_high_blood_pressure_b", "stage": "acknowledged"},
    {"id": "demo-chronic-3", "given": "Mariam", "family": "Al Shamsi", "gender": "female", "birth": "1979-11-23", "result_set": "high_hba1c", "stage": "patient_contacted"},
    {"id": "demo-chronic-4", "given": "Hamdan", "family": "Al Ketbi", "gender": "male", "birth": "1983-05-08", "result_set": "raised_bmi", "stage": "patient_acknowledged"},
)

# The stages used. Never `owner_accepted` or `confirmatory_completed`: see the module docstring.
STAGES = tuple(dict.fromkeys(p["stage"] for p in DEMO_PATIENTS))
RECORDER_STATES = ("owner_accepted", "confirmatory_completed")  # what tools/video/record.ts findCases looks for
# The state a step is made from. A step whose state is not current is skipped: an earlier run did it, or the case moved on.
FROM_STATE = {"accept_ownership": "acknowledged", "contact_patient": "owner_accepted", "patient_acknowledge": "patient_contacted"}
# The events after the intake (the automation acknowledges and assigns on creation), per stage reached.
STEPS_TO = {
    "acknowledged": (),
    "patient_contacted": ("accept_ownership", "contact_patient"),
    "patient_acknowledged": ("accept_ownership", "contact_patient", "patient_acknowledge"),
}


def case_id_for(patient_id: str) -> str:
    return str(uuid.uuid5(CASE_NAMESPACE, f"{PATHWAY}/Patient/{patient_id}"))


def event_id_for(case_id: str, step: str) -> str:
    return str(uuid.uuid5(CASE_NAMESPACE, f"{case_id}|{step}"))


def registered_provider_for(patient_id: str, providers: list[str]) -> str | None:
    """The same arithmetic as simulator/create_cases.py and app.providers.provider_for_patient."""
    if not providers:
        return None
    return sorted(providers)[int(hashlib.sha256(patient_id.encode()).hexdigest(), 16) % len(providers)]


def fhir_patient(p: dict) -> dict:
    """The FHIR Patient written for a demo patient: a name, a gender, a birth date, a tag that says SYNTHETIC. No Emirates ID,
    no address, no phone: nothing that could belong to a real person."""
    return {"resourceType": "Patient", "id": p["id"], "meta": {"tag": [SYNTHETIC_TAG]},
            "identifier": [{"system": PATIENT_ID_SYSTEM, "value": p["id"]}],
            "name": [{"use": "official", "family": p["family"], "given": [p["given"]]}], "gender": p["gender"], "birthDate": p["birth"]}


def plan(patients: tuple[dict, ...] | list[dict] = DEMO_PATIENTS) -> list[dict]:
    """Deterministic plan: one case per demo patient with its target stage and its synthetic result set. `results` goes in the
    intake body only; `tier` is what the pack gives it."""
    out = []
    for p in patients:
        rs = RESULT_SETS[p["result_set"]]
        out.append({"patient_id": p["id"], "case_id": case_id_for(p["id"]), "stage": p["stage"], "steps": STEPS_TO[p["stage"]],
                    "result_set": p["result_set"], "tier": rs["tier"], "results": rs["results"]})
    return out


def redacted(items: list[dict]) -> list[dict]:
    """The plan without the result values: what `--show` prints."""
    return [{k: v for k, v in item.items() if k != "results"} for item in items]


def summary_line(created: int, exists: int, states: dict[str, int], tiers: dict[str, int]) -> str:
    """The one line printed at the end: counts of cases, states and tiers. No clinical value."""
    return (f"SUMMARY pathway={PATHWAY} cases_created={created} cases_already_created={exists} "
            f"states={json.dumps(states, sort_keys=True)} tiers={json.dumps(tiers, sort_keys=True)}")


class Seeder:
    def __init__(self, client: httpx.Client, api: str) -> None:
        self.client, self.api = client, api
        self.tokens: dict[tuple, str] = {}

    def login(self, role: str, name: str, patient_ref: str | None = None) -> dict:
        key = (role, name, patient_ref)
        if key not in self.tokens:
            body = {"role": role, "name": name, **({"patient_ref": patient_ref} if patient_ref else {})}
            r = self.client.post(f"{self.api}/auth/dev-login", json=body)
            r.raise_for_status()
            self.tokens[key] = r.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[key]}"}

    def create(self, item: dict, providers: list[str]) -> str:
        body = {"case_id": item["case_id"], "patient_ref": f"Patient/{item['patient_id']}", "pathway": PATHWAY,
                "results": item["results"]}
        registered = registered_provider_for(item["patient_id"], providers)
        if registered:
            body["registered_provider"] = registered
        body["insurance_scheme"] = insurance_scheme_for(item["patient_id"])  # ADR-056: a synthetic, deterministic mix for the demo
        r = self.client.post(f"{self.api}/cases", json=body, headers=self.login("coordinator", "simulator"))
        if r.status_code == 201:
            return "created"
        if r.status_code == 409 and "CaseAlreadyExists" in r.text:
            return "exists"
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")

    def case(self, case_id: str) -> dict:
        r = self.client.get(f"{self.api}/cases/{case_id}", headers=self.login("coordinator", "simulator"))
        r.raise_for_status()
        return r.json()

    def walk(self, item: dict) -> str:
        """Do the steps still to do for the case's stage; returns its state afterwards. Idempotent: ids are uuid5 of the step."""
        case_id, patient_ref = item["case_id"], f"Patient/{item['patient_id']}"
        for step in item["steps"]:
            current = self.case(case_id)
            if current["state"] != FROM_STATE[step]:
                continue
            owner = current.get("owner") or {}
            name = owner.get("display_name") if isinstance(owner, dict) else None
            if step == "patient_acknowledge":
                headers, payload = self.login("patient", "demo patient", patient_ref), {"lang": "ar"}
            elif name:
                headers, payload = self.login("primary_care_owner", name), ({"channel": "phone"} if step == "contact_patient" else {})
            else:
                raise RuntimeError(f"case {case_id} has no owner yet: is the stack running with AUTOMATION_ENABLED=true?")
            r = self.client.post(f"{self.api}/cases/{case_id}/events", headers=headers,
                                 json={"type": step, "payload": payload, "event_id": event_id_for(case_id, step)})
            if r.status_code != 201:
                raise RuntimeError(f"{step} on {case_id}: HTTP {r.status_code}: {r.text[:300]}")
        return self.case(case_id)["state"]


def ensure_patients(client: httpx.Client, fhir: str, patients: tuple[dict, ...] | list[dict]) -> int:
    """Write the demo patients that HAPI does not have yet (a GET, and a PUT only on 404); returns how many were written."""
    written = 0
    for p in patients:
        r = client.get(f"{fhir}/Patient/{p['id']}")
        if r.status_code == 200:
            continue
        if r.status_code != 404:
            r.raise_for_status()
        client.put(f"{fhir}/Patient/{p['id']}", json=fhir_patient(p), headers={"Content-Type": "application/fhir+json"}).raise_for_status()
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=len(DEMO_PATIENTS), help=f"how many demo cases, at most {len(DEMO_PATIENTS)} (default all)")
    ap.add_argument("--show", action="store_true", help="print the plan (without result values) and exit (no network call)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api = os.environ.get("API_URL", DEFAULT_API).rstrip("/")
    fhir = os.environ.get("FHIR_BASE_URL", DEFAULT_FHIR).rstrip("/")
    chosen = DEMO_PATIENTS[: max(0, min(args.cases, len(DEMO_PATIENTS)))]
    items = plan(chosen)
    if args.show:
        print(json.dumps(redacted(items), indent=2))
        return 0
    with httpx.Client(timeout=30.0) as client:
        try:
            written = ensure_patients(client, fhir, chosen)
        except httpx.HTTPError as exc:
            log.error("cannot write the demo patients to HAPI at %s (%s); is the stack up?", fhir, exc)
            return 2
        if written:
            log.info("wrote %d synthetic demo patient(s) to HAPI", written)
        try:
            import yaml

            providers = sorted(yaml.safe_load((REPO_ROOT / "config" / "providers.yaml").read_text())["registration"])
        except Exception:  # noqa: BLE001
            providers = []
        seeder = Seeder(client, api)
        created = exists = 0
        states: dict[str, int] = {}
        tiers: dict[str, int] = {}
        try:
            for item in items:
                status = seeder.create(item, providers)
                created += status == "created"
                exists += status == "exists"
                state = seeder.walk(item)
                states[state] = states.get(state, 0) + 1
                tiers[item["tier"]] = tiers.get(item["tier"], 0) + 1
        except (RuntimeError, httpx.HTTPError) as exc:
            log.error("%s", exc)
            return 1
    print(summary_line(created, exists, states, tiers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
