"""SEC-1: the compose lint (every published port on 127.0.0.1) and the compose files themselves."""

from __future__ import annotations

import shutil
import subprocess
import textwrap

import pytest

import compose_lint as cl
from conftest import REPO_ROOT


def lint(text: str) -> list[str]:
    import yaml

    return cl.lint_data(yaml.load(textwrap.dedent(text), Loader=cl._Loader), "t.yml")


@pytest.mark.parametrize(
    "port",
    ['"8000:8000"', '"0.0.0.0:8000:8000"', '"${API_PORT:-8000}:8000"', '"8000"', '"[::]:8000:8000"', '"192.168.1.5:8000:8000"',
     "8000", '"8000:8000/tcp"', '"${API_BIND:-0.0.0.0}:8000:8000"', '"8000-8010:8000-8010"'],
)
def test_a_port_without_the_loopback_ip_fails(port):
    problems = lint(f"services:\n  api:\n    ports:\n      - {port}\n")
    assert len(problems) == 1 and "api" in problems[0], (port, problems)


@pytest.mark.parametrize(
    "port",
    ['"127.0.0.1:8000:8000"', '"127.0.0.1:${API_PORT:-8000}:8000"', '"[::1]:8000:8000"', '"localhost:8000:8000"',
     '"127.0.0.1:8000:8000/tcp"', '"127.0.0.1:8000-8010:8000-8010"'],
)
def test_a_loopback_port_passes(port):
    assert lint(f"services:\n  api:\n    ports:\n      - {port}\n") == []


def test_long_syntax():
    bad = lint("services:\n  a:\n    ports:\n      - target: 80\n        published: 8080\n")
    assert len(bad) == 1
    bad = lint("services:\n  a:\n    ports:\n      - target: 80\n        published: 8080\n        host_ip: 0.0.0.0\n")
    assert len(bad) == 1
    assert lint("services:\n  a:\n    ports:\n      - target: 80\n        published: 8080\n        host_ip: 127.0.0.1\n") == []


def test_expose_and_services_without_ports_are_ignored():
    assert lint("services:\n  a:\n    expose: ['8000']\n  b:\n    image: x\n  c:\n    ports: []\n") == []


def test_overlay_tags_are_understood():
    text = """
    services:
      postgres:
        ports: !reset []
        networks: !override [offline]
      edge:
        ports:
          - "8000:8000"
    """
    problems = lint(text)
    assert len(problems) == 1 and "edge" in problems[0]


def test_every_port_in_the_repos_compose_files_is_on_loopback():
    for name in cl.DEFAULT_FILES:
        path = REPO_ROOT / name
        assert path.exists(), name
        assert cl.lint_file(path) == [], name


def test_the_lint_would_catch_the_old_compose_file():
    old = subprocess.run(["git", "-C", str(REPO_ROOT), "show", "17ab78f:docker-compose.yml"], capture_output=True, text=True)
    if old.returncode != 0:
        pytest.skip("the pre-SEC-1 commit is not in this clone")
    import yaml

    problems = cl.lint_data(yaml.load(old.stdout, Loader=cl._Loader), "old")
    assert len(problems) == 3  # postgres, fhir, api


def test_the_script_exits_1_on_a_bad_file_and_0_on_a_good_one(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text('services:\n  a:\n    ports: ["80:80"]\n')
    good = tmp_path / "good.yml"
    good.write_text('services:\n  a:\n    ports: ["127.0.0.1:80:80"]\n')
    script = str(REPO_ROOT / "tools" / "check" / "compose_lint.py")
    import sys

    assert subprocess.run([sys.executable, script, str(bad)], capture_output=True).returncode == 1
    assert subprocess.run([sys.executable, script, str(good)], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")


@needs_docker
@pytest.mark.parametrize("files", [["docker-compose.yml"], ["docker-compose.yml", "docker-compose.offline.yml"]])
def test_docker_compose_config_is_valid(files):
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", f]
    cmd += ["config", "-q"]
    done = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if "Cannot connect to the Docker daemon" in done.stderr:
        pytest.skip("docker daemon is not running")
    assert done.returncode == 0, done.stderr


@needs_docker
def test_the_rendered_config_has_the_hardening():
    """Read the effective configuration, not the file: interpolation, anchors and overlays included."""
    import json

    done = subprocess.run(["docker", "compose", "--profile", "tests", "config", "--format", "json"], cwd=REPO_ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip(done.stderr.strip()[:200])
    cfg = json.loads(done.stdout)
    svc = cfg["services"]
    for name, service in svc.items():
        for port in service.get("ports", []):
            assert port.get("host_ip") == "127.0.0.1", (name, port)
    api_url = svc["api"]["environment"]["DATABASE_URL"]
    assert api_url.startswith("postgresql://closure_app:") and "@postgres:5432/" in api_url
    assert svc["migrate"]["environment"]["DATABASE_URL"].startswith("postgresql://closure:")
    assert svc["fhir"]["environment"]["SPRING_DATASOURCE_URL"].endswith("/hapi")
    assert svc["api"]["build"]["target"] == "runtime" and svc["api-test"]["build"]["target"] == "test"
    assert svc["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    for name in ("scheduler", "watchdog"):  # they speak HTTP to the API and never see a database URL
        assert "DATABASE_URL" not in svc[name].get("environment", {})
