"""ADR-060: the deploy side of one-click demo sign-in: where the demo passwords may go, which Keycloak client allows direct grants,
the edge location, and the smoke test. No cloud and no Docker."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

GCP = REPO_ROOT / "deploy" / "gcp"
sys.path.insert(0, str(GCP / "vm"))
import render_env  # noqa: E402
import render_realm  # noqa: E402

HOST = "34-1-2-3.sslip.io"
SECRETS = {n: (n.replace("-", "") + "0123456789abcdef" * 4)[:48] for n in render_env.REQUIRED}
ROLE_SECRET = {"COORDINATOR": "pw-coordinator", "CLINICIAN": "pw-clinician", "SUPERVISOR": "pw-supervisor", "PATIENT": "pw-patient"}


def test_demo_passwords_are_in_api_env_only_and_only_in_production_demo_mode():
    prod = render_env.build_files(HOST, SECRETS, "production")
    for role, secret in ROLE_SECRET.items():
        assert prod["api.env"][f"DEMO_SIGNIN_PW_{role}"] == SECRETS[secret]
    assert prod["api.env"]["DEMO_SIGNIN_CLIENT_ID"] == "screening-closure-demo"
    assert prod["api.env"]["DEMO_SIGNIN_TOKEN_URL"].startswith("http://keycloak-prod:8080/auth/realms/screening-closure/")  # private network
    assert prod["api.env"]["DEMO_SIMULATED_CLOCK"] == "1" and prod["api.env"]["DEMO_DEPLOYMENT"] == "1"  # the gate they live behind
    for name in ("compose.env", "scheduler.env", "watchdog.env"):
        assert not any(k.startswith("DEMO_SIGNIN") for k in prod[name]), name
    seed = render_env.build_files(HOST, SECRETS, "seed")
    assert not any(k.startswith("DEMO_SIGNIN") for f in seed.values() for k in f)  # the seed phase (dev login) has no use for them


def test_only_the_four_demo_roles_have_a_password_and_the_other_users_do_not():
    keys = {k for k in render_env.build_files(HOST, SECRETS)["api.env"] if k.startswith("DEMO_SIGNIN_PW_")}
    assert keys == {f"DEMO_SIGNIN_PW_{r}" for r in ROLE_SECRET}
    assert not any(s in json.dumps(render_env.build_files(HOST, SECRETS)["api.env"]) for s in (SECRETS["pw-lab"], SECRETS["pw-owner"], SECRETS["pw-specialist"]))


def test_rendered_production_api_env_passes_the_start_up_check_with_the_passwords(monkeypatch):
    from app import auth
    from test_gcp_hosting import _settings_from

    fresh = _settings_from(render_env.build_files(HOST, SECRETS), monkeypatch)
    assert fresh.demo_signin_pw_coordinator == SECRETS["pw-coordinator"] and auth.demo_controls_active()
    assert auth.secure_config_problems() == []
    monkeypatch.setattr(__import__("app.config", fromlist=["settings"]).settings, "demo_deployment", False)
    assert any("DEMO_SIGNIN_PW" in p for p in auth.secure_config_problems())  # passwords without the gate: refuse to start


def _realm(host=HOST):
    src = json.loads((REPO_ROOT / "config/keycloak/screening-closure-realm.json").read_text())
    return render_realm.render(src, host)


def test_direct_grants_only_on_the_demo_client_in_the_rendered_realm_and_never_in_the_committed_one():
    committed = json.loads((REPO_ROOT / "config/keycloak/screening-closure-realm.json").read_text())
    assert not any(c.get("directAccessGrantsEnabled") for c in committed["clients"])  # local dev realm and web client: browser flow only
    out = _realm()
    granted = [c["clientId"] for c in out["clients"] if c.get("directAccessGrantsEnabled")]
    assert granted == ["screening-closure-demo"]
    web = next(c for c in out["clients"] if c["clientId"] == "screening-closure-web")
    assert web["directAccessGrantsEnabled"] is False and web["standardFlowEnabled"] is True
    demo = next(c for c in out["clients"] if c["clientId"] == "screening-closure-demo")
    assert demo["publicClient"] is True and demo["standardFlowEnabled"] is False and demo["serviceAccountsEnabled"] is False
    assert demo["redirectUris"] == [] and demo["webOrigins"] == []
    assert "localhost" not in json.dumps(out) and out["bruteForceProtected"] is True
    # the same token shape as the web client: the API's audience and the claims it reads
    assert [m["name"] for m in demo["protocolMappers"]] == [m["name"] for m in web["protocolMappers"]]
    assert all(u["credentials"][0]["value"].startswith("${KC_PW_") for u in out["users"])  # still no password in the file


def test_rendering_the_realm_twice_does_not_duplicate_the_client():
    once = _realm()
    twice = render_realm.render(once, HOST)
    assert [c["clientId"] for c in twice["clients"]].count("screening-closure-demo") == 1


def test_edge_has_a_tight_location_for_the_sign_in_and_the_api_keeps_its_own_limit():
    t = (GCP / "edge/nginx.conf.template").read_text()
    block = t[t.index("location = /api/demo/sign-in"):]
    block = block[:block.index("}")]
    assert "limit_req zone=login_ip burst=5" in block and "limit_conn conn_ip 5" in block
    assert "proxy_set_header X-Forwarded-For $remote_addr" in block  # the visitor's address, overwritten, for the API's bucket
    assert t.index("location = /api/demo/sign-in") < t.index("location /api/ {")
    assert "proxy_pass http://api:8000" in block


def test_ensure_script_is_idempotent_and_prints_no_secret():
    s = (GCP / "vm/ensure-demo-client.sh").read_text()
    assert subprocess.run(["bash", "-n", str(GCP / "vm/ensure-demo-client.sh")]).returncode == 0
    assert "already present" in s and "KC_BOOTSTRAP_ADMIN_PASSWORD" in s and "--password" in s and "$KC_BOOTSTRAP_ADMIN_PASSWORD" in s
    assert "ensure-demo-client.sh" in (GCP / "vm/sc-up.sh").read_text()


def test_smoke_test_signs_in_as_the_coordinator_and_reads_cases_with_the_token():
    s = (GCP / "smoke-test.sh").read_text()
    assert "/api/demo/sign-in" in s and '"role":"coordinator"' in s
    assert "access_token" in s and "password" in s.lower() and "/api/cases" in s
    assert subprocess.run(["bash", "-n", str(GCP / "smoke-test.sh")]).returncode == 0
