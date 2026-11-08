"""The hosted demo's staging (simulator/stage_demo_state.py): API calls only, idempotent, and it respects the product's rules.

It is exercised against a small in-memory fake of the API (httpx.MockTransport), so the test needs no stack. The fake knows just
enough of the engine to answer the staging's calls: a case per id, a state per event type, the clock, and the sweep that the
scheduler container would run after a clock move."""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "simulator"))
sys.path.insert(0, str(REPO / "api"))

import stage_demo_state as st  # noqa: E402
from app import policy  # noqa: E402  (the API's own role policy: the fake enforces it, so a role the real API refuses fails here too)

START = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
TRANSITIONS = {  # event type -> (from, to)
    "accept_ownership": ("acknowledged", "owner_accepted"), "contact_patient": ("owner_accepted", "patient_contacted"),
    "patient_acknowledge": ("patient_contacted", "patient_acknowledged"), "book_confirmatory": ("patient_acknowledged", "confirmatory_booked"),
    "complete_confirmatory": ("confirmatory_booked", "confirmatory_completed"), "clinician_review": ("confirmatory_completed", "clinician_reviewed"),
    "close": ("clinician_reviewed", "closed"),
}
ROWS = [{"report_id": f"r{i}", "patient_id": f"p{i}"} for i in range(10)]
BIG_ROWS = [{"report_id": f"r{i}", "patient_id": f"p{i}"} for i in range(26)]  # the size of the hosted cohort


class FakeApi:
    def __init__(self, other_cases: int = 0) -> None:
        self.now = START
        self.cases: dict[str, dict] = {}
        self.seen: set[str] = set()
        self.log: list[tuple[str, str]] = []
        self.drafts: dict[str, list[dict]] = {}
        self.agency_runs = 0
        self.on_demand_scan_only = False  # the scheduler's own loop is not running: a scan happens only when the stager asks
        self.scan_posts = 0
        for i in range(other_cases):
            self._new(str(uuid.uuid4()))

    def _new(self, cid: str, registered: str | None = None) -> dict:
        owner = "Dr. Huda Rahman" if registered == "Marina Digestive Health" else "Dr. Layla Haddad"  # config/providers.yaml
        c = {"case_id": cid, "state": "acknowledged", "booked_for": None, "result_recorded": False, "pending_handoff": False, "owner": {"display_name": owner, "role": "primary_care_owner"},
             "due_at": (self.now + timedelta(days=1)).isoformat(), "overdue": False, "escalation_level": 0,
             "read_model": {"escalation_accepted": False}, "events": [], "priority": None}
        self.cases[cid] = c
        return c

    def sweep(self) -> None:
        """What the scheduler container does: past due -> overdue + rung 1; each further 24 h -> the next rung."""
        for c in self.cases.values():
            if c["state"] == "closed":
                continue
            late = (self.now - datetime.fromisoformat(c["due_at"])).total_seconds() / 86400
            if late > 0:
                c["overdue"] = True
                c["escalation_level"] = 3 if late >= 7 else 2 if late >= 3 else 1

    def refuse(self, c: dict, role: str, name: str, allowed, what: str) -> httpx.Response | None:
        """The API's role policy and owner scope (api/app/policy.py: EVENT_POLICY / ROUTE_POLICY, authorize_owner_scope)."""
        if role not in allowed:
            return httpx.Response(403, json={"detail": f"role {role!r} may not emit {what!r}"})
        owner_ok = c["owner"]["display_name"] == name or (what == "handoff_accepted" and c["pending_handoff"])
        if role in policy.OWNER_SCOPED and not owner_ok and what != "accept_ownership":
            return httpx.Response(403, json={"detail": f"{role} {name!r} may only act on cases they own"})
        return None

    def scan(self, c: dict) -> None:
        """automation.scan_evidence: a booked case whose appointment has begun and whose Procedure is in HAPI is completed by the
        automation (not by a person) and gets a closure proposal."""
        if c["state"] == "confirmatory_booked" and c["result_recorded"] and datetime.fromisoformat(c["booked_for"]).date() <= self.now.date():
            c["state"] = "confirmatory_completed"
            c["events"].append("complete_confirmatory")
            c["events"].append("closure_proposed")

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        self.log.append((method, path))
        if path == "/auth/dev-login":
            return httpx.Response(200, json={"token": f"t-{body['role']}-{body['name']}", "user_id": f"u-{body['name'].replace(' ', '')}"})
        if path == "/scheduler/evidence-scan":
            self.scan_posts += 1
            if request.headers.get("authorization") != "Bearer automation-token":
                return httpx.Response(401, json={"detail": "bad token"})
            for c in self.cases.values():
                self.scan(c)
            return httpx.Response(200, json={"asked": 1})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "sim_time": self.now.isoformat()})
        if path == "/clock/advance":
            self.now += timedelta(hours=body["hours"])
            self.sweep()
            return httpx.Response(200, json={"sim_time": self.now.isoformat()})
        if path == "/agency/run":
            assert request.headers["authorization"] == "Bearer t-supervisor-Dr. Noura Al Hammadi", "the supervisor starts the run"
            self.agency_runs += 1
            n = sum(1 for c in self.cases.values() if c["state"] != "closed")
            return httpx.Response(200, json={"run_id": "8f1c2d3e-0000", "status": "done", "created": self.agency_runs == 1,
                                             "counts": {"total": n, "by_status": {"done": n - 1, "needs_human": 1, "failed": 0}}})
        if path == "/cases" and method == "POST":
            if body["case_id"] in self.cases:
                return httpx.Response(409, text="CaseAlreadyExists")
            self._new(body["case_id"], body.get("registered_provider"))
            return httpx.Response(201, json={})
        if path == "/cases" and method == "GET":
            return httpx.Response(200, json=list(self.cases.values())[: int(request.url.params.get("limit", 100))])
        m = re.fullmatch(r"/cases/([^/]+)(?:/(events|confirmatory-result|drafts))?", path)
        assert m, f"unexpected route {method} {path}"
        cid, sub = m.group(1), m.group(2)
        c = self.cases[cid]
        role, name = request.headers["authorization"].removeprefix("Bearer t-").split("-", 1)
        if sub is None:
            if not self.on_demand_scan_only:
                self.scan(c)  # the scheduler container's evidence scan runs between the stager's calls
            return httpx.Response(200, json=c)
        if sub == "confirmatory-result":
            denied = self.refuse(c, role, name, policy.ROUTE_POLICY[("POST", "/cases/{case_id}/confirmatory-result")], "confirmatory-result")
            if denied:
                return denied
            if c["state"] != "confirmatory_booked":
                return httpx.Response(409, text="no colonoscopy to record in this state")
            c["result_recorded"] = True
            return httpx.Response(201, json={"evidence_ref": f"Procedure/colonoscopy-{cid}"})
        if sub == "drafts":
            if method == "GET":
                return httpx.Response(200, json=self.drafts.get(cid, []))
            d = {"draft_id": str(uuid.uuid4()), "provider": "anthropic", "fallback_used": False, "approved_at": None, "rejected_at": None, **body}
            self.drafts.setdefault(cid, []).append(d)
            return httpx.Response(201, json=d)
        # events
        if body["event_id"] in self.seen:
            return httpx.Response(200, json={"duplicate": True})
        self.seen.add(body["event_id"])
        t = body["type"]
        denied = self.refuse(c, role, name, policy.EVENT_POLICY[t], t)
        if denied:
            self.seen.discard(body["event_id"])
            return denied
        if t == "handoff_requested":
            c["pending_handoff"] = True
        elif t == "handoff_accepted":
            c["pending_handoff"] = False
            c["owner"] = {"display_name": "Dr. Karim Nasser", "role": "specialist_owner"}
        elif t == "escalation_accepted":
            c["read_model"]["escalation_accepted"] = True
        elif t == "callback_requested":
            assert c["state"] == "owner_accepted", "a call-back is a request the coordinator can answer only where contact is allowed"
        else:
            frm, to = TRANSITIONS[t]
            assert c["state"] == frm, f"{t} in {c['state']}"
            c["state"] = to
            if t == "book_confirmatory":
                c["booked_for"] = body["payload"]["appointment_date"]
        c["events"].append(t)
        return httpx.Response(201, json={})


def run(fake: FakeApi, phase: str, rows=ROWS) -> st.Stager:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    s = st.Stager(client, "http://api", rows, sleep=lambda _s: None)
    if phase in ("overdue", "all"):
        s.stage_overdue()
    if phase in ("rest", "all"):  # the order main() runs them in
        s.stage_draft()
        s.stage_signoff()
        s.stage_closed()
        s.pool = s.stage_tour_pool()
        s.stage_agency()
    return s


def signoff_row() -> int:
    return st.Stager(httpx.Client(transport=httpx.MockTransport(FakeApi().handler)), "http://api", ROWS).signoff_row()


def by_row(fake: FakeApi, row: int, rows=ROWS) -> dict:
    return fake.cases[st.case_id_of(rows[row])]


def test_the_rows_are_distinct_and_leave_the_local_demo_rows_alone():
    rows = {st.ROW_OVERDUE, signoff_row(), st.ROW_CLOSED, st.ROW_DRAFT}
    assert len(rows) == 4 and not rows & {0, 1}  # row 0 is the named patient of scenario.py, row 1 the local second case


def test_steps_to_run_continue_from_the_current_state_and_stop_at_the_target():
    assert st.steps_to_run("acknowledged", "closed") == st.STEPS
    assert st.steps_to_run("confirmatory_booked", "confirmatory_completed") == ["record_result"]
    assert st.steps_to_run("confirmatory_completed", "confirmatory_completed") == []
    assert st.steps_to_run("clinician_reviewed", "closed") == ["close"]
    assert st.steps_to_run("closed", "closed") == []
    with pytest.raises(st.StageError):
        st.steps_to_run("referral_rejected", "closed")


def test_the_clock_is_moved_to_one_hour_past_the_due_date():
    now = START
    assert st.hours_to_pass(now + timedelta(days=1), now) == 25
    assert st.hours_to_pass(now - timedelta(hours=3), now) == 1


def test_staging_produces_every_stage_the_demo_needs():
    fake = FakeApi()
    run(fake, "all")
    over = by_row(fake, st.ROW_OVERDUE)
    assert over["overdue"] and over["escalation_level"] == 2 and not over["read_model"]["escalation_accepted"]  # waiting for a person
    assert over["state"] == "acknowledged"  # untouched: nobody accepted it
    signoff = by_row(fake, signoff_row())
    assert signoff["state"] == "confirmatory_completed"
    # the PHYSICIAN login (Dr. Huda Rahman, the hosted "Physician" role) owns it and no hand-off happened: the sign-off panel is hers
    assert signoff["owner"]["display_name"] == "Dr. Huda Rahman" and "handoff_requested" not in signoff["events"]
    closed = by_row(fake, st.ROW_CLOSED)
    assert closed["state"] == "closed" and closed["events"][-2:] == ["clinician_review", "close"]
    draft_case = st.case_id_of(ROWS[st.ROW_DRAFT])
    pending = fake.drafts[draft_case]
    assert len(pending) == 1 and not pending[0]["approved_at"] and not pending[0]["rejected_at"]
    assert fake.now > START  # the clock moved forward only


def test_a_rerun_changes_nothing():
    fake = FakeApi()
    run(fake, "all")
    snapshot = {k: (c["state"], list(c["events"]), c["escalation_level"]) for k, c in fake.cases.items()}
    drafts = {k: len(v) for k, v in fake.drafts.items()}
    clock, calls = fake.now, len(fake.log)
    run(fake, "all")
    assert {k: (c["state"], list(c["events"]), c["escalation_level"]) for k, c in fake.cases.items()} == snapshot
    assert {k: len(v) for k, v in fake.drafts.items()} == drafts
    assert fake.now == clock and not [r for r in fake.log[calls:] if r == ("POST", "/clock/advance")]
    assert not [r for r in fake.log[calls:] if r[0] == "POST" and r[1].endswith("/events")]  # no event was even re-posted


def test_it_will_not_move_the_clock_when_other_cases_would_be_pushed_overdue():
    fake = FakeApi(other_cases=3)
    s = run(fake, "overdue")
    assert "SKIPPED" in s.summary["overdue"] and fake.now == START
    assert ("POST", "/clock/advance") not in fake.log


def test_it_uses_the_api_only_and_never_approves_a_draft_or_touches_consent():
    fake = FakeApi()
    run(fake, "all")
    paths = {p for _, p in fake.log}
    assert not [p for p in paths if "approve" in p or "reject" in p or "consent" in p or "send" in p], paths
    assert not [e for c in fake.cases.values() for e in c["events"] if "consent" in e or "approv" in e]
    src = (REPO / "simulator/stage_demo_state.py").read_text()
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    for banned in ("psycopg", "sqlalchemy", "sqlite3", "DATABASE_URL", "INSERT INTO", "docker"):
        assert banned not in code, banned


def test_the_draft_is_requested_by_a_person_login_and_waits_for_the_owner():
    fake = FakeApi()
    run(fake, "rest")
    d = fake.drafts[st.case_id_of(ROWS[st.ROW_DRAFT])][0]
    assert (d["purpose"], d["channel"], d["language"]) == ("result_notification", "sms", "ar")
    assert by_row(fake, st.ROW_DRAFT)["state"] == "owner_accepted"  # the owner took the case; the draft awaits their decision


def test_the_hosted_seed_runs_the_phases_in_the_right_places():
    hosted = (REPO / "deploy/gcp/vm/seed-on-vm.sh").read_text()
    cmd = hosted[hosted.index("python simulator/load_cohort.py"):hosted.rindex("tools/demo/snapshot.sh save")]
    order = ["generate_results.py", "stage_demo_state.py --phase overdue", "create_cases.py", "create_chronic_cases.py", "stage_demo_state.py --phase rest"]
    assert [cmd.index(x) for x in order] == sorted(cmd.index(x) for x in order)
    assert "stage_demo_state" not in (REPO / "Makefile").read_text().split("\ndemo-seed:\n", 1)[1].split("\n\n", 1)[0]  # the local video demo is untouched


def test_the_sign_off_case_belongs_to_the_physician_login_not_to_the_specialist():
    fake = FakeApi()
    run(fake, "rest")
    case = by_row(fake, signoff_row())
    assert case["owner"]["display_name"] == st.PHYSICIAN[1] == "Dr. Huda Rahman"
    assert case["state"] == "confirmatory_completed" and "complete_confirmatory" in case["events"]
    # it is a different case from the one the specialist closes
    assert by_row(fake, st.ROW_CLOSED)["owner"]["display_name"] == "Dr. Karim Nasser"


def test_no_physician_row_in_the_manifest_is_an_error_not_a_silent_specialist_case():
    few = ROWS[:8]
    s = st.Stager(httpx.Client(transport=httpx.MockTransport(FakeApi().handler)), "http://api", few)
    with pytest.raises(st.StageError):
        s.signoff_row()


def test_the_sign_off_staging_obeys_the_real_role_policy():
    """Regression (PR #67): the stager had the primary-care owner post `complete_confirmatory`, which EVENT_POLICY gives to the
    specialist only; the hosted seed died on the 403. The fake now enforces the API's own policy, so the same slip fails here."""
    assert policy.EVENT_POLICY["complete_confirmatory"] == frozenset({policy.SPEC})  # the premise: not a primary-care step
    fake = FakeApi()
    run(fake, "rest")  # every event of every stage went through policy.EVENT_POLICY / ROUTE_POLICY and the owner scope
    case = by_row(fake, signoff_row())
    assert case["owner"]["display_name"] == st.PHYSICIAN[1]  # still hers: nobody was handed the case
    assert case["events"].count("complete_confirmatory") == 1 and case["events"][-1] == "closure_proposed"  # completed by the scan
    assert "handoff_requested" not in case["events"]


def test_the_fake_refuses_what_the_policy_refuses():
    fake = FakeApi()
    s = run(fake, "overdue")
    case_id, ref, _ = s.ensure_case(signoff_row())
    s.walk(case_id, ref, "confirmatory_completed", via_specialist=False)
    owner = s.assigned_owner(case_id)
    assert owner[0] == "primary_care_owner"
    with pytest.raises(st.StageError, match="403"):
        s.event(owner, case_id, "complete_confirmatory", {"result_ref": "Procedure/x"})  # the owner may not
    with pytest.raises(st.StageError, match="403"):
        s.event(st.SPECIALIST, case_id, "complete_confirmatory", {"result_ref": "Procedure/x"})  # nor may a specialist who is not the owner


def test_a_sign_off_case_the_scan_never_completes_is_an_error():
    fake = FakeApi()
    fake.scan = lambda c: None  # the scheduler container is not running
    with pytest.raises(st.StageError, match="evidence scan"):
        run(fake, "rest")


# ---- the tour pool and the agency run ------------------------------------------------------------------------------------

def test_the_tour_pool_is_staged_from_unused_rows_and_leaves_enough_untouched_cases():
    fake = FakeApi()
    s = run(fake, "rest", BIG_ROWS)
    pool = s.pool
    assert {k: len(v) for k, v in pool.items()} == st.TOUR_POOL  # 2 call-backs, 4 reached by phone, 3 with the result recorded
    used = {0, 1, st.ROW_OVERDUE, st.ROW_CLOSED, st.ROW_DRAFT, s.signoff_row()}
    rows = [r for v in pool.values() for r in v]
    assert len(rows) == len(set(rows)) == 9 and not set(rows) & used, "no row is staged twice or taken from the other stages"
    untouched = [i for i in range(len(BIG_ROWS)) if i not in used | set(rows)]
    assert len(untouched) >= st.MIN_UNTOUCHED
    for r in pool["callback"]:
        c = by_row(fake, r, BIG_ROWS)
        assert c["state"] == "owner_accepted" and c["events"][-1] == "callback_requested"
    for r in pool["contacted"]:
        assert by_row(fake, r, BIG_ROWS)["state"] == "patient_contacted"
    for r in pool["completed"]:
        assert by_row(fake, r, BIG_ROWS)["state"] == "confirmatory_completed"
    assert not [r for r in untouched if st.case_id_of(BIG_ROWS[r]) in fake.cases], "the stager creates and touches none of the rest"


def test_a_small_cohort_shrinks_the_pool_before_it_touches_the_untouched_rows():
    pool = st.Stager(httpx.Client(transport=httpx.MockTransport(FakeApi().handler)), "http://api", ROWS).pool_rows()
    assert pool == {"callback": [], "contacted": [], "completed": []}  # 10 rows: nothing to spare
    mid = [{"report_id": f"r{i}", "patient_id": f"p{i}"} for i in range(16)]
    pool = st.Stager(httpx.Client(transport=httpx.MockTransport(FakeApi().handler)), "http://api", mid).pool_rows()
    assert sum(map(len, pool.values())) == 16 - 6 - st.MIN_UNTOUCHED and len(pool["callback"]) == st.TOUR_POOL["callback"]


def test_the_callback_is_the_patients_own_request_and_the_coordinator_may_answer_it():
    fake = FakeApi()
    s = run(fake, "rest", BIG_ROWS)
    assert "callback_requested" in policy.EVENT_POLICY and policy.PATIENT in policy.EVENT_POLICY["callback_requested"]
    assert policy.COORD in policy.EVENT_POLICY["contact_patient"], "the call-back the coordinator records is an event the policy gives them"
    assert s.summary["callbacks"].startswith("2 case(s)")


def test_one_agency_run_is_started_last_by_the_supervisor_and_a_rerun_adds_nothing():
    fake = FakeApi()
    s = run(fake, "rest", BIG_ROWS)
    runs = [i for i, r in enumerate(fake.log) if r == ("POST", "/agency/run")]
    writes = [i for i, r in enumerate(fake.log) if r[0] == "POST" and (r[1].endswith("/events") or r[1].endswith("/drafts"))]
    assert len(runs) == 1 and runs[0] > max(writes), "the agents read the cases after every stage has run"
    assert "task(s)" in s.summary["agency"] and "waiting for a person" in s.summary["agency"]
    events = {k: list(c["events"]) for k, c in fake.cases.items()}
    run(fake, "rest", BIG_ROWS)
    assert {k: list(c["events"]) for k, c in fake.cases.items()} == events, "a rerun posts no new case event"


def test_an_agency_run_with_no_work_fails_the_stage_instead_of_snapshotting_an_empty_page():
    fake = FakeApi()
    s = run(fake, "rest", BIG_ROWS)
    s.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "done", "counts": {"total": 0}}) if r.url.path == "/agency/run" else fake.handler(r)))
    with pytest.raises(st.StageError):
        s.stage_agency()


def run_signoff(fake: FakeApi, token: str | None, sleeps: list) -> None:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    s = st.Stager(client, "http://api", ROWS, sleep=sleeps.append, scan_token=token)
    s.stage_signoff()


def test_the_sign_off_step_asks_for_the_evidence_scan_once_instead_of_waiting_for_the_scheduler():
    fake, sleeps = FakeApi(), []
    fake.on_demand_scan_only = True
    run_signoff(fake, "automation-token", sleeps)
    assert fake.scan_posts == 1
    assert by_row(fake, signoff_row())["state"] == "confirmatory_completed"
    assert sleeps == []  # the scan had already run when the first poll looked: nothing to wait for


def test_without_the_token_it_only_polls_and_gives_up_after_about_thirty_seconds():
    fake, sleeps = FakeApi(), []
    fake.on_demand_scan_only = True
    with pytest.raises(st.StageError, match="evidence scan"):
        run_signoff(fake, None, sleeps)
    assert fake.scan_posts == 0
    assert 28 <= sum(sleeps) <= 32 and st.EVIDENCE_WAIT_SECONDS == 30


def test_a_refused_scan_request_does_not_stop_the_stager_when_the_scheduler_does_the_scan():
    fake, sleeps = FakeApi(), []
    run_signoff(fake, "wrong-token", sleeps)  # 401 from the scan route: logged, the scheduler's loop (the fake's GET) completes it
    assert fake.scan_posts == 1 and by_row(fake, signoff_row())["state"] == "confirmatory_completed"
