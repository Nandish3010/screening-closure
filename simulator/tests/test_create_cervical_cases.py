"""The cervical demo seed (simulator/create_cervical_cases.py): deterministic, idempotent, and safe for the video.

The second pathway's demo cases are an opt-in extra of the demo seed. The existing video storyboard finds its cases BY STATE
(tools/video/record.ts `findCases`: the first case at `owner_accepted`, the first at `confirmatory_completed`), so the seed
must never put a cervical case in either state, and `make demo-seed` must not seed them unless asked."""

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

import create_cervical_cases as seed  # noqa: E402

TODAY = date(2026, 10, 31)


def patient(pid, gender="female", born="1985-06-01"):
    return {"resource": {"resourceType": "Patient", "id": pid, "gender": gender, "birthDate": born}}


def test_it_picks_adult_women_outside_the_colorectal_manifest_in_id_order():
    entries = [patient("30"), patient("4"), patient("12", gender="male"), patient("9", born="2015-01-01"),
               patient("7", born="1940-01-01"), patient("21"), patient("5", born="not a date"), patient("8")]
    assert seed.pick_patients(entries, exclude={"8"}, count=3, today=TODAY) == ["4", "21", "30"]
    assert seed.pick_patients(entries, exclude=set(), count=10, today=TODAY) == ["4", "8", "21", "30"]
    assert seed.age_on("1985-06-01", TODAY) == 41 and seed.age_on("1985-12-31", TODAY) == 40


def test_case_ids_are_deterministic_and_separate_from_the_colorectal_cases():
    a, b = seed.case_id_for("42"), seed.case_id_for("43")
    assert a == seed.case_id_for("42") and a != b
    import create_cases

    assert a != create_cases.case_id_for("42") and seed.CASE_NAMESPACE != create_cases.CASE_NAMESPACE
    assert seed.event_id_for(a, "accept_ownership") != seed.event_id_for(a, "contact_patient")


def test_no_cervical_demo_case_is_ever_in_a_state_the_video_recorder_looks_for():
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


def test_the_default_demo_seed_does_not_create_cervical_cases_and_the_snapshot_flow_is_untouched():
    mk = (REPO / "Makefile").read_text()
    recipe = mk.split("\ndemo-seed:\n", 1)[1].split("\n\n", 1)[0]
    assert "create_cervical_cases" not in recipe and '$(DEMO_SECOND_PATHWAY)" = "1"' in recipe
    assert "demo-seed-cervical:\n\t$(PY) simulator/create_cervical_cases.py" in mk
    snapshot = mk.split("\ndemo-snapshot:", 1)[1].split("\n\n", 1)[0]
    assert "cervical" not in snapshot and "cervical" not in mk.split("\ndemo-stage:", 1)[1].split("\n\n", 1)[0]
    storyboard = (REPO / "tools/video/storyboard.yaml").read_text()
    assert "cervical" not in storyboard.lower(), "the video storyboard is not changed by the second pathway"


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


def test_the_seeder_creates_cervical_cases_walks_them_to_their_stages_and_a_rerun_changes_nothing():
    api = FakeApi()
    patients = ["101", "102", "103", "104"]
    items, states = run_seed(api, patients)
    assert states == list(seed.STAGES)
    assert all(b["pathway"] == "cervical_hpv" and b["case_id"] == i["case_id"] for b, i in zip(api.created_with, items))
    assert all(b.get("registered_provider") for b in api.created_with)
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
