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
picked case still at `acknowledged` with its owner named but not yet accepted (`make demo-reset` puts both back from
the snapshot). It refuses otherwise instead of producing a different story.

Automation (A21, ADR-032). The stack runs with AUTOMATION_ENABLED=true, so the story follows what `system:automation`
does: the case is acknowledged and assigned to the registered provider's default owner when it is created (the owner
still accepts); pre-approved template messages go out at the points config/outreach_policy.yaml names; a rejected or
bounced referral is re-requested from the next provider in config/providers.yaml, and only an exhausted list reaches a
coordinator; the evidence scan finds the colonoscopy in HAPI, completes the confirmatory step and proposes the closure
(`closure_proposed`), which only a clinician turns into `close`. The scenario calls the sweep, the automation scan and
the evidence scan itself right after every event that can make something due, so the log does not depend on when the
scheduler container's own loop (which runs the same scans every few seconds) happens to fire. Two rules keep that
exact: a scan always follows the sweep it depends on, and a clock move never makes a sweep event and an automation
event due at the same moment (the 2-working-day reminder gets its own move before the SLA breach).

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
COORDINATOR = ("coordinator", "Maryam Al Suwaidi", None)
SUPERVISOR = ("supervisor", "Dr. Noura Al Hammadi", None)
# The owner is whoever the automation assigned: the default owner of the provider the patient is registered with
# (config/providers.yaml `registration`, chosen from the patient id by simulator/create_cases.py). The case says which.
OWNERS = {
    "Dr. Layla Haddad": ("primary_care_owner", "Dr. Layla Haddad", "Riverside Primary Care"),
    "Dr. Huda Rahman": ("primary_care_owner", "Dr. Huda Rahman", "Marina Digestive Health"),
}
NO_CAPACITY = ("specialist_owner", "Dr. Rana Farouk", "Al Noor Endoscopy Centre")  # declines for capacity
SPECIALIST = ("specialist_owner", "Dr. Karim Nasser", "Corniche Gastro Unit")  # first in the reroute order; takes the case
AUTOMATION_ACTOR = "system:automation"
REROUTED_TO = "Corniche Gastro Unit"  # what the automation picks after Al Noor (config/providers.yaml reroute_order)
APPOINTMENT_RESCAN = timedelta(hours=6)  # the evidence scan does not re-ask HAPI about a "no" sooner (EVIDENCE_NEGATIVE_TTL)


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
    owner: tuple | None = None  # the login of the owner the automation assigned (set by the first step)

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
        """Supervisor moves the clock to exactly `target` (an exact hour), then the scheduler sweeps once and the
        automation scans (a scan always after the sweep it may depend on: a bounce is re-routed, a rung is reminded)."""
        now = self.sim_now()
        if target > now:
            hours = (target - now).total_seconds() / 3600
            assert hours == int(hours), f"clock move of {hours} h is not a whole number of hours"
            self.call(SUPERVISOR, "POST", "/clock/advance", {"hours": int(hours)})
        self.sweep()
        self.automation_scan()

    def sweep(self) -> dict:
        r = self.http.post(f"{API}/scheduler/sweep", headers={"Authorization": f"Bearer {SCHEDULER_TOKEN}"}, timeout=300)
        if r.status_code != 200:
            raise ScenarioError(f"scheduler sweep refused: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def _scan(self, route: str) -> dict:
        r = self.http.post(f"{API}/scheduler/{route}", headers={"Authorization": f"Bearer {SCHEDULER_TOKEN}"}, timeout=300)
        if r.status_code != 200:
            raise ScenarioError(f"{route} refused: HTTP {r.status_code} {r.text[:200]}")
        out = r.json()
        if not out.get("enabled"):
            raise ScenarioError(f"{route} answered {out}: the API runs without the automation (AUTOMATION_ENABLED=true is what the demo needs)")
        if out.get("errors"):
            raise ScenarioError(f"{route} reported errors: {out['errors']}")
        return out

    def automation_scan(self) -> dict:
        """One pass of the automation (intake, re-route, due pre-approved messages), as the scheduler container does every loop."""
        return self._scan("automation-scan")

    def evidence_scan(self) -> dict:
        """One pass of the evidence scan (asks HAPI for the colonoscopy of booked and completed cases)."""
        return self._scan("evidence-scan")

    def events(self, type_: str | None = None) -> list[dict]:
        evs = self.case()["events"]
        return [e for e in evs if type_ is None or e["type"] == type_]

    def expect_state(self, state: str) -> dict:
        got = self.case()
        if got["state"] != state:
            raise ScenarioError(f"expected state {state!r}, the case is in {got['state']!r}")
        return got

    # ---- the steps ------------------------------------------------------------------------
    def s_result_received(self):
        """The positive FIT of the named patient is in and the automation has taken the intake: the case is acknowledged and its owner, the registered provider's default owner, is named. Nobody has accepted yet."""
        c = self.expect_state("acknowledged")
        name = (c["owner"] or {}).get("display_name")
        if name not in OWNERS:
            raise ScenarioError(f"the automation assigned {c['owner']!r}, who is not in the demo cast {sorted(OWNERS)}")
        self.owner = OWNERS[name]

    def s_auto_intake(self):
        """The log shows who did the intake: `acknowledge` and `assign_ownership` were both written by system:automation (flagged auto), naming the owner and the provider the patient is registered with."""
        ack = next(iter(self.events("acknowledge")), None)
        assign = next(iter(self.events("assign_ownership")), None)
        assert ack and ack["actor"] == AUTOMATION_ACTOR and ack["payload"].get("auto") is True, ack
        assert assign and assign["actor"] == AUTOMATION_ACTOR and assign["payload"].get("auto") is True, assign
        assert assign["payload"]["provider"] == self.owner[2], assign["payload"]

    def s_owner_accepted(self):
        """The named owner accepts ownership: the case now has an accepted owner and a clock. The pre-approved result notice goes out on its own."""
        self.event(self.owner, "accept_ownership")
        self.expect_state("owner_accepted")
        self.automation_scan()
        sent = [e for e in self.events("message_sent") if e["payload"].get("auto")]
        assert sent and sent[-1]["actor"] == AUTOMATION_ACTOR, "the automation did not send the result notice"

    def s_patient_unreachable(self):
        """The owner calls the patient; nobody answers, so the patient never acknowledges."""
        self.event(self.owner, "contact_patient", {"channel": "phone"})
        self.expect_state("patient_contacted")
        self.automation_scan()

    def s_auto_reminder(self):
        """Two working days after the call the policy's first reminder is due: the clock passes it and the automation sends the pre-approved template (no draft, nobody to approve it). The SLA is not breached yet, so the sweep has nothing to add."""
        started = datetime.fromisoformat(self.case()["read_model"]["clock_started_at"])
        before = len(self.events("message_sent"))
        self.advance_to(whole_hour_after(self.calendar.add_working_days(started, 2)))
        sent = self.events("message_sent")
        assert len(sent) == before + 1 and sent[-1]["payload"].get("auto"), "the 2-working-day reminder was not sent"
        assert not self.case()["overdue"]

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
        draft = self.call(self.owner, "POST", f"/cases/{self.case_id}/drafts", {"purpose": "reminder", "channel": "sms", "language": "ar"})
        self.draft_id = draft["draft_id"]
        approved = self.call(self.owner, "POST", f"/drafts/{self.draft_id}/approve")
        assert approved["sent"] is True, approved

    def s_patient_acknowledged(self):
        """The patient opens the PWA and taps "I understand"."""
        self.event(("patient", self.patient_name, None), "patient_acknowledge", {"lang": "ar"}, patient_ref=self.patient_ref)
        self.expect_state("patient_acknowledged")
        self.automation_scan()

    def s_handoff_rejected(self):
        """The owner hands the case to Al Noor Endoscopy; they reject it for capacity. Ownership stays with the referrer."""
        self.event(self.owner, "handoff_requested", {"to_provider": NO_CAPACITY[2]}, name="handoff_requested", n=0)
        self.event(NO_CAPACITY, "handoff_rejected", {"reason": "capacity"})
        self.expect_state("referral_rejected")

    def s_auto_rerouted(self):
        """Nobody has to re-route it: for a capacity rejection the automation asks the next provider in config/providers.yaml (Corniche Gastro Unit), as `handoff_requested` from system:automation, and the request is pending."""
        self.automation_scan()
        pending = self.case()["read_model"]["pending_handoff"]
        assert pending and pending["to_provider"] == REROUTED_TO, pending
        auto = [e for e in self.events("handoff_requested") if e["actor"] == AUTOMATION_ACTOR]
        assert auto and auto[-1]["payload"].get("auto") is True, auto

    def s_handoff_bounced(self):
        """Corniche does not answer either: three working days later the scheduler bounces the request. With Al Noor declined, Corniche timed out and the owner's own provider skipped, the reroute list is used up (a registration with Riverside would try Marina Digestive Health once more first): the automation stops and flags the case `reroute_exhausted` for a coordinator."""
        for _ in range(4):  # one bounce per provider the automation asks; it stops when the list is used up
            pending = self.case()["read_model"]["pending_handoff"]
            if pending is None:
                break
            requested = datetime.fromisoformat(pending["requested_at"])
            self.advance_to(whole_hour_after(self.calendar.add_working_days(requested, 3)))
        c = self.case()
        last = c["read_model"]["last_handoff"]
        assert last and last.get("bounced") is True, last
        assert c["read_model"]["pending_handoff"] is None and c["read_model"]["review_reason"] == "reroute_exhausted", c["read_model"]["review_reason"]
        exhausted = self.events("review_requested")
        assert exhausted and exhausted[-1]["actor"] == AUTOMATION_ACTOR and exhausted[-1]["payload"]["reason"] == "reroute_exhausted"

    def s_reassigned(self):
        """The coordinator, the person the exhausted list reaches, re-routes the case back to the owner, who owns it again at `owner_accepted`."""
        owner = f"{self.owner[0]}:{self.user_id(self.owner)}"
        self.event(COORDINATOR, "reassign", {"owner": owner, "provider": self.owner[2]})
        self.expect_state("owner_accepted")
        self.automation_scan()

    def s_handoff_accepted(self):
        """The owner asks Corniche Gastro Unit again (the coordinator has phoned them); Dr. Karim Nasser accepts and becomes the owner."""
        self.event(self.owner, "handoff_requested", {"to_provider": SPECIALIST[2]}, name="handoff_requested", n=1)
        self.event(SPECIALIST, "handoff_accepted")
        c = self.case()
        assert c["owner"]["display_name"] == SPECIALIST[1], c["owner"]
        self.automation_scan()

    def s_patient_reacknowledged(self):
        """The new owner calls the patient, who acknowledges again (a re-routed referral goes back through contact)."""
        self.event(SPECIALIST, "contact_patient", {"channel": "phone"}, name="recontact")
        self.event(("patient", self.patient_name, None), "patient_acknowledge", {"lang": "ar"}, name="reack", patient_ref=self.patient_ref)
        self.expect_state("patient_acknowledged")
        self.automation_scan()

    def s_booked(self):
        """Dr. Nasser books the colonoscopy, 14 days out; the appointment confirmation goes to the patient from the pre-approved template."""
        when = (self.sim_now() + timedelta(days=14)).date()
        self.appointment_date = when.isoformat()
        self.event(SPECIALIST, "book_confirmatory", {"appointment_ref": "Appointment/demo-colonoscopy", "appointment_date": self.appointment_date})
        self.expect_state("confirmatory_booked")
        self.automation_scan()

    def s_colonoscopy_done(self):
        """The clock advances to the appointment day: the colonoscopy has taken place. The evidence scan looks in HAPI as soon as the appointment has begun and finds nothing yet, so there is no closure proposal."""
        day = datetime.fromisoformat(self.appointment_date).replace(tzinfo=UTC, hour=9)
        self.advance_to(day)
        self.evidence_scan()  # the first ask: a definite "no" is remembered for six simulated hours (which keeps the container's own scans out of the next steps)
        assert self.case()["read_model"]["closure_proposal"] is None

    def s_result_recorded(self):
        """The specialist records the colonoscopy: a Procedure and DiagnosticReport are created in HAPI, and the case is told the result exists."""
        out = self.call(SPECIALIST, "POST", f"/cases/{self.case_id}/confirmatory-result")
        self.evidence_ref = out["evidence_ref"]
        r = self.http.get(f"{FHIR}/{self.evidence_ref}")
        assert r.status_code == 200, f"{self.evidence_ref} is not in HAPI (HTTP {r.status_code})"
        self.event(SPECIALIST, "complete_confirmatory", {"result_ref": self.evidence_ref})
        self.expect_state("confirmatory_completed")

    def s_closure_proposed(self):
        """Six simulated hours on (the evidence scan does not re-ask about a case it found nothing for any sooner) the scan finds the Procedure and proposes the closure (`closure_proposed`, with the Procedure as evidence). Nothing is closed: the clinician reviews and signs."""
        self.advance_to(self.sim_now() + APPOINTMENT_RESCAN)
        self.evidence_scan()
        c = self.expect_state("confirmatory_completed")
        proposal = c["read_model"]["closure_proposal"]
        assert proposal and proposal["evidence_ref"] == self.evidence_ref, proposal
        proposed = self.events("closure_proposed")
        assert proposed and proposed[-1]["actor"] == AUTOMATION_ACTOR and proposed[-1]["payload"].get("auto") is True, proposed

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
    "result_received", "auto_intake", "owner_accepted", "patient_unreachable", "auto_reminder", "overdue",
    "escalation_supervisor", "escalation_coordinator", "escalation_accepted", "draft_sent",
    "patient_acknowledged", "handoff_rejected", "auto_rerouted", "handoff_bounced", "reassigned", "handoff_accepted",
    "patient_reacknowledged", "booked", "colonoscopy_done", "result_recorded", "closure_proposed",
    "clinician_review", "closed",
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
        if state != "acknowledged":
            print(f"scenario: the demo case is at {state!r}, not at 'acknowledged' (acknowledged and assigned by the automation, as a fresh case is): "
                  f"run `make demo-reset` first, and make sure the stack runs with AUTOMATION_ENABLED=true", file=sys.stderr)
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
