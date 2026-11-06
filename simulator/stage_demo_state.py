#!/usr/bin/env python3
"""Stage the HOSTED demo's opening state: a worklist that already shows the product working.

A judge who signs in to the hosted demo should not meet 26 untouched cases. This script, run by `deploy/gcp/vm/seed-on-vm.sh`
while the stack is in seed mode (APP_ENV=dev, loopback only, dev login), brings a handful of cases to different stages so that
the worklist, the Today screen and the dashboard funnel open in a lived-in state. Everything goes through the real API as the
demo cast (the same logins as tools/demo/scenario.py); it writes no database row and bypasses no guard. The simulation clock is
moved only with the supervisor's `POST /clock/advance` and the sweeps that follow are the scheduler container's own.

  API_URL         default http://localhost:8000
  FHIR_BASE_URL   not used here (the API writes the colonoscopy evidence to HAPI itself)

Two phases, because a case can only be overdue if the clock passes its due date, and the clock never moves back:

  --phase overdue   BEFORE `create_cases.py`: creates ONE case (a row of the cohort manifest, the id `create_cases.py` will give it, so
                    its later run says "exists"), then moves the clock past its follow-up date and waits for the real scheduler to
                    mark it overdue and escalate it up to the supervisor's rung, where it waits for a person to accept it. The
                    other cases are created afterwards, so their clocks start at the moved time and none of them is overdue. This
                    phase refuses to move the clock when other cases already exist (it would push them overdue).
  --phase rest      AFTER `create_chronic_cases.py`: a case with a PENDING AI DRAFT for the case owner to review (the recorded
                    Claude output, replay only, never sent), a case at the clinician's sign-off, and a case CLOSED with a documented
                    outcome, so the funnel's last step is not empty.
  --phase all       both (the first refuses on a database that already has cases, the second still runs).

What it never does (the product rules hold): it approves no draft, so no message goes to a patient (the hosted outreach policy stays
unsigned in any case); it signs nothing for the clinician except in the one closed case, where the specialist login that owns it
reviews and closes it with the Procedure in HAPI as evidence, through the same guard as anyone; it touches no consent.

Safe to rerun: case ids are the same uuid5 as `create_cases.py`, every event id is a uuid5 of the case and the step, and each step
is skipped when the case is already past it. It also checks (read only) that an urgent long-term-condition case is open and
untouched, which is what puts it near the top of Today, and says so if it is not.

Exit codes: 0 done or already done, 1 a step failed, 2 the stack is not reachable or the cohort manifest is missing.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))  # simulator/, for `import create_cases`

import create_cases  # noqa: E402
from insurance_mix import insurance_scheme_for  # noqa: E402

log = logging.getLogger("stage_demo_state")

DEFAULT_API = "http://localhost:8000"
# Rows of the cohort manifest (seed order). Rows 0 (the named patient of tools/demo/scenario.py) and 1 are left to the local demo.
ROW_OVERDUE, ROW_SIGNOFF, ROW_CLOSED, ROW_DRAFT = 4, 5, 6, 7
NAMESPACE = uuid.UUID("5b0c1a4e-7d3e-4a6f-9a51-2f0e8c7d9b10")  # for this script's event ids (never change: idempotency)

COORDINATOR = ("coordinator", "Maryam Al Suwaidi", None)
SUPERVISOR = ("supervisor", "Dr. Noura Al Hammadi", None)
SPECIALIST = ("specialist_owner", "Dr. Karim Nasser", "Corniche Gastro Unit")
SPECIALIST_PROVIDER = "Corniche Gastro Unit"
APPOINTMENT_IN_DAYS = 14

# State -> index of the next step on the way to the colonoscopy result and the closure.
STEPS = ["accept_ownership", "hand_off", "contact_patient", "patient_acknowledge", "book_confirmatory", "record_result",
         "clinician_review", "close"]
FIRST_STEP = {"acknowledged": 0, "owner_accepted": 1, "patient_contacted": 3, "patient_acknowledged": 4, "confirmatory_booked": 5,
              "confirmatory_completed": 6, "clinician_reviewed": 7, "closed": len(STEPS)}
TARGET_INDEX = {"confirmatory_completed": 6, "closed": len(STEPS)}  # steps to run: STEPS[FIRST:TARGET]


class StageError(RuntimeError):
    """A step failed or the case is somewhere the script does not expect. The message says which and what to do."""


def manifest_rows(path: Path = create_cases.MANIFEST) -> list[dict]:
    if not path.exists():
        raise StageError(f"cohort manifest {path} not found: run generate_results.py first")
    return json.loads(path.read_text())["results"]


def case_id_of(entry: dict) -> str:
    return create_cases.case_id_for(entry["report_id"])


def event_id(case_id: str, step: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{case_id}|{step}"))


def steps_to_run(state: str, target: str) -> list[str]:
    """The steps still to do from `state` to reach `target` (confirmatory_completed or closed); [] when there already or past it."""
    if target not in TARGET_INDEX:
        raise ValueError(target)
    if state not in FIRST_STEP:
        raise StageError(f"the case is in {state!r}, which is off the main path to {target!r}")
    return STEPS[FIRST_STEP[state]:TARGET_INDEX[target]]


def hours_to_pass(due: datetime, now: datetime) -> int:
    """Whole hours to advance so the clock is at least one hour past `due` (never 0)."""
    return max(1, math.ceil((due - now).total_seconds() / 3600) + 1)


def owner_name(case: dict) -> str | None:
    owner = case.get("owner")
    return owner.get("display_name") if isinstance(owner, dict) else owner


def escalation_waiting(case: dict) -> bool:
    return (case.get("escalation_level") or 0) > 0 and not (case.get("read_model") or {}).get("escalation_accepted")


class Stager:
    def __init__(self, client: httpx.Client, api: str, rows: list[dict], sleep=time.sleep, poll_seconds: float = 2.0) -> None:
        self.client, self.api, self.rows = client, api, rows
        self.sleep, self.poll = sleep, poll_seconds
        self.tokens: dict[tuple, dict] = {}
        self.providers = create_cases.registration_providers()
        self.summary: dict[str, str] = {}

    # ---- plumbing -----------------------------------------------------------------------------------------------------
    def login(self, who, patient_ref: str | None = None) -> dict:
        role, name, provider = who
        key = (role, name, patient_ref)
        if key not in self.tokens:
            body = {"role": role, "name": name, **({"provider": provider} if provider else {}), **({"patient_ref": patient_ref} if patient_ref else {})}
            r = self.client.post(f"{self.api}/auth/dev-login", json=body)
            r.raise_for_status()
            self.tokens[key] = r.json()
        return self.tokens[key]

    def call(self, who, method: str, path: str, body=None, ok=(200, 201), patient_ref: str | None = None):
        r = self.client.request(method, f"{self.api}{path}", json=body,
                                headers={"Authorization": f"Bearer {self.login(who, patient_ref)['token']}"})
        if r.status_code not in ok:
            raise StageError(f"{method} {path} as {who[0]} {who[1]!r}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def event(self, who, case_id: str, type_: str, payload: dict | None = None, patient_ref: str | None = None):
        return self.call(who, "POST", f"/cases/{case_id}/events",
                         {"type": type_, "payload": payload or {}, "event_id": event_id(case_id, type_)}, patient_ref=patient_ref)

    def case(self, case_id: str) -> dict:
        return self.call(COORDINATOR, "GET", f"/cases/{case_id}")

    def sim_now(self) -> datetime:
        return datetime.fromisoformat(self.client.get(f"{self.api}/health").json()["sim_time"])

    def wait_for(self, what: str, test, timeout: float, *, raise_on_timeout: bool = True):
        """Poll `test()` (truthy = done) every `poll` seconds; the scheduler container runs a sweep every few seconds on its own."""
        waited = 0.0
        while True:
            got = test()
            if got:
                return got
            if waited >= timeout:
                if raise_on_timeout:
                    raise StageError(f"timed out after {timeout:.0f} s waiting for {what}: is the scheduler container running?")
                return None
            self.sleep(self.poll)
            waited += self.poll

    # ---- cases --------------------------------------------------------------------------------------------------------
    def ensure_case(self, row: int) -> tuple[str, str, str]:
        """Create the case of manifest row `row` if it is not there (the same body and id as create_cases.py); (case_id, patient_ref, status)."""
        if row >= len(self.rows):
            raise StageError(f"the cohort manifest has {len(self.rows)} result(s): no row {row}")
        entry = self.rows[row]
        case_id, patient_ref = case_id_of(entry), f"Patient/{entry['patient_id']}"
        body = {"case_id": case_id, "patient_ref": patient_ref, "result_ref": f"DiagnosticReport/{entry['report_id']}",
                "insurance_scheme": insurance_scheme_for(entry["patient_id"])}
        registered = create_cases.registered_provider_for(entry["patient_id"], self.providers)
        if registered:
            body["registered_provider"] = registered
        r = self.client.post(f"{self.api}/cases", json=body, headers={"Authorization": f"Bearer {self.login(COORDINATOR)['token']}"})
        if r.status_code == 201:
            status = "created"
        elif r.status_code == 409 and "CaseAlreadyExists" in r.text:
            status = "exists"
        else:
            raise StageError(f"creating the case of row {row}: HTTP {r.status_code} {r.text[:300]}")
        self.wait_for("the automation to name the owner", lambda: owner_name(self.case(case_id)), 60)
        return case_id, patient_ref, status

    def other_cases(self, case_id: str) -> int:
        r = self.client.get(f"{self.api}/cases", params={"limit": 5},
                            headers={"Authorization": f"Bearer {self.login(COORDINATOR)['token']}"})
        r.raise_for_status()
        rows = r.json()
        rows = rows if isinstance(rows, list) else rows.get("cases", rows.get("items", []))
        return sum(1 for c in rows if c.get("case_id") != case_id)

    # ---- phase 1: the overdue case and its escalation ------------------------------------------------------------------
    def stage_overdue(self) -> str:
        case_id, _, _ = self.ensure_case(ROW_OVERDUE)
        case = self.case(case_id)
        if (case.get("escalation_level") or 0) >= 2 and escalation_waiting(case):
            return self._note("overdue", f"case {case_id[:8]} is already overdue with its escalation waiting for the supervisor")
        if self.other_cases(case_id):
            return self._note("overdue", "SKIPPED: other cases exist, and moving the clock would push them overdue too "
                                         "(run this phase before create_cases.py, on an empty database)")
        if not case.get("overdue"):
            hours = hours_to_pass(datetime.fromisoformat(case["due_at"]), self.sim_now())
            self.call(SUPERVISOR, "POST", "/clock/advance", {"hours": hours})
            self.wait_for("the scheduler to mark the case overdue", lambda: self.case(case_id).get("overdue"), 120)
        for _ in range(14):  # one day at a time; each step crosses at most one rung, which the sweep emits in order
            case = self.case(case_id)
            if (case.get("escalation_level") or 0) >= 2:
                break
            level = case.get("escalation_level") or 0
            self.call(SUPERVISOR, "POST", "/clock/advance", {"hours": 24})
            self.wait_for("the next escalation rung", lambda: (self.case(case_id).get("escalation_level") or 0) > level, 40, raise_on_timeout=False)
        case = self.case(case_id)
        if (case.get("escalation_level") or 0) < 2 or not escalation_waiting(case):
            raise StageError(f"case {case_id[:8]} did not reach the supervisor's rung (level {case.get('escalation_level')})")
        return self._note("overdue", f"case {case_id[:8]} is overdue, escalated to level {case['escalation_level']}, waiting for a person to accept it")

    # ---- phase 2: the draft, the sign-off, the closed case --------------------------------------------------------------
    def assigned_owner(self, case_id: str):
        case = self.case(case_id)
        owner = case.get("owner")
        name = owner_name(case)
        if not name:
            raise StageError(f"case {case_id[:8]} has no owner yet: is the stack running with AUTOMATION_ENABLED=true?")
        if name == SPECIALIST[1]:
            return SPECIALIST
        role = owner.get("role") if isinstance(owner, dict) else None
        return (role or "primary_care_owner", name, None)

    def walk(self, case_id: str, patient_ref: str, target: str) -> str:
        """Run the steps still to do to `target`; each is a real event by the person whose step it is. Returns the state reached."""
        for step in steps_to_run(self.case(case_id)["state"], target):
            self._step(step, case_id, patient_ref)
        return self.case(case_id)["state"]

    def _step(self, step: str, case_id: str, patient_ref: str) -> None:
        if step == "accept_ownership":
            self.event(self.assigned_owner(case_id), case_id, "accept_ownership")
        elif step == "hand_off":
            if owner_name(self.case(case_id)) != SPECIALIST[1]:
                self.event(self.assigned_owner(case_id), case_id, "handoff_requested", {"to_provider": SPECIALIST_PROVIDER})
                self.event(SPECIALIST, case_id, "handoff_accepted")
        elif step == "contact_patient":
            self.event(SPECIALIST, case_id, "contact_patient", {"channel": "phone"})
        elif step == "patient_acknowledge":
            self.event(("patient", "demo patient", None), case_id, "patient_acknowledge", {"lang": "ar"}, patient_ref=patient_ref)
        elif step == "book_confirmatory":
            when = (self.sim_now() + timedelta(days=APPOINTMENT_IN_DAYS)).date().isoformat()
            self.event(SPECIALIST, case_id, "book_confirmatory", {"appointment_ref": f"APPT-DEMO-{case_id[:8]}", "appointment_date": when})
        elif step == "record_result":
            out = self.call(SPECIALIST, "POST", f"/cases/{case_id}/confirmatory-result")
            self.event(SPECIALIST, case_id, "complete_confirmatory", {"result_ref": out["evidence_ref"]})
        elif step in ("clinician_review", "close"):
            signer = f"Practitioner/{self.login(SPECIALIST)['user_id']}"
            payload = {"signed_by": signer}
            if step == "close":
                payload["evidence_ref"] = f"Procedure/colonoscopy-{case_id}"
            self.event(SPECIALIST, case_id, step, payload)
        else:
            raise StageError(f"unknown step {step!r}")

    def stage_signoff(self) -> str:
        case_id, ref, _ = self.ensure_case(ROW_SIGNOFF)
        state = self.walk(case_id, ref, "confirmatory_completed")
        return self._note("signoff", f"case {case_id[:8]} is at {state!r}, waiting for the clinician's review and signature")

    def stage_closed(self) -> str:
        case_id, ref, _ = self.ensure_case(ROW_CLOSED)
        state = self.walk(case_id, ref, "closed")
        if state != "closed":
            raise StageError(f"case {case_id[:8]} ended in {state!r}, not 'closed'")
        return self._note("closed", f"case {case_id[:8]} is closed with the colonoscopy as documented evidence and the clinician's signature")

    def stage_draft(self) -> str:
        case_id, _, _ = self.ensure_case(ROW_DRAFT)
        if self.case(case_id)["state"] == "acknowledged":
            self.event(self.assigned_owner(case_id), case_id, "accept_ownership")  # the owner takes the case (a person's step)
        drafts = self.call(COORDINATOR, "GET", f"/cases/{case_id}/drafts")
        drafts = drafts if isinstance(drafts, list) else drafts.get("drafts", [])
        pending = [d for d in drafts if not d.get("approved_at") and not d.get("rejected_at")]
        if pending:
            return self._note("draft", f"case {case_id[:8]} already has a draft waiting for the owner to review")
        d = self.call(COORDINATOR, "POST", f"/cases/{case_id}/drafts", {"purpose": "result_notification", "channel": "sms", "language": "ar"})
        origin = "a recorded Claude output" if d.get("provider") == "anthropic" and not d.get("fallback_used") else f"provider {d.get('provider')!r}, fallback={d.get('fallback_used')}"
        return self._note("draft", f"case {case_id[:8]} has a draft ({origin}) waiting for the owner to approve or reject; nothing is sent")

    def check_urgent_chronic(self) -> str:
        r = self.client.get(f"{self.api}/cases", params={"limit": 100, "pathway": "chronic_conditions"},
                            headers={"Authorization": f"Bearer {self.login(COORDINATOR)['token']}"})
        r.raise_for_status()
        rows = r.json()
        rows = rows if isinstance(rows, list) else rows.get("cases", rows.get("items", []))
        urgent = [c for c in rows if ((c.get("priority") or {}).get("tier")) == "urgent" and c.get("state") != "closed"]
        return self._note("urgent", f"{len(urgent)} urgent long-term-condition case(s) open" if urgent else "WARNING: no open urgent long-term-condition case (run create_chronic_cases.py first)")

    def _note(self, key: str, text: str) -> str:
        self.summary[key] = text
        log.info("%s: %s", key, text)
        return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=("overdue", "rest", "all"), default="all")
    ap.add_argument("--manifest", type=Path, default=create_cases.MANIFEST)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api = os.environ.get("API_URL", DEFAULT_API).rstrip("/")
    try:
        rows = manifest_rows(args.manifest)
    except StageError as exc:
        log.error("%s", exc)
        return 2
    with httpx.Client(timeout=60.0) as client:
        try:
            client.get(f"{api}/health").raise_for_status()
        except httpx.HTTPError as exc:
            log.error("the API at %s is not reachable (%s); start the stack first", api, exc)
            return 2
        stager = Stager(client, api, rows)
        try:
            if args.phase in ("overdue", "all"):
                stager.stage_overdue()
            if args.phase in ("rest", "all"):
                stager.stage_draft()
                stager.stage_signoff()
                stager.stage_closed()
                stager.check_urgent_chronic()
        except (StageError, httpx.HTTPError, KeyError) as exc:
            log.error("FAILED: %s: %s", type(exc).__name__, exc)
            return 1
    print("SUMMARY " + json.dumps(stager.summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
