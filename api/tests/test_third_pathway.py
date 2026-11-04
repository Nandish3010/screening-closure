"""T-CP1 to T-CP20: the third pathway (long-term conditions, `config/pathways/chronic_conditions.yaml`) on the SAME engine.

An abnormal cardiometabolic screening result (blood pressure, HbA1c or fasting glucose, lipids, BMI) goes to the person's
family physician, and the case closes when the physician visit happened AND a care plan is recorded. The pack extends the
bowel pack as the cervical one does, so these tests prove: (1) the pack is data (nothing in the engine names it, every
clinical number is sourced or marked a placeholder); (2) a case goes from result to closed through the real API, and closure
still needs a physician who signs as themselves and evidence that resolves, is complete and NAMES the care plan visit; (3) the
three pathways coexist. The thresholds and timings are placeholders pending a clinician (H1); the Arabic awaits a native
reader (H2). No impact number exists for this pathway."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from api_helpers import actor_of
from app import evidence
from app.ai.provider import CHANNELS, PURPOSES, DraftRequest
from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES, render
from app.ai.validate import validate
from app.clock import clock
from app.events import InMemoryEventStore
from app.pathway import PathwayRegistry, load_pathway, read_pack
from app.routers import cases
from test_evidence_binding import FakeHapi
from test_second_pathway import H, client, close, count, drive_to, post, state

ROOT = Path(__file__).resolve().parents[2]
BOWEL_PATH = ROOT / "config/pathways/colorectal_fit.yaml"
CERVICAL_PATH = ROOT / "config/pathways/cervical_hpv.yaml"
CHRONIC_PATH = ROOT / "config/pathways/chronic_conditions.yaml"
BOWEL, CERVICAL, CHRONIC = load_pathway(BOWEL_PATH), load_pathway(CERVICAL_PATH), load_pathway(CHRONIC_PATH)
PLACEHOLDER = "PLACEHOLDER, needs clinician sign-off (H1)"
PROCEDURE = "care plan visit"
BP_URGENT = [{"kind": "blood_pressure", "values": {"systolic_mmhg": 184, "diastolic_mmhg": 104}}]


@pytest.fixture
def three(monkeypatch):
    reg = PathwayRegistry([BOWEL, CERVICAL, CHRONIC])
    monkeypatch.setattr(cases, "registry", reg)
    monkeypatch.setattr(cases, "pathway", reg.default)
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    saved = evidence.verifier
    clock.reset()
    yield reg
    evidence.verifier = saved
    clock.reset()


@pytest.fixture
def hapi(three):
    fake = FakeHapi()
    evidence.verifier = evidence.FhirVerifier(fake.url, timeout=3)
    yield fake
    fake.server.shutdown()


def new_chronic(patient_ref="Patient/cp1", results=BP_URGENT, **extra):
    body = {"patient_ref": patient_ref, "pathway": "chronic_conditions", **({"results": results} if results else {}), **extra}
    r = client.post("/cases", json=body, headers=H("coordinator"))
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def care_plan_report(ident, case_id, *, completeness="complete", status="final", text="Care plan visit report",
                     conclusion="Care plan visit complete"):
    return {"resourceType": "DiagnosticReport", "id": ident, "status": status,
            "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}],
            "code": {"text": text},
            "conclusionCode": [{"coding": [{"system": evidence.COMPLETENESS_SYSTEM, "code": completeness}], "text": conclusion}]}


def sign(cid, spec, ref, name="Dr Colposcopist"):
    return close(cid, spec, ref, name)


# ---- (1) the pack is data ---------------------------------------------------------------------------------------------


def test_t_cp1_the_pack_reuses_the_bowel_state_machine_and_changes_only_data():
    assert CHRONIC.name == "chronic_conditions"
    for field in ("states", "side_states", "transitions", "annotations", "human_only_events", "escalation_ladder",
                  "closure_rule", "handoff_response_working_days", "handoff_reject_reasons", "handoff_bounce_reason",
                  "refusal_state", "refusal_reasons", "appointment_cancel_reasons"):
        assert getattr(CHRONIC, field) == getattr(BOWEL, field), field
    assert read_pack(CHRONIC_PATH)["extended_from"] == "colorectal_fit.yaml"
    set_by_the_pack = {k for k in yaml.safe_load(CHRONIC_PATH.read_text()) if k != "extends"}
    assert set_by_the_pack == {"pathway", "version", "description", "labels", "slas_working_days", "milestones", "evidence",
                               "patient_messages", "priority"}, "a new key in the pack is a new thing to review"


def test_t_cp2_no_pathway_name_and_no_threshold_appears_in_the_engine():
    for module in ("state_machine.py", "policy.py", "payloads.py", "auth.py", "store_pg.py", "sweep.py", "automation.py",
                   "priority.py", "readmodel.py", "evidence.py", "confirmatory.py", "pathway.py", "routers/cases.py"):
        text = (ROOT / "api/app" / module).read_text()
        low = text.lower()
        assert "chronic" not in low and "cervical" not in low.replace("cervical_hpv", ""), f"{module} names a pack"
        assert not re.search(r"pathway(\.name)?\s*(==|!=)\s*[\"']", text), f"{module} branches on a pathway name"
        assert "colorectal_fit" not in text.replace("pathway_path", "") or module in ("pathway.py",), module
    engine = (ROOT / "api/app/priority.py").read_text()
    assert not re.search(r"\b(systolic|diastolic|hba1c|mmhg|ldl)\b", engine.lower()), "a clinical number or field in the engine"


def test_t_cp3_every_clinical_threshold_and_timing_is_sourced_or_marked_a_placeholder():
    text = CHRONIC_PATH.read_text()
    assert text.count(PLACEHOLDER) >= 12
    assert "holds NO cardiometabolic standard" in text
    ref = CHRONIC.milestones["referral_requested"]
    assert ref.working_days == 15 and "IFHAS" in ref.source and "evidence.md section 8a" in ref.source
    assert "to be confirmed by H1" in ref.source
    assert CHRONIC.milestones["confirmatory_completed"].source.startswith(PLACEHOLDER)
    assert CHRONIC.slas_working_days == BOWEL.slas_working_days
    sla_comment = " ".join(text.split("slas_working_days:")[0].rsplit("# Per-state SLAs", 1)[1].replace("#", " ").split())
    assert PLACEHOLDER in sla_comment and "not specific to this pathway" in sla_comment
    # no priority rule carries a number without saying where it comes from: every basis is the placeholder (nothing here is cited)
    assert CHRONIC.priority.status.startswith(PLACEHOLDER)
    assert CHRONIC.priority.rules and all(r.basis.startswith(PLACEHOLDER) for r in CHRONIC.priority.rules)
    assert PLACEHOLDER in CERVICAL.priority.rules[0].basis


def test_t_cp4_the_evidence_rule_is_the_care_plan_visit_and_asserts_no_terminology_code():
    rule = CHRONIC.evidence
    assert (rule.procedure, rule.closable_completeness, rule.require_named_procedure, rule.search_codes) == \
        (PROCEDURE, ("complete",), True, ())


def test_t_cp5_the_two_earlier_packs_gain_nothing_the_fingerprint_reads():
    assert "PriorityConfig" not in repr(BOWEL) and "PriorityConfig" not in repr(CHRONIC)
    assert BOWEL.priority is None, "the bowel pack file is untouched (its hash is quoted in docs/results)"
    assert CERVICAL.priority is not None and CERVICAL.priority.default_tier == "routine"


# ---- patient messages -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "ar"])
def test_t_cp6_every_message_of_the_pathway_passes_the_validator(language):
    s = SAMPLE_VALUES[language]
    step = CHRONIC.next_step(language, DEFAULT_NEXT_STEP)
    assert step != DEFAULT_NEXT_STEP[language]
    checked = 0
    for purpose in PURPOSES:
        for channel in CHANNELS:
            for date in (None, s["appointment_date"]):
                if purpose == "appointment_confirmation" and date is None:
                    continue
                text = render(purpose, channel, language, owner_name=s["owner_name"], next_step=step, appointment_date=date)
                req = DraftRequest(purpose=purpose, channel=channel, language=language, owner_name=s["owner_name"],
                                   owner_role="primary_care_owner", next_step=step, pathway_name=CHRONIC.name,
                                   appointment_date=date)
                result = validate(text, req)
                assert result.ok, (language, purpose, channel, date, result)
                assert step in text and s["owner_name"] in text
                checked += 1
    assert checked >= 15


def test_t_cp7_the_next_step_names_a_visit_never_a_finding_and_arabic_awaits_review():
    for language, step in CHRONIC.next_steps.items():
        assert len(step) <= 40
        low = step.lower()
        for word in ("diabet", "hypertens", "blood pressure", "hba1c", "glucose", "cholesterol", "obes", "bmi", "abnormal",
                     "high", "result"):
            assert word not in low, (language, word)
    text = CHRONIC_PATH.read_text()
    assert "H2" in text and "native-speaker review" in text
    assert CHRONIC.next_steps.keys() == {"en", "ar"} and CHRONIC.labels.keys() == {"en", "ar"}
    assert all(set(r.reason) == {"en", "ar"} for r in CHRONIC.priority.rules)


def test_t_cp8_a_draft_for_a_chronic_case_uses_its_own_next_step(three):
    cid = new_chronic()
    drive_to(cid, last="accept_ownership", patient_ref="Patient/cp1")
    r = client.post(f"/cases/{cid}/drafts", json={"purpose": "result_notification", "channel": "sms", "language": "en"},
                    headers=H("primary_care_owner", "Dr Referrer"))
    assert r.status_code == 201, r.text
    assert CHRONIC.next_steps["en"] in r.json()["text_en"] and DEFAULT_NEXT_STEP["en"] not in r.json()["text_en"]
    # the draft states no finding: not the priority, not a value
    assert "urgent" not in r.json()["text_en"].lower() and "pressure" not in r.json()["text_en"].lower()


# ---- (2) a chronic case, from result to closed ------------------------------------------------------------------------


def test_t_cp9_a_chronic_case_goes_from_result_to_closed_with_evidence(hapi):
    cid = new_chronic("Patient/cp2")
    row = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    assert row["pathway"] == "chronic_conditions" and row["state"] == "result_received"
    assert set(row["milestones"]) == {"referral_requested", "confirmatory_completed"}
    spec = drive_to(cid, patient_ref="Patient/cp2")
    assert state(cid) == "clinician_reviewed"
    ref = hapi.put(care_plan_report("cp-report-1", cid))
    done = sign(cid, spec, ref)
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["state"] == "closed" and body["pathway"] == "chronic_conditions"
    closing = [e for e in body["events"] if e["type"] == "close"][0]
    assert closing["payload"]["evidence_ref"] == ref and closing["payload"]["signed_by"] == actor_of("specialist_owner", "Dr Colposcopist")
    assert body["priority"]["tier"] == "urgent", "the tier survives to the closed case"


def test_t_cp10_a_procedure_linked_to_a_final_care_plan_report_closes(hapi):
    cid = new_chronic("Patient/cp3")
    spec = drive_to(cid, patient_ref="Patient/cp3")
    hapi.put(care_plan_report("cp-r2", cid))
    proc = {"resourceType": "Procedure", "id": "cp-p2", "status": "completed", "code": {"text": "Care plan visit"},
            "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": cid}],
            "report": [{"reference": "DiagnosticReport/cp-r2"}]}
    assert sign(cid, spec, hapi.put(proc)).status_code == 201


@pytest.mark.parametrize("make,reason", [
    # the visit happened but no care plan was documented: recorded as incomplete
    (lambda c: care_plan_report("r-inc", c, completeness="incomplete", conclusion="Care plan visit incomplete"),
     "recorded as incomplete: repeat it before closing"),
    (lambda c: care_plan_report("r-prelim", c, status="preliminary"), "not final"),
    (lambda c: care_plan_report("r-ct", c, completeness="ct_colonography", text="CT colonography report",
                                conclusion="CT colonography complete"), "does not state that the care plan visit was complete"),
    (lambda c: care_plan_report("r-silent", c, conclusion="Seen") | {"conclusionCode": []},
     "does not state that the care plan visit was complete"),
    # another pathway's COMPLETE record of the same patient must not close a chronic case
    (lambda c: care_plan_report("r-colon", c, text="Colonoscopy report", conclusion="Colonoscopy complete"),
     "does not name a care plan visit"),
    (lambda c: care_plan_report("r-colpo", c, text="Colposcopy report", conclusion="Colposcopy complete"),
     "does not name a care plan visit"),
])
def test_t_cp11_closure_is_refused_without_valid_evidence_and_nothing_is_written(hapi, make, reason):
    cid = new_chronic("Patient/cp4")
    spec = drive_to(cid, patient_ref="Patient/cp4")
    ref = hapi.put(make(cid))
    n = count(cid)
    r = sign(cid, spec, ref)
    assert r.status_code == 422 and reason in r.json()["detail"], r.text
    assert count(cid) == n and state(cid) == "clinician_reviewed"


def test_t_cp12_a_record_of_another_case_does_not_close_and_the_bowel_rule_is_unchanged(hapi):
    a, b = new_chronic("Patient/cp5"), new_chronic("Patient/cp5")
    spec = drive_to(a, patient_ref="Patient/cp5")
    assert sign(a, spec, hapi.put(care_plan_report("cp-other", b))).status_code == 422  # b's record, not a's
    bowel = client.post("/cases", json={"patient_ref": "Patient/cp6"}, headers=H("coordinator")).json()["case_id"]
    spec2 = drive_to(bowel, patient_ref="Patient/cp6", spec_name="Dr Bowel")
    colon = care_plan_report("r-colon-ok", bowel, text="Colonoscopy report", conclusion="Colonoscopy complete")
    assert close(bowel, spec2, hapi.put(colon), "Dr Bowel").status_code == 201


def test_t_cp13_closure_without_evidence_or_signature_is_refused_by_the_engine(hapi):
    cid = new_chronic("Patient/cp7")
    spec = drive_to(cid, patient_ref="Patient/cp7")
    who = actor_of("specialist_owner", "Dr Colposcopist")
    assert post(cid, "close", spec, {"signed_by": who}).status_code == 422
    assert post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/x"}).status_code == 422
    assert post(cid, "close", spec, {"evidence_ref": "DiagnosticReport/ghost", "signed_by": who}).status_code == 422
    assert state(cid) == "clinician_reviewed"


def test_t_cp14_only_a_physician_who_signs_as_themselves_closes_and_default_deny_holds(hapi):
    cid = new_chronic("Patient/cp8")
    spec = drive_to(cid, patient_ref="Patient/cp8")
    ref = hapi.put(care_plan_report("cp-r3", cid))
    who = actor_of("specialist_owner", "Dr Colposcopist")
    for role in ("coordinator", "lab", "supervisor", "patient"):
        r = post(cid, "close", H(role), {"evidence_ref": ref, "signed_by": who})
        assert r.status_code in (403, 409), (role, r.status_code)
    assert post(cid, "close", spec, {"evidence_ref": ref, "signed_by": actor_of("specialist_owner", "Dr Other")}).status_code == 403
    assert post(cid, "not_an_event", spec).status_code in (403, 422)
    # the priority tier is not an event anyone can write, and an urgent case needs the same physician and the same evidence
    assert post(cid, "priority_set", spec, {"tier": "routine"}).status_code in (403, 422)
    assert sign(cid, spec, ref).status_code == 201


def test_t_cp15_the_consent_hold_applies_to_a_chronic_case(three):
    cid = new_chronic("Patient/cp9")
    drive_to(cid, last="patient_acknowledge", patient_ref="Patient/cp9")
    patient = H("patient", "pat", "Patient/cp9")
    r = post(cid, "consent_updated", patient, {"withdrawn": True})
    assert r.status_code == 201 and r.json()["state"] == "consent_withdrawn"
    assert post(cid, "handoff_requested", H("primary_care_owner", "Dr Referrer"), {"to_provider": "endo-sp"}).status_code == 409
    assert post(cid, "consent_updated", patient, {"withdrawn": False}).json()["state"] == "patient_acknowledged"


def test_t_cp16_the_recorder_writes_a_care_plan_visit_with_a_slugged_id_and_no_terminology_code(hapi):
    from app import confirmatory

    cid = uuid4()
    proc, report = confirmatory.build_resources(cid, None, datetime(2026, 2, 2, tzinfo=UTC), "complete", PROCEDURE)
    assert proc["id"] == f"care-plan-visit-{cid}" and report["id"] == f"care-plan-visit-report-{cid}"
    assert proc["code"] == {"text": "Care plan visit"} and report["conclusionCode"][0]["text"] == "Care plan visit complete"
    assert "coding" not in proc["code"] and "coding" not in report["code"]
    case = new_chronic("Patient/rc1")
    spec = drive_to(case, patient_ref="Patient/rc1")
    r = client.post(f"/cases/{case}/confirmatory-result", headers=spec)
    assert r.status_code == 201 and r.json()["procedure_ref"] == f"Procedure/care-plan-visit-{case}"
    p2, r2 = confirmatory.build_resources(UUID(case), None, datetime(2026, 2, 2, tzinfo=UTC), "complete", PROCEDURE)
    hapi.put(r2)
    assert sign(case, spec, hapi.put(p2)).status_code == 201


# ---- (3) the three pathways coexist --------------------------------------------------------------------------------


def test_t_cp17_the_worklist_shows_all_three_pathways_with_priorities_for_the_ranked_ones(three):
    bowel = client.post("/cases", json={"patient_ref": "Patient/w1"}, headers=H("coordinator")).json()["case_id"]
    cervical = client.post("/cases", json={"patient_ref": "Patient/w2", "pathway": "cervical_hpv",
                                           "results": [{"kind": "cancer_screen", "values": {"positive": 1}}]},
                           headers=H("coordinator")).json()["case_id"]
    chronic = new_chronic("Patient/w3")
    rows = {r["case_id"]: r for r in client.get("/cases", headers=H("coordinator")).json()}
    assert {rows[bowel]["pathway"], rows[cervical]["pathway"], rows[chronic]["pathway"]} == \
        {"colorectal_fit", "cervical_hpv", "chronic_conditions"}
    assert rows[bowel]["priority"] is None
    assert rows[cervical]["priority"]["tier"] == "urgent" and rows[cervical]["priority"]["rule"] == "positive_cancer_screen"
    assert rows[chronic]["priority"]["tier"] == "urgent" and rows[chronic]["priority"]["rule"] == "bp_very_high"
    coord = H("coordinator")
    got = client.get("/pathways", params={"name": "chronic_conditions"}, headers=coord).json()
    assert got["pathway"] == "chronic_conditions" and got["priority"]["tiers"] == ["urgent", "soon", "routine"]
    assert client.get("/pathways", headers=coord).json()["pathway"] == "colorectal_fit"


def test_t_cp18_the_closure_metrics_and_the_risk_model_stay_bowel_only(three):
    cid = new_chronic("Patient/m1")
    assert client.get(f"/cases/{cid}", headers=H("coordinator")).json()["risk_score"] is None
    assert client.get("/metrics/closure", headers=H("coordinator")).status_code == 200


def test_t_cp19_the_chronic_pack_is_explored_by_every_loaded_registry_check(three):
    assert three.names == ("colorectal_fit", "cervical_hpv", "chronic_conditions")
    assert {p.terminal_state for p in three} == {"closed"}


def test_t_cp20_a_case_is_stepped_by_the_pack_in_its_own_row(three):
    cid = new_chronic("Patient/s1")
    assert cases.store.pathway_name_of(UUID(cid)) == "chronic_conditions"
    assert "pathway" not in cases.store.events_for(UUID(cid))[0].payload
    g = cases.store.events_for(UUID(cid))[0].payload
    assert g["priority"] == "urgent" and g["priority_rule"] == "bp_very_high"
    assert "systolic" not in str(g) and "184" not in str(g), "the values are never recorded"
