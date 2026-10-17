"""D9: the score and its explanation as served by `GET /cases` and `GET /cases/{id}`, and the pure
scoring functions. The shipped model's coefficient signs are pinned here so a retrain that flips a
sensible effect is noticed. The hard rule (the score orders a worklist and decides nothing) is in
test_risk_boundary.py."""

import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from api_helpers import login_headers, make_client
from app.clock import clock
from app.events import Event, InMemoryEventStore
from app.risk import features as F
from app.risk import score as S
from app.risk.model import MODEL_PATH, default_model
from app.routers import cases
from app.state_machine import CaseModel

client = make_client()


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    clock.reset()
    yield
    clock.reset()


def post(cid, type_, headers, payload=None):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def new_case(patient_ref="Patient/rk-1", **extra):
    r = client.post("/cases", json={"patient_ref": patient_ref, **extra}, headers=H("coordinator", "rk coord"))
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


# ---------------------------------------------------------------- the pure functions

T0 = datetime(2026, 1, 5, 8, tzinfo=UTC)


def case_at(stage="patient_contacted", days=14.0, **kw):
    return F.CaseFeatures(stage=stage, days_since_result=days, **kw)


def test_the_score_is_a_probability_for_every_stage_and_extreme():
    for stage in F.STAGES:
        for days in (0, 10, 90, 500):
            for kw in ({}, {"consent_withdrawn": True, "prior_bounces": 3, "prior_escalations": 3}, {"contact_valid": False}):
                p = S.score_features(case_at(stage, days, **kw))
                assert 0.0 <= p <= 1.0


def test_a_case_with_nothing_left_to_lose_scores_zero_with_no_factors():
    assert S.score_features(None) == 0.0 and S.explain_features(None) == {"score": 0.0, "top_factors": []}
    done = CaseModel(state="confirmatory_completed")
    ev = [Event(uuid4(), "case_created", T0, "system:intake", {})]
    assert S.score_case(done, ev, T0, T0 + timedelta(days=30)) == 0.0
    assert S.explain_case(CaseModel(state="closed"), ev, T0, T0) == {"score": 0.0, "top_factors": []}


def test_the_shipped_model_has_the_signs_the_pathway_implies():
    base = S.score_features(case_at())
    assert S.score_features(case_at(consent_withdrawn=True)) > base + 0.2          # a withdrawn patient is the likeliest to be lost
    assert S.score_features(case_at(days=60)) > S.score_features(case_at(days=7))   # the longer a case stalls, the likelier it is lost
    assert S.score_features(case_at("confirmatory_booked")) < S.score_features(case_at("acknowledged")) - 0.3  # a booked colonoscopy is the safest place to be
    assert S.score_features(case_at("patient_acknowledged")) < S.score_features(case_at("patient_contacted"))


def test_unknown_attributes_are_imputed_and_change_nothing_by_themselves():
    plain = S.explain_features(case_at())
    assert plain["score"] == S.score_features(case_at())
    names = {t["feature"] for t in plain["top_factors"]}
    assert names <= {"stage", "days_since_result", "prior_escalations", "prior_bounces", "consent_withdrawn", "provider_changed", "contact_valid"}


def test_top_factors_are_the_three_largest_in_absolute_log_odds_and_say_what_they_saw():
    f = case_at("acknowledged", 75.0, consent_withdrawn=True, prior_bounces=2, language="en", channel="letter",
                contact_valid=False, age_band="60-69")
    out = S.explain_features(f)
    tops = out["top_factors"]
    assert len(tops) == 3 and set(tops[0]) == {"feature", "value", "contribution"}
    mags = [abs(t["contribution"]) for t in tops]
    assert mags == sorted(mags, reverse=True)
    full = default_model().contributions(f)
    assert {t["feature"] for t in tops} == {k for k, _ in sorted(full.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:3]}
    by_feature = {t["feature"]: t for t in tops}
    if "consent_withdrawn" in by_feature:
        assert by_feature["consent_withdrawn"]["value"] is True
    assert out["score"] == S.score_features(f)


def test_scoring_is_pure_and_repeatable():
    ev = [Event(uuid4(), "case_created", T0, "system:intake", {"contact_valid": False})]
    before = copy.deepcopy([(e.type, e.payload) for e in ev])
    m = CaseModel(state="owner_accepted")
    a = S.score_case(m, ev, T0, T0 + timedelta(days=20))
    assert a == S.score_case(m, ev, T0, T0 + timedelta(days=20))
    assert before == [(e.type, e.payload) for e in ev]
    assert S.explain_case(m, ev, T0, T0 + timedelta(days=20)) == S.explain_case(m, ev, T0, T0 + timedelta(days=20))


def test_the_model_file_is_loaded_once():
    assert default_model() is default_model()
    assert json.loads(MODEL_PATH.read_text())["training"]["n_cases"] > 0


# ---------------------------------------------------------------- over HTTP


def drive(cid, depth):
    lab, pco = H("lab", "rk lab"), H("primary_care_owner", "rk pco")
    steps = [("acknowledge", lab, {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
             ("patient_acknowledge", H("patient", "rk pat", "Patient/rk-1"), {}),
             ("handoff_requested", pco, {"to_provider": "endo-1"})]
    for type_, headers, payload in steps[:depth]:
        post(cid, type_, headers, payload)


def test_get_cases_fills_risk_score_for_staff_and_leaves_it_null_for_patients():
    cid = new_case()
    drive(cid, 3)
    for role in ("coordinator", "supervisor", "lab", "primary_care_owner", "specialist_owner"):
        rows = client.get("/cases", headers=H(role, f"rk {role}")).json()
        assert [r["case_id"] for r in rows] == [cid]
        assert isinstance(rows[0]["risk_score"], float) and 0.0 <= rows[0]["risk_score"] <= 1.0, role
    patient = client.get("/cases", headers=H("patient", "rk pat", "Patient/rk-1")).json()
    assert [r["case_id"] for r in patient] == [cid] and patient[0]["risk_score"] is None


def test_a_case_page_carries_the_explanation_for_staff_only():
    cid = new_case(contact_valid=False)
    drive(cid, 3)
    page = client.get(f"/cases/{cid}", headers=H("coordinator", "rk coord")).json()
    assert page["risk"]["score"] == page["risk_score"]
    tops = page["risk"]["top_factors"]
    assert 1 <= len(tops) <= 3 and all(set(t) == {"feature", "value", "contribution"} for t in tops)
    assert [abs(t["contribution"]) for t in tops] == sorted((abs(t["contribution"]) for t in tops), reverse=True)
    listed = client.get("/cases", headers=H("coordinator", "rk coord")).json()[0]
    assert listed["risk_score"] == page["risk_score"]
    patient = client.get(f"/cases/{cid}", headers=H("patient", "rk pat", "Patient/rk-1")).json()
    assert patient["risk"] is None and patient["risk_score"] is None
    # a response to a patient's own event does not leak it either
    r = post(cid, "consent_updated", H("patient", "rk pat", "Patient/rk-1"), {"channel_sms": False})
    assert r["risk"] is None and r["risk_score"] is None
    assert post(cid, "patient_acknowledge", H("coordinator", "rk coord"))["risk"]["score"] >= 0.0  # staff do get it back


def test_the_score_moves_with_what_the_log_shows_but_the_worklist_order_does_not():
    a = new_case("Patient/rk-a")
    new_case("Patient/rk-b")
    drive(a, 3)
    order = [r["case_id"] for r in client.get("/cases", headers=H("coordinator", "rk coord")).json()]
    r0 = {r["case_id"]: r["risk_score"] for r in client.get("/cases", headers=H("coordinator", "rk coord")).json()}
    clock.advance(days=40)
    rows = client.get("/cases", headers=H("coordinator", "rk coord")).json()
    assert [r["case_id"] for r in rows] == order            # the API never reorders by risk: the web's concern
    assert {r["case_id"]: r["risk_score"] for r in rows}[a] != r0[a]  # but the score itself moved (more days since the result)
    post(a, "consent_updated", H("patient", "rk pat", "Patient/rk-a"), {"withdrawn": True})
    after = {r["case_id"]: r["risk_score"] for r in client.get("/cases", headers=H("coordinator", "rk coord")).json()}
    assert after[a] > {r["case_id"]: r["risk_score"] for r in rows}[a]


def test_a_completed_colonoscopy_scores_zero():
    cid = new_case()
    drive(cid, 3)
    pco = H("primary_care_owner", "rk pco")
    post(cid, "patient_acknowledge", H("patient", "rk pat", "Patient/rk-1"))
    post(cid, "handoff_requested", pco, {"to_provider": "endo-1"})
    spec = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": "rk spec", "provider": "endo-1"})
    spec = {"Authorization": f"Bearer {spec.json()['token']}"}
    post(cid, "handoff_accepted", spec)
    post(cid, "book_confirmatory", spec)
    booked = client.get("/cases", headers=H("coordinator", "rk coord")).json()[0]["risk_score"]
    post(cid, "complete_confirmatory", spec)
    done = client.get("/cases", headers=H("coordinator", "rk coord")).json()[0]
    assert done["state"] == "confirmatory_completed" and done["risk_score"] == 0.0 and booked > 0.0


def test_a_broken_model_never_breaks_the_worklist(monkeypatch):
    cid = new_case()
    drive(cid, 2)

    def boom(*a, **k):
        raise FileNotFoundError("model.json")

    monkeypatch.setattr(cases.risk, "score_case", boom)
    monkeypatch.setattr(cases.risk, "explain_case", boom)
    rows = client.get("/cases", headers=H("coordinator", "rk coord")).json()
    assert [r["risk_score"] for r in rows] == [None]
    page = client.get(f"/cases/{cid}", headers=H("coordinator", "rk coord"))
    assert page.status_code == 200 and page.json()["risk"] is None and page.json()["risk_score"] is None


def test_creating_a_case_returns_a_row_without_risk():
    r = client.post("/cases", json={"patient_ref": "Patient/rk-9"}, headers=H("coordinator", "rk coord"))
    assert r.status_code == 201 and r.json()["risk"] is None and r.json()["risk_score"] is None
