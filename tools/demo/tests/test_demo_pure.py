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
    for earlier, later in [("acknowledged", "owner_accepted"), ("patient_unreachable", "overdue"), ("overdue", "escalation_supervisor"),
                           ("escalation_coordinator", "draft_sent"), ("draft_sent", "patient_acknowledged"),
                           ("handoff_rejected", "handoff_bounced"), ("handoff_bounced", "reassigned"), ("reassigned", "handoff_accepted"),
                           ("booked", "colonoscopy_done"), ("colonoscopy_done", "result_recorded"), ("result_recorded", "clinician_review"),
                           ("clinician_review", "closed")]:
        assert order.index(earlier) < order.index(later), (earlier, later)
