"""The chronic-conditions demo seed (simulator/create_chronic_cases.py): deterministic, idempotent, and safe for the video.

The third pathway's demo cases are an opt-in extra of the demo seed. The existing video storyboard finds its cases BY STATE
(tools/video/record.ts `findCases`: the first case at `owner_accepted`, the first at `confirmatory_completed`), so the seed
must never put a chronic case in either state, and `make demo-seed` must not seed them unless asked."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "simulator"))
sys.path.insert(0, str(REPO / "api"))

import create_chronic_cases as seed  # noqa: E402

TODAY = date(2026, 10, 31)


def patient(pid, gender="female", born="1985-06-01"):
    return {"resource": {"resourceType": "Patient", "id": pid, "gender": gender, "birthDate": born}}


def test_it_picks_adults_of_any_gender_aged_30_to_70_outside_the_colorectal_manifest_in_id_order():
    entries = [patient("30"), patient("4", gender="male"), patient("12", gender="other"), patient("9", born="2015-01-01"),
               patient("7", born="1940-01-01"), patient("21", gender="unknown"), patient("5", born="not a date"), patient("8"),
               patient("6", born="2000-01-01")]
    assert seed.pick_patients(entries, exclude={"8"}, count=3, today=TODAY) == ["4", "12", "21"]
    assert seed.pick_patients(entries, exclude=set(), count=10, today=TODAY) == ["4", "8", "12", "21", "30"]
    assert (seed.MIN_AGE, seed.MAX_AGE) == (30, 70)
    assert seed.age_on("1985-06-01", TODAY) == 41 and seed.age_on("1985-12-31", TODAY) == 40
    assert seed.pick_patients([patient("1", born="1996-10-31"), patient("2", born="1996-11-01")], set(), 5, TODAY) == ["1"]


def test_case_ids_are_deterministic_and_separate_from_the_colorectal_and_cervical_cases_and_from_each_other():
    a, b = seed.case_id_for("42"), seed.case_id_for("43")
    assert a == seed.case_id_for("42") and a != b
    import create_cases
    import create_cervical_cases

    assert a != create_cases.case_id_for("42") and a != create_cervical_cases.case_id_for("42")
    assert len({seed.CASE_NAMESPACE, create_cases.CASE_NAMESPACE, create_cervical_cases.CASE_NAMESPACE}) == 3
    assert seed.event_id_for(a, "accept_ownership") != seed.event_id_for(a, "contact_patient")


def test_no_chronic_demo_case_is_ever_in_a_state_the_video_recorder_looks_for():
    recorder = (REPO / "tools/video/record.ts").read_text()
    looked_for = set(re.findall(r'r\.state === "([a-z_]+)"', recorder))
    assert looked_for == set(seed.RECORDER_STATES), "the recorder's states changed: update RECORDER_STATES and the seed's stages"
    assert not set(seed.STAGES) & looked_for
    plan = seed.plan([str(i) for i in range(12)])
    assert {p["stage"] for p in plan} == set(seed.STAGES)
    assert all(p["stage"] not in looked_for for p in plan)
    # the states the steps pass THROUGH are not final states of any case
    for item in plan:
        assert all(step in seed.FROM_STATE for step in item["steps"])


def test_the_default_demo_seed_does_not_create_chronic_cases_and_the_snapshot_flow_is_untouched():
    mk = (REPO / "Makefile").read_text()
    recipe = mk.split("\ndemo-seed:\n", 1)[1].split("\n\n", 1)[0]
    assert "create_chronic_cases" not in recipe and "demo-seed-chronic" in recipe and '$(DEMO_THIRD_PATHWAY)" = "1"' in recipe
    assert "demo-seed-chronic:\n\t$(PY) simulator/create_chronic_cases.py" in mk
    assert " demo-seed-chronic " in mk.splitlines()[0]
    snapshot = mk.split("\ndemo-snapshot:", 1)[1].split("\n\n", 1)[0]
    assert "chronic" not in snapshot and "chronic" not in mk.split("\ndemo-stage:", 1)[1].split("\n\n", 1)[0]
    storyboard = (REPO / "tools/video/storyboard.yaml").read_text()
    assert "chronic" not in storyboard.lower(), "the video storyboard is not changed by the third pathway"


def pack_priority():
    from app.pathway import load_pathway

    return load_pathway(REPO / "config/pathways/chronic_conditions.yaml").priority


def test_the_plan_cycles_four_distinct_result_sets_and_the_real_pack_gives_each_its_intended_tier():
    from app.priority import derive

    assert seed.PATHWAY == "chronic_conditions"
    config = pack_priority()
    assert [rs["tier"] for rs in seed.RESULT_SETS] == ["urgent", "soon", "routine", "routine"]
    assert len({rs["name"] for rs in seed.RESULT_SETS}) == 4
    assert len({json.dumps(rs["results"], sort_keys=True) for rs in seed.RESULT_SETS}) == 4
    expected_rules = ["bp_very_high", "hba1c_high", "hba1c_raised", "bmi_raised"]
    for rs, rule in zip(seed.RESULT_SETS, expected_rules):
        got = derive(config, rs["results"])
        assert (got.tier, got.rule) == (rs["tier"], rule), rs["name"]
    items = seed.plan([str(i) for i in range(8)])
    assert [it["result_set"] for it in items] == [rs["name"] for rs in seed.RESULT_SETS] * 2
    for it in items:
        assert derive(config, it["results"]).tier == it["tier"]
    assert {it["tier"] for it in items} == {"urgent", "soon", "routine"}


def test_no_clinical_value_appears_in_what_the_script_prints():
    values = {str(v) for rs in seed.RESULT_SETS for r in rs["results"] for v in r["values"].values()}
    items = seed.plan(["101", "102", "103", "104"])
    shown = json.dumps(seed.redacted(items))
    line = seed.summary_line(4, 0, {"acknowledged": 1, "patient_contacted": 2, "patient_acknowledged": 1},
                             {"urgent": 1, "soon": 1, "routine": 2})
    for text in (shown, line):
        assert '"results"' not in text and "systolic" not in text and "mmol" not in text and "kg_per_m2" not in text
        assert not any(re.search(rf"(?<![\w-]){v}(?![\w-])", text) for v in values), text
    assert line.startswith("SUMMARY pathway=chronic_conditions ") and '"urgent": 1' in line
    src = Path(seed.__file__).read_text()
    assert src.count("print(") == 2  # the redacted plan and the summary line, nothing else


class FakeApi:
    """The slice of the API the seeder uses: logins, POST /cases, GET /cases/{id}, POST events, with the state machine."""

    NEXT = {("acknowledged", "accept_ownership"): "owner_accepted", ("owner_accepted", "contact_patient"): "patient_contacted",
            ("patient_contacted", "patient_acknowledge"): "patient_acknowledged"}

    def __init__(self):
        self.cases, self.events, self.created_with = {}, [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/dev-login":
            body = json.loads(request.content)
            return httpx.Response(200, json={"token": f"{body['role']}|{body['name']}"})
        if path == "/cases" and request.method == "POST":
            body = json.loads(request.content)
            self.created_with.append(body)
            if body["case_id"] in self.cases:
                return httpx.Response(409, text="CaseAlreadyExists")
            self.cases[body["case_id"]] = {"state": "acknowledged", "owner": {"display_name": "Dr. Layla Haddad"}}
            return httpx.Response(201, json={})
        m = re.fullmatch(r"/cases/([0-9a-f-]+)(/events)?", path)
        if m and request.method == "GET":
            return httpx.Response(200, json={"case_id": m[1], **self.cases[m[1]]})
        if m and m[2] and request.method == "POST":
            body = json.loads(request.content)
            case = self.cases[m[1]]
            nxt = self.NEXT.get((case["state"], body["type"]))
            if nxt is None:
                return httpx.Response(409, text="InvalidTransition")
            case["state"] = nxt
            self.events.append((body["type"], request.headers["authorization"], body["event_id"]))
            return httpx.Response(201, json={})
        return httpx.Response(404)


def run_seed(api, patients):
    items = seed.plan(patients)
    with httpx.Client(transport=httpx.MockTransport(api.handler)) as client:
        s = seed.Seeder(client, "http://api")
        out = []
        for item in items:
            s.create(item, ["Marina Digestive Health", "Riverside Primary Care"])
            out.append(s.walk(item))
        return items, out


def test_the_seeder_creates_chronic_cases_walks_them_to_their_stages_and_a_rerun_changes_nothing():
    api = FakeApi()
    patients = ["101", "102", "103", "104"]
    items, states = run_seed(api, patients)
    assert states == list(seed.STAGES)
    assert all(b["pathway"] == "chronic_conditions" and b["case_id"] == i["case_id"] and b["results"] == i["results"] for b, i in zip(api.created_with, items))
    assert all(b.get("registered_provider") for b in api.created_with)
    assert all(b["results"] for b in api.created_with)
    assert not {c["state"] for c in api.cases.values()} & set(seed.RECORDER_STATES)
    n_events = len(api.events)
    assert n_events == 2 + 3 + 2 and {e[0] for e in api.events} == {"accept_ownership", "contact_patient", "patient_acknowledge"}
    again_items, again_states = run_seed(api, patients)
    assert again_states == states and len(api.events) == n_events, "a rerun writes no event twice"
    # the patient acknowledges as the patient, the rest as the case's owner
    by_type = {e[0]: e[1] for e in api.events}
    assert by_type["patient_acknowledge"].startswith("Bearer patient|") and by_type["accept_ownership"] == "Bearer primary_care_owner|Dr. Layla Haddad"


def test_a_case_without_an_owner_is_reported_not_guessed():
    api = FakeApi()
    with httpx.Client(transport=httpx.MockTransport(api.handler)) as client:
        s = seed.Seeder(client, "http://api")
        item = seed.plan(["201", "202"])[1]  # a patient_contacted case: needs the owner to accept
        s.create(item, [])
        api.cases[item["case_id"]]["owner"] = None
        with pytest.raises(RuntimeError, match="no owner yet"):
            s.walk(item)
