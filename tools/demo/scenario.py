#!/usr/bin/env python3
"""The 7-minute demo scenario, scripted through the API (E8).

Picks ONE named synthetic patient from the seeded cohort, deterministically (the first case in seed order, that is
the first positive FIT in `data/results/manifest.json`; the display name is the Synthea name from HAPI), and drives
that patient's case through the whole story with real API calls, moving the simulation clock as a supervisor does.
Every step is an ordinary API call by an ordinary demo login, so everything the demo shows is in the event log
and the FHIR store afterwards.

    python3 tools/demo/scenario.py                    run every step
    python3 tools/demo/scenario.py --until overdue    stop after that step (rehearse the live part from there)
    python3 tools/demo/scenario.py --list             list the steps with what each one does
    make demo-scenario [UNTIL=<step>]                 the same through make

It needs the stack up, the cohort seeded, and the demo state clean: the simulation clock at its start and the
picked case still at `result_received` (`make demo-reset` puts both back from the snapshot). It refuses
otherwise instead of producing a different story.

Determinism (T-E9): every event id is a uuid5 of the case and the step, every clock move lands on an exact whole
hour computed with the same working-day calendar as the sweep, and the scheduler sweep is called directly so the
result does not depend on when the scheduler container wakes up. The log of the case is hashed at the end with
random ids (draft and notification ids) replaced; two runs after `make demo-reset` print the same hash.

Environment: API_URL (default http://localhost:8000), FHIR_BASE_URL (default http://localhost:8080/fhir),
SCHEDULER_SERVICE_TOKEN (default: the development token compose runs with), DEMO_MANIFEST (default
data/results/manifest.json; `make test-offline` points it at a one-row manifest for its own empty stack).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from app.calendar import default_calendar  # noqa: E402  (the same working-day calendar the sweep uses)

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
FHIR = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")
SCHEDULER_TOKEN = os.environ.get("SCHEDULER_SERVICE_TOKEN", "dev-only-scheduler-service-token")
MANIFEST = Path(os.environ.get("DEMO_MANIFEST") or ROOT / "data" / "results" / "manifest.json")
CASE_NAMESPACE = uuid.UUID("0b8e6d5a-3c1f-4a52-8a77-9d4e2f6b1c33")  # simulator/create_cases.py
SIM_START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # config SIM_CLOCK_START

# The demo cast. Names are synthetic; the logins are the same ones the web's role switcher uses.
LAB = ("lab", "Intake Lab", None)
COORDINATOR = ("coordinator", "Maryam Al Suwaidi", None)
SUPERVISOR = ("supervisor", "Dr. Noura Al Hammadi", None)
OWNER = ("primary_care_owner", "Dr. Layla Haddad", "Riverside Primary Care")
NO_CAPACITY = ("specialist_owner", "Dr. Rana Farouk", "Al Noor Endoscopy Centre")
SPECIALIST = ("specialist_owner", "Dr. Karim Nasser", "Corniche Gastro Unit")
SILENT_PROVIDER = "Marina Digestive Health"  # never answers: the scheduler bounces the request


class ScenarioError(RuntimeError):
    pass


def step_id(case_id: str, name: str, n: int = 0) -> str:
    """Deterministic event id for (case, step, nth event of the step)."""
    return str(uuid.uuid5(uuid.UUID(case_id), f"demo|{name}|{n}"))


def whole_hour_after(moment: datetime) -> datetime:
    """The first whole hour strictly after `moment` (clock moves land on exact hours: no float residue)."""
    base = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return base + timedelta(hours=1)


@dataclass
class Demo:
    http: httpx.Client
    tokens: dict = field(default_factory=dict)
    ids: dict = field(default_factory=dict)  # role:name -> user_id
    case_id: str = ""
    patient_ref: str = ""
    patient_name: str = ""
    timings: list = field(default_factory=list)
    calendar: object = field(default_factory=default_calendar)
    draft_id: str = ""
    evidence_ref: str = ""
    appointment_date: str = ""

    # ---- plumbing -------------------------------------------------------------------------
    def login(self, who, patient_ref: str | None = None) -> str:
        role, name, provider = who
        key = f"{role}:{name}"
        if key not in self.tokens:
            body = {"role": role, "name": name}
            if provider:
                body["provider"] = provider
            if patient_ref:
                body["patient_ref"] = patient_ref
            r = self.http.post(f"{API}/auth/dev-login", json=body)
            r.raise_for_status()
            self.tokens[key] = r.json()["token"]
            self.ids[key] = r.json()["user_id"]
        return self.tokens[key]

    def user_id(self, who) -> str:
        self.login(who)
        return self.ids[f"{who[0]}:{who[1]}"]

    def call(self, who, method: str, path: str, body=None, ok=(200, 201), patient_ref=None) -> dict:
        r = self.http.request(
            method, f"{API}{path}", json=body,
            headers={"Authorization": f"Bearer {self.login(who, patient_ref)}"},
        )
        if r.status_code not in ok:
            raise ScenarioError(f"{method} {path} as {who[0]} {who[1]!r}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def event(self, who, type_: str, payload: dict | None = None, name: str = "", n: int = 0, patient_ref=None) -> dict:
        return self.call(
            who, "POST", f"/cases/{self.case_id}/events",
            {"type": type_, "payload": payload or {}, "event_id": step_id(self.case_id, name or type_, n)},
            patient_ref=patient_ref,
        )

    def case(self) -> dict:
        return self.call(COORDINATOR, "GET", f"/cases/{self.case_id}")

    def sim_now(self) -> datetime:
        return datetime.fromisoformat(self.http.get(f"{API}/health").json()["sim_time"])

    def advance_to(self, target: datetime) -> None:
        """Supervisor moves the clock to exactly `target` (an exact hour), then the scheduler sweeps once."""
        now = self.sim_now()
        if target <= now:
            self.sweep()
            return
        hours = (target - now).total_seconds() / 3600
        assert hours == int(hours), f"clock move of {hours} h is not a whole number of hours"
        self.call(SUPERVISOR, "POST", "/clock/advance", {"hours": int(hours)})
        self.sweep()

    def sweep(self) -> dict:
        r = self.http.post(f"{API}/scheduler/sweep", headers={"Authorization": f"Bearer {SCHEDULER_TOKEN}"}, timeout=300)
        if r.status_code != 200:
            raise ScenarioError(f"scheduler sweep refused: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def expect_state(self, state: str) -> dict:
        got = self.case()
        if got["state"] != state:
            raise ScenarioError(f"expected state {state!r}, the case is in {got['state']!r}")
        return got

    # ---- the steps ------------------------------------------------------------------------
    def s_result_received(self):
        """The positive FIT of the named patient is in: the case is at `result_received`, nobody owns it."""
        c = self.expect_state("result_received")
        assert c["owner"] is None

    def s_acknowledged(self):
        """The lab acknowledges the result."""
        self.event(LAB, "acknowledge")
        self.expect_state("acknowledged")

    def s_owner_accepted(self):
        """Dr. Layla Haddad (primary care) accepts ownership: the case now has a named owner and a clock."""
        self.event(OWNER, "accept_ownership")
        self.expect_state("owner_accepted")

    def s_patient_unreachable(self):
        """The owner calls the patient; nobody answers, so the patient never acknowledges."""
        self.event(OWNER, "contact_patient", {"channel": "phone"})
        self.expect_state("patient_contacted")

    def s_overdue(self):
        """Clock advances past the 5-working-day patient-acknowledgement SLA: the scheduler marks the case overdue and sends rung 1 (reminder to the owner)."""
        due = datetime.fromisoformat(self.case()["due_at"])
        self.advance_to(whole_hour_after(due))
        c = self.case()
        assert c["overdue"] and c["escalation_level"] == 1, (c["overdue"], c["escalation_level"])

    def _rung(self, level: int, working_days: int):
        due = datetime.fromisoformat(self.case()["due_at"])  # the breach's due date once overdue
        self.advance_to(whole_hour_after(self.calendar.add_working_days(due, working_days)))
        assert self.case()["escalation_level"] == level, self.case()["escalation_level"]

    def s_escalation_supervisor(self):
        """Three working days later, rung 2: the supervisor is notified."""
        self._rung(2, 3)

    def s_escalation_coordinator(self):
        """Seven working days after the breach, rung 3: the programme coordinator is notified. Nobody has accepted the first two."""
        self._rung(3, 7)

    def s_escalation_accepted(self):
        """The coordinator accepts rung 3. (An acceptance stops the ladder, so the supervisor can only accept rung 2, before rung 3 exists; to show all three rungs the last is accepted by its own target.)"""
        self.event(COORDINATOR, "escalation_accepted", {"rung": "coordinator"})
        assert self.case()["read_model"]["escalation_accepted"] is True

    def s_draft_sent(self):
        """The owner has an Arabic reminder drafted for the patient, approves it, and it is sent (the model drafts, the human decides)."""
        draft = self.call(OWNER, "POST", f"/cases/{self.case_id}/drafts", {"purpose": "reminder", "channel": "sms", "language": "ar"})
        self.draft_id = draft["draft_id"]
        approved = self.call(OWNER, "POST", f"/drafts/{self.draft_id}/approve")
        assert approved["sent"] is True, approved

    def s_patient_acknowledged(self):
        """The patient opens the PWA and taps "I understand"."""
        self.event(("patient", self.patient_name, None), "patient_acknowledge", {"lang": "ar"}, patient_ref=self.patient_ref)
        self.expect_state("patient_acknowledged")

    def s_handoff_rejected(self):
        """The owner hands the case to Al Noor Endoscopy; they reject it for capacity. Ownership stays with the referrer and a re-route clock starts."""
        self.event(OWNER, "handoff_requested", {"to_provider": NO_CAPACITY[2]}, name="handoff_requested", n=0)
        self.event(NO_CAPACITY, "handoff_rejected", {"reason": "capacity"})
        self.expect_state("referral_rejected")

    def s_handoff_bounced(self):
        """The owner tries Marina Digestive Health, who never answer: three working days later the scheduler bounces the request."""
        self.event(OWNER, "handoff_requested", {"to_provider": SILENT_PROVIDER}, name="handoff_requested", n=1)
        requested = datetime.fromisoformat(self.case()["read_model"]["pending_handoff"]["requested_at"])
        self.advance_to(whole_hour_after(self.calendar.add_working_days(requested, 3)))
        last = self.case()["read_model"]["last_handoff"]
        assert last and last.get("bounced") is True, last

    def s_reassigned(self):
        """The coordinator re-routes the bounced referral back to the owner, who now owns it again at `owner_accepted`."""
        owner = f"{OWNER[0]}:{self.user_id(OWNER)}"
        self.event(COORDINATOR, "reassign", {"owner": owner, "provider": OWNER[2]})
        self.expect_state("owner_accepted")

    def s_handoff_accepted(self):
        """The owner asks Corniche Gastro Unit; Dr. Karim Nasser accepts and becomes the owner."""
        self.event(OWNER, "handoff_requested", {"to_provider": SPECIALIST[2]}, name="handoff_requested", n=2)
        self.event(SPECIALIST, "handoff_accepted")
        c = self.case()
        assert c["owner"]["display_name"] == SPECIALIST[1], c["owner"]

    def s_patient_reacknowledged(self):
        """The new owner calls the patient, who acknowledges again (a re-routed referral goes back through contact)."""
        self.event(SPECIALIST, "contact_patient", {"channel": "phone"}, name="recontact")
        self.event(("patient", self.patient_name, None), "patient_acknowledge", {"lang": "ar"}, name="reack", patient_ref=self.patient_ref)
        self.expect_state("patient_acknowledged")

    def s_booked(self):
        """Dr. Nasser books the colonoscopy, 14 days out."""
        when = (self.sim_now() + timedelta(days=14)).date()
        self.appointment_date = when.isoformat()
        self.event(SPECIALIST, "book_confirmatory", {"appointment_ref": "Appointment/demo-colonoscopy", "appointment_date": self.appointment_date})
        self.expect_state("confirmatory_booked")

    def s_colonoscopy_done(self):
        """The clock advances to the appointment day: the colonoscopy has taken place."""
        day = datetime.fromisoformat(self.appointment_date).replace(tzinfo=UTC, hour=9)
        self.advance_to(day)

    def s_result_recorded(self):
        """The specialist records the colonoscopy: a Procedure and DiagnosticReport are created in HAPI, and the case is told the result exists."""
        out = self.call(SPECIALIST, "POST", f"/cases/{self.case_id}/confirmatory-result")
        self.evidence_ref = out["evidence_ref"]
        r = self.http.get(f"{FHIR}/{self.evidence_ref}")
        assert r.status_code == 200, f"{self.evidence_ref} is not in HAPI (HTTP {r.status_code})"
        self.event(SPECIALIST, "complete_confirmatory", {"result_ref": self.evidence_ref})
        self.expect_state("confirmatory_completed")

    def s_clinician_review(self):
        """The clinician reviews the result and signs as themselves."""
        self.event(SPECIALIST, "clinician_review", {"signed_by": f"Practitioner/{self.user_id(SPECIALIST)}"})
        self.expect_state("clinician_reviewed")

    def s_closed(self):
        """The clinician closes the case with the Procedure as evidence and their own Practitioner as the signature. No automated actor could."""
        if not self.evidence_ref:
            self.evidence_ref = f"Procedure/colonoscopy-{self.case_id}"
        self.event(SPECIALIST, "close", {"evidence_ref": self.evidence_ref, "signed_by": f"Practitioner/{self.user_id(SPECIALIST)}"})
        self.expect_state("closed")


STEPS = [
    "result_received", "acknowledged", "owner_accepted", "patient_unreachable", "overdue",
    "escalation_supervisor", "escalation_coordinator", "escalation_accepted", "draft_sent",
    "patient_acknowledged", "handoff_rejected", "handoff_bounced", "reassigned", "handoff_accepted",
    "patient_reacknowledged", "booked", "colonoscopy_done", "result_recorded", "clinician_review", "closed",
]


def wait_for_hapi(http: httpx.Client, seconds: float = 90, pause: float = 2) -> bool:
    """True once HAPI answers `/metadata`; False after `seconds` (it takes up to ~40 s to boot after a reset)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            if http.get(f"{FHIR}/metadata").status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            return False
        time.sleep(pause)


# ---- picking the patient --------------------------------------------------------------------------------


def pick_case(index: int = 0) -> tuple[str, str]:
    """(case_id, patient_ref) of the `index`-th positive FIT in seed order: the cohort manifest's row `index`.

    Row 0 is the named patient of this scenario. `stage_second_case.py` takes row 1, so the two never meet.
    """
    if not MANIFEST.exists():
        raise ScenarioError(f"{MANIFEST} not found: seed the cohort first (make demo-seed)")
    rows = json.loads(MANIFEST.read_text())["results"]
    if index >= len(rows):
        raise ScenarioError(f"{MANIFEST} has {len(rows)} result(s): there is no row {index}")
    row = rows[index]
    case_id = str(uuid.uuid5(CASE_NAMESPACE, f"DiagnosticReport/{row['report_id']}"))
    return case_id, f"Patient/{row['patient_id']}"


def synthea_name(http: httpx.Client, patient_ref: str) -> str:
    """The patient's display name from HAPI (Synthea): given and family names without Synthea's numeric suffixes."""
    r = http.get(f"{FHIR}/{patient_ref}")
    r.raise_for_status()
    name = (r.json().get("name") or [{}])[0]
    parts = [*name.get("given", []), name.get("family", "")]
    clean = " ".join(re.sub(r"\d+$", "", p) for p in parts if p).strip()
    return clean or patient_ref


# ---- the event log hash (T-E9) ---------------------------------------------------------------------------

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# Ids derived from a random one: since SEC-2 the notification id is a uuid5 derived from the (random, uuid4) draft id
# through its outbox row, so it changes from run to run although it is not a uuid4 itself.
_RANDOM_DERIVED_KEYS = frozenset({"notification_id"})


def _normal(value):
    """Random ids (uuid4: draft ids) and the notification ids derived from them become a placeholder; everything else
    is kept as is."""
    if isinstance(value, dict):
        return {k: "<uuid4>" if k in _RANDOM_DERIVED_KEYS and isinstance(v, str) and _UUID.match(v) else _normal(v)
                for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normal(v) for v in value]
    if isinstance(value, str) and _UUID.match(value) and uuid.UUID(value).version == 4:
        return "<uuid4>"
    return value


def event_log_hash(events: list[dict]) -> str:
    """sha256 over (type, at, actor, payload) of every event in order, with random ids normalised away."""
    rows = [[e["type"], e["at"], e["actor"], _normal(e["payload"])] for e in events]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---- main -------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", choices=STEPS, help="stop after this step")
    ap.add_argument("--list", action="store_true", help="list the steps and exit")
    ap.add_argument("--json", action="store_true", help="print the final summary as JSON")
    args = ap.parse_args(argv)
    methods = {s: getattr(Demo, f"s_{s}") for s in STEPS}
    if args.list:
        for i, s in enumerate(STEPS, 1):
            print(f"{i:2d}. {s:24s} {(methods[s].__doc__ or '').strip()}")
        return 0

    with httpx.Client(timeout=60) as http:
        demo = Demo(http)
        try:
            health = http.get(f"{API}/health").json()
        except httpx.HTTPError as exc:
            print(f"scenario: the API at {API} is not reachable ({exc}); start the stack with `make up`", file=sys.stderr)
            return 2
        if datetime.fromisoformat(health["sim_time"]) != SIM_START:
            print(f"scenario: the simulation clock is at {health['sim_time']}, not at its start {SIM_START.isoformat()}: run `make demo-reset` first", file=sys.stderr)
            return 2
        if not wait_for_hapi(http):
            print(f"scenario: HAPI at {FHIR} is not answering; the stack is not ready", file=sys.stderr)
            return 2
        try:
            demo.case_id, demo.patient_ref = pick_case()
            demo.patient_name = synthea_name(http, demo.patient_ref)
            state = demo.case()["state"]
        except (ScenarioError, httpx.HTTPError) as exc:
            print(f"scenario: cannot pick the demo case: {exc}", file=sys.stderr)
            return 2
        if state != "result_received":
            print(f"scenario: the demo case is already at {state!r}: run `make demo-reset` first", file=sys.stderr)
            return 2
        print(f"scenario: patient {demo.patient_name} ({demo.patient_ref}), case {demo.case_id}")
        t_all = time.monotonic()
        last = args.until or STEPS[-1]
        for i, name in enumerate(STEPS, 1):
            t0 = time.monotonic()
            try:
                methods[name](demo)
            except (ScenarioError, AssertionError, httpx.HTTPError) as exc:
                print(f"scenario: step {i} {name} FAILED: {exc}", file=sys.stderr)
                return 1
            dt = time.monotonic() - t0
            demo.timings.append((name, dt))
            print(f"  {i:2d}/{len(STEPS)} {name:24s} {dt:5.1f} s  sim {demo.sim_now():%Y-%m-%d %H:%M}")
            if name == last:
                break
        final = demo.case()
        summary = {
            "patient": demo.patient_name, "patient_ref": demo.patient_ref, "case_id": demo.case_id,
            "state": final["state"], "events": len(final["events"]), "sim_time": demo.sim_now().isoformat(),
            "event_log_hash": event_log_hash(final["events"]), "seconds": round(time.monotonic() - t_all, 1),
            "step_seconds": {n: round(t, 1) for n, t in demo.timings},
        }
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"scenario: done in {summary['seconds']} s: state {summary['state']}, {summary['events']} events, sim {summary['sim_time']}")
            print(f"EVENT LOG HASH {summary['event_log_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
