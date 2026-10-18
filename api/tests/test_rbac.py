"""T-A26: identity and RBAC. Role comes from the authenticated identity, the event actor is
derived from it, a wrong-role call is refused with nothing written, routes without a policy
entry fail closed, and automated actors cannot authenticate."""

import base64
import json
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth, policy
from app.config import settings
from app.routers import cases
from api_helpers import ROLES, actor_of, login_headers, make_client, user_id_of

client = make_client()
EVIDENCE = "DiagnosticReport/1"


def signed(role="specialist_owner", name=None):
    """A close payload signed as the logged-in clinician (A15): signed_by is their own identity."""
    return {"evidence_ref": EVIDENCE, "signed_by": f"Practitioner/{user_id_of(role, name or f'test {role}')}"}


SPEC_PROVIDER = "test specialist_owner"  # a clinician's default provider is their login name


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def new_case(patient_ref="Patient/1"):
    r = client.post("/cases", json={"patient_ref": patient_ref}, headers=H("coordinator"))
    assert r.status_code == 201
    return r.json()["case_id"]


def post(case_id, type_, headers, payload=None, **extra):
    return client.post(
        f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}, **extra},
        headers=headers,
    )


def event_count(case_id):
    return len(cases.store.events_for(UUID(case_id)))


# -- identity ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_each_role_gets_the_expected_me(role):
    h = H(role, "Some Name")
    me = client.get("/me", headers=h).json()
    assert me == {"user_id": user_id_of(role, "Some Name"), "role": role, "display_name": "Some Name"}
    assert "Some Name" not in me["user_id"]  # opaque (A14)


def test_patient_me_includes_patient_ref():
    me = client.get("/me", headers=H("patient", "pat", "Patient/9")).json()
    assert me["patient_ref"] == "Patient/9"


def test_dev_login_response_has_a_usable_token():
    r = client.post("/auth/dev-login", json={"role": "supervisor", "name": "sam"}).json()
    assert r["role"] == "supervisor" and r["token_type"] == "bearer"
    assert client.get("/me", headers={"Authorization": f"Bearer {r['token']}"}).json()["role"] == "supervisor"


@pytest.mark.parametrize("role", ["system", "scheduler", "watchdog", "model"])
def test_automated_actors_cannot_authenticate(role):
    r = client.post("/auth/dev-login", json={"role": role, "name": "x"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"role": "doctor", "name": "x"},
        {"role": "lab", "name": ""},
        {"role": "lab", "name": "a:b"},  # a colon would make the actor ambiguous
        {"role": "lab", "name": "x", "patient_ref": "Patient/1"},
        {"role": "patient", "name": "x", "patient_ref": "../etc"},
        {"role": "lab", "name": "x", "extra": 1},
    ],
)
def test_dev_login_rejects_bad_input(body):
    assert client.post("/auth/dev-login", json=body).status_code in (403, 422)


def forge(claims, secret=None):
    body = auth._b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{auth._sign(body, secret or settings.dev_auth_secret)}"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "garbage",
        "a.b",
        forge({"v": 1, "role": "coordinator", "name": "x"}, secret="wrong-secret"),
        forge({"v": 1, "role": "model", "name": "drafter"}),  # validly signed, automated role
        forge({"v": 1, "role": "scheduler", "name": "x"}),
        forge({"v": 1, "role": "lab", "name": "a:b"}),
        forge({"v": 1}),
    ],
)
def test_bad_tokens_are_401(token):
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_tampered_token_is_rejected():
    token = client.post("/auth/dev-login", json={"role": "lab", "name": "x"}).json()["token"]
    body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    claims["role"] = "supervisor"
    forged_body = auth._b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    assert client.get("/me", headers={"Authorization": f"Bearer {forged_body}.{sig}"}).status_code == 401


def test_unauthenticated_requests_are_401_except_public_routes():
    assert client.get("/health").status_code == 200
    for method, path in [("get", "/me"), ("get", "/cases"), ("get", "/pathways"), ("post", "/cases")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_dev_login_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "dev_login_enabled", False)
    assert client.post("/auth/dev-login", json={"role": "lab", "name": "x"}).status_code == 404


def test_insecure_defaults_refuse_to_start_outside_dev(monkeypatch):
    """The short version; the full matrix is api/tests/test_prod_config.py (SEC-1)."""
    auth.assert_secure_config()  # dev: fine
    monkeypatch.setattr(settings, "app_env", "prod")
    with pytest.raises(RuntimeError, match="DEV_AUTH_SECRET"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "dev_auth_secret", "x" * 40)
    with pytest.raises(RuntimeError, match="SCHEDULER_SERVICE_TOKEN"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "scheduler_service_token", "y" * 40)
    with pytest.raises(RuntimeError, match="WATCHDOG_SERVICE_TOKEN"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "watchdog_service_token", "z" * 40)
    with pytest.raises(RuntimeError, match="AUTOMATION_SERVICE_TOKEN"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "automation_service_token", "q" * 40)
    with pytest.raises(RuntimeError, match="DEV_LOGIN_ENABLED"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "dev_login_enabled", False)
    auth.assert_secure_config()


# -- actor derived from identity -----------------------------------------------------------


def test_actor_is_derived_from_identity_and_a_body_actor_is_rejected():
    case_id = new_case()
    before = event_count(case_id)
    r = post(case_id, "acknowledge", H("lab", "lab one"), actor="clinician:dr_who")
    assert r.status_code == 422
    assert event_count(case_id) == before
    ok = post(case_id, "acknowledge", H("lab", "lab one"))
    assert ok.status_code == 201
    assert ok.json()["events"][-1]["actor"] == actor_of("lab", "lab one")


def test_events_cannot_be_dated_after_the_simulation_clock():
    case_id = new_case()
    r = post(case_id, "acknowledge", H("lab"), at="2099-01-01T00:00:00Z")
    assert r.status_code == 422
    r = post(case_id, "acknowledge", H("lab"), at="2026-01-04T00:00:00Z")  # SEC-2: a user may not backdate
    assert r.status_code == 422 and "backdated" in r.json()["detail"]
    assert post(case_id, "acknowledge", H("lab")).status_code == 201


# -- wrong role is refused and nothing is written --------------------------------------------


def walk(case_id, upto):
    """Advance a case with the right roles: lab, owner, patient, specialist..."""
    steps = [
        ("acknowledge", "lab", {}),
        ("accept_ownership", "primary_care_owner", {}),
        ("contact_patient", "primary_care_owner", {}),
        ("patient_acknowledge", "patient", {}),
        # ownership passes to the specialist only on acceptance (A5)
        ("handoff_requested", "primary_care_owner", {"to_provider": SPEC_PROVIDER}),
        ("handoff_accepted", "specialist_owner", {}),
        ("book_confirmatory", "specialist_owner", {}),
        ("complete_confirmatory", "specialist_owner", {}),
        ("clinician_review", "specialist_owner", {}),
    ]
    for type_, role, payload in steps[:upto]:
        h = H(role, patient_ref="Patient/1" if role == "patient" else None)
        assert post(case_id, type_, h, payload).status_code == 201, type_


@pytest.mark.parametrize(
    "type_,wrong_role",
    [
        ("accept_ownership", "lab"),
        ("accept_ownership", "patient"),
        ("accept_ownership", "coordinator"),
        ("close", "coordinator"),
        ("close", "supervisor"),
        ("close", "lab"),
        ("close", "patient"),
        ("complete_confirmatory", "primary_care_owner"),
        ("escalate", "lab"),
        ("mark_overdue", "supervisor"),
        ("message_sent", "supervisor"),
        ("watchdog_flagged", "coordinator"),
        ("reopen", "coordinator"),
    ],
)
def test_wrong_role_gets_403_and_writes_nothing(type_, wrong_role):
    case_id = new_case()
    walk(case_id, 9)  # clinician_reviewed: close is otherwise valid
    before = event_count(case_id)
    r = post(case_id, type_, H(wrong_role, patient_ref="Patient/1" if wrong_role == "patient" else None),
             signed() if type_ == "close" else {})
    assert r.status_code == 403, r.text
    assert event_count(case_id) == before


def test_owner_can_close_with_evidence_and_the_actor_is_recorded():
    case_id = new_case()
    walk(case_id, 9)
    r = post(case_id, "close", H("specialist_owner"), signed())
    assert r.status_code == 201 and r.json()["state"] == "closed"
    assert r.json()["events"][-1]["actor"] == actor_of("specialist_owner")


def test_close_without_evidence_is_refused_for_an_allowed_role_and_writes_nothing():
    # 422 from the payload schema (A14); the state machine's own guard (409) is tested directly.
    case_id = new_case()
    walk(case_id, 9)
    before = event_count(case_id)
    assert post(case_id, "close", H("specialist_owner"), {}).status_code == 422
    assert event_count(case_id) == before


def test_unlisted_event_types_cannot_be_posted_by_anyone():
    case_id = new_case()
    for role in [*ROLES, "scheduler"]:
        h = auth_service_headers() if role == "scheduler" else H(role)
        for type_ in ("case_created", "bogus_event", "message_sent"):
            assert post(case_id, type_, h).status_code == 403, (role, type_)


def auth_service_headers():
    return {"Authorization": f"Bearer {settings.scheduler_service_token}"}


# -- patients ------------------------------------------------------------------------------


def test_patient_may_only_act_on_their_own_case_and_only_three_event_types():
    mine, theirs = new_case("Patient/1"), new_case("Patient/2")
    pat = H("patient", "pat", "Patient/1")
    assert client.get(f"/cases/{mine}", headers=pat).status_code == 200
    assert client.get(f"/cases/{theirs}", headers=pat).status_code == 404  # looks like unknown
    assert client.get(f"/cases/{uuid4()}", headers=pat).status_code == 404
    # a patient's list holds their own cases only (A9)
    listed = client.get("/cases", headers=pat).json()
    assert mine in [c["case_id"] for c in listed] and theirs not in [c["case_id"] for c in listed]
    assert post(theirs, "consent_updated", pat, {"withdrawn": True}).status_code == 404
    assert post(theirs, "callback_requested", pat).status_code == 404
    assert post(mine, "callback_requested", pat).status_code == 201
    assert post(mine, "consent_updated", pat, {"withdrawn": True}).status_code == 201
    allowed = {t for t, roles in policy.EVENT_POLICY.items() if "patient" in roles}
    assert allowed == {"patient_acknowledge", "consent_updated", "callback_requested"}
    for type_ in set(policy.EVENT_POLICY) - allowed:
        assert post(mine, type_, pat).status_code == 403, type_


def test_patient_without_a_patient_ref_sees_no_case():
    case_id = new_case("Patient/1")
    nobody = H("patient", "pat")
    assert client.get(f"/cases/{case_id}", headers=nobody).status_code == 404


def test_staff_and_patient_routes_by_role():
    for role in ("lab", "coordinator", "supervisor"):
        assert client.post("/cases", json={}, headers=H(role)).status_code == 201, role
    for role in ("primary_care_owner", "specialist_owner", "patient"):
        assert client.post("/cases", json={}, headers=H(role)).status_code == 403, role
    for role in ROLES:
        assert client.get("/cases", headers=H(role)).status_code == 200, role  # patients: their own (A9)


# -- service identity ----------------------------------------------------------------------


def test_scheduler_service_token_maps_to_system_scheduler_and_its_event_types():
    sched = auth_service_headers()
    me = client.get("/me", headers=sched).json()
    assert me["user_id"] == "system:scheduler" and me["role"] == "scheduler"
    case_id = new_case()
    assert post(case_id, "acknowledge", sched).status_code == 403
    assert post(case_id, "close", sched, signed()).status_code == 403
    assert client.get("/cases", headers=sched).status_code == 403
    assert client.post("/cases", json={}, headers=sched).status_code == 403
    allowed = {t for t, roles in policy.EVENT_POLICY.items() if "scheduler" in roles}
    # handoff_rejected: only the bounce (reason timeout), enforced by the state machine (A5)
    assert allowed == {"mark_overdue", "escalate", "handoff_rejected"}  # watchdog_flagged moved to the watchdog (A11)


def test_wrong_service_token_is_401():
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


# -- fail closed ---------------------------------------------------------------------------


def test_a_route_without_a_policy_entry_is_denied_for_everyone():
    probe = FastAPI(dependencies=[Depends(policy.enforce_route_policy)])

    @probe.get("/undocumented")
    def undocumented():  # pragma: no cover - must never run
        return {"leaked": True}

    c = TestClient(probe)
    assert c.get("/undocumented").status_code == 403
    token = auth.issue_token("supervisor", "boss")
    assert c.get("/undocumented", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert c.get("/undocumented", headers=auth_service_headers()).status_code == 403


def test_every_real_route_has_a_policy_entry_and_no_entry_is_stale():
    from app.main import app

    routes = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }
    assert routes == set(policy.ROUTE_POLICY), routes ^ set(policy.ROUTE_POLICY)


def test_event_policy_covers_exactly_the_pathway_vocabulary():
    from app.main import pathway

    postable = set(pathway.event_types) - {"case_created"}
    assert set(policy.EVENT_POLICY) == postable, set(policy.EVENT_POLICY) ^ postable
    # only roles that exist
    for roles in policy.EVENT_POLICY.values():
        assert roles <= set(ROLES) | {"scheduler", "watchdog", "automation"}


def test_decision_matrix_examples():
    p = policy.EVENT_POLICY
    assert p["accept_ownership"] == {"primary_care_owner", "specialist_owner", "supervisor"}
    assert p["close"] == {"primary_care_owner", "specialist_owner"}
    assert "coordinator" not in p["close"] and "supervisor" not in p["close"]
