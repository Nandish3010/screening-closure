"""ADR-060: one-click sign-in on the hosted demo. POST /demo/sign-in exists only under the demo gate, takes one of four fixed roles,
asks the identity provider (mocked here) for tokens with that role's demo user, and never returns or logs a password."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, demo_signin, policy
from app.config import settings
from app.routers import demo as demo_router
from fastapi import Depends

API_DIR = Path(__file__).resolve().parents[1]
PWS = {"coordinator": "pw-coord-SECRET-1", "clinician": "pw-clin-SECRET-2", "supervisor": "pw-sup-SECRET-3", "patient": "pw-pat-SECRET-4"}

mini = FastAPI(dependencies=[Depends(policy.enforce_route_policy)])
mini.include_router(demo_router.router)
client = TestClient(mini)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for role, pw in PWS.items():
        monkeypatch.setattr(settings, f"demo_signin_pw_{role}", pw)
    monkeypatch.setattr(settings, "demo_signin_token_url", "http://keycloak-prod:8080/auth/realms/screening-closure/protocol/openid-connect/token")
    monkeypatch.setattr(settings, "demo_signin_per_ip_limit", 3)
    monkeypatch.setattr(settings, "demo_signin_global_limit", 100)
    demo_signin.forget()
    yield
    demo_signin.forget()


class Idp:
    def __init__(self, status=200, body=None, raises=None):
        self.calls, self.status, self.raises = [], status, raises
        self.body = body if body is not None else {"access_token": "AT", "refresh_token": "RT", "id_token": "IT", "token_type": "Bearer",
                                                   "expires_in": 300, "refresh_expires_in": 1800, "scope": "openid", "session_state": "x",
                                                   "not-before-policy": 0}

    def __call__(self, url, form):
        self.calls.append((url, dict(form)))
        if self.raises:
            raise self.raises
        return httpx.Response(self.status, json=self.body)


def post(role, ip="203.0.113.7"):
    return client.post("/demo/sign-in", json={"role": role}, headers={"X-Forwarded-For": ip})


def test_each_of_the_four_roles_gets_tokens_for_its_fixed_user(monkeypatch):
    idp = Idp()
    monkeypatch.setattr(demo_signin, "_exchange", idp)
    for i, (role, user) in enumerate({"coordinator": "maryam.alsuwaidi", "clinician": "huda.rahman", "supervisor": "noura.alhammadi", "patient": "demo.patient"}.items()):
        r = post(role, ip=f"203.0.113.{i}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] == "AT" and body["refresh_token"] == "RT" and body["expires_in"] == 300
        assert body["client_id"] == settings.demo_signin_client_id
        url, form = idp.calls[-1]
        assert url.endswith("/protocol/openid-connect/token") and url.startswith("http://keycloak-prod:")  # private network
        assert (form["grant_type"], form["username"], form["password"], form["client_id"]) == ("password", user, PWS[role], "screening-closure-demo")


def test_the_answer_never_contains_a_password_or_an_unlisted_field(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp(body={"access_token": "AT", "password": "leaked", "session_state": "s", "x": 1}))
    r = post("coordinator")
    assert r.status_code == 200
    assert set(r.json()) == {"access_token", "client_id"}
    assert "password" not in r.text.lower() and not any(pw in r.text for pw in PWS.values())


@pytest.mark.parametrize("role", ["lab", "primary_care_owner", "specialist_owner", "owner", "Coordinator", "admin", "", "maryam.alsuwaidi", "../x"])
def test_only_the_four_fixed_roles_are_accepted(monkeypatch, role):
    idp = Idp()
    monkeypatch.setattr(demo_signin, "_exchange", idp)
    r = post(role)
    assert r.status_code == 422 and idp.calls == [] and "access_token" not in r.text


def test_extra_fields_are_refused_so_a_caller_cannot_choose_the_user(monkeypatch):
    idp = Idp()
    monkeypatch.setattr(demo_signin, "_exchange", idp)
    r = client.post("/demo/sign-in", json={"role": "coordinator", "username": "lab.intake"})
    assert r.status_code == 422 and idp.calls == []


def test_per_address_rate_limit_then_another_address_still_works(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    assert [post("patient").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert post("patient", ip="198.51.100.9").status_code == 200


def test_global_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "demo_signin_global_limit", 2)
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    assert [post("patient", ip=f"192.0.2.{i}").status_code for i in range(3)] == [200, 200, 429]


def test_the_visitor_is_the_edge_supplied_last_address_not_one_the_client_chose(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    for _ in range(3):  # a client prepending fake addresses does not get a fresh bucket
        assert client.post("/demo/sign-in", json={"role": "patient"}, headers={"X-Forwarded-For": "1.1.1.%d, 203.0.113.5" % _}).status_code == 200
    assert client.post("/demo/sign-in", json={"role": "patient"}, headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.5"}).status_code == 429


def test_identity_provider_failures_are_plain_503s_without_secrets(monkeypatch, caplog):
    for idp in (Idp(status=401, body={"error": "invalid_grant"}), Idp(raises=httpx.ConnectError("boom")), Idp(body={"nope": 1}), Idp(body=[1])):
        demo_signin.forget()
        monkeypatch.setattr(demo_signin, "_exchange", idp)
        with caplog.at_level(logging.INFO, logger="app.demo"):
            r = post("coordinator")
        assert r.status_code == 503 and "password sign-in" in r.json()["detail"]
        assert "Traceback" not in r.text and not any(pw in r.text for pw in PWS.values())
    assert not any(pw in rec.getMessage() for rec in caplog.records for pw in PWS.values())


def test_a_role_without_its_password_configured_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "demo_signin_pw_patient", "")
    idp = Idp()
    monkeypatch.setattr(demo_signin, "_exchange", idp)
    assert post("patient").status_code == 503 and idp.calls == []


def test_it_logs_the_role_and_nothing_personal(monkeypatch, caplog):
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    with caplog.at_level(logging.INFO, logger="app.demo"):
        post("supervisor", ip="203.0.113.99")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("role=supervisor" in m for m in msgs)
    assert not any("203.0.113.99" in m or "AT" == m or "noura" in m for m in msgs)


def test_the_token_url_defaults_to_the_jwks_host(monkeypatch):
    monkeypatch.setattr(settings, "demo_signin_token_url", "")
    monkeypatch.setattr(settings, "oidc_jwks_url", "http://keycloak-prod:8080/auth/realms/r/protocol/openid-connect/certs")
    assert demo_signin.token_url() == "http://keycloak-prod:8080/auth/realms/r/protocol/openid-connect/token"


def test_the_route_is_public_in_the_policy_table_and_nothing_else_is():
    assert policy.ROUTE_POLICY[("POST", "/demo/sign-in")] == policy.PUBLIC
    assert [k for k, v in policy.ROUTE_POLICY.items() if v == policy.PUBLIC and k[1].startswith("/demo")] == [("POST", "/demo/sign-in")]


def test_demo_passwords_outside_the_gate_refuse_to_start(monkeypatch):
    monkeypatch.setattr(settings, "demo_deployment", False)
    assert any("DEMO_SIGNIN_PW" in p for p in auth.secure_config_problems())


# ---- the gate, in real processes --------------------------------------------------------------------------------------------

def _status_in_process(extra: dict) -> dict:
    code = ("import json\nfrom fastapi.testclient import TestClient\nfrom app.main import app\n"
            "c = TestClient(app)\n"
            "r = c.post('/demo/sign-in', json={'role': 'coordinator'}, headers={'X-Forwarded-For': '203.0.113.1'})\n"
            "print(json.dumps({'status': r.status_code, 'body': r.text, 'paths': [p for p in app.openapi()['paths'] if p.startswith('/demo')]}))\n")
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED", "CLOCK_MODE", "DEMO_SIMULATED_CLOCK", "DEMO_DEPLOYMENT")}
    env.update(PYTHONPATH=str(API_DIR), DEV_AUTH_SECRET="k" * 40, **extra)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
    assert done.returncode == 0, done.stderr[-600:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("extra", [
    dict(APP_ENV="dev"),
    dict(APP_ENV="dev", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1", CLOCK_MODE="sim", DEMO_SIGNIN_PW_COORDINATOR="x" * 20),
    dict(APP_ENV="production", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1"),
    dict(APP_ENV="production", CLOCK_MODE="wall", DEMO_DEPLOYMENT="1"),
    dict(APP_ENV="production", CLOCK_MODE="wall", DEMO_SIGNIN_PW_COORDINATOR="x" * 20),
])
def test_404_without_the_gate_in_dev_and_in_production_without_the_demo_flags(extra):
    got = _status_in_process(extra)
    assert got["status"] == 404 and got["paths"] == [] and "access_token" not in got["body"]


def test_the_route_exists_under_the_full_gate_and_the_app_still_validates_the_role():
    got = _status_in_process(dict(APP_ENV="production", AUTH_MODE="oidc", OIDC_ISSUER="https://h/auth/realms/r", CLOCK_MODE="sim",
                                  DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1", DATABASE_URL="postgresql://x:y@localhost:1/z",
                                  SCHEDULER_SERVICE_TOKEN="s" * 40, WATCHDOG_SERVICE_TOKEN="w" * 40, AUTOMATION_SERVICE_TOKEN="a" * 40,
                                  DEV_LOGIN_ENABLED="false", FHIR_VERIFY_EVIDENCE="true"))
    # password not configured in this process -> the route exists (not 404) and says it is not set up
    assert got["status"] == 503 and "/demo/sign-in" in got["paths"], got
