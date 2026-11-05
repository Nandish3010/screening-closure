"""The chronic-conditions demo seed (simulator/create_chronic_cases.py): deterministic, idempotent, in the demo snapshot, and safe for the video.

The third pathway's demo cases are part of the demo snapshot (the hosted demo's Today screen shows the urgent ones). The video
recorder finds its cases BY STATE (tools/video/record.ts `findCases`: the first case at `owner_accepted`, the first at
`confirmatory_completed`), so the seed must never put a chronic case in either state. The patients are synthetic and
Emirati-named; they are written to HAPI by the seed itself."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "simulator"))
sys.path.insert(0, str(REPO / "api"))

import create_chronic_cases as seed  # noqa: E402


def test_the_demo_patients_are_four_synthetic_emirati_named_people_with_fixed_ids_and_no_real_identifier():
    ps = seed.DEMO_PATIENTS
    assert len(ps) == 4 and len({p["id"] for p in ps}) == 4
    assert all(p["family"].startswith("Al ") for p in ps) and len({p["family"] for p in ps}) == 4
    staff = (REPO / "web/src/api/fixtures.ts").read_text()
    assert not any(p["family"] in staff for p in ps), "a patient shares a family name with the staff cast"
    for p in ps:
        res = seed.fhir_patient(p)
        assert res["resourceType"] == "Patient" and res["id"] == p["id"] and res["name"][0]["family"] == p["family"]
        assert res["meta"]["tag"][0]["code"] == "SYNTHETIC"
        assert set(res) == {"resourceType", "id", "meta", "identifier", "name", "gender", "birthDate"}  # no address, phone or national id
        born = int(p["birth"][:4])
        assert 1940 <= born <= 2000  # adults; a range for the demo, NOT an eligibility statement (H1)
    manifest = REPO / "data/results/manifest.json"
    if manifest.exists():
        assert not {p["id"] for p in ps} & {str(r["patient_id"]) for r in json.loads(manifest.read_text())["results"]}


def test_ensure_patients_writes_only_the_missing_ones_and_a_rerun_writes_nothing():
    store: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        pid = request.url.path.rsplit("/", 1)[1]
        if request.method == "GET":
            return httpx.Response(200 if pid in store else 404, json=store.get(pid, {}))
        store[pid] = json.loads(request.content)
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert seed.ensure_patients(client, "http://fhir", seed.DEMO_PATIENTS) == 4
        assert seed.ensure_patients(client, "http://fhir", seed.DEMO_PATIENTS) == 0
    assert set(store) == {p["id"] for p in seed.DEMO_PATIENTS} and all(r["meta"]["tag"][0]["code"] == "SYNTHETIC" for r in store.values())


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
    plan = seed.plan()
    assert {p["stage"] for p in plan} == set(seed.STAGES)
    assert all(p["stage"] not in looked_for for p in plan)
    # the states the steps pass THROUGH are not final states of any case
    for item in plan:
        assert all(step in seed.FROM_STATE for step in item["steps"])


def test_the_demo_snapshot_flow_seeds_the_chronic_cases_after_staging_and_the_hosted_seed_does_too():
    mk = (REPO / "Makefile").read_text()
    snapshot = mk.split("\ndemo-snapshot:", 1)[1].split("\n", 1)[0]
    assert "demo-stage" in snapshot and "demo-seed-chronic" in snapshot and snapshot.index("demo-stage") < snapshot.index("demo-seed-chronic")
    assert "demo-seed-chronic:\n\t$(PY) simulator/create_chronic_cases.py" in mk
    recipe = mk.split("\ndemo-seed:\n", 1)[1].split("\n\n", 1)[0]
    assert "create_chronic_cases" not in recipe and '$(DEMO_THIRD_PATHWAY)" = "1"' in recipe  # the plain seed stays opt-in; the snapshot adds them
    hosted = (REPO / "deploy/gcp/vm/seed-on-vm.sh").read_text()
    assert hosted.rindex("simulator/create_cases.py") < hosted.rindex("simulator/create_chronic_cases.py") < hosted.rindex("tools/demo/snapshot.sh save")
    # the recorder still seeds them itself after the scenario; that is a no-op on a snapshot that has them (409 = exists)
    recorder = (REPO / "tools/video/record.ts").read_text()
    assert 'make("demo-seed-chronic")' in recorder and recorder.index('make("demo-scenario"') < recorder.index('make("demo-seed-chronic")')
    assert 'r.priority?.tier === "urgent"' in recorder


def pack_priority():
    from app.pathway import load_pathway

    return load_pathway(REPO / "config/pathways/chronic_conditions.yaml").priority


def test_the_plan_covers_blood_pressure_hba1c_and_bmi_and_the_real_pack_gives_each_its_intended_tier():
    from app.priority import derive

    assert seed.PATHWAY == "chronic_conditions"
    config = pack_priority()
    items = seed.plan()
    assert [it["tier"] for it in items] == ["urgent", "urgent", "soon", "routine"]
    assert {r["kind"] for it in items for r in it["results"]} == {"blood_pressure", "hba1c", "bmi"}
    assert len({json.dumps(it["results"], sort_keys=True) for it in items}) == 4
    for it, rule in zip(items, ["bp_very_high", "bp_very_high", "hba1c_high", "bmi_raised"]):
        got = derive(config, it["results"])
        assert (got.tier, got.rule) == (it["tier"], rule), it["result_set"]
    assert all(seed.RESULT_SETS[it["result_set"]]["tier"] == it["tier"] for it in items)


def test_the_urgent_cases_stay_untouched_by_a_person_so_that_they_are_on_the_today_screen():
    items = seed.plan()
    urgent = [it for it in items if it["tier"] == "urgent"]
    assert len(urgent) == 2 and all(it["stage"] == "acknowledged" and it["steps"] == () for it in urgent)
    assert {it["stage"] for it in items if it["tier"] != "urgent"} == {"patient_contacted", "patient_acknowledged"}
    # ready for the mock Sahatna booking callback (Integrations page): one case waits for an appointment
    assert any(it["stage"] == "patient_acknowledged" for it in items)


def test_no_clinical_value_appears_in_what_the_script_prints():
    values = {str(v) for rs in seed.RESULT_SETS.values() for r in rs["results"] for v in r["values"].values()}
    items = seed.plan()
    shown = json.dumps(seed.redacted(items))
    line = seed.summary_line(4, 0, {"acknowledged": 2, "patient_contacted": 1, "patient_acknowledged": 1},
                             {"urgent": 2, "soon": 1, "routine": 1})
    for text in (shown, line):
        assert '"results"' not in text and "systolic" not in text and "mmol" not in text and "kg_per_m2" not in text
        assert not any(re.search(rf"(?<![\w-]){v}(?![\w-])", text) for v in values), text
    assert line.startswith("SUMMARY pathway=chronic_conditions ") and '"urgent": 2' in line
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


def run_seed(api):
    items = seed.plan()
    with httpx.Client(transport=httpx.MockTransport(api.handler)) as client:
        s = seed.Seeder(client, "http://api")
        out = []
        for item in items:
            s.create(item, ["Marina Digestive Health", "Riverside Primary Care"])
            out.append(s.walk(item))
        return items, out


def test_the_seeder_creates_chronic_cases_walks_them_to_their_stages_and_a_rerun_changes_nothing():
    api = FakeApi()
    items, states = run_seed(api)
    assert states == [p["stage"] for p in seed.DEMO_PATIENTS]
    assert all(b["pathway"] == "chronic_conditions" and b["case_id"] == i["case_id"] and b["results"] == i["results"] for b, i in zip(api.created_with, items))
    assert all(b.get("registered_provider") for b in api.created_with)
    assert all(b["insurance_scheme"] in ("thiqa", "enhanced", "basic", "unknown") for b in api.created_with)  # ADR-056
    assert all(b["results"] for b in api.created_with)
    assert not {c["state"] for c in api.cases.values()} & set(seed.RECORDER_STATES)
    n_events = len(api.events)
    assert n_events == 0 + 0 + 2 + 3 and {e[0] for e in api.events} == {"accept_ownership", "contact_patient", "patient_acknowledge"}
    again_items, again_states = run_seed(api)
    assert again_states == states and len(api.events) == n_events, "a rerun writes no event twice"
    # the patient acknowledges as the patient, the rest as the case's owner
    by_type = {e[0]: e[1] for e in api.events}
    assert by_type["patient_acknowledge"].startswith("Bearer patient|") and by_type["accept_ownership"] == "Bearer primary_care_owner|Dr. Layla Haddad"


def test_a_case_without_an_owner_is_reported_not_guessed():
    api = FakeApi()
    with httpx.Client(transport=httpx.MockTransport(api.handler)) as client:
        s = seed.Seeder(client, "http://api")
        item = seed.plan()[2]  # a patient_contacted case: needs the owner to accept
        s.create(item, [])
        api.cases[item["case_id"]]["owner"] = None
        with pytest.raises(RuntimeError, match="no owner yet"):
            s.walk(item)
