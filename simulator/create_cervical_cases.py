#!/usr/bin/env python3
"""Create a few cervical-screening demo cases (the second pathway, `config/pathways/cervical_hpv.yaml`) on the running stack.

An OPT-IN extra step of the demo seed: `make demo-seed-cervical`, or `make demo-seed DEMO_SECOND_PATHWAY=1`. It is not part of
the default seed, the demo snapshot or the video: the storyboard's captions say "all 22 open cases", the recorder looks for
the first case at `owner_accepted` and at `confirmatory_completed`, and the agency reads every loaded pathway's cases, so
cases of a second pathway on the snapshotted stack would change what the video shows. (`make demo-reset` restores the
snapshot and removes them.)

  API_URL         default http://localhost:8000
  FHIR_BASE_URL   default http://localhost:8080/fhir   (female Synthea patients, to give the cases a real Patient)

What it does, all through the real API, as the demo logins:

  * picks `--cases` (default 4) female patients aged 25 to 65 from HAPI, NOT in the colorectal manifest, in id order;
  * creates one case each with `pathway: cervical_hpv` and a deterministic id (uuid5), so a rerun is idempotent (409 = exists);
  * walks them to different stages so the worklist shows the pathway at work. The stages are chosen so that none of them is
    `owner_accepted` or `confirmatory_completed` (the two states the video recorder looks for, tools/video/record.ts
    `findCases`): acknowledged, patient_contacted, patient_acknowledged, patient_contacted.

No clinical content: the patients are synthetic, nothing is diagnosed, no result is encoded, no impact number is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger("create_cervical_cases")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API = "http://localhost:8000"
DEFAULT_FHIR = "http://localhost:8080/fhir"
MANIFEST = REPO_ROOT / "data" / "results" / "manifest.json"
PATHWAY = "cervical_hpv"
# Fixed namespace for deterministic case ids (never change: it would break idempotency).
CASE_NAMESPACE = uuid.UUID("5d7b9c2e-61a4-4f0b-9b3a-2c8e1f4a7d90")
MIN_AGE, MAX_AGE = 25, 65  # synthetic cohort range for the demo; NOT a screening-eligibility statement (H1)

# The stage each case is walked to, in order. Never `owner_accepted` or `confirmatory_completed`: see the module docstring.
STAGES = ("acknowledged", "patient_contacted", "patient_acknowledged", "patient_contacted")
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


def age_on(birth: str, today: date) -> int | None:
    try:
        y, m, d = (int(x) for x in birth[:10].split("-"))
        born = date(y, m, d)
    except (ValueError, TypeError):
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def pick_patients(bundle_entries: list[dict], exclude: set[str], count: int, today: date) -> list[str]:
    """Patient ids of female patients aged MIN_AGE..MAX_AGE who are not in `exclude`, in id order, at most `count`."""
    ids = []
    for entry in bundle_entries:
        res = entry.get("resource", entry)
        pid = str(res.get("id", ""))
        age = age_on(res.get("birthDate", ""), today)
        if pid and pid not in exclude and res.get("gender") == "female" and age is not None and MIN_AGE <= age <= MAX_AGE:
            ids.append(pid)
    return sorted(set(ids), key=lambda s: (len(s), s))[:count]


def manifest_patients(path: Path = MANIFEST) -> set[str]:
    try:
        return {str(r["patient_id"]) for r in json.loads(path.read_text())["results"]}
    except (OSError, ValueError, KeyError):
        return set()


def registered_provider_for(patient_id: str, providers: list[str]) -> str | None:
    """The same arithmetic as simulator/create_cases.py and app.providers.provider_for_patient."""
    if not providers:
        return None
    return sorted(providers)[int(hashlib.sha256(patient_id.encode()).hexdigest(), 16) % len(providers)]


def plan(patient_ids: list[str]) -> list[dict]:
    """Deterministic plan: one case per patient with its target stage (cycling through STAGES)."""
    out = []
    for i, pid in enumerate(patient_ids):
        stage = STAGES[i % len(STAGES)]
        out.append({"patient_id": pid, "case_id": case_id_for(pid), "stage": stage, "steps": STEPS_TO[stage]})
    return out


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
        body = {"case_id": item["case_id"], "patient_ref": f"Patient/{item['patient_id']}", "pathway": PATHWAY}
        registered = registered_provider_for(item["patient_id"], providers)
        if registered:
            body["registered_provider"] = registered
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=4, help="how many cervical demo cases (default 4)")
    ap.add_argument("--show", action="store_true", help="print the plan and exit (no API call except the HAPI patient search)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api = os.environ.get("API_URL", DEFAULT_API).rstrip("/")
    fhir = os.environ.get("FHIR_BASE_URL", DEFAULT_FHIR).rstrip("/")
    with httpx.Client(timeout=30.0) as client:
        try:
            r = client.get(f"{fhir}/Patient", params={"gender": "female", "_count": 200})
            r.raise_for_status()
            entries = r.json().get("entry", [])
        except (httpx.HTTPError, ValueError) as exc:
            log.error("cannot read patients from HAPI at %s (%s); run `make demo-seed` first", fhir, exc)
            return 2
        chosen = pick_patients(entries, manifest_patients(), args.cases, date.today())
        if len(chosen) < args.cases:
            log.warning("only %d suitable female patients found for %d cases", len(chosen), args.cases)
        items = plan(chosen)
        if args.show:
            print(json.dumps(items, indent=2))
            return 0
        try:
            import yaml

            providers = sorted(yaml.safe_load((REPO_ROOT / "config" / "providers.yaml").read_text())["registration"])
        except Exception:  # noqa: BLE001
            providers = []
        seeder = Seeder(client, api)
        created = exists = 0
        states: dict[str, int] = {}
        try:
            for item in items:
                status = seeder.create(item, providers)
                created += status == "created"
                exists += status == "exists"
                state = seeder.walk(item)
                states[state] = states.get(state, 0) + 1
        except (RuntimeError, httpx.HTTPError) as exc:
            log.error("%s", exc)
            return 1
    print(f"SUMMARY pathway={PATHWAY} cases_created={created} cases_already_created={exists} states={json.dumps(states, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
