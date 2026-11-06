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

START = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
TRANSITIONS = {  # event type -> (from, to)
    "accept_ownership": ("acknowledged", "owner_accepted"), "contact_patient": ("owner_accepted", "patient_contacted"),
    "patient_acknowledge": ("patient_contacted", "patient_acknowledged"), "book_confirmatory": ("patient_acknowledged", "confirmatory_booked"),
    "complete_confirmatory": ("confirmatory_booked", "confirmatory_completed"), "clinician_review": ("confirmatory_completed", "clinician_reviewed"),
    "close": ("clinician_reviewed", "closed"),
}
ROWS = [{"report_id": f"r{i}", "patient_id": f"p{i}"} for i in range(10)]


class FakeApi:
    def __init__(self, other_cases: int = 0) -> None:
        self.now = START
        self.cases: dict[str, dict] = {}
        self.seen: set[str] = set()
        self.log: list[tuple[str, str]] = []
        self.drafts: dict[str, list[dict]] = {}
        for i in range(other_cases):
            self._new(str(uuid.uuid4()))

    def _new(self, cid: str) -> dict:
        c = {"case_id": cid, "state": "acknowledged", "owner": {"display_name": "Dr. Layla Haddad", "role": "primary_care_owner"},
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

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        self.log.append((method, path))
        if path == "/auth/dev-login":
            return httpx.Response(200, json={"token": f"t-{body['role']}-{body['name']}", "user_id": f"u-{body['name'].replace(' ', '')}"})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "sim_time": self.now.isoformat()})
        if path == "/clock/advance":
            self.now += timedelta(hours=body["hours"])
            self.sweep()
            return httpx.Response(200, json={"sim_time": self.now.isoformat()})
        if path == "/cases" and method == "POST":
            if body["case_id"] in self.cases:
                return httpx.Response(409, text="CaseAlreadyExists")
            self._new(body["case_id"])
            return httpx.Response(201, json={})
        if path == "/cases" and method == "GET":
            return httpx.Response(200, json=list(self.cases.values())[: int(request.url.params.get("limit", 100))])
        m = re.fullmatch(r"/cases/([^/]+)(?:/(events|confirmatory-result|drafts))?", path)
        assert m, f"unexpected route {method} {path}"
        cid, sub = m.group(1), m.group(2)
        c = self.cases[cid]
        if sub is None:
            return httpx.Response(200, json=c)
        if sub == "confirmatory-result":
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
        if t == "handoff_requested":
            pass
        elif t == "handoff_accepted":
            c["owner"] = {"display_name": "Dr. Karim Nasser", "role": "specialist_owner"}
        elif t == "escalation_accepted":
            c["read_model"]["escalation_accepted"] = True
        else:
            frm, to = TRANSITIONS[t]
            assert c["state"] == frm, f"{t} in {c['state']}"
            c["state"] = to
        c["events"].append(t)
        return httpx.Response(201, json={})


def run(fake: FakeApi, phase: str) -> st.Stager:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    s = st.Stager(client, "http://api", ROWS, sleep=lambda _s: None)
    if phase in ("overdue", "all"):
        s.stage_overdue()
    if phase in ("rest", "all"):
        s.stage_draft()
        s.stage_signoff()
        s.stage_closed()
    return s


def by_row(fake: FakeApi, row: int) -> dict:
    return fake.cases[st.case_id_of(ROWS[row])]


def test_the_rows_are_distinct_and_leave_the_local_demo_rows_alone():
    rows = {st.ROW_OVERDUE, st.ROW_SIGNOFF, st.ROW_CLOSED, st.ROW_DRAFT}
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
    assert by_row(fake, st.ROW_SIGNOFF)["state"] == "confirmatory_completed"
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
