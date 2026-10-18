"""E9: staging the demo's second case. No stack needed: the API is an in-memory fake behind `httpx.MockTransport`, so
the case selection, the walk, the resume and the "already there" logic run in `make test-tools` (the live run is `make
demo-stage` against the real stack)."""

import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/demo, for `import scenario`

import scenario  # noqa: E402
import stage_second_case as stage  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
REAL_CLIENT = httpx.Client  # the tests replace httpx.Client (stage_second_case builds its own); the fake needs the real one
CASE = "11111111-2222-5333-8444-555555555555"
PATIENT = "Patient/777"
CLOCK = "2026-01-05T08:00:00+00:00"

ASSIGNED = "Dr. Layla Haddad"  # who the automation (A21) assigned: the default owner of the provider the patient is registered with

# event type -> (state it needs, state it leads to), the part of the pathway the staging walks. `handoff_requested` and
# `handoff_accepted` annotate the case (they change the owner, not the state), so they are handled apart.
TRANSITIONS = {
    "accept_ownership": ("acknowledged", "owner_accepted"),
    "contact_patient": ("owner_accepted", "patient_contacted"),
    "patient_acknowledge": ("patient_contacted", "patient_acknowledged"),
    "book_confirmatory": ("patient_acknowledged", "confirmatory_booked"),
    "complete_confirmatory": ("confirmatory_booked", "confirmatory_completed"),
}
EVENT_ORDER = ["accept_ownership", "handoff_requested", "handoff_accepted", "contact_patient", "patient_acknowledge",
               "book_confirmatory", "complete_confirmatory"]


class FakeApi:
    """Just enough of the API for the staging: dev login, health, one case that the automation has acknowledged and assigned,
    its events, the confirmatory-result route and the two scheduler scans."""

    def __init__(self, state="acknowledged", owner=ASSIGNED, clock=CLOCK, proposal=None, automation=True):
        self.state = state
        self.owner = owner  # display name of the owner, or None
        self.clock = clock
        self.proposal = proposal
        self.automation = automation
        self.pending: str | None = None  # provider a handoff was requested from
        self.events: list[dict] = []
        self.log: list[tuple[str, str]] = []  # (method, path) of every call
        self.fail_on: dict[str, int] = {}  # event type -> HTTP status to answer instead of applying it
        self.users: dict[str, str] = {}

    # ---- helpers the tests read
    @property
    def writes(self) -> list[tuple[str, str]]:
        """Writes to the case (events and the colonoscopy result); the scans and the login are not."""
        return [c for c in self.log if c[0] == "POST" and c[1] != "/auth/dev-login" and not c[1].startswith("/scheduler/")]

    def client(self) -> httpx.Client:
        return REAL_CLIENT(transport=httpx.MockTransport(self.handle), timeout=5)

    # ---- the fake
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.log.append((request.method, path))
        body = json.loads(request.content) if request.content else {}
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "sim_time": self.clock})
        if path == "/auth/dev-login":
            name = body["name"]
            self.users[name] = f"u-{len(self.users) + 1}"
            return httpx.Response(200, json={"token": f"{body['role']}|{name}", "user_id": self.users[name]})
        if path.startswith("/fhir/Patient/"):
            return httpx.Response(200, json={"name": [{"given": ["Reyna401", "Marie12"], "family": "Kuhlman484"}]})
        if path == "/fhir/metadata":
            return httpx.Response(200, json={})
        if path in ("/scheduler/automation-scan", "/scheduler/evidence-scan"):
            if not self.automation:
                return httpx.Response(200, json={"enabled": False})
            if path.endswith("evidence-scan") and self.state == "confirmatory_completed" and self.proposal is None:
                self.proposal = {"evidence_ref": f"Procedure/colonoscopy-{CASE}", "proposed_at": self.clock}
            return httpx.Response(200, json={"enabled": True, "errors": []})
        role, _, name = request.headers["authorization"].removeprefix("Bearer ").partition("|")
        if request.method == "GET" and path == f"/cases/{CASE}":
            owner = {"actor": f"x:{self.owner}", "display_name": self.owner, "role": "specialist_owner"} if self.owner else None
            return httpx.Response(200, json={"case_id": CASE, "state": self.state, "owner": owner, "events": self.events,
                                             "read_model": {"closure_proposal": self.proposal}})
        if request.method == "POST" and path == f"/cases/{CASE}/events":
            etype = body["type"]
            if etype in self.fail_on:
                return httpx.Response(self.fail_on[etype], json={"detail": f"scripted failure on {etype}"})
            if any(e["event_id"] == body["event_id"] for e in self.events):
                return httpx.Response(200, json={"state": self.state})  # an idempotent retry writes nothing
            if etype == "handoff_requested":
                if name != self.owner:
                    return httpx.Response(403, json={"detail": "only the owner may request a handoff"})
                self.pending = body["payload"]["to_provider"]
            elif etype == "handoff_accepted":
                if self.pending is None or name == self.owner:
                    return httpx.Response(403, json={"detail": "nothing to accept"})
                self.owner, self.pending = name, None
            else:
                need, after = TRANSITIONS[etype]
                if self.state != need:
                    return httpx.Response(409, json={"detail": f"event {etype!r} not allowed from state {self.state!r}"})
                if name != self.owner and etype != "patient_acknowledge":
                    return httpx.Response(403, json={"detail": f"only the owner ({self.owner}) may do {etype}"})
                self.state = after
            self.events.append({"event_id": body["event_id"], "type": etype, "role": role, "actor_name": name, "payload": body["payload"]})
            return httpx.Response(201, json={"state": self.state})
        if request.method == "POST" and path == f"/cases/{CASE}/confirmatory-result":
            if name != self.owner:
                return httpx.Response(403, json={"detail": "only the case owner"})
            return httpx.Response(200, json={"evidence_ref": f"Procedure/colonoscopy-{CASE}"})
        return httpx.Response(404, json={"detail": f"no route {request.method} {path}"})


@pytest.fixture(autouse=True)
def _urls(monkeypatch):
    monkeypatch.setattr(scenario, "API", "http://api.test")
    monkeypatch.setattr(scenario, "FHIR", "http://api.test/fhir")


def demo_for(fake: FakeApi) -> scenario.Demo:
    demo = scenario.Demo(fake.client())
    demo.case_id, demo.patient_ref, demo.patient_name = CASE, PATIENT, "Reyna Marie Kuhlman"
    return demo


def write_manifest(tmp_path, monkeypatch, n=3) -> list[dict]:
    rows = [{"patient_id": str(100 + i), "report_id": f"aaaa{i:04d}-0000-5000-8000-00000000000{i}"} for i in range(n)]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"results": rows}))
    monkeypatch.setattr(scenario, "MANIFEST", path)
    return rows


# ---- which case ---------------------------------------------------------------------------------------------


def test_the_second_case_is_row_one_of_the_manifest_and_never_the_named_patients(tmp_path, monkeypatch):
    rows = write_manifest(tmp_path, monkeypatch)
    case_id, patient_ref = stage.pick_second_case()
    assert patient_ref == "Patient/101"
    assert case_id == str(uuid.uuid5(scenario.CASE_NAMESPACE, f"DiagnosticReport/{rows[1]['report_id']}"))
    assert (case_id, patient_ref) != scenario.pick_case(), "scenario.py drives row 0: the two must never meet"
    assert scenario.pick_case() == scenario.pick_case(0) != scenario.pick_case(1) == (case_id, patient_ref)
    assert stage.pick_second_case() == (case_id, patient_ref), "deterministic"


def test_a_manifest_with_one_result_cannot_give_a_second_case(tmp_path, monkeypatch):
    write_manifest(tmp_path, monkeypatch, n=1)
    with pytest.raises(scenario.ScenarioError, match="no row 1"):
        stage.pick_second_case()


def test_a_manifest_that_repeats_a_result_is_refused(tmp_path, monkeypatch):
    row = {"patient_id": "100", "report_id": "aaaa0000-0000-5000-8000-000000000000"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"results": [row, row]}))
    monkeypatch.setattr(scenario, "MANIFEST", path)
    with pytest.raises(stage.StageError, match="repeats"):
        stage.pick_second_case()


# ---- what is left to do from a state --------------------------------------------------------------------------


def test_remaining_steps_from_every_state_on_the_path():
    assert stage.remaining_steps("acknowledged") == stage.STEPS  # the automation has taken the intake: the walk starts there
    assert stage.remaining_steps("owner_accepted") == stage.STEPS[1:] and stage.STEPS[1] == "hand_off"
    assert stage.remaining_steps("patient_contacted") == ["patient_acknowledge", "book_confirmatory", "record_result"]
    assert stage.remaining_steps("confirmatory_booked") == ["record_result"]
    assert stage.remaining_steps("confirmatory_completed") == []
    for past in ("clinician_reviewed", "closed"):
        assert stage.remaining_steps(past) == []
    # every state on the path has its first step, in pathway order
    assert list(stage.FIRST_STEP) == stage.PATH and sorted(stage.FIRST_STEP.values()) == list(stage.FIRST_STEP.values())


@pytest.mark.parametrize("state", ["referral_rejected", "reopened", "consent_withdrawn", "lost_contact", "nonsense"])
def test_a_state_off_the_main_path_is_refused_with_the_way_out(state):
    with pytest.raises(stage.StageError, match="make demo-reset"):
        stage.remaining_steps(state)


def test_a_case_the_automation_has_not_taken_in_is_refused_and_the_message_says_why():
    with pytest.raises(stage.StageError, match="AUTOMATION_ENABLED=true"):
        stage.remaining_steps("result_received")


# ---- the walk -------------------------------------------------------------------------------------------------


def test_staging_walks_the_real_steps_in_order_and_ends_owned_by_the_specialist():
    fake = FakeApi()
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["outcome"] == "staged" and result["state"] == "confirmatory_completed"
    assert result["owner"] == scenario.SPECIALIST[1] == "Dr. Karim Nasser"
    assert result["steps_run"] == stage.STEPS
    assert [e["type"] for e in fake.events] == EVENT_ORDER
    by = {e["type"]: e for e in fake.events}
    # nobody acknowledges: the automation did, and named the owner, who is the one that accepts and hands the case on
    assert "acknowledge" not in by
    assert by["accept_ownership"]["role"] == "primary_care_owner" and by["accept_ownership"]["actor_name"] == ASSIGNED
    assert by["handoff_requested"]["actor_name"] == ASSIGNED and by["handoff_requested"]["payload"] == {"to_provider": "Corniche Gastro Unit"}
    assert by["handoff_accepted"]["role"] == "specialist_owner" and by["handoff_accepted"]["actor_name"] == "Dr. Karim Nasser"
    assert by["contact_patient"]["actor_name"] == "Dr. Karim Nasser"
    assert by["patient_acknowledge"]["role"] == "patient" and by["patient_acknowledge"]["payload"] == {"lang": "ar"}
    assert by["book_confirmatory"]["payload"] == {"appointment_ref": "APPT-2026-0002", "appointment_date": "2026-01-19"}  # clock + 14 days
    assert by["complete_confirmatory"]["payload"] == {"result_ref": f"Procedure/colonoscopy-{CASE}"}
    assert ("POST", f"/cases/{CASE}/confirmatory-result") in fake.log


def test_the_staged_case_carries_the_closure_proposal_the_evidence_scan_makes():
    fake = FakeApi()
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert fake.proposal == {"evidence_ref": f"Procedure/colonoscopy-{CASE}", "proposed_at": CLOCK}
    assert result["closure_proposed"] is True
    scans = [p for _, p in fake.log if p.startswith("/scheduler/")]
    assert scans.count("/scheduler/evidence-scan") == 1 and scans[-1] == "/scheduler/evidence-scan"  # after the result, not before
    assert "/scheduler/automation-scan" in scans  # the pre-approved messages go out in the staging, not whenever the container looks


def test_the_staging_fails_loudly_when_the_evidence_scan_proposes_nothing():
    fake = FakeApi(state="confirmatory_booked", owner="Dr. Karim Nasser")
    fake.handle = _wrap_no_proposal(fake)
    with pytest.raises(stage.StageError, match="did not propose the closure"):
        stage.stage(demo_for(fake), "Reyna Marie Kuhlman")


def _wrap_no_proposal(fake: FakeApi):
    original = fake.handle

    def handle(request):
        if request.url.path == "/scheduler/evidence-scan":
            fake.log.append((request.method, request.url.path))
            return httpx.Response(200, json={"enabled": True, "errors": []})  # found nothing
        return original(request)

    return handle


def test_the_automation_being_off_is_reported_not_papered_over():
    fake = FakeApi(automation=False)
    with pytest.raises(stage.StageError, match="AUTOMATION_ENABLED=true"):
        stage.stage(demo_for(fake), "Reyna Marie Kuhlman")


def test_the_clock_is_never_touched_and_only_the_scans_are_called_besides_the_case():
    fake = FakeApi()
    stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    paths = {p for _, p in fake.log}
    assert not any(p.startswith("/clock") or p == "/scheduler/sweep" for p in paths), paths
    assert {p for _, p in fake.writes} == {f"/cases/{CASE}/events", f"/cases/{CASE}/confirmatory-result"}
    assert fake.clock == CLOCK


def test_event_ids_are_deterministic_uuid5s_of_the_case_and_the_step():
    a, b = FakeApi(), FakeApi()
    stage.stage(demo_for(a), "Reyna Marie Kuhlman")
    stage.stage(demo_for(b), "Reyna Marie Kuhlman")
    ids = [e["event_id"] for e in a.events]
    assert ids == [e["event_id"] for e in b.events]
    assert len(set(ids)) == len(ids) == len(EVENT_ORDER)
    assert all(uuid.UUID(i).version == 5 for i in ids)
    # and they cannot collide with the named patient's scenario steps (those are "demo|<step>|n")
    assert not set(ids) & {scenario.step_id(CASE, t, n) for t in EVENT_ORDER for n in range(3)}


def test_the_booking_date_follows_the_simulation_clock():
    fake = FakeApi(clock="2026-02-26T09:00:00+00:00")
    stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    booked = next(e for e in fake.events if e["type"] == "book_confirmatory")
    assert booked["payload"]["appointment_date"] == "2026-03-12"


def test_the_other_registration_owner_works_the_same_way():
    fake = FakeApi(owner="Dr. Huda Rahman")  # a patient registered with Marina Digestive Health
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["state"] == "confirmatory_completed" and result["owner"] == "Dr. Karim Nasser"
    assert fake.events[0]["actor_name"] == "Dr. Huda Rahman"


def test_an_owner_outside_the_demo_cast_is_an_error_naming_the_cast():
    fake = FakeApi(owner="Dr. Stranger")
    with pytest.raises(stage.StageError, match="Dr. Stranger.*not in the demo cast"):
        stage.stage(demo_for(fake), "Reyna Marie Kuhlman")


# ---- safe to rerun --------------------------------------------------------------------------------------------


def test_a_second_run_changes_nothing_and_says_so():
    fake = FakeApi()
    stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    events, writes = list(fake.events), len(fake.writes)
    again = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert again["outcome"] == "already_staged" and again["steps_run"] == [] and again["owner"] == "Dr. Karim Nasser"
    assert fake.events == events and len(fake.writes) == writes
    assert "already at 'confirmatory_completed'" in stage.describe(again) and "nothing to do" in stage.describe(again)


@pytest.mark.parametrize("state", ["clinician_reviewed", "closed"])
def test_a_case_past_the_target_is_left_alone(state):
    fake = FakeApi(state=state, owner="Dr. Karim Nasser")
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["outcome"] == "past_target" and result["state"] == state
    assert fake.writes == [] and fake.events == []
    assert "already past the target" in stage.describe(result)


def test_a_part_way_case_continues_from_its_state():
    fake = FakeApi(state="patient_contacted", owner="Dr. Karim Nasser")
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["outcome"] == "staged" and result["steps_run"] == ["patient_acknowledge", "book_confirmatory", "record_result"]
    assert [e["type"] for e in fake.events] == ["patient_acknowledge", "book_confirmatory", "complete_confirmatory"]


def test_a_case_already_with_the_specialist_is_not_handed_over_again():
    fake = FakeApi(state="owner_accepted", owner="Dr. Karim Nasser")
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["state"] == "confirmatory_completed"
    assert [e["type"] for e in fake.events] == ["contact_patient", "patient_acknowledge", "book_confirmatory", "complete_confirmatory"]


def test_recording_the_result_twice_is_harmless():
    """An earlier run died after the Procedure was written but before complete_confirmatory: the rerun redoes both."""
    fake = FakeApi(state="confirmatory_booked", owner="Dr. Karim Nasser")
    demo = demo_for(fake)
    demo.call(scenario.SPECIALIST, "POST", f"/cases/{CASE}/confirmatory-result")  # the first, interrupted run
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["state"] == "confirmatory_completed" and [e["type"] for e in fake.events] == ["complete_confirmatory"]


def test_a_case_at_the_target_without_a_proposal_gets_one_without_a_new_event():
    fake = FakeApi(state="confirmatory_completed", owner="Dr. Karim Nasser")
    result = stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert result["outcome"] == "already_staged" and fake.proposal is not None and fake.writes == []


def test_at_the_target_with_another_owner_is_an_error_not_a_success():
    fake = FakeApi(state="confirmatory_completed", owner="Dr. Someone Else")
    with pytest.raises(stage.StageError, match="owner is 'Dr. Someone Else'.*make demo-reset"):
        stage.stage(demo_for(fake), "Reyna Marie Kuhlman")


def test_an_unstageable_state_writes_nothing():
    fake = FakeApi(state="referral_rejected")
    with pytest.raises(stage.StageError):
        stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert fake.writes == []


def test_an_api_refusal_names_the_step_and_the_status():
    fake = FakeApi()
    fake.fail_on["book_confirmatory"] = 422
    with pytest.raises(stage.StageError, match=r"step 'book_confirmatory' failed \(steps done before it: accept_ownership, hand_off, contact_patient, patient_acknowledge.*HTTP 422"):
        stage.stage(demo_for(fake), "Reyna Marie Kuhlman")
    assert fake.state == "patient_acknowledged"  # it got that far and stopped


# ---- the command line ------------------------------------------------------------------------------------------


@pytest.fixture
def cli(tmp_path, monkeypatch):
    write_manifest(tmp_path, monkeypatch)
    # the fake serves one case id: make the manifest's second row produce it
    monkeypatch.setattr(stage, "pick_second_case", lambda: (CASE, PATIENT))
    monkeypatch.setattr(scenario, "wait_for_hapi", lambda http, *a, **k: True)

    def run(fake: FakeApi, *args) -> int:
        monkeypatch.setattr(stage.httpx, "Client", lambda **kw: fake.client())
        return stage.main(list(args))

    return run


def test_main_stages_then_reports_already_there(cli, capsys):
    fake = FakeApi()
    assert cli(fake) == 0
    out = capsys.readouterr().out
    assert "staged at 'confirmatory_completed', owned by Dr. Karim Nasser" in out and PATIENT in out and CASE in out
    assert cli(fake) == 0
    assert "already at 'confirmatory_completed'" in capsys.readouterr().out
    assert len(fake.events) == len(EVENT_ORDER)


def test_main_json_output(cli, capsys):
    assert cli(FakeApi(), "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "staged" and out["case_id"] == CASE and out["state"] == "confirmatory_completed"


def test_main_exits_1_with_a_clear_message_when_the_staging_fails(cli, capsys):
    fake = FakeApi()
    fake.fail_on["accept_ownership"] = 403
    assert cli(fake) == 1
    err = capsys.readouterr().err
    assert "FAILED" in err and CASE in err and "HTTP 403" in err
    assert "step 'accept_ownership' failed (steps done before it: none; rerun to continue)" in err


def test_main_exits_1_when_the_case_cannot_be_staged(cli, capsys):
    assert cli(FakeApi(state="referral_rejected")) == 1
    assert "off the main path" in capsys.readouterr().err


def test_main_exits_2_when_the_api_is_down(cli, capsys, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(stage.httpx, "Client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(refuse)))
    assert stage.main([]) == 2
    err = capsys.readouterr().err
    assert "not reachable" in err and "make up" in err


def test_main_exits_2_when_hapi_is_not_ready(cli, capsys, monkeypatch):
    monkeypatch.setattr(scenario, "wait_for_hapi", lambda http, *a, **k: False)
    fake = FakeApi()
    assert cli(fake) == 2
    assert "HAPI" in capsys.readouterr().err and fake.writes == []


def test_main_warns_but_continues_when_the_clock_has_moved(cli, capsys):
    fake = FakeApi(clock="2026-01-14T09:00:00+00:00")
    assert cli(fake) == 0
    assert "clock is at 2026-01-14" in capsys.readouterr().err


def test_main_show_needs_no_api(tmp_path, monkeypatch, capsys):
    rows = write_manifest(tmp_path, monkeypatch)
    monkeypatch.setattr(stage.httpx, "Client", lambda **kw: pytest.fail("--show must not call the API"))
    assert stage.main(["--show"]) == 0
    out = capsys.readouterr().out
    case_id = str(uuid.uuid5(scenario.CASE_NAMESPACE, f"DiagnosticReport/{rows[1]['report_id']}"))
    assert "Patient/101" in out and case_id in out and case_id[:8] in out and "Dr. Karim Nasser" in out


def test_main_exits_2_when_there_is_no_second_case(tmp_path, monkeypatch, capsys):
    write_manifest(tmp_path, monkeypatch, n=1)
    assert stage.main(["--show"]) == 2
    assert "cannot pick the second case" in capsys.readouterr().err


# ---- the wiring: Makefile, docs, scenario ----------------------------------------------------------------------


def test_the_makefile_stages_before_it_saves_the_snapshot():
    makefile = (ROOT / "Makefile").read_text()
    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
    assert "demo-stage" in phony.split() and "demo-snapshot" in phony.split()
    assert re.search(r"^demo-snapshot: demo-stage\n\ttools/demo/snapshot\.sh save$", makefile, re.M), "demo-snapshot must run demo-stage first"
    assert re.search(r"^demo-stage:\n\t\$\(PY\) tools/demo/stage_second_case\.py$", makefile, re.M)


def test_the_docs_describe_the_staging():
    readme = (ROOT / "tools" / "demo" / "README.md").read_text()
    assert "make demo-stage" in readme and "## The second case" in readme and "stage_second_case.py --show" in readme
    script = (ROOT / "docs" / "submission" / "demo-script.md").read_text()
    before = script[script.index("## Before going on stage"):script.index("## The 7 minutes")]
    assert "make demo-stage && make demo-snapshot" in before and "--show" in before
    assert "Do not take the snapshot after the staging" not in script, "the snapshot now INCLUDES the staged case"


def test_the_scenario_still_refuses_unless_the_clock_is_at_its_start_which_staging_never_moves():
    # the staging writes with the clock where it is, so the scenario's start check passes after a snapshot taken with it
    assert scenario.SIM_START == datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    assert datetime.fromisoformat(CLOCK) == scenario.SIM_START
