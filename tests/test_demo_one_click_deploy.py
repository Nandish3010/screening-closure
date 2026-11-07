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
    assert demo["publicClient"] is False and demo["clientAuthenticatorType"] == "client-secret"  # confidential: the public cannot use it
    assert demo["secret"] == "${KC_DEMO_CLIENT_SECRET}" and "secret" not in web  # a placeholder Keycloak fills from its environment
    assert demo["standardFlowEnabled"] is False and demo["serviceAccountsEnabled"] is False
    assert demo["redirectUris"] == [] and demo["webOrigins"] == []
    assert "localhost" not in json.dumps(out) and out["bruteForceProtected"] is True
    # the same token shape as the web client: the API's audience and the claims it reads
    assert [m["name"] for m in demo["protocolMappers"]] == [m["name"] for m in web["protocolMappers"]]
    assert all(u["credentials"][0]["value"].startswith("${KC_PW_") for u in out["users"])  # still no password in the file


def test_demo_client_secret_reaches_only_the_api_and_keycloak():
    prod = render_env.build_files(HOST, SECRETS, "production")
    secret = SECRETS["demo-client-secret"]
    assert "demo-client-secret" in render_env.REQUIRED
    assert prod["api.env"]["DEMO_SIGNIN_CLIENT_SECRET"] == secret and prod["compose.env"]["KC_DEMO_CLIENT_SECRET"] == secret
    for name in ("scheduler.env", "watchdog.env"):
        assert secret not in json.dumps(prod[name])
    assert "DEMO_SIGNIN_CLIENT_SECRET" not in json.dumps(render_env.build_files(HOST, SECRETS, "seed")["api.env"])
    assert "KC_DEMO_CLIENT_SECRET" in (GCP / "compose.gcp.yml").read_text()  # Keycloak gets it for the import and for ensure-demo-client.sh
    assert "demo-client-secret" in (GCP / "lib.sh").read_text()  # deploy.sh creates every SC_ITEMS secret, idempotently


def test_brute_force_lockouts_are_temporary_and_short():
    out = _realm()
    assert out["bruteForceProtected"] is True and out["permanentLockout"] is False
    assert out["maxFailureWaitSeconds"] <= 60 and out["waitIncrementSeconds"] <= out["maxFailureWaitSeconds"]
    assert out["maxDeltaTimeSeconds"] <= 3600  # failures are forgotten quickly too


def test_rendering_the_realm_twice_does_not_duplicate_the_client():
    once = _realm()
    twice = render_realm.render(once, HOST)
    assert [c["clientId"] for c in twice["clients"]].count("screening-closure-demo") == 1


def test_edge_has_a_tight_location_for_the_sign_in_and_the_api_keeps_its_own_limit():
    t = (GCP / "edge/nginx.conf.template").read_text()
    block = t[t.index("location = /api/demo/sign-in"):]
    block = block[:block.index("}")]
    assert "limit_req zone=signin_ip burst=60" in block and "limit_conn conn_ip 40" in block  # S1: a shared-NAT panel of ~30 can click at once
    assert "limit_req_zone $binary_remote_addr zone=signin_ip:10m rate=20r/s" in t
    assert "proxy_set_header X-Forwarded-For $remote_addr" in block  # the visitor's address, overwritten, for the API's bucket
    assert t.index("location = /api/demo/sign-in") < t.index("location /api/ {")
    assert "proxy_pass http://api:8000" in block


def test_ensure_script_is_idempotent_and_prints_no_secret():
    s = (GCP / "vm/ensure-demo-client.sh").read_text()
    assert subprocess.run(["bash", "-n", str(GCP / "vm/ensure-demo-client.sh")]).returncode == 0
    assert "already present" in s and "KC_BOOTSTRAP_ADMIN_PASSWORD" in s and "--password" in s and "$KC_BOOTSTRAP_ADMIN_PASSWORD" in s
    assert "ensure-demo-client.sh" in (GCP / "vm/sc-up.sh").read_text()


def test_ensure_script_updates_an_existing_client_in_place_to_confidential_with_the_secret_and_sets_the_realm_lockouts():
    s = (GCP / "vm/ensure-demo-client.sh").read_text()
    assert 'kc update "clients/$uuid"' in s and '"publicClient": False' in s and "client-secret" in s  # runs whether or not it was created
    assert "$KC_DEMO_CLIENT_SECRET" in s and "-f -" in s  # the secret comes from the container's environment, over stdin
    assert "--secret" not in s and "secret=" not in s  # never an argument (it would show in a process list)
    assert "update realms/screening-closure" in s and "maxFailureWaitSeconds" in s
    assert 'c.pop("secret"' in s  # the create path never stores the import placeholder as the secret


def test_smoke_test_signs_in_as_the_coordinator_and_reads_cases_with_the_token():
    s = (GCP / "smoke-test.sh").read_text()
    assert "/api/demo/sign-in" in s and '"role":"coordinator"' in s
    assert "access_token" in s and "password" in s.lower() and "/api/cases" in s
    assert subprocess.run(["bash", "-n", str(GCP / "smoke-test.sh")]).returncode == 0


def test_jwks_and_token_urls_name_the_keycloak_service_the_overlay_actually_defines():
    """Found in the local rehearsal: the JWKS URL said `keycloak`, but the hosted overlay's service is `keycloak-prod`, so the API
    could not fetch signing keys and every token (one-click or not) was refused with 401."""
    import yaml

    services = yaml.safe_load((GCP / "compose.gcp.yml").read_text())["services"]
    f = render_env.build_files(HOST, SECRETS)
    for url in (f["compose.env"]["OIDC_JWKS_URL"], f["api.env"]["DEMO_SIGNIN_TOKEN_URL"]):
        assert url.split("//")[1].split(":")[0] in services, url


def _edge_csp() -> dict[str, list[str]]:
    import re

    t = (GCP / "edge/nginx.conf.template").read_text()
    loc = t[t.index("    location / {"):]
    loc = loc[:loc.index("\n    }")]
    m = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', loc)
    assert m, "the web app location must send a Content-Security-Policy"
    assert all(h in loc for h in ("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options"))  # a location's own add_header drops the inherited ones
    return {d.split()[0]: d.split()[1:] for d in m.group(1).split("; ")}


def test_edge_sends_a_csp_the_built_web_app_satisfies():
    csp = _edge_csp()
    assert csp["frame-ancestors"] == ["'none'"] and csp["object-src"] == ["'none'"] and csp["base-uri"] == ["'self'"]
    assert csp["script-src"] == ["'self'"]  # no inline script, no eval, no third-party script
    assert csp["default-src"] == ["'self'"] and csp["connect-src"] == ["'self'"]  # the API (/api) and Keycloak (/auth) are the same origin
    assert "'unsafe-eval'" not in json.dumps(csp) and "*" not in json.dumps(csp)
    # what the app needs: React style attributes, self-hosted fonts, data/blob images, the service worker and manifest
    assert "'unsafe-inline'" in csp["style-src"] and csp["font-src"] == ["'self'"] and csp["worker-src"] == ["'self'"]
    assert set(csp["img-src"]) == {"'self'", "data:", "blob:"}
    html = (REPO_ROOT / "web/index.html").read_text()  # the entry page: one external module, nothing inline, no third-party host
    import re

    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html and "onclick" not in html and "http" not in html.replace("http-equiv", "")


def test_csp_is_not_applied_to_keycloak_and_the_smoke_test_checks_it():
    t = (GCP / "edge/nginx.conf.template").read_text()
    kc = t[t.index("location /auth/ {"):]
    assert "Content-Security-Policy" not in kc[:kc.index("\n    }")]
    s = (GCP / "smoke-test.sh").read_text()
    assert "content-security-policy" in s.lower() and "frame-ancestors 'none'" in s and "script-src 'self'" in s
    assert subprocess.run(["bash", "-n", str(GCP / "smoke-test.sh")]).returncode == 0
