"""ADR-060: one-click sign-in on the hosted demo. POST /demo/sign-in exists only under the demo gate, takes one of four fixed roles,
asks the identity provider (mocked here) for tokens with that role's demo user, and never returns or logs a password."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

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
    monkeypatch.setattr(settings, "demo_signin_window_seconds", 600)
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
        return self.status, (self.body if self.status == 200 else None)


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


def test_a_429_carries_a_stable_code_and_retry_after(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    for _ in range(3):
        post("patient")
    r = post("patient")
    assert r.status_code == 429 and r.json()["code"] == "signin_rate_limited"
    assert 1 <= int(r.headers["Retry-After"]) <= 600 and r.json()["retry_after"] == int(r.headers["Retry-After"])
    monkeypatch.setattr(settings, "demo_signin_global_limit", 3)
    g = post("patient", ip="198.51.100.77")
    assert g.status_code == 429 and g.json()["code"] == "signin_busy" and int(g.headers["Retry-After"]) >= 1


def test_the_retry_after_shrinks_as_the_window_moves(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    t = [1000.0]
    monkeypatch.setattr(demo_signin, "_mono", lambda: t[0])
    for _ in range(3):
        post("patient")
    t[0] += 500
    assert int(post("patient").headers["Retry-After"]) == 100
    t[0] += 101  # the first attempts have aged out of the 600 s window
    assert post("patient").status_code == 200


def _default(name):
    return type(settings).model_fields[name].default


def test_the_default_limits_let_a_shared_nat_panel_in_but_still_bound_abuse():
    assert _default("demo_signin_per_ip_limit") >= 120
    assert _default("demo_signin_global_limit") >= 4 * _default("demo_signin_per_ip_limit") // 1  # several such panels at once
    assert _default("demo_signin_global_limit") <= 5000  # and still a ceiling


def test_sixty_sign_ins_from_one_address_are_not_limited_at_the_defaults(monkeypatch):
    monkeypatch.setattr(settings, "demo_signin_per_ip_limit", _default("demo_signin_per_ip_limit"))
    monkeypatch.setattr(settings, "demo_signin_global_limit", _default("demo_signin_global_limit"))
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    assert {post("coordinator", ip="203.0.113.50").status_code for _ in range(60)} == {200}
    r = [post("coordinator", ip="203.0.113.50").status_code for _ in range(70)]
    assert r.count(429) == 10  # the 121st and later are refused


def test_attempts_that_failed_because_the_provider_was_not_ready_are_not_counted(monkeypatch):
    # the limit is 3 per address: these failures must not use any of it
    for idp in (Idp(raises=ConnectionRefusedError("boom")), Idp(status=502), Idp(status=503), Idp(status=404), Idp(body={"nope": 1})):
        monkeypatch.setattr(demo_signin, "_exchange", idp)
        for _ in range(4):
            r = post("coordinator")
            assert r.status_code == 503 and r.json()["code"] == "signin_warming_up" and r.headers["Retry-After"] == "5", idp.status
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    assert [post("coordinator").status_code for _ in range(4)] == [200, 200, 200, 429]  # the full allowance is still there


def test_a_provider_refusal_and_a_lockout_are_not_counted_either_but_are_not_warming_up(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp(status=401))
    for _ in range(5):
        r = post("coordinator")
        assert r.status_code == 503 and r.json()["code"] == "signin_refused" and "Retry-After" not in r.headers

    class Locked(Idp):
        def __call__(self, url, form):
            return 400, {"error": "invalid_grant", "error_description": "Account temporarily disabled"}
    monkeypatch.setattr(demo_signin, "_exchange", Locked())
    r = post("coordinator")
    assert r.status_code == 503 and r.json()["code"] == "signin_locked" and r.headers["Retry-After"] == "60"
    assert len(demo_signin._all) == 0


def test_successes_are_still_counted_when_failures_are_mixed_in(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", Idp(raises=ConnectionRefusedError("x")))
    post("patient")
    monkeypatch.setattr(demo_signin, "_exchange", Idp())
    post("patient"); post("patient")
    assert len(demo_signin._all) == 2 and len(demo_signin._per_ip["203.0.113.7"]) == 2


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
    for idp in (Idp(status=401, body={"error": "invalid_grant"}), Idp(raises=ConnectionRefusedError("boom")), Idp(body={"nope": 1}), Idp(body=[1])):
        demo_signin.forget()
        monkeypatch.setattr(demo_signin, "_exchange", idp)
        with caplog.at_level(logging.INFO, logger="app.demo"):
            r = post("coordinator")
        assert r.status_code == 503 and r.json()["code"].startswith("signin_") and r.json()["detail"]
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


# ---- the real HTTP exchange against a local stand-in for Keycloak's token endpoint ------------------------------------------------

def test_exchange_posts_a_form_and_parses_json_and_reports_refusals_and_outages():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            seen["form"], seen["ctype"] = {k: v[0] for k, v in form.items()}, self.headers["Content-Type"]
            ok = form.get("password") == ["right"]
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"access_token": "AT"} if ok else {"error": "invalid_grant"}).encode())

        def log_message(self, *a):  # silent
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/token"
    try:
        assert demo_signin._exchange(url, {"password": "right", "username": "u"}) == (200, {"access_token": "AT"})
        assert seen["ctype"] == "application/x-www-form-urlencoded" and seen["form"]["username"] == "u"
        assert demo_signin._exchange(url, {"password": "wrong"}) == (401, {"error": "invalid_grant"})
    finally:
        srv.shutdown()
        srv.server_close()
    with pytest.raises(OSError):
        demo_signin._exchange(url, {"password": "right"})  # nothing listening any more
    with pytest.raises(OSError):
        demo_signin._exchange("file:///etc/passwd", {})


def test_the_client_secret_is_sent_when_configured_and_never_returned(monkeypatch):
    monkeypatch.setattr(settings, "demo_signin_client_secret", "client-SECRET-xyz")
    idp = Idp()
    monkeypatch.setattr(demo_signin, "_exchange", idp)
    r = post("coordinator")
    assert r.status_code == 200 and "client-SECRET-xyz" not in r.text and "client_secret" not in r.text
    assert idp.calls[-1][1]["client_secret"] == "client-SECRET-xyz"


def test_no_client_secret_field_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "demo_signin_client_secret", "")
    idp = Idp()
    monkeypatch.setattr(demo_signin, "_exchange", idp)
    assert post("coordinator").status_code == 200 and "client_secret" not in idp.calls[-1][1]


@pytest.mark.parametrize("status,body", [(400, {"error": "invalid_grant", "error_description": "Account temporarily disabled"}),
                                         (400, {"error": "invalid_grant", "error_description": "Account disabled"}),
                                         (401, {"error": "invalid_grant", "error_description": "User is locked"})])
def test_a_locked_user_is_reported_as_a_temporary_lock_with_a_wait_not_an_opaque_503(monkeypatch, status, body):
    monkeypatch.setattr(demo_signin, "_exchange", lambda url, form: (status, body))
    r = post("supervisor")
    assert r.status_code == 503
    msg = r.json()["detail"]
    assert "supervisor" in msg and "temporarily locked" in msg and f"{demo_signin.LOCKOUT_RETRY_SECONDS} s" in msg
    assert PWS["supervisor"] not in r.text


def test_other_refusals_stay_the_generic_message(monkeypatch):
    monkeypatch.setattr(demo_signin, "_exchange", lambda url, form: (401, {"error": "unauthorized_client", "error_description": "Invalid client secret"}))
    r = post("supervisor")
    assert r.status_code == 503 and "temporarily locked" not in r.json()["detail"] and "refused" in r.json()["detail"]
