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
                    It also stages the TOUR POOL (see TOUR_POOL): a few more cases brought to the states the guided tour's steps need
                    (a patient asked for a call-back: the coordinator's own work; reached by phone, for "remind" and "refer"; the
                    colonoscopy recorded, for "outcome"), so many judges can walk the tour in one day without the pool running out.
                    Last, ONE agency run (`POST /agency/run` as the supervisor, the same call as `make agency-run`): the agents'
                    tasks on the cases as they stand, so the Agency page shows real work from the first visit. The stack's scheduler
                    never starts a run by itself (AGENCY_EVERY_SWEEPS=0, so the snapshot stays deterministic).
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
ROW_OVERDUE, ROW_CLOSED, ROW_DRAFT = 4, 6, 7
# The sign-off case is chosen, not fixed: the first row from SIGNOFF_FROM_ROW whose registered provider is the PHYSICIAN's, so the
# hosted "Physician" one-click login (Dr. Huda Rahman, Marina Digestive Health) OWNS it and its sign-off panel is hers.
SIGNOFF_FROM_ROW = 8
EVIDENCE_WAIT_SECONDS = 30  # the sign-off step waits this long for the evidence scan (it was 120 while a cached "no" could hide the case)
PHYSICIAN = ("primary_care_owner", "Dr. Huda Rahman", "Marina Digestive Health")
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
TARGET_INDEX = {"patient_contacted": 3, "confirmatory_completed": 6, "closed": len(STEPS)}  # steps to run: STEPS[FIRST:TARGET]

# The tour pool (the guided tour, `GET /demo/guide`, hands each judge session an untouched case per step; a case a judge acts on is
# used up until the daily reset). The cohort is small (22 positive results), so the pool is a share of it, not 10 per step:
# these cases are staged in the states the steps ask for, AND the API gives any case that still fits a step once the untouched
# ones are gone (api/app/demo_controls.py, `fresh: false`). Counts are the most each state gets; MIN_UNTOUCHED rows are always
# left alone for "record" and "prioritise", so on a small cohort the pool shrinks first.
TOUR_POOL = {"callback": 2, "contacted": 4, "completed": 3}
MIN_UNTOUCHED = 6


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
    def __init__(self, client: httpx.Client, api: str, rows: list[dict], sleep=time.sleep, poll_seconds: float = 2.0,
                 scan_token: str | None = None) -> None:
        self.client, self.api, self.rows = client, api, rows
        self.scan_token = scan_token  # the automation service token, when the stager is given it (see trigger_evidence_scan)
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

    def trigger_evidence_scan(self) -> bool:
        """Ask the API to run the evidence scan now (`POST /scheduler/evidence-scan`, open to the automation service token), so the
        sign-off step does not depend on the scheduler's cadence. Idempotent (deterministic event ids). Needs the token (env
        AUTOMATION_SERVICE_TOKEN); without it, or if the API refuses (404/403/409 fence), the scheduler's own loop does the scan
        and the wait below simply polls. Never raises: it is a nudge, the wait is the check."""
        if not self.scan_token:
            return False
        try:
            r = self.client.post(f"{self.api}/scheduler/evidence-scan", headers={"Authorization": f"Bearer {self.scan_token}"})
        except httpx.HTTPError as exc:
            log.warning("evidence scan not triggered (%s); waiting for the scheduler's own", type(exc).__name__)
            return False
        if r.status_code != 200:
            log.warning("evidence scan not triggered: HTTP %s; waiting for the scheduler's own", r.status_code)
        return r.status_code == 200

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

    def walk(self, case_id: str, patient_ref: str, target: str, via_specialist: bool = True) -> str:
        """Run the steps still to do to `target`; each is a real event by the person whose step it is. Returns the state reached.
        `via_specialist`: the owner hands the case to the specialist, who does the rest (the closed case). Without it the case's own
        owner does every step it may (the sign-off case: the physician login owns it). One step is not an owner's to write: the
        policy lets only a specialist post `complete_confirmatory` (EVENT_POLICY), and a specialist may act only on a case it
        owns. So on a case a primary-care owner keeps, the owner records the colonoscopy and the evidence scan (the scheduler
        container, as in production) completes the step and proposes the closure; the stager waits for it."""
        self.team = SPECIALIST if via_specialist else self.assigned_owner(case_id)
        self.via_specialist = via_specialist
        for step in steps_to_run(self.case(case_id)["state"], target):
            self._step(step, case_id, patient_ref)
        return self.case(case_id)["state"]

    def _step(self, step: str, case_id: str, patient_ref: str) -> None:
        team = self.team  # whoever carries the case from here: the specialist, or the owner themself
        if step == "accept_ownership":
            self.event(self.assigned_owner(case_id), case_id, "accept_ownership")
        elif step == "hand_off":
            if self.via_specialist and owner_name(self.case(case_id)) != SPECIALIST[1]:
                self.event(self.assigned_owner(case_id), case_id, "handoff_requested", {"to_provider": SPECIALIST_PROVIDER})
                self.event(SPECIALIST, case_id, "handoff_accepted")
        elif step == "contact_patient":
            self.event(team, case_id, "contact_patient", {"channel": "phone"})
        elif step == "patient_acknowledge":
            self.event(("patient", "demo patient", None), case_id, "patient_acknowledge", {"lang": "ar"}, patient_ref=patient_ref)
        elif step == "book_confirmatory":
            # The owner path needs the appointment to have begun, or the evidence scan will not look for the colonoscopy.
            days = APPOINTMENT_IN_DAYS if self.via_specialist else 0
            when = (self.sim_now() + timedelta(days=days)).date().isoformat()
            self.event(team, case_id, "book_confirmatory", {"appointment_ref": f"APPT-DEMO-{case_id[:8]}", "appointment_date": when})
        elif step == "record_result":
            out = self.call(team, "POST", f"/cases/{case_id}/confirmatory-result")
            if self.via_specialist:  # a specialist owns the case: complete_confirmatory is theirs to post
                self.event(team, case_id, "complete_confirmatory", {"result_ref": out["evidence_ref"]})
            else:  # a primary-care owner may not post it: the scheduler's evidence scan finds the Procedure and completes the step
                # The API forgets its remembered "no" when the result is recorded (automation.evidence_recorded), so one scan
                # after the write finds it: ask for it once, then poll for at most EVIDENCE_WAIT_SECONDS.
                self.trigger_evidence_scan()
                self.wait_for("the evidence scan to complete the confirmatory step",
                              lambda: self.case(case_id)["state"] != "confirmatory_booked", EVIDENCE_WAIT_SECONDS)
        elif step in ("clinician_review", "close"):
            signer = f"Practitioner/{self.login(team)['user_id']}"
            payload = {"signed_by": signer}
            if step == "close":
                payload["evidence_ref"] = f"Procedure/colonoscopy-{case_id}"
            self.event(team, case_id, step, payload)
        else:
            raise StageError(f"unknown step {step!r}")

    def signoff_row(self) -> int:
        """The manifest row of the sign-off case: the first from SIGNOFF_FROM_ROW registered with the physician's provider."""
        for i in range(SIGNOFF_FROM_ROW, len(self.rows)):
            if create_cases.registered_provider_for(self.rows[i]["patient_id"], self.providers) == PHYSICIAN[2]:
                return i
        raise StageError(f"no cohort row from {SIGNOFF_FROM_ROW} on is registered with {PHYSICIAN[2]!r}: the physician would own no sign-off case")

    def stage_signoff(self) -> str:
        case_id, ref, _ = self.ensure_case(self.signoff_row())
        if owner_name(self.case(case_id)) != PHYSICIAN[1]:
            raise StageError(f"case {case_id[:8]} is owned by {owner_name(self.case(case_id))!r}, not {PHYSICIAN[1]!r}")
        state = self.walk(case_id, ref, "confirmatory_completed", via_specialist=False)  # the physician owns it: no hand-off
        if state != "confirmatory_completed":
            raise StageError(f"case {case_id[:8]} is at {state!r}, not 'confirmatory_completed': nothing for the physician to sign")
        return self._note("signoff", f"case {case_id[:8]} is owned by {PHYSICIAN[1]} and is at {state!r}, waiting for her review and signature")

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

    # ---- the tour pool and the agency run -------------------------------------------------------------------------------
    def pool_rows(self) -> dict[str, list[int]]:
        """Manifest rows for each tour-pool state: rows not used above (0 and 1 stay for the local demo), taken from the END of
        the manifest so the early rows stay untouched, and never more than leaves MIN_UNTOUCHED rows alone. Deterministic."""
        reserved = {0, 1, ROW_OVERDUE, ROW_CLOSED, ROW_DRAFT, self.signoff_row()}
        free = [i for i in range(len(self.rows)) if i not in reserved]
        room = max(0, len(free) - MIN_UNTOUCHED)
        out: dict[str, list[int]] = {}
        taken = 0
        for name in ("callback", "contacted", "completed"):  # the order is the priority when the cohort is small
            n = min(TOUR_POOL[name], max(0, room - taken))
            out[name] = [free[-1 - (taken + k)] for k in range(n)]
            taken += n
        return out

    def stage_callbacks(self, rows: list[int]) -> str:
        """The patient asks for a call-back (their own button in the patient app); the case needs a person to phone them, which the
        coordinator may do (EVENT_POLICY: contact_patient). The owner has accepted the case first, so the call is a step the case allows."""
        done = []
        for row in rows:
            case_id, ref, _ = self.ensure_case(row)
            if self.case(case_id)["state"] == "acknowledged":
                self.event(self.assigned_owner(case_id), case_id, "accept_ownership")
            if self.case(case_id)["state"] == "owner_accepted":
                self.event(("patient", "demo patient", None), case_id, "callback_requested",
                           {"reason": "wants_to_speak_to_clinician", "lang": "ar"}, patient_ref=ref)
            done.append(case_id[:8])
        return self._note("callbacks", f"{len(done)} case(s) with a patient's call-back request for the coordinator: {', '.join(done) or 'none'}")

    def stage_to(self, rows: list[int], target: str, note: str) -> str:
        reached = []
        for row in rows:
            case_id, ref, _ = self.ensure_case(row)
            state = self.walk(case_id, ref, target, via_specialist=False)
            if state != target:
                raise StageError(f"case {case_id[:8]} is at {state!r}, not {target!r}")
            reached.append(case_id[:8])
        return self._note(note, f"{len(reached)} case(s) at {target!r} for the tour: {', '.join(reached) or 'none'}")

    def stage_tour_pool(self) -> dict[str, list[int]]:
        pool = self.pool_rows()
        self.stage_callbacks(pool["callback"])
        self.stage_to(pool["contacted"], "patient_contacted", "pool_contacted")
        self.stage_to(pool["completed"], "confirmatory_completed", "pool_completed")
        return pool

    def stage_agency(self) -> str:
        """One agency run at the current simulated time, as the supervisor: the agents prepare work on the cases as they now stand and
        stop (they write no case event as the agency; a draft they cause goes through the drafting route and waits for the owner).
        The same call as `make agency-run`. A second call at the same simulated time returns the same run."""
        out = self.call(SUPERVISOR, "POST", "/agency/run")
        counts = out.get("counts", {})
        by = counts.get("by_status", {})
        if out.get("status") not in ("done", "budget_exhausted") or not counts.get("total"):
            raise StageError(f"the agency run did not produce work: {json.dumps({k: out.get(k) for k in ('status', 'counts')})[:300]}")
        return self._note("agency", f"run {str(out.get('run_id'))[:8]} is {out['status']}: {counts['total']} task(s), "
                                    f"{by.get('done', 0)} done, {by.get('needs_human', 0)} waiting for a person, {by.get('failed', 0)} failed")

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
        stager = Stager(client, api, rows, scan_token=os.environ.get("AUTOMATION_SERVICE_TOKEN") or None)
        try:
            if args.phase in ("overdue", "all"):
                stager.stage_overdue()
            if args.phase in ("rest", "all"):
                stager.stage_draft()
                stager.stage_signoff()
                stager.stage_closed()
                stager.stage_tour_pool()
                stager.check_urgent_chronic()
                stager.stage_agency()  # last: the agents read the cases as staged
        except (StageError, httpx.HTTPError, KeyError) as exc:
            log.error("FAILED: %s: %s", type(exc).__name__, exc)
            return 1
    print("SUMMARY " + json.dumps(stager.summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
