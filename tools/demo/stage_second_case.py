#!/usr/bin/env python3
"""Stage the demo's SECOND case at `confirmatory_completed`, owned by the specialist login (E9).

The live demo (docs/submission/demo-script.md) ends on a case that is ready to be reviewed and closed: the colonoscopy
is done and recorded, the owner is the specialist Dr. Karim Nasser. This script prepares that case through the real API,
so it is a normal case with a normal event log, and so it can go into the demo snapshot (`make demo-stage && make
demo-snapshot`: `make demo-reset` then brings it back every time).

    python3 tools/demo/stage_second_case.py            walk the case to confirmatory_completed (or say it is there already)
    python3 tools/demo/stage_second_case.py --show     print which case it would use (reads the cohort manifest only)
    python3 tools/demo/stage_second_case.py --json     print the outcome as JSON
    make demo-stage                                    the same through make

Which case: the SECOND positive FIT in seed order (row 1 of `data/results/manifest.json`; `scenario.py` drives row 0, so
the two never meet). The case id is the same uuid5 `simulator/create_cases.py` gave it.

What it does, as the logins of the demo script (the same ones as scenario.py and the web's role switcher). The stack runs
with the automation on (A21), so the case arrived acknowledged and assigned to the default owner of the provider the
patient is registered with (config/providers.yaml): that owner accepts ownership (the automation sends the pre-approved
result notice) and hands the case to Corniche Gastro Unit, where Dr. Karim Nasser (specialist, the demo's clinician)
accepts and records the patient contact; the patient acknowledges; Dr. Nasser books the colonoscopy 14 days after the
simulation clock and records the result (`POST /cases/{id}/confirmatory-result` writes the `Procedure` and
`DiagnosticReport` to HAPI, then `complete_confirmatory` carries the `Procedure` as `result_ref`). A last evidence scan
makes the automation propose the closure (`closure_proposed`, evidence prefilled for the clinician's one-click sign-off):
it would do so within seconds anyway (the Procedure is in HAPI), so it is done here, where the snapshot will contain it,
and not by the scheduler container after a reset. The simulation clock is not moved, which `scenario.py` requires of the
snapshot.

Safe to rerun: every event id is a uuid5 of the case and the step, the Procedure id is deterministic, and the script reads
the case first. A case that is part-way (an earlier run stopped) continues from its state; one already at
`confirmatory_completed` (or past it, reviewed or closed) is left alone and the script says so. Exit codes: 0 done or
already done, 1 the staging failed or the case cannot be staged, 2 the stack is not reachable or not ready.

Environment: the same as scenario.py (API_URL, FHIR_BASE_URL, DEMO_MANIFEST).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tools/demo, for `import scenario`

import scenario  # noqa: E402
from scenario import OWNERS, REROUTED_TO, SPECIALIST, Demo, ScenarioError  # noqa: E402

SECOND_CASE_INDEX = 1  # row of the cohort manifest; scenario.py takes row 0
APPOINTMENT_REF = "APPT-2026-0002"
APPOINTMENT_IN_DAYS = 14

# The main path from the intake to the target (the automation acknowledges and assigns on creation, so it starts at
# `acknowledged`), and the steps still to do from each state.
PATH = [
    "acknowledged", "owner_accepted", "patient_contacted", "patient_acknowledged",
    "confirmatory_booked", "confirmatory_completed",
]
TARGET = PATH[-1]
STEPS = ["accept_ownership", "hand_off", "contact_patient", "patient_acknowledge", "book_confirmatory", "record_result"]
FIRST_STEP = {"acknowledged": 0, "owner_accepted": 1, "patient_contacted": 3, "patient_acknowledged": 4,
              "confirmatory_booked": 5, "confirmatory_completed": len(STEPS)}
PAST_TARGET = ("clinician_reviewed", "closed")  # the case got here and moved on: nothing to stage


class StageError(RuntimeError):
    """The case cannot be staged (an unexpected state or owner). The message says what to do."""


def pick_second_case() -> tuple[str, str]:
    """(case_id, patient_ref) of the second positive FIT in seed order. Never the named patient of scenario.py."""
    case_id, patient_ref = scenario.pick_case(SECOND_CASE_INDEX)
    if (case_id, patient_ref) == scenario.pick_case(0):
        raise StageError("the second case is the named patient's case: the cohort manifest repeats a result")
    return case_id, patient_ref


def remaining_steps(state: str) -> list[str]:
    """The steps still to do from `state`, in order. [] when the case is at the target or past it."""
    if state in PAST_TARGET:
        return []
    if state not in FIRST_STEP:
        hint = " (the automation has not taken the intake: is the stack running with AUTOMATION_ENABLED=true?)" if state == "result_received" else ""
        raise StageError(
            f"the case is in {state!r}, which is off the main path to {TARGET!r}{hint}: run `make demo-reset` and stage again"
        )
    return STEPS[FIRST_STEP[state]:]


def _owner_name(case: dict) -> str | None:
    owner = case.get("owner")
    return owner.get("display_name") if isinstance(owner, dict) else owner


class Stager:
    """One step per method, named `do_<step>`; each is a real API call by a demo login and checks the state it expects."""

    def __init__(self, demo: Demo, patient_name: str) -> None:
        self.demo = demo
        self.patient_name = patient_name
        self.evidence_ref = ""

    def run(self, step: str) -> None:
        getattr(self, f"do_{step}")()

    def _event(self, who, type_: str, payload: dict | None = None, **kw) -> dict:
        # the ids are uuid5(case, "demo|stage_<type>|0"): a rerun cannot write an event twice
        return self.demo.event(who, type_, payload, name=f"stage_{type_}", **kw)

    def _assigned_owner(self) -> tuple:
        """The login of the case's current owner: the automation's assignee, or the specialist once the case is with them."""
        name = _owner_name(self.demo.case())
        who = OWNERS.get(name) or (SPECIALIST if name == SPECIALIST[1] else None)
        if who is None:
            raise StageError(f"the case is owned by {name!r}, who is not in the demo cast ({sorted([*OWNERS, SPECIALIST[1]])})")
        return who

    def do_accept_ownership(self) -> None:
        self._event(self._assigned_owner(), "accept_ownership")
        self.demo.expect_state("owner_accepted")
        self.demo.automation_scan()  # the pre-approved result notice goes out now, not whenever the container's loop looks

    def do_hand_off(self) -> None:
        if _owner_name(self.demo.case()) == SPECIALIST[1]:
            return  # an earlier run got this far
        self._event(self._assigned_owner(), "handoff_requested", {"to_provider": REROUTED_TO})
        self._event(SPECIALIST, "handoff_accepted")
        self.demo.expect_state("owner_accepted")
        self.demo.automation_scan()

    def do_contact_patient(self) -> None:
        self._event(SPECIALIST, "contact_patient", {"channel": "phone"})
        self.demo.expect_state("patient_contacted")

    def do_patient_acknowledge(self) -> None:
        self._event(("patient", self.patient_name, None), "patient_acknowledge", {"lang": "ar"}, patient_ref=self.demo.patient_ref)
        self.demo.expect_state("patient_acknowledged")

    def do_book_confirmatory(self) -> None:
        when = (self.demo.sim_now() + timedelta(days=APPOINTMENT_IN_DAYS)).date().isoformat()
        self._event(SPECIALIST, "book_confirmatory", {"appointment_ref": APPOINTMENT_REF, "appointment_date": when})
        self.demo.expect_state("confirmatory_booked")
        self.demo.automation_scan()  # the appointment confirmation

    def do_record_result(self) -> None:
        out = self.demo.call(SPECIALIST, "POST", f"/cases/{self.demo.case_id}/confirmatory-result")
        self.evidence_ref = out["evidence_ref"]
        self._event(SPECIALIST, "complete_confirmatory", {"result_ref": self.evidence_ref})
        self.demo.expect_state(TARGET)


def stage(demo: Demo, patient_name: str) -> dict:
    """Bring the case to `confirmatory_completed` owned by the specialist, or report that it is there or past it.

    Raises StageError on failure (a step names itself in the message), or ScenarioError / httpx.HTTPError if the case cannot even be read. Returns a summary dict:
    `outcome` is "staged", "already_staged" or "past_target".
    """
    case = demo.case()
    state = case["state"]
    todo = remaining_steps(state)
    summary = {"case_id": demo.case_id, "patient_ref": demo.patient_ref, "patient": patient_name, "steps_run": []}
    if state in PAST_TARGET:
        return {**summary, "outcome": "past_target", "state": state, "owner": _owner_name(case)}
    stager = Stager(demo, patient_name)
    for step in todo:
        try:
            stager.run(step)
        except (ScenarioError, AssertionError, httpx.HTTPError) as exc:
            done = ", ".join(summary["steps_run"]) or "none"
            raise StageError(f"step {step!r} failed (steps done before it: {done}; rerun to continue): {exc}") from exc
        summary["steps_run"].append(step)
    final = demo.case()
    if final["state"] != TARGET:
        raise StageError(f"the case ended in {final['state']!r}, not {TARGET!r}")
    owner = _owner_name(final)
    if owner != SPECIALIST[1]:
        raise StageError(
            f"the case is at {TARGET!r} but its owner is {owner!r}, not {SPECIALIST[1]!r}: run `make demo-reset` and stage again"
        )
    if final["read_model"].get("closure_proposal") is None:
        try:
            demo.evidence_scan()  # the automation finds the Procedure and proposes the closure (a person still signs)
            final = demo.case()
        except (ScenarioError, httpx.HTTPError) as exc:
            raise StageError(f"the evidence scan failed (the case is staged; rerun to propose the closure): {exc}") from exc
        if final["read_model"].get("closure_proposal") is None:
            raise StageError("the evidence scan did not propose the closure although the Procedure is in HAPI")
    return {**summary, "outcome": "staged" if todo else "already_staged", "state": final["state"], "owner": owner,
            "closure_proposed": True}


def describe(result: dict) -> str:
    who = f"{result['patient']} ({result['patient_ref']}), case {result['case_id']}"
    if result["outcome"] == "past_target":
        return f"stage-second-case: {who} is already past the target (state {result['state']!r}): nothing to do"
    if result["outcome"] == "already_staged":
        return f"stage-second-case: {who} is already at {TARGET!r}, owned by {result['owner']}: nothing to do"
    return f"stage-second-case: {who} staged at {TARGET!r}, owned by {result['owner']} ({', '.join(result['steps_run'])})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="store_true", help="print which case would be staged and exit (no API needed)")
    ap.add_argument("--json", action="store_true", help="print the outcome as JSON")
    args = ap.parse_args(argv)

    try:
        case_id, patient_ref = pick_second_case()
    except (ScenarioError, StageError) as exc:
        print(f"stage-second-case: cannot pick the second case: {exc}", file=sys.stderr)
        return 2
    if args.show:
        print(f"stage-second-case: the second case is {patient_ref}, case {case_id} (short id {case_id[:8]}), "
              f"staged at {TARGET!r} and owned by {SPECIALIST[1]}")
        return 0

    with httpx.Client(timeout=60) as http:
        try:
            health = http.get(f"{scenario.API}/health").json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"stage-second-case: the API at {scenario.API} is not reachable ({exc}); start the stack with `make up`", file=sys.stderr)
            return 2
        if not scenario.wait_for_hapi(http):
            print(f"stage-second-case: HAPI at {scenario.FHIR} is not answering; the stack is not ready", file=sys.stderr)
            return 2
        demo = Demo(http)
        demo.case_id, demo.patient_ref = case_id, patient_ref
        try:
            demo.patient_name = scenario.synthea_name(http, patient_ref)
            if datetime.fromisoformat(health["sim_time"]) != scenario.SIM_START:
                print(f"stage-second-case: note: the simulation clock is at {health['sim_time']}, not at its start "
                      f"{scenario.SIM_START.isoformat()}; do not take the snapshot from this state", file=sys.stderr)
            result = stage(demo, demo.patient_name)
        except (StageError, ScenarioError, AssertionError, httpx.HTTPError, KeyError) as exc:
            print(f"stage-second-case: FAILED for case {case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(result, indent=2) if args.json else describe(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
