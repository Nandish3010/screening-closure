"""The hosted demo's simulated clock under production config (deploy/gcp): a narrow, explicit gate, every other check intact."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import auth
from app.config import settings

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def prod(monkeypatch):
    """A production configuration that is otherwise fully valid, on the WALL clock, with both demo flags off."""
    for name, value in (("dev_login_enabled", False), ("dev_auth_secret", "s" * 40), ("scheduler_service_token", "y" * 32),
                        ("watchdog_service_token", "z" * 32), ("automation_service_token", "w" * 32),
                        ("agency_service_token", "v" * 32), ("outreach_demo_approval", False), ("app_env", "production"),
                        ("fhir_verify_evidence", True), ("auth_mode", "oidc"), ("oidc_issuer", "https://id.example.test/realms/sc"),
                        ("clock_mode", "wall"), ("demo_simulated_clock", False), ("demo_deployment", False)):
        monkeypatch.setattr(settings, name, value)
    assert auth.secure_config_problems() == []
    return monkeypatch


def test_sim_clock_in_production_is_refused_by_default(prod):
    prod.setattr(settings, "clock_mode", "sim")
    assert any("CLOCK_MODE is not 'wall'" in p for p in auth.secure_config_problems())


def test_both_flags_with_the_sim_clock_start(prod):
    prod.setattr(settings, "clock_mode", "sim")
    prod.setattr(settings, "demo_simulated_clock", True)
    prod.setattr(settings, "demo_deployment", True)
    assert auth.secure_config_problems() == []
    assert auth.demo_clock_active() is True


@pytest.mark.parametrize("sim,deploy", [(True, False), (False, True)])
def test_a_single_flag_is_not_enough(prod, sim, deploy):
    prod.setattr(settings, "clock_mode", "sim")
    prod.setattr(settings, "demo_simulated_clock", sim)
    prod.setattr(settings, "demo_deployment", deploy)
    assert auth.secure_config_problems() != []
    assert auth.demo_clock_active() is False
    with pytest.raises(RuntimeError):
        auth.assert_secure_config()


def test_the_flags_do_not_work_with_the_wall_clock_mode(prod):
    prod.setattr(settings, "demo_simulated_clock", True)
    prod.setattr(settings, "demo_deployment", True)  # clock_mode stays wall
    assert any("CLOCK_MODE is not 'sim'" in p for p in auth.secure_config_problems())
    assert auth.demo_clock_active() is False


@pytest.mark.parametrize("field,value", [("dev_login_enabled", True), ("auth_mode", "dev"), ("outreach_demo_approval", True),
                                         ("fhir_verify_evidence", False), ("dev_auth_secret", "short"),
                                         ("oidc_issuer", "http://id.example.test/realms/sc")])
def test_every_other_fail_closed_check_still_applies_under_the_demo_gate(prod, field, value):
    prod.setattr(settings, "clock_mode", "sim")
    prod.setattr(settings, "demo_simulated_clock", True)
    prod.setattr(settings, "demo_deployment", True)
    prod.setattr(settings, field, value)
    with pytest.raises(RuntimeError):
        auth.assert_secure_config()


def _probe(**extra: str) -> dict:
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "print(json.dumps({'paths': sorted(p for p in app.openapi()['paths'] if p.startswith('/clock')),\n"
        "  'reset_like': sorted(p for p in app.openapi()['paths'] if 'reset' in p or p.startswith('/demo')),\n"
        "  'advance_unauth': c.post('/clock/advance', json={'days': 1}).status_code,\n"
        "  'reset': c.post('/clock/reset').status_code,\n"
        "  'health': c.get('/health').json()}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED", "CLOCK_MODE")}
    env.update(PYTHONPATH=str(API_DIR), DEV_AUTH_SECRET="k" * 40, APP_ENV="production", **extra)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_demo_registers_advance_only_and_health_reports_the_demo_clock():
    out = _probe(CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1")
    assert out["paths"] == ["/clock/advance"] and out["reset"] == 404
    assert out["advance_unauth"] in (401, 403)  # supervisor token required; nothing anonymous
    assert out["health"]["demo"]["simulated_clock"] is True and out["health"]["demo"]["deployment"] is True
    assert out["health"]["clock_mode"] == "sim"
    # ADR-058 (amends ADR-055): the API still never restores a database itself (it stops and replaces both). The one reset-like path
    # is a supervisor's REQUEST (and ADR-060 adds the gated, tokens-only /demo/sign-in; no password is returned), which writes a file the host script acts on; the clock's own /clock/reset stays absent.
    assert out["reset_like"] == ["/demo/guide", "/demo/missed-followup", "/demo/reset", "/demo/sign-in"]


def test_without_both_flags_production_has_no_clock_route_and_no_demo_marker():
    out = _probe(CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1")  # one flag: the route is not registered
    assert out["paths"] == [] and "demo" not in out["health"]
    out = _probe(CLOCK_MODE="wall")
    assert out["paths"] == [] and "demo" not in out["health"] and out["reset_like"] == []
    # the judge role cards key off `demo.deployment`: a production deployment (flag alone, or none) never reports it
    out = _probe(CLOCK_MODE="wall", DEMO_DEPLOYMENT="1")
    assert "demo" not in out["health"]
