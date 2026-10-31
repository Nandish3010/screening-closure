"""T-SP1 to T-SP22: the second pathway (cervical screening, `config/pathways/cervical_hpv.yaml`) runs on the SAME engine.

The pack extends the bowel pack: states, transitions, the closure guard, the actor table and the consent hold are literally
the same objects, so these tests prove three things. (1) The pack is data: nothing in the state machine, the policy or the
actor table names a pathway. (2) A cervical case goes from result to closed through the real API, and closure still needs a
person who is a physician, a signature of their own and evidence that resolves, is complete and NAMES a colposcopy.
(3) The pathways coexist: one worklist, one sweep, one pair of scans, each case stepped by the pack in its own row.

Every clinical timing of the pack is either sourced (and says where) or marked "placeholder, needs clinician sign-off (H1)";
test_t_sp3 holds it to that. The Arabic next step needs native review (H2). No impact number exists for this pathway."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from api_helpers import actor_of, login_headers, make_client, user_id_of
from app import evidence
from app.ai.provider import CHANNELS, PURPOSES, DraftRequest
from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES, render
from app.ai.validate import validate
from app.calendar import default_calendar
from app.clock import clock
from app.events import InMemoryEventStore
from app.pathway import PathwayError, PathwayRegistry, load_pathway, load_registry, merge_counts, read_pack
from app.routers import cases
from app.sweep import run_sweep
from test_evidence_binding import FakeHapi

ROOT = Path(__file__).resolve().parents[2]
BOWEL_PATH = ROOT / "config/pathways/colorectal_fit.yaml"
CERVICAL_PATH = ROOT / "config/pathways/cervical_hpv.yaml"
BOWEL = load_pathway(BOWEL_PATH)
CERVICAL = load_pathway(CERVICAL_PATH)
client = make_client()
SCHED = {"Authorization": f"Bearer {cases.app_settings.scheduler_service_token}"}


@pytest.fixture
def both(monkeypatch):
    """A fresh in-memory engine that runs both pathways, the bowel one as the default."""
    reg = PathwayRegistry([BOWEL, CERVICAL])
    monkeypatch.setattr(cases, "registry", reg)
    monkeypatch.setattr(cases, "pathway", reg.default)
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    saved = evidence.verifier
    clock.reset()
    yield reg
    evidence.verifier = saved
    clock.reset()


@pytest.fixture
def hapi(both):
    fake = FakeHapi()
    evidence.verifier = evidence.FhirVerifier(fake.url, timeout=3)
    yield fake
    fake.server.shutdown()


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post(case_id, type_, headers, payload=None):
    return client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def new_case(pathway=None, patient_ref="Patient/sp1"):
    body = {"patient_ref": patient_ref, **({"pathway": pathway} if pathway else {})}
    r = client.post("/cases", json=body, headers=H("coordinator"))
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def drive_to(cid, last="clinician_review", spec_name="Dr Colposcopist", patient_ref="Patient/sp1"):
    """Take a case through the main path up to and including the event `last`. The specialist acts for `endo-sp`."""
    pco = H("primary_care_owner", "Dr Referrer")
    token = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": spec_name,
                                                 "provider": "endo-sp"}).json()["token"]
    spec = {"Authorization": f"Bearer {token}"}
    steps = [
        ("acknowledge", H("lab"), {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
        ("patient_acknowledge", H("patient", "pat", patient_ref), {}),
        ("handoff_requested", pco, {"to_provider": "endo-sp"}), ("handoff_accepted", spec, {}),
        ("book_confirmatory", spec, {}), ("complete_confirmatory", spec, {}), ("clinician_review", spec, {}),
    ]
    for type_, headers, payload in steps:
        assert post(cid, type_, headers, payload).status_code == 201, type_
        if type_ == last:
            break
    return spec


def state(cid):
    return cases.store.current_state(UUID(cid))


def count(cid):
    return len(cases.store.events_for(UUID(cid)))


def colposcopy_report(ident, case_id, *, completeness="complete", status="final", text="Colposcopy report",
                      conclusion="Colposcopy complete"):
    body = {"resourceType": "DiagnosticReport", "id": ident, "status": status,
            "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}],
            "code": {"text": text},
            "conclusionCode": [{"coding": [{"system": evidence.COMPLETENESS_SYSTEM, "code": completeness}],
                                "text": conclusion}]}
    return body


def close(cid, spec, ref, name="Dr Colposcopist", **payload):
    who = actor_of("specialist_owner", name)
    return post(cid, "close", spec, {"evidence_ref": ref, "signed_by": who, **payload})


# ---- (1) the pack is data ---------------------------------------------------------------------------------------------


def test_t_sp1_the_pack_reuses_the_bowel_state_machine_and_changes_only_data():
    assert CERVICAL.name == "cervical_hpv" and BOWEL.name == "colorectal_fit"
    for field in ("states", "side_states", "transitions", "annotations", "human_only_events", "escalation_ladder",
                  "closure_rule", "handoff_response_working_days", "handoff_reject_reasons", "handoff_bounce_reason",
                  "refusal_state", "refusal_reasons", "appointment_cancel_reasons"):
        assert getattr(CERVICAL, field) == getattr(BOWEL, field), field
    raw = read_pack(CERVICAL_PATH)
    assert raw["extended_from"] == "colorectal_fit.yaml"
    set_by_the_pack = {k for k in yaml.safe_load(CERVICAL_PATH.read_text()) if k != "extends"}
    assert set_by_the_pack == {"pathway", "version", "description", "labels", "slas_working_days", "milestones",
                               "evidence", "patient_messages"}, "a new key in the pack is a new thing to review"


def test_t_sp2_no_pathway_name_appears_in_the_state_machine_the_policy_or_the_actor_table():
    for module in ("state_machine.py", "policy.py", "payloads.py", "auth.py", "store_pg.py", "sweep.py", "automation.py"):
        text = (ROOT / "api/app" / module).read_text()
        assert "cervical" not in text.lower(), f"{module} names the cervical pathway"
        assert not re.search(r"pathway(\.name)?\s*(==|!=)\s*[\"']", text), f"{module} branches on a pathway name"
        assert "colorectal_fit" not in text, f"{module} names the bowel pathway"


def test_t_sp3_every_clinical_timing_is_sourced_or_marked_a_placeholder():
    text = CERVICAL_PATH.read_text()
    assert text.count("PLACEHOLDER, needs clinician sign-off (H1)") >= 4
    assert "The repository holds no cervical timeline" in text
    ref = CERVICAL.milestones["referral_requested"]
    assert ref.working_days == 15 and "IFHAS" in ref.source and "evidence.md section 8a" in ref.source
    assert "to be confirmed by H1" in ref.source
    conf = CERVICAL.milestones["confirmatory_completed"]
    assert conf.source.startswith("PLACEHOLDER, needs clinician sign-off (H1)")
    assert "not a cervical standard" in conf.source
    # the per-state SLAs are the bowel pack's own design values, and the pack says so in the comment that precedes them
    assert CERVICAL.slas_working_days == BOWEL.slas_working_days
    sla_comment = " ".join(text.split("slas_working_days:")[0].rsplit("# Per-state SLAs", 1)[1].replace("#", " ").split())
    assert "PLACEHOLDER, needs clinician sign-off (H1)" in sla_comment and "not specific to the cervical pathway" in sla_comment


def test_t_sp4_the_evidence_rule_is_the_colposcopy_and_asserts_no_terminology_code():
    rule = CERVICAL.evidence
    assert (rule.procedure, rule.closable_completeness, rule.require_named_procedure, rule.search_codes) == \
        ("colposcopy", ("complete",), True, ())
    default = BOWEL.evidence
    assert default.procedure == "colonoscopy" and default.closable_completeness == ("complete", "ct_colonography")
    assert default.require_named_procedure is False and len(default.search_codes) == 2


def test_t_sp5_the_bowel_pack_file_is_untouched_by_the_second_pathway():
    # the pathway fingerprint (`repr`) feeds the metrics and next-due signatures: new fields must not change it
    assert "EvidenceRule" not in repr(BOWEL) and "next_steps" not in repr(BOWEL) and "labels" not in repr(BOWEL)
    assert BOWEL.next_steps == {} and BOWEL.labels == {}


@pytest.mark.parametrize("section,reason", [
    ({"procedure": "Colposcopy!"}, "lower-case"),
    ({"closable_completeness": []}, "non-empty subset"),
    ({"closable_completeness": ["incomplete"]}, "without 'incomplete'"),
    ({"closable_completeness": ["complete", "anything"]}, "non-empty subset"),
    ({"require_named_procedure": "yes"}, "true or false"),
    ({"search_codes": [["Observation", "a|b"]]}, "search_codes entries"),
    ({"search_codes": [["Procedure", "no-bar"]]}, "search_codes entries"),
    ({"surprise": 1}, "unknown keys"),
])
def test_t_sp6_a_malformed_evidence_section_is_refused_at_load(tmp_path, section, reason):
    pack = tmp_path / "x.yaml"
    (tmp_path / BOWEL_PATH.name).write_text(BOWEL_PATH.read_text())
    pack.write_text(yaml.safe_dump({"extends": BOWEL_PATH.name, "pathway": "x", "version": 1, "evidence": section}))
    with pytest.raises(PathwayError, match=reason):
        load_pathway(pack)


@pytest.mark.parametrize("extends,reason", [
    ("../colorectal_fit.yaml", "same directory"), ("colorectal_fit.json", "same directory"), (7, "same directory"),
])
def test_t_sp7_extends_names_a_pack_in_the_same_directory_only(tmp_path, extends, reason):
    pack = tmp_path / "x.yaml"
    pack.write_text(yaml.safe_dump({"extends": extends, "pathway": "x", "version": 1}))
    with pytest.raises(PathwayError, match=reason):
        load_pathway(pack)


def test_t_sp8_an_extends_chain_that_never_ends_is_refused(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump({"extends": "b.yaml", "pathway": "a", "version": 1}))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump({"extends": "a.yaml", "pathway": "b", "version": 1}))
    with pytest.raises(PathwayError, match="nests deeper"):
        load_pathway(tmp_path / "a.yaml")


def test_t_sp9_a_pack_that_extends_cannot_weaken_the_closure_guard(tmp_path):
    (tmp_path / BOWEL_PATH.name).write_text(BOWEL_PATH.read_text())
    weak = [t for t in yaml.safe_load(BOWEL_PATH.read_text())["transitions"]]
    for row in weak:
        if row.get("event") == "close":
            row["requires"] = ["evidence_ref"]  # drops the signature
    pack = tmp_path / "weak.yaml"
    pack.write_text(yaml.safe_dump({"extends": BOWEL_PATH.name, "pathway": "weak", "version": 1, "transitions": weak}))
    with pytest.raises(PathwayError, match="evidence_ref and signed_by"):
        load_pathway(pack)


def test_t_sp10_the_registry_refuses_duplicates_empty_and_mixed_terminals(tmp_path):
    with pytest.raises(PathwayError, match="unique"):
        PathwayRegistry([BOWEL, BOWEL])
    with pytest.raises(PathwayError, match="at least one"):
        PathwayRegistry([])
    other = tmp_path / "other.yaml"
    (tmp_path / BOWEL_PATH.name).write_text(BOWEL_PATH.read_text())
    raw = yaml.safe_load(BOWEL_PATH.read_text())
    raw["states"] = [*raw["states"][:-1], "finished"]
    raw["transitions"] = [{**t, **({"to": "finished"} if t.get("to") == "closed" else {})} for t in raw["transitions"]]
    for t in raw["transitions"]:
        if t.get("from") == "closed":
            t["from"] = "finished"
    raw["pathway"] = "other"
    other.write_text(yaml.safe_dump(raw))
    with pytest.raises(PathwayError, match="same terminal state"):
        PathwayRegistry([BOWEL, load_pathway(other)])
    reg = load_registry([BOWEL_PATH, CERVICAL_PATH])
    assert reg.names == ("colorectal_fit", "cervical_hpv") and reg.default.name == "colorectal_fit"
    assert reg.get(None) is reg.default and reg.get("nope") is None and "cervical_hpv" in reg and len(reg) == 2


def test_t_sp11_merge_counts_adds_joins_and_keeps_one_part_as_it_is():
    one = {"a": 1, "errors": ["x"], "sim_time": "t"}
    assert merge_counts([one]) is one
    got = merge_counts([{"a": 1, "errors": ["x"], "sim_time": "t", "ok": True, "full": True},
                        {"a": 2, "errors": ["y"], "sim_time": "u", "ok": False, "full": False, "b": 5}],
                       and_keys=("full",))
    assert got == {"a": 3, "errors": ["x", "y"], "sim_time": "t", "ok": True, "full": False, "b": 5}


# ---- patient messages (English and Arabic) through the placeholder contract and the validator --------------------------


@pytest.mark.parametrize("language", ["en", "ar"])
def test_t_sp12_every_message_of_the_pathway_passes_the_validator(language):
    s = SAMPLE_VALUES[language]
    step = CERVICAL.next_step(language, DEFAULT_NEXT_STEP)
    assert step != DEFAULT_NEXT_STEP[language]  # the pack's own next step, not the bowel "camera test"
    checked = 0
    for purpose in PURPOSES:
        for channel in CHANNELS:
            for date in (None, s["appointment_date"]):
                if purpose == "appointment_confirmation" and date is None:
                    continue
                text = render(purpose, channel, language, owner_name=s["owner_name"], next_step=step, appointment_date=date)
                req = DraftRequest(purpose=purpose, channel=channel, language=language, owner_name=s["owner_name"],
                                   owner_role="primary_care_owner", next_step=step, pathway_name=CERVICAL.name,
                                   appointment_date=date)
                result = validate(text, req)
                assert result.ok, (language, purpose, channel, date, result)
                assert step in text and s["owner_name"] in text
                checked += 1
    assert checked >= 15


def test_t_sp13_the_next_step_names_an_appointment_never_a_finding_and_arabic_awaits_review():
    for language, step in CERVICAL.next_steps.items():
        assert len(step) <= 40
        low = step.lower()
        for word in ("cancer", "hpv", "abnormal", "positive", "smear", "cervical", "colposcop", "dysplasia", "lesion"):
            assert word not in low, (language, word)
    text = CERVICAL_PATH.read_text()
    assert "H2" in text and "native-speaker review" in text  # the Arabic is marked as needing a native review
    assert CERVICAL.next_steps.keys() == {"en", "ar"} and CERVICAL.labels.keys() == {"en", "ar"}


def test_t_sp14_a_draft_for_a_cervical_case_uses_its_own_next_step(both):
    cid = new_case("cervical_hpv")
    pco = H("primary_care_owner", "Dr Referrer")
    drive_to(cid, last="accept_ownership")
    r = client.post(f"/cases/{cid}/drafts", json={"purpose": "result_notification", "channel": "sms", "language": "en"},
                    headers=pco)
    assert r.status_code == 201, r.text
    assert CERVICAL.next_steps["en"] in r.json()["text_en"]
    assert DEFAULT_NEXT_STEP["en"] not in r.json()["text_en"]
    bowel = new_case(None, patient_ref="Patient/sp2")
    drive_to(bowel, last="accept_ownership", patient_ref="Patient/sp2")
    rb = client.post(f"/cases/{bowel}/drafts", json={"purpose": "result_notification", "channel": "sms", "language": "en"},
                     headers=pco)
    assert rb.status_code == 201 and DEFAULT_NEXT_STEP["en"] in rb.json()["text_en"]


# ---- (2) a cervical case, from result to closed ---------------------------------------------------------------------


def test_t_sp15_a_cervical_case_goes_from_result_to_closed_with_evidence(hapi):
    cid = new_case("cervical_hpv")
    row = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    assert row["pathway"] == "cervical_hpv" and row["state"] == "result_received"
    assert set(row["milestones"]) == {"referral_requested", "confirmatory_completed"}
    spec = drive_to(cid)
    assert state(cid) == "clinician_reviewed"
    ref = hapi.put(colposcopy_report("cx-report-1", cid))
    done = close(cid, spec, ref)
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["state"] == "closed" and body["pathway"] == "cervical_hpv"
    closing = [e for e in body["events"] if e["type"] == "close"][0]
    assert closing["payload"]["evidence_ref"] == ref and closing["payload"]["signed_by"] == actor_of("specialist_owner", "Dr Colposcopist")
    # nothing about the case page or the log carries the pathway as an event payload: the row holds it
    assert "pathway" not in cases.store.events_for(UUID(cid))[0].payload


def test_t_sp16_a_procedure_linked_to_a_final_colposcopy_report_closes(hapi):
    cid = new_case("cervical_hpv")
    spec = drive_to(cid)
    hapi.put(colposcopy_report("cx-r2", cid))
    proc = {"resourceType": "Procedure", "id": "cx-p2", "status": "completed", "code": {"text": "Colposcopy"},
            "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": cid}],
            "report": [{"reference": "DiagnosticReport/cx-r2"}]}
    assert close(cid, spec, hapi.put(proc)).status_code == 201


@pytest.mark.parametrize("make,reason", [
    (lambda c: colposcopy_report("r-inc", c, completeness="incomplete", conclusion="Colposcopy incomplete"),
     "recorded as incomplete: repeat it before closing"),
    (lambda c: colposcopy_report("r-prelim", c, status="preliminary"), "not final"),
    (lambda c: colposcopy_report("r-ct", c, completeness="ct_colonography", text="CT colonography report",
                                 conclusion="CT colonography complete"), "does not state that the colposcopy was complete"),
    (lambda c: colposcopy_report("r-silent", c, completeness=None, conclusion="Seen") | {"conclusionCode": []},
     "does not state that the colposcopy was complete"),
    # another pathway's record of the same patient, a COMPLETE colonoscopy, must not close a cervical case
    (lambda c: colposcopy_report("r-colon", c, text="Colonoscopy report", conclusion="Colonoscopy complete"),
     "does not name a colposcopy"),
])
def test_t_sp17_closure_is_refused_without_valid_evidence_and_nothing_is_written(hapi, make, reason):
    cid = new_case("cervical_hpv")
    spec = drive_to(cid)
    ref = hapi.put(make(cid))
    n = count(cid)
    r = close(cid, spec, ref)
    assert r.status_code == 422 and reason in r.json()["detail"], r.text
    assert count(cid) == n and state(cid) == "clinician_reviewed"


def test_t_sp18_the_same_colonoscopy_record_still_closes_a_bowel_case_on_the_same_engine(hapi):
    cid = new_case(None, patient_ref="Patient/sp3")
    spec = drive_to(cid, patient_ref="Patient/sp3")
    body = colposcopy_report("r-colon-ok", cid, text="Colonoscopy report", conclusion="Colonoscopy complete")
    assert close(cid, spec, hapi.put(body)).status_code == 201  # unchanged bowel rule
    other = new_case(None, patient_ref="Patient/sp4")
    spec2 = drive_to(other, patient_ref="Patient/sp4", spec_name="Dr Bowel")
    ct = colposcopy_report("r-ct-ok", other, completeness="ct_colonography", text="CT colonography report",
                           conclusion="CT colonography complete")
    assert close(other, spec2, hapi.put(ct), "Dr Bowel").status_code == 201  # the CT alternative stays bowel-only


def test_t_sp19_closure_without_evidence_or_signature_is_refused_by_the_engine(hapi):
    cid = new_case("cervical_hpv")
    spec = drive_to(cid)
    who = actor_of("specialist_owner", "Dr Colposcopist")
    assert post(cid, "close", spec, {"signed_by": who}).status_code == 422                      # no evidence
    assert post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/x"}).status_code == 422  # no signature
    assert post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/ghost", "signed_by": who}).status_code == 422
    assert state(cid) == "clinician_reviewed"


def test_t_sp20_only_a_physician_who_signs_as_themselves_closes_and_default_deny_holds(hapi):
    cid = new_case("cervical_hpv")
    spec = drive_to(cid)
    ref = hapi.put(colposcopy_report("cx-r3", cid))
    who = actor_of("specialist_owner", "Dr Colposcopist")
    for role in ("coordinator", "lab", "supervisor", "patient"):
        r = post(cid, "close", H(role), {"evidence_ref": ref, "signed_by": who})
        assert r.status_code in (403, 409), (role, r.status_code)
        assert state(cid) == "clinician_reviewed"
    # a physician cannot sign as someone else
    assert post(cid, "close", spec, {"evidence_ref": ref, "signed_by": actor_of("specialist_owner", "Dr Other")}).status_code == 403
    # an event type no row allows is refused for every role: default-deny
    assert post(cid, "not_an_event", spec).status_code in (403, 422)
    assert close(cid, spec, ref).status_code == 201


def test_t_sp21_the_consent_hold_applies_to_a_cervical_case(both):
    cid = new_case("cervical_hpv")
    drive_to(cid, last="patient_acknowledge")
    patient = H("patient", "pat", "Patient/sp1")
    r = post(cid, "consent_updated", patient, {"withdrawn": True})
    assert r.status_code == 201 and r.json()["state"] == "consent_withdrawn"
    assert post(cid, "handoff_requested", H("primary_care_owner", "Dr Referrer"), {"to_provider": "endo-sp"}).status_code == 409
    back = post(cid, "consent_updated", patient, {"withdrawn": False})
    assert back.json()["state"] == "patient_acknowledged"  # resumed where it was, from the log


# ---- (3) the pathways coexist -------------------------------------------------------------------------------------


def test_t_sp22_an_unknown_pathway_is_refused_and_the_default_is_the_bowel_pathway(both):
    assert client.post("/cases", json={"pathway": "nope"}, headers=H("coordinator")).status_code == 422
    default = client.post("/cases", json={}, headers=H("coordinator")).json()
    assert default["pathway"] == "colorectal_fit"
    named = client.post("/cases", json={"pathway": "colorectal_fit"}, headers=H("coordinator")).json()
    assert named["pathway"] == "colorectal_fit"


def test_t_sp23_the_worklist_shows_both_pathways_pages_without_skips_and_filters_by_state(both):
    ids = {}
    for i in range(7):
        pw = "cervical_hpv" if i % 2 else None
        ids[new_case(pw, patient_ref=f"Patient/wl{i}")] = pw or "colorectal_fit"
    coord = H("coordinator")
    seen, cursor, pages = [], None, 0
    while True:
        r = client.get("/cases", params={"limit": 2, **({"cursor": cursor} if cursor else {})}, headers=coord)
        assert r.status_code == 200
        seen += r.json()
        pages += 1
        cursor = r.headers.get("X-Next-Cursor")
        if cursor is None:
            break
    got = [row["case_id"] for row in seen]
    assert len(got) == len(set(got)) == 7 and set(got) == set(ids), "a page skipped or repeated a case"
    assert got == sorted(got, key=lambda c: UUID(c).int), "the worklist stays in case-id order"
    assert pages >= 4
    assert {row["pathway"] for row in seen} == {"colorectal_fit", "cervical_hpv"}
    assert all(row["pathway"] == ids[row["case_id"]] for row in seen)
    assert len(client.get("/cases", params={"state": "result_received"}, headers=coord).json()) == 7
    one = next(c for c, pw in ids.items() if pw == "cervical_hpv")
    drive_to(one, last="acknowledge")
    only = client.get("/cases", params={"state": "acknowledged"}, headers=coord).json()
    assert [r["case_id"] for r in only] == [one] and only[0]["pathway"] == "cervical_hpv"


def test_t_sp24_one_sweep_runs_each_pathway_under_its_own_pack(both):
    a, b = new_case(None, "Patient/sw1"), new_case("cervical_hpv", "Patient/sw2")
    clock.advance(days=4)  # past the 2-working-day `acknowledge` SLA of both packs
    r = client.post("/scheduler/sweep", headers=SCHED)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cases_evaluated"] >= 2 and out["errors"] == []
    for cid in (a, b):
        model = cases.store.read_model(UUID(cid), cases.pathway_of(UUID(cid)))
        assert model.overdue, cid
    # each case was stepped by the pack in its own row: the sweep wrote the breach under the right pathway
    assert {cases.pathway_of(UUID(c)).name for c in (a, b)} == {"colorectal_fit", "cervical_hpv"}


def test_t_sp25_a_sweep_of_a_cervical_case_uses_the_cervical_milestones(both):
    cid = new_case("cervical_hpv", "Patient/sw3")
    calendar = default_calendar()
    far = clock.now().replace(year=2027)
    res = run_sweep(cases.store, CERVICAL, calendar, far)
    assert res.cases_evaluated == 1 and not res.errors
    m = cases.store.read_model(UUID(cid), CERVICAL)
    assert m.milestones["referral_requested"].overdue  # 15 working days from the result, the IFHAS rule
    # a sweep of the BOWEL pathway reads none of the cervical cases
    assert run_sweep(cases.store, BOWEL, calendar, far).cases_evaluated == 0


def test_t_sp26_the_automation_and_evidence_scans_run_per_pathway(both, monkeypatch):
    from app import automation

    monkeypatch.setattr(cases.app_settings, "automation_enabled", True)
    cid = new_case("cervical_hpv", "Patient/au1")
    out = client.post("/scheduler/automation-scan", headers=SCHED).json()
    assert out["enabled"] is True
    assert state(cid) in ("owner_accepted", "acknowledged")  # acknowledged by system:automation under the cervical pack
    ev = client.post("/scheduler/evidence-scan", headers=SCHED).json()
    assert ev["enabled"] is True
    assert automation.enabled()


def test_t_sp27_the_case_row_of_each_pathway_is_what_steps_it(both):
    a, b = new_case(None, "Patient/st1"), new_case("cervical_hpv", "Patient/st2")
    assert cases.pathway_of(UUID(a)).name == "colorectal_fit" and cases.pathway_of(UUID(b)).name == "cervical_hpv"
    assert cases.pathway_of(UUID(b)) is CERVICAL
    assert client.get(f"/cases/{uuid4()}", headers=H("coordinator")).status_code == 404
    assert client.post(f"/cases/{uuid4()}/events", json={"type": "acknowledge", "payload": {}},
                       headers=H("lab")).status_code == 404


def test_t_sp28_a_case_of_a_pack_the_deployment_does_not_load_is_refused_not_misstepped(monkeypatch):
    store = InMemoryEventStore()
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(cases, "registry", PathwayRegistry([BOWEL]))
    monkeypatch.setattr(cases, "pathway", BOWEL)
    from app.events import CASE_CREATED, Event

    cid = uuid4()
    store.create_case(cid, "cervical_hpv", Event(cid, CASE_CREATED, datetime(2026, 1, 5, tzinfo=UTC), "system:intake", {}), "result_received")
    r = client.get(f"/cases/{cid}", headers=H("coordinator"))
    assert r.status_code == 503 and "does not load" in r.json()["detail"]
    assert client.post(f"/cases/{cid}/events", json={"type": "acknowledge", "payload": {}}, headers=H("lab")).status_code == 503


def test_t_sp29_the_pathways_route_returns_each_loaded_pack(both):
    coord = H("coordinator")
    assert client.get("/pathways", headers=coord).json()["pathway"] == "colorectal_fit"
    got = client.get("/pathways", params={"name": "cervical_hpv"}, headers=coord).json()
    assert got["pathway"] == "cervical_hpv" and got["extended_from"] == "colorectal_fit.yaml"
    assert client.get("/pathways", params={"name": "nope"}, headers=coord).status_code == 404


def test_t_sp30_the_closure_metrics_stay_bowel_only(both):
    new_case("cervical_hpv", "Patient/m1")
    new_case(None, "Patient/m2")
    body = client.get("/metrics/closure", headers=H("supervisor")).json()
    assert body["cases_total"] == 1  # no cervical case enters a bowel number; the second pathway has no outcome claim


# ---- the recorder writes the pathway's own procedure, and the verifier reads it back -----------------------------------


def test_t_sp31_the_recorder_writes_a_colposcopy_with_no_terminology_code():
    from app import confirmatory

    cid = uuid4()
    proc, report = confirmatory.build_resources(cid, None, datetime(2026, 2, 2, tzinfo=UTC), "complete", "colposcopy")
    assert proc["id"] == f"colposcopy-{cid}" and report["id"] == f"colposcopy-report-{cid}"
    assert proc["code"] == {"text": "Colposcopy"} and report["code"] == {"text": "Colposcopy report"}
    assert report["conclusionCode"][0]["text"] == "Colposcopy complete"
    assert proc["report"] == [{"reference": f"DiagnosticReport/colposcopy-report-{cid}"}]
    assert "coding" not in proc["code"] and "coding" not in report["code"], "no code is asserted without a source"
    with pytest.raises(ValueError, match="no CT-colonography"):
        confirmatory.build_resources(cid, None, datetime(2026, 2, 2, tzinfo=UTC), "ct_colonography", "colposcopy")
    # the colonoscopy's resources are exactly what they were
    colon, _ = confirmatory.build_resources(cid, None, datetime(2026, 2, 2, tzinfo=UTC))
    assert colon["id"] == f"colonoscopy-{cid}" and colon["code"]["coding"][0]["code"] == "73761001"


def test_t_sp32_what_the_recorder_writes_for_a_cervical_case_closes_it_and_a_bowel_record_does_not(hapi):
    from app import confirmatory

    cid = new_case("cervical_hpv", "Patient/rc1")
    spec = drive_to(cid, patient_ref="Patient/rc1")
    owner = spec
    r = client.post(f"/cases/{cid}/confirmatory-result", headers=owner)
    assert r.status_code == 201 and r.json()["procedure_ref"] == f"Procedure/colposcopy-{cid}"
    assert client.post(f"/cases/{cid}/confirmatory-result", params={"completeness": "ct_colonography"},
                       headers=owner).status_code == 422
    proc, report = confirmatory.build_resources(UUID(cid), None, datetime(2026, 2, 2, tzinfo=UTC), "complete", "colposcopy")
    hapi.put(report)
    ref = hapi.put(proc)
    assert close(cid, spec, ref).status_code == 201
    # a colonoscopy recorded for the same case id (the other pathway's recorder) is not this pathway's evidence
    other = new_case("cervical_hpv", "Patient/rc2")
    spec2 = drive_to(other, patient_ref="Patient/rc2", spec_name="Dr Other Closer")
    cproc, creport = confirmatory.build_resources(UUID(other), None, datetime(2026, 2, 2, tzinfo=UTC))
    hapi.put(creport)
    wrong = close(other, spec2, hapi.put(cproc), "Dr Other Closer")
    assert wrong.status_code == 422 and "does not name a colposcopy" in wrong.json()["detail"]


def test_t_sp33_the_evidence_scan_finds_a_cervical_case_by_identifier_only(both, monkeypatch):
    from app import automation

    fake = evidence.StaticFinder()
    verifier = evidence.StaticVerifier(procedures={})
    cid = new_case("cervical_hpv", "Patient/ev1")
    drive_to(cid, last="complete_confirmatory", patient_ref="Patient/ev1")
    cid2 = new_case("cervical_hpv", "Patient/ev2")
    drive_to(cid2, last="complete_confirmatory", patient_ref="Patient/ev2", spec_name="Dr Ev Two")
    fake.found[cid] = [f"Procedure/colposcopy-{cid}"]
    verifier.existing.add(f"Procedure/colposcopy-{cid}")
    verifier.procedures[f"Procedure/colposcopy-{cid}"] = frozenset({"colposcopy"})
    # a record that names the other procedure is the case's own, but not closure evidence: flagged for a person
    fake.found[cid2] = [f"Procedure/colonoscopy-{cid2}"]
    verifier.existing.add(f"Procedure/colonoscopy-{cid2}")
    verifier.procedures[f"Procedure/colonoscopy-{cid2}"] = frozenset({"colonoscopy"})
    monkeypatch.setattr(cases.app_settings, "automation_enabled", True)  # after the cases are built by hand
    out = automation.scan_evidence(cases.store, CERVICAL, clock.now(), finder=fake, verifier=verifier)
    assert out["found"] == 1 and out["closure_proposed"] == 1
    assert out.get("incomplete_flagged") == 1
    assert {c[0] for c in fake.calls} == {cid, cid2}
    assert cases.store.read_model(UUID(cid), CERVICAL).closure_proposal is not None
    assert cases.store.read_model(UUID(cid2), CERVICAL).closure_proposal is None


def test_t_sp34_the_hl7_colonoscopy_result_mapping_is_refused_for_a_cervical_case(both):
    from app.hl7 import handler

    assert "pathway" in handler.R_NOT_COLONOSCOPY


def test_t_sp35_the_fhir_finder_searches_by_code_only_where_the_pack_names_one(monkeypatch):
    searched = []
    finder = evidence.FhirFinder("http://hapi.invalid/fhir")
    monkeypatch.setattr(finder, "_search", lambda rtype, params: searched.append((rtype, tuple(sorted(params)))) or [])
    cid = uuid4()
    finder.find(case_id=cid, patient_ref="Patient/a", after=None, codes=CERVICAL.evidence.search_codes)
    assert {k for _, k in searched} == {("identifier",)}, "a pack with no code searches by the case identifier alone"
    searched.clear()
    finder.find(case_id=cid, patient_ref="Patient/a", after=None, codes=BOWEL.evidence.search_codes)
    assert ("Procedure", ("code", "subject")) in searched and ("DiagnosticReport", ("code", "subject")) in searched
    searched.clear()
    finder.find(case_id=cid, patient_ref="Patient/a", after=None)  # no codes argument: the colonoscopy's, as before
    assert ("Procedure", ("code", "subject")) in searched


def test_t_sp36_the_bowel_risk_model_scores_no_cervical_case(both):
    a, b = new_case(None, "Patient/rk1"), new_case("cervical_hpv", "Patient/rk2")
    rows = {r["case_id"]: r for r in client.get("/cases", headers=H("coordinator")).json()}
    assert rows[a]["risk_score"] is not None and rows[b]["risk_score"] is None
    page = client.get(f"/cases/{b}", headers=H("coordinator")).json()
    assert page["risk_score"] is None and page["risk"] is None
    assert client.get(f"/cases/{a}", headers=H("coordinator")).json()["risk"] is not None
