"""Hosted judge demo on Google Cloud (deploy/gcp, docs/governance/hosting-gcp.md): config tests, no cloud and no Docker.

The load-bearing one: the env files the VM renders from Secret Manager must pass the API's own fail-closed production check.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml

import compose_lint as cl
from conftest import REPO_ROOT

GCP = REPO_ROOT / "deploy" / "gcp"
sys.path.insert(0, str(GCP / "vm"))
import render_env  # noqa: E402
import render_realm  # noqa: E402

HOST = "34-1-2-3.sslip.io"
SECRETS = {n: (n.replace("-", "") + "0123456789abcdef" * 4)[:48] for n in render_env.REQUIRED}  # distinct, 48 chars


def _settings_from(files: dict, monkeypatch):
    from app.config import Settings, settings

    env = {}
    for name in ("compose.env", "api.env"):
        env.update(files[name])
    env["FHIR_VERIFY_EVIDENCE"] = "true"  # docker-compose.yml sets it for the api service
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    fresh = Settings(_env_file=None)
    for field in Settings.model_fields:
        monkeypatch.setattr(settings, field, getattr(fresh, field))
    return fresh


def test_production_files_pass_the_fail_closed_check(monkeypatch):
    from app import auth

    files = render_env.build_files(HOST, SECRETS, "production")
    s = _settings_from(files, monkeypatch)
    assert auth.secure_config_problems() == []
    assert s.app_env == "production" and s.auth_mode == "oidc"
    assert s.clock_mode == "sim" and s.demo_simulated_clock and s.demo_deployment  # the narrow demo gate, both flags
    assert s.oidc_issuer == f"https://{HOST}/auth/realms/screening-closure"
    assert s.dev_login_enabled is False and s.outreach_demo_approval is False


def test_either_demo_flag_alone_would_not_start(monkeypatch):
    from app import auth

    files = render_env.build_files(HOST, SECRETS, "production")
    del files["api.env"]["DEMO_DEPLOYMENT"]
    _settings_from(files, monkeypatch)
    monkeypatch.setattr(__import__("app.config", fromlist=["settings"]).settings, "demo_deployment", False)
    assert any("DEMO_DEPLOYMENT" in p for p in auth.secure_config_problems())


def test_seed_mode_is_dev_and_never_production(monkeypatch):
    files = render_env.build_files(HOST, SECRETS, "seed")
    assert files["api.env"]["APP_ENV"] == "dev" and files["compose.env"]["CLOCK_MODE"] == "sim"


def test_llm_is_replay_only_with_a_zero_spend_cap_and_no_key():
    f = render_env.build_files(HOST, SECRETS)
    c = f["compose.env"]
    assert (c["LLM_CACHE_MODE"], c["LLM_NETWORK_ENABLED"], c["LLM_DAILY_TOKEN_BUDGET"]) == ("replay", "false", "0")
    assert c["TRIAGE_REMOTE_URL"] == "http://triage:8765"  # the on-premises GGUF build, never a third-party AI
    assert f["api.env"]["TRIAGE_CALIBRATION_PATH"].endswith("calibration_gguf.json")
    assert not any("ANTHROPIC" in k for d in f.values() for k in d)


def test_service_tokens_are_scoped_per_container():
    f = render_env.build_files(HOST, SECRETS)
    assert list(f["scheduler.env"]) == ["SCHEDULER_SERVICE_TOKEN"] and list(f["watchdog.env"]) == ["WATCHDOG_SERVICE_TOKEN"]


def test_missing_secret_and_newline_are_refused():
    with pytest.raises(ValueError, match="missing"):
        render_env.build_files(HOST, {k: v for k, v in SECRETS.items() if k != "pw-patient"})
    with pytest.raises(ValueError, match="newline"):
        render_env._lines({"A": "x\ny"})


def test_realm_has_only_the_public_host_and_brute_force_protection():
    src = json.loads((REPO_ROOT / "config/keycloak/screening-closure-realm.json").read_text())
    out = render_realm.render(src, HOST)
    web = next(c for c in out["clients"] if c["clientId"] == "screening-closure-web")
    assert web["redirectUris"] == [f"https://{HOST}/*"] and web["webOrigins"] == [f"https://{HOST}"]
    assert out["bruteForceProtected"] is True
    assert "localhost" not in json.dumps(out)
    assert all(u["credentials"][0]["value"].startswith("${KC_PW_") for u in out["users"])  # no password in the file


def test_only_caddy_is_published_publicly():
    data = yaml.load((GCP / "compose.gcp.yml").read_text(), Loader=cl._Loader)
    findings = cl.lint_data(data, "gcp")
    assert len(findings) == 2 and all("'caddy'" in f for f in findings), findings  # 80 and 443, nothing else
    for name in ("caddy", "web", "keycloak-prod"):
        assert data["services"][name]["profiles"] == ["public"]  # the seed phase has no public listener
    for name in ("triage", "triage-llama"):
        assert data["services"][name]["profiles"] == ["triage"] and "ports" not in data["services"][name]  # private network only
    assert "postgres" not in str(data["services"].get("postgres", {}).get("ports", ""))


def test_overlay_has_no_literal_secret_and_requires_the_rendered_ones():
    text = (GCP / "compose.gcp.yml").read_text()
    for must in ("KC_DB_PASSWORD: ${KC_DB_PASSWORD:?", "KC_PW_PATIENT: ${KC_PW_PATIENT:?", "/etc/sc/api.env"):
        assert must in text
    assert "dev-only" not in text and "closure_dev_password" not in text


def test_edge_limits_and_blocks():
    t = (GCP / "edge/nginx.conf.template").read_text()
    for must in ("limit_req_zone", "limit_req zone=api_ip", "limit_req zone=login_ip", "set_real_ip_from",
                 "scheduler|watchdog|hl7", "location = /api/clock/advance", "location ~ ^/api/clock { return 404; }",
                 "location = /api/metrics { return 404; }", r"^/auth/(admin|realms/master)"):
        assert must in t, must
    assert "Strict-Transport-Security" in (GCP / "edge/Caddyfile").read_text()


def test_shell_scripts_parse_and_deploy_refuses_without_the_flag():
    for f in [*GCP.glob("*.sh"), *GCP.glob("vm/*.sh"), REPO_ROOT / "tools/demo/snapshot.sh"]:
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0, f
    r = subprocess.run(["bash", str(GCP / "deploy.sh")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "--yes-spend" in r.stdout


def test_scripts_never_log_in_create_accounts_or_print_secrets():
    for f in GCP.rglob("*.sh"):
        lines = [l for l in f.read_text().splitlines() if not l.lstrip().startswith("#") and "echo" not in l]
        body = "\n".join(lines)
        assert "gcloud auth login" not in body, f
        assert "echo $secrets_json" not in body and 'echo "$secrets_json"' not in body


def test_hosted_build_flag_is_set_for_the_honest_reply_text():
    assert "VITE_HOSTED_DEMO=1" in (GCP / "edge/Dockerfile").read_text()
    assert "VITE_HOSTED_DEMO" in (REPO_ROOT / "web/src/i18n/strings.ts").read_text()


def test_secret_list_in_lib_matches_the_renderer():
    import re

    names = re.search(r'SC_ITEMS="([^"]+)"', (GCP / "lib.sh").read_text()).group(1).split()
    assert sorted(names) == sorted(render_env.REQUIRED)


def test_triage_is_on_premises_and_the_hosted_label_says_so():
    strings = (REPO_ROOT / "web/src/i18n/strings.ts").read_text()
    assert "On-premises model (runs on our server, no cloud AI)" in strings
    text = (GCP / "compose.gcp.yml").read_text()
    assert "ghcr.io/ggml-org/llama.cpp:server" in text and "app.triage.gguf_server" in text
    assert "ANTHROPIC" not in text and "anthropic.com" not in text


def test_demo_clock_banner_and_gate_are_wired():
    assert "demo-clock-banner" in (REPO_ROOT / "web/src/components/DemoBanner.tsx").read_text()
    assert "DemoBanner" in (REPO_ROOT / "web/src/components/Header.tsx").read_text()
    assert "Demo, synthetic data, simulated time" in (REPO_ROOT / "web/src/i18n/strings.ts").read_text()
