"""T-A67 to T-A69 (A24, ADR-036): closure evidence must be complete.

`close` and the automatic `complete_confirmatory` need a final DiagnosticReport that states a complete colonoscopy (or a
CT colonography), or a completed Procedure linked to one. A Procedure alone, a stopped / not-done / in-progress
Procedure, a report that is not final, and a report that says incomplete or says nothing are refused (422 on `close`);
the evidence scan flags the record (`review_requested {incomplete_procedure}`) instead of proposing a closure."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app import confirmatory, evidence, payloads
from app.state_machine import AUTOMATION_ACTOR, InvalidTransition
from test_automation import (  # noqa: F401  (`fresh` is a fixture)
    KARIM, PATHWAY, actor, appointment_passed, booked_case, cases, clock, evidence_scan, found, fresh, log, login, model,
    post, scan, types,
)
from test_evidence import count, post as post_event, reviewed_case, state
from test_evidence_binding import close, hapi, procedure, with_report  # noqa: F401  (`hapi` is a fixture)

AT = datetime(2026, 1, 5, tzinfo=UTC)


def report(ident, *, case_id, status="final", completeness="complete", text=None, code_text="Colonoscopy report",
           loinc=True):
    body = {"resourceType": "DiagnosticReport", "id": ident, "status": status,
            "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": str(case_id)}],
            "code": {"text": code_text}}
    if loinc:
        body["code"]["coding"] = [{"system": "http://loinc.org", "code": "18746-8"}]
    if completeness is not None:
        body["conclusionCode"] = [{"coding": [{"system": evidence.COMPLETENESS_SYSTEM, "code": completeness}]}]
    if text is not None:
        body["conclusionCode"] = [{"text": text}]
    return body


# ---- T-A67: the verdict, through `close` ----------------------------------------------------------------------------


def test_t_a67_a_final_report_of_a_complete_colonoscopy_closes(hapi):
    cid, spec = reviewed_case("Dr Complete", patient_ref="Patient/c1")
    ref = hapi.put(report("r-complete", case_id=cid))
    assert close(cid, spec, ref, "Dr Complete").status_code == 201 and state(cid) == "closed"


def test_t_a67_a_completed_procedure_linked_to_such_a_report_closes(hapi):
    cid, spec = reviewed_case("Dr Linked", patient_ref="Patient/c2")
    ref = with_report(hapi, procedure("p-linked", case_id=cid), case_id=cid)
    assert close(cid, spec, ref, "Dr Linked").status_code == 201


def test_t_a67_a_procedure_alone_is_refused(hapi):
    cid, spec = reviewed_case("Dr Alone", patient_ref="Patient/c3")
    ref = hapi.put(procedure("p-alone", case_id=cid))
    n = count(cid)
    r = close(cid, spec, ref, "Dr Alone")
    assert r.status_code == 422 and "procedure alone" in r.json()["detail"]
    assert count(cid) == n and state(cid) == "clinician_reviewed"


@pytest.mark.parametrize("status", ["not-done", "stopped", "in-progress", "entered-in-error", "preparation"])
def test_t_a67_a_procedure_that_is_not_completed_is_refused_even_with_a_complete_report(hapi, status):
    cid, spec = reviewed_case(f"Dr Status {status[:4]}", patient_ref="Patient/c4")
    proc = procedure(f"p-{status}", case_id=cid)
    proc["status"] = status
    ref = with_report(hapi, proc, case_id=cid)
    r = close(cid, spec, ref, f"Dr Status {status[:4]}")
    assert r.status_code == 422 and "not completed" in r.json()["detail"]
    assert state(cid) == "clinician_reviewed"


def test_t_a67_a_report_that_says_incomplete_is_refused_whether_cited_or_linked(hapi):
    cid, spec = reviewed_case("Dr Incomplete", patient_ref="Patient/c5")
    ref = hapi.put(report("r-incomplete", case_id=cid, completeness="incomplete"))
    r = close(cid, spec, ref, "Dr Incomplete")
    assert r.status_code == 422 and "incomplete" in r.json()["detail"]
    linked = with_report(hapi, procedure("p-inc", case_id=cid), case_id=cid, completeness="incomplete")
    assert close(cid, spec, linked, "Dr Incomplete").status_code == 422
    assert state(cid) == "clinician_reviewed"


@pytest.mark.parametrize("status", ["preliminary", "registered", "partial", "amended", "entered-in-error"])
def test_t_a67_a_report_that_is_not_final_is_refused(hapi, status):
    cid, spec = reviewed_case(f"Dr NotFinal {status[:4]}", patient_ref="Patient/c6")
    ref = hapi.put(report(f"r-{status}", case_id=cid, status=status))
    r = close(cid, spec, ref, f"Dr NotFinal {status[:4]}")
    assert r.status_code == 422 and "not final" in r.json()["detail"]


def test_t_a67_a_report_that_says_nothing_about_completeness_is_refused(hapi):
    cid, spec = reviewed_case("Dr Silent", patient_ref="Patient/c7")
    ref = hapi.put(report("r-silent", case_id=cid, completeness=None))
    r = close(cid, spec, ref, "Dr Silent")
    assert r.status_code == 422 and "does not state" in r.json()["detail"]


def test_t_a67_a_ct_colonography_report_is_accepted(hapi):
    cid, spec = reviewed_case("Dr CT", patient_ref="Patient/c8")
    ref = hapi.put(report("r-ct", case_id=cid, completeness="ct_colonography", loinc=False, code_text="CT colonography report"))
    assert close(cid, spec, ref, "Dr CT").status_code == 201


@pytest.mark.parametrize("key,text,ok", [(1, "Colonoscopy complete", True), (2, "  complete   COLONOSCOPY ", True),
                                         (3, "CT colonography complete", True), (4, "Colonoscopy incomplete", False),
                                         (5, "Caecum not reached", False), (6, "complete", False)])
def test_t_a67_plain_text_forms_are_a_short_allow_list(hapi, key, text, ok):
    cid, spec = reviewed_case(f"Dr Text {key}", patient_ref="Patient/c9")
    ref = hapi.put(report(f"r-text-{key}", case_id=cid, completeness=None, text=text))
    assert (close(cid, spec, ref, f"Dr Text {key}").status_code == 201) is ok


def test_t_a67_incomplete_wins_when_a_report_says_both(hapi):
    cid, spec = reviewed_case("Dr Both", patient_ref="Patient/c10")
    body = report("r-both", case_id=cid, completeness="complete")
    body["code"]["text"] = "Incomplete colonoscopy"
    body["code"]["coding"].append({"system": evidence.COMPLETENESS_SYSTEM, "code": "incomplete"})
    ref = hapi.put(body)
    assert close(cid, spec, ref, "Dr Both").status_code == 422


def test_t_a67_a_link_to_another_cases_report_proves_nothing(hapi):
    mine, spec = reviewed_case("Dr Mine", patient_ref="Patient/c11")
    theirs, _ = reviewed_case("Dr Theirs", patient_ref="Patient/c12")
    hapi.put(report("r-theirs", case_id=theirs))
    proc = procedure("p-mine", case_id=mine)
    proc["report"] = [{"reference": "DiagnosticReport/r-theirs"}]
    ref = hapi.put(proc)
    assert close(mine, spec, ref, "Dr Mine").status_code == 422


def test_t_a67_unsafe_links_are_ignored_not_fetched(hapi):
    cid, spec = reviewed_case("Dr Links", patient_ref="Patient/c13")
    proc = procedure("p-links", case_id=cid)
    proc["report"] = [{"reference": "DiagnosticReport/../Patient/x"}, {"reference": "http://evil/x"}, {"display": "x"}, "x"]
    ref = hapi.put(proc)
    assert close(cid, spec, ref, "Dr Links").status_code == 422
    assert not any("evil" in r or ".." in r for r in hapi.requests)


def test_t_a67_the_unit_verdict_of_the_static_verifier():
    v = evidence.StaticVerifier({"Procedure/a", "Procedure/b", "Procedure/c"},
                                completeness={"Procedure/b": "incomplete", "Procedure/c": "ct_colonography"})
    assert v.assess("Procedure/a", case_id=UUID(int=1)).acceptable
    assert v.assess("Procedure/c", case_id=UUID(int=1)).acceptable
    b = v.assess("Procedure/b", case_id=UUID(int=1))
    assert b.resolves and not b.acceptable and b.incomplete and "incomplete" in b.reason
    assert not v.assess("Procedure/zzz", case_id=UUID(int=1)).resolves


def test_t_a67_close_through_the_route_with_static_verifier_refuses_incomplete():
    saved = evidence.verifier
    try:
        evidence.verifier = evidence.StaticVerifier({"Procedure/inc"}, completeness={"Procedure/inc": "incomplete"})
        cid, spec = reviewed_case("Dr Static Inc", patient_ref="Patient/c14")
        r = post_event(cid, "close", spec, {"evidence_ref": "Procedure/inc", "signed_by": f"specialist_owner:{_uid('Dr Static Inc')}"})
        assert r.status_code == 422 and "not closure evidence" in r.json()["detail"]
    finally:
        evidence.verifier = saved


def _uid(name):
    from app.auth import opaque_user_id
    return opaque_user_id("specialist_owner", name)


def test_t_a67_verification_off_still_accepts_any_well_formed_reference():
    assert isinstance(evidence.verifier, evidence.AcceptAllVerifier)
    assert evidence.verifier.assess("Procedure/anything", case_id=UUID(int=1)).acceptable


# ---- T-A68: what the recorder writes is exactly what the verifier reads --------------------------------------------


@pytest.mark.parametrize("completeness,proc_status", [("complete", "completed"), ("incomplete", "stopped"),
                                                       ("ct_colonography", "completed")])
def test_t_a68_the_recorder_writes_what_the_verifier_reads(hapi, completeness, proc_status):
    case_id = UUID(int=900 + len(completeness))
    proc, rep = confirmatory.build_resources(case_id, {"reference": "Patient/rec"}, AT, completeness)
    assert proc["status"] == proc_status and rep["status"] == "final"
    assert proc["report"] == [{"reference": confirmatory.report_ref(case_id)}]
    assert rep["conclusionCode"][0]["coding"][0] == {"system": evidence.COMPLETENESS_SYSTEM, "code": completeness}
    hapi.put(rep)
    ref = hapi.put(proc)
    verdict = evidence.verifier.assess(ref, case_id=case_id, patient_ref="Patient/rec")
    assert verdict.resolves and verdict.acceptable is (completeness != "incomplete")
    assert evidence.assess_report(rep).completeness == completeness


def test_t_a68_an_unknown_completeness_is_refused_by_the_recorder():
    with pytest.raises(ValueError):
        confirmatory.build_resources(UUID(int=1), None, AT, "mostly")


def test_t_a68_the_route_passes_completeness_to_the_recorder_and_rejects_garbage():
    saved = confirmatory.recorder
    try:
        confirmatory.recorder = recorder = confirmatory.StaticRecorder()
        from test_confirmatory import booked_case as route_case
        cid, spec = route_case("Dr Route Comp")
        r = post_route(cid, spec, "incomplete")
        assert r.status_code == 201 and r.json()["completeness"] == "incomplete"
        assert post_route(cid, spec, None).json()["completeness"] == "complete"
        assert post_route(cid, spec, "ct_colonography").status_code == 201
        assert post_route(cid, spec, "mostly").status_code == 422
        assert recorder.completeness == ["incomplete", "complete", "ct_colonography"]
    finally:
        confirmatory.recorder = saved


def post_route(cid, headers, completeness):
    from api_helpers import make_client
    c = make_client()
    q = f"?completeness={completeness}" if completeness else ""
    return c.post(f"/cases/{cid}/confirmatory-result{q}", headers=headers)


# ---- T-A69: the automation flags an incomplete record instead of proposing a closure --------------------------------


def _scan_with(cid, completeness, ref=None):
    ref = ref or f"Procedure/colonoscopy-{cid}"
    evidence.verifier = evidence.StaticVerifier({ref}, completeness={ref: completeness})
    found(cid, ref)
    appointment_passed()
    return ref


def test_t_a69_a_complete_record_still_completes_the_step_and_proposes_the_closure(fresh):
    cid = booked_case()
    _scan_with(cid, "complete")
    out = evidence_scan()
    assert out["completed"] == 1 and out["closure_proposed"] == 1 and "incomplete_flagged" not in out


def test_t_a69_a_ct_colonography_record_is_proposed(fresh):
    cid = booked_case()
    ref = _scan_with(cid, "ct_colonography")
    out = evidence_scan()
    assert out["closure_proposed"] == 1 and model(cid).closure_proposal["evidence_ref"] == ref


def test_t_a69_an_incomplete_record_is_flagged_not_completed_and_not_proposed(fresh):
    cid = booked_case()
    ref = _scan_with(cid, "incomplete")
    before = types(cid)
    out = evidence_scan()
    assert out["incomplete_flagged"] == 1 and "completed" not in out and "closure_proposed" not in out
    tail = log(cid)[-1]
    assert (tail.type, tail.actor) == ("review_requested", AUTOMATION_ACTOR)
    assert tail.payload["reason"] == "incomplete_procedure" and tail.payload["evidence_ref"] == ref
    assert types(cid) == [*before, "review_requested"]
    m = model(cid)
    assert m.state == "confirmatory_booked" and m.closure_proposal is None
    assert m.incomplete_procedure["evidence_ref"] == ref
    assert m.to_json()["review_reason"] == "incomplete_procedure" and m.to_json()["incomplete_procedure"] is not None


def test_t_a69_the_flag_is_written_once_and_the_scan_does_not_ask_again_within_the_ttl(fresh):
    cid = booked_case()
    _scan_with(cid, "incomplete")
    evidence_scan()
    again = evidence_scan()
    assert again.get("skipped_recent_no") == 1 and types(cid).count("review_requested") == 1
    clock.advance(hours=7)
    assert evidence_scan().get("incomplete_flagged") == 1  # same event id: a retry, still one event in the log
    assert types(cid).count("review_requested") == 1


def test_t_a69_re_recording_a_complete_result_clears_the_flag_and_proposes(fresh):
    cid = booked_case()
    ref = _scan_with(cid, "incomplete")
    evidence_scan()
    assert model(cid).incomplete_procedure is not None
    evidence.verifier = evidence.StaticVerifier({ref}, completeness={ref: "ct_colonography"})
    clock.advance(hours=7)
    out = evidence_scan()
    assert out["closure_proposed"] == 1 and out["completed"] == 1
    m = model(cid)
    assert m.incomplete_procedure is None and m.closure_proposal["evidence_ref"] == ref and m.state == "confirmatory_completed"


def test_t_a69_incomplete_procedure_is_the_automations_alone_with_a_strict_schema(fresh):
    cid = booked_case()
    base = {"reason": "incomplete_procedure", "source_event_id": str(log(cid)[0].event_id),
            "evidence_ref": "Procedure/colonoscopy-x"}
    payloads.validate("review_requested", base)
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("review_requested", {k: v for k, v in base.items() if k != "evidence_ref"})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("review_requested", {**base, "reason": "opt_out"})  # evidence_ref belongs to this reason
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("review_requested", {**base, "evidence_ref": "Patient/1"})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("review_requested", {**base, "note": "free text"})
    from app.events import Event
    for who in ("system:triage", "system:consent", "system:scheduler"):
        with pytest.raises(InvalidTransition):
            cases.store.append(Event(UUID(cid), "review_requested", clock.now(), who, base), PATHWAY)


def test_t_a69_the_flag_is_refused_outside_the_scanned_states(fresh):
    from test_automation import referral_pending_case
    from app.events import Event
    cid = referral_pending_case()
    payload = {"reason": "incomplete_procedure", "source_event_id": str(log(cid)[0].event_id),
               "evidence_ref": "Procedure/colonoscopy-x"}
    with pytest.raises(InvalidTransition, match="incomplete_procedure: not allowed"):
        cases.store.append(Event(UUID(cid), "review_requested", clock.now(), AUTOMATION_ACTOR, payload), PATHWAY)


def test_t_a69_a_human_cannot_post_the_flag(fresh):
    cid = booked_case()
    payload = {"reason": "incomplete_procedure", "source_event_id": str(log(cid)[0].event_id),
               "evidence_ref": "Procedure/colonoscopy-x"}
    post(cid, "review_requested", login(*KARIM), payload, expect=403)


def test_t_a69_close_after_the_flag_needs_complete_evidence_and_clears_it(fresh):
    cid = booked_case()
    ref = _scan_with(cid, "incomplete")
    evidence_scan()
    karim = login(*KARIM)
    post(cid, "complete_confirmatory", karim)  # a person may complete the step; the verdict gates the closure
    post(cid, "clinician_review", karim)
    evidence.verifier = evidence.StaticVerifier({ref}, completeness={ref: "incomplete"})
    post(cid, "close", karim, {"evidence_ref": ref, "signed_by": actor(KARIM[0], KARIM[1])}, expect=422)
    evidence.verifier = evidence.StaticVerifier({ref}, completeness={ref: "ct_colonography"})
    page = post(cid, "close", karim, {"evidence_ref": ref, "signed_by": actor(KARIM[0], KARIM[1])})
    assert page["state"] == "closed" and page["read_model"]["incomplete_procedure"] is None
