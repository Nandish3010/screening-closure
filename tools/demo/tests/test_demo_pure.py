"""The parts of the demo tooling that need no stack (they run in `make test-tools`, so in `make check`)."""

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/demo, for `import scenario` (no conftest: tests/ has its own)

import scenario  # noqa: E402

README = (Path(__file__).resolve().parents[1] / "README.md").read_text()


def test_step_ids_are_deterministic_and_distinct():
    case = str(uuid.uuid4())
    assert scenario.step_id(case, "close") == scenario.step_id(case, "close")
    assert scenario.step_id(case, "close") != scenario.step_id(case, "close", 1)
    assert scenario.step_id(case, "close") != scenario.step_id(str(uuid.uuid4()), "close")
    assert uuid.UUID(scenario.step_id(case, "close")).version == 5


def test_clock_moves_land_on_whole_hours():
    assert scenario.whole_hour_after(datetime(2026, 1, 7, 8, 0, tzinfo=UTC)) == datetime(2026, 1, 7, 9, 0, tzinfo=UTC)
    assert scenario.whole_hour_after(datetime(2026, 1, 7, 8, 30, 12, tzinfo=UTC)) == datetime(2026, 1, 7, 9, 0, tzinfo=UTC)


def test_event_log_hash_ignores_random_ids_but_nothing_else():
    def log(draft_id, note="x"):
        return [
            {"type": "message_approved", "at": "2026-01-20T08:00:00+00:00", "actor": "primary_care_owner:abc", "payload": {"draft_id": draft_id, "note": note}},
            {"type": "message_sent", "at": "2026-01-20T08:00:00+00:00", "actor": "system:notifier", "payload": {"notification_id": str(uuid.uuid4())}},
            # SEC-2: the outbox derives the notification id from the random draft id (a uuid5, still random per run)
            {"type": "message_sent", "at": "2026-01-20T08:00:00+00:00", "actor": "system:notifier",
             "payload": {"draft_id": draft_id, "notification_id": str(uuid.uuid5(uuid.NAMESPACE_URL, draft_id))}},
        ]

    a = scenario.event_log_hash(log(str(uuid.uuid4())))
    b = scenario.event_log_hash(log(str(uuid.uuid4())))
    assert a == b, "two runs with different draft and notification ids must hash the same"
    assert scenario.event_log_hash(log(str(uuid.uuid4()), note="y")) != a
    deterministic = str(uuid.uuid5(uuid.NAMESPACE_DNS, "e"))  # a uuid5 event id is data, it is kept
    assert scenario.event_log_hash(log(deterministic)) != a


def test_the_demo_case_is_the_first_positive_in_seed_order(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    rows = [{"patient_id": "111", "report_id": "aaaa1111-0000-5000-8000-000000000001"},
            {"patient_id": "222", "report_id": "bbbb2222-0000-5000-8000-000000000002"}]
    manifest.write_text(json.dumps({"results": rows}))
    monkeypatch.setattr(scenario, "MANIFEST", manifest)
    case_id, patient_ref = scenario.pick_case()
    assert patient_ref == "Patient/111"
    assert case_id == str(uuid.uuid5(scenario.CASE_NAMESPACE, f"DiagnosticReport/{rows[0]['report_id']}"))
    assert scenario.pick_case() == (case_id, patient_ref)


def test_the_display_name_is_the_synthea_name_without_its_numeric_suffixes():
    class Http:
        def get(self, url):
            class R:
                def raise_for_status(self): ...
                def json(self): return {"name": [{"given": ["Reyna401", "Marie12"], "family": "Kuhlman484"}]}
            return R()

    assert scenario.synthea_name(Http(), "Patient/1") == "Reyna Marie Kuhlman"


def test_every_step_is_implemented_documented_and_in_the_readme():
    for step in scenario.STEPS:
        method = getattr(scenario.Demo, f"s_{step}")
        assert (method.__doc__ or "").strip(), f"step {step} has no description"
        assert f"`{step}`" in README, f"step {step} is not in tools/demo/README.md"
    assert len(set(scenario.STEPS)) == len(scenario.STEPS)
    # no step method without an entry in STEPS
    implemented = {n[2:] for n in dir(scenario.Demo) if n.startswith("s_")}
    assert implemented == set(scenario.STEPS)


def test_the_story_follows_the_demo_script_order():
    order = scenario.STEPS
    for earlier, later in [("result_received", "auto_intake"), ("auto_intake", "owner_accepted"), ("patient_unreachable", "auto_reminder"),
                           ("auto_reminder", "overdue"), ("overdue", "escalation_supervisor"),
                           ("escalation_coordinator", "draft_sent"), ("draft_sent", "patient_acknowledged"),
                           ("handoff_rejected", "auto_rerouted"), ("auto_rerouted", "handoff_bounced"),
                           ("handoff_bounced", "reassigned"), ("reassigned", "handoff_accepted"),
                           ("booked", "colonoscopy_done"), ("colonoscopy_done", "result_recorded"),
                           ("result_recorded", "closure_proposed"), ("closure_proposed", "clinician_review"),
                           ("clinician_review", "closed")]:
        assert order.index(earlier) < order.index(later), (earlier, later)


def test_the_lab_no_longer_acknowledges_and_the_closure_proposal_follows_the_appointment_date():
    # A21: the automation acknowledges and assigns on creation, so there is no manual acknowledge step; and the evidence
    # scan only proposes the closure after the booked date (the clock is moved to it first), which is why the step that
    # expects `closure_proposed` comes after the one that moves the clock to the appointment day
    assert "acknowledged" not in scenario.STEPS and not hasattr(scenario, "LAB")
    assert scenario.STEPS.index("colonoscopy_done") < scenario.STEPS.index("result_recorded") < scenario.STEPS.index("closure_proposed")
    assert scenario.STEPS.index("closure_proposed") < scenario.STEPS.index("closed")
    assert "closure_proposed" in (scenario.Demo.s_closure_proposed.__doc__ or "")
    assert scenario.AUTOMATION_ACTOR == "system:automation"
    # whole-hour clock moves, as T-E9 needs
    assert scenario.APPOINTMENT_RESCAN.total_seconds() % 3600 == 0


def test_the_cast_and_the_reroute_follow_config_providers_yaml():
    import yaml

    cfg = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "providers.yaml").read_text())
    # every registration owner the automation can assign is a login the scenario knows, with the provider the config gives
    for provider, row in cfg["registration"].items():
        who = scenario.OWNERS[row["owner"]["name"]]
        assert (who[0], who[2]) == (row["owner"]["role"], provider)
    order = cfg["referral"]["reroute_order"]["gastroenterology"]
    # Al Noor declines for capacity (a reason the automation re-routes) and the next provider it asks is the specialist's
    assert "capacity" in cfg["referral"]["reroute_reasons"]
    assert order.index(scenario.NO_CAPACITY[2]) > order.index(scenario.REROUTED_TO) == 0
    assert scenario.SPECIALIST[2] == scenario.REROUTED_TO


def test_the_readme_has_the_current_hash_line_format():
    import re

    assert re.search(r"^EVENT LOG HASH [0-9a-f]{64}$", README, re.M)
    assert "AGENCY_EVERY_SWEEPS" in README and "make agency-run" in README


# ---- A22: the agency is off in the demo stack and runs on demand --------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]


def test_compose_keeps_the_scheduler_from_starting_agency_runs_and_the_automation_on():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "AGENCY_EVERY_SWEEPS: ${AGENCY_EVERY_SWEEPS:-0}" in compose
    assert "AUTOMATION_ENABLED: ${AUTOMATION_ENABLED:-true}" in compose
    # HAPI must not serve a cached "nothing yet" to the evidence scan
    assert 'hapi.fhir.reuse_cached_search_results_millis: "-1"' in compose


def test_make_agency_run_is_a_phony_target_that_runs_the_script():
    import re

    makefile = (ROOT / "Makefile").read_text()
    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
    assert "agency-run" in phony.split()
    assert re.search(r"^agency-run:\n\t\$\(PY\) tools/demo/agency_run\.py$", makefile, re.M)


def test_agency_run_posts_as_the_supervisor_and_prints_the_summary(monkeypatch, capsys):
    import agency_run
    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/auth/dev-login":
            assert json.loads(request.content)["role"] == "supervisor"
            return httpx.Response(200, json={"token": "tok", "user_id": "u"})
        return httpx.Response(200, json={"run_id": "r1", "status": "done", "sim_at": "2026-01-05T08:00:00+00:00", "created": True,
                                         "counts": {"total": 3, "by_status": {"done": 3, "failed": 0}}, "rationale": {"outreach": "outreach: 1 draft_message."}})

    real = httpx.Client
    monkeypatch.delenv("AGENCY_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(agency_run.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert agency_run.main() == 0
    assert seen == [("POST", "/auth/dev-login", None), ("POST", "/agency/run", "Bearer tok")]
    out = capsys.readouterr().out
    assert "run r1 done" in out and "3 task(s): 3 done" in out and "outreach: 1 draft_message." in out


def test_agency_run_uses_the_service_token_when_given_and_exits_2_when_the_api_is_down(monkeypatch, capsys):
    import agency_run
    import httpx

    real = httpx.Client
    seen = []

    def ok(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"run_id": "r", "status": "done", "counts": {}})

    monkeypatch.setenv("AGENCY_SERVICE_TOKEN", "svc")
    monkeypatch.setattr(agency_run.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(ok), **kw))
    assert agency_run.main() == 0 and seen == [("/agency/run", "Bearer svc")]

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(agency_run.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(down), **kw))
    assert agency_run.main() == 2
    assert "not reachable" in capsys.readouterr().err
