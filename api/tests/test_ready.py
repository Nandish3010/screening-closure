"""`GET /ready` (db, HAPI, scheduler) next to the cheap `/health`, and the commit that `/health` reports."""

from __future__ import annotations

import time
import urllib.error

import pytest

from api_helpers import make_client
from app import build_info, ops, readiness
from app.config import settings

client = make_client()


@pytest.fixture(autouse=True)
def settle(monkeypatch):
    monkeypatch.setattr(readiness, "_STARTED", time.monotonic())  # a young process: no heartbeat yet is "warming", not "down"
    monkeypatch.setattr(settings, "fhir_verify_evidence", False)
    monkeypatch.setattr(settings, "fhir_projection", False)


def old_process(monkeypatch):
    monkeypatch.setattr(readiness, "_STARTED", time.monotonic() - 3600)


def test_a_young_stack_with_no_hapi_in_use_is_ready_and_says_why_hapi_was_skipped():
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"
    c = r.json()["checks"]
    assert c["db"] == {"ok": True} and c["hapi"]["skipped"] and c["scheduler"]["status"] == "warming"


def test_no_scheduler_heartbeat_in_an_old_process_is_not_ready(monkeypatch):
    old_process(monkeypatch)
    monkeypatch.setattr(ops.store, "heartbeats", lambda: {})
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
    assert r.json()["checks"]["scheduler"]["ok"] is False and r.json()["checks"]["db"]["ok"] is True


def test_a_recent_scheduler_heartbeat_is_ready(monkeypatch):
    old_process(monkeypatch)
    beat = ops.Heartbeat("scheduler", None, 5.0, {"consecutive_failures": 0})
    monkeypatch.setattr(ops.store, "heartbeats", lambda: {"scheduler": beat})
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["scheduler"] == {"ok": True, "status": "ok", "age_seconds": 5.0}


def test_a_stale_scheduler_heartbeat_is_not_ready(monkeypatch):
    beat = ops.Heartbeat("scheduler", None, 600.0, {})
    monkeypatch.setattr(ops.store, "heartbeats", lambda: {"scheduler": beat})
    assert client.get("/ready").status_code == 503


def test_a_database_that_errors_is_not_ready_and_the_error_text_is_not_returned(monkeypatch):
    def boom():
        raise RuntimeError("connection to server at 10.0.0.5 failed: password authentication failed for user closure_app")

    monkeypatch.setattr(ops.store, "heartbeats", boom)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["db"] == {"ok": False, "reason": "RuntimeError"}
    assert "10.0.0.5" not in r.text and "password" not in r.text


def test_a_database_that_hangs_makes_the_answer_no_not_a_hung_probe(monkeypatch):
    monkeypatch.setattr(readiness, "CHECK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(ops.store, "heartbeats", lambda: time.sleep(2))
    t0 = time.monotonic()
    r = client.get("/ready")
    assert time.monotonic() - t0 < 1.5
    assert r.status_code == 503 and r.json()["checks"]["db"] == {"ok": False, "reason": "timed out"}


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_hapi_is_checked_when_the_deployment_uses_it(monkeypatch):
    monkeypatch.setattr(settings, "fhir_verify_evidence", True)
    seen = []
    monkeypatch.setattr(readiness.urllib.request, "urlopen", lambda url, timeout=None: seen.append((url, timeout)) or _Resp(200))
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["hapi"] == {"ok": True}
    assert seen[0][0].endswith("/metadata?_summary=true") and seen[0][1] <= 2.0


def test_hapi_down_is_not_ready(monkeypatch):
    monkeypatch.setattr(settings, "fhir_projection", True)

    def refuse(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(readiness.urllib.request, "urlopen", refuse)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["hapi"] == {"ok": False, "reason": "URLError"}


def test_ready_is_public_like_health_and_carries_no_patient_data():
    from app import policy

    assert policy.ROUTE_POLICY[("GET", "/ready")] == policy.PUBLIC
    assert set(client.get("/ready").json()) == {"status", "checks"}


# ---- the commit in /health ------------------------------------------------------------------------------------------------

def test_health_reports_the_commit_from_the_environment(monkeypatch):
    monkeypatch.setenv("SC_COMMIT_SHA", "02327d1AbCdEf0123456789")
    assert client.get("/health").json()["commit"] == "02327d1abcde"  # lower-cased, 12 characters


def test_the_git_archive_placeholder_and_junk_both_mean_dev(monkeypatch):
    monkeypatch.delenv("SC_COMMIT_SHA", raising=False)
    assert build_info.commit() in ("dev",) or len(build_info.commit()) == 12  # an unexpanded checkout says dev; an archive says the hash
    monkeypatch.setenv("SC_COMMIT_SHA", "$(touch /tmp/x)")
    monkeypatch.setattr(build_info, "_read", lambda: "$Format:%H$\n")
    assert build_info.commit() == "dev"
    monkeypatch.setattr(build_info, "_read", lambda: "0123456789abcdef0123456789abcdef01234567\n")
    assert build_info.commit() == "0123456789ab"


def test_the_placeholder_is_expanded_by_git_archive():
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    assert "export-subst" in (repo / ".gitattributes").read_text() and "_build_sha.txt" in (repo / ".gitattributes").read_text()
    assert (repo / "api/app/_build_sha.txt").read_text().strip() == "$Format:%H$"
    if subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"], capture_output=True).returncode != 0:
        pytest.skip("not a git checkout")
    tar = subprocess.run(["git", "-C", str(repo), "archive", "HEAD", "api/app/_build_sha.txt"], capture_output=True)
    if tar.returncode != 0:
        pytest.skip("git archive unavailable")
    import io, tarfile

    body = tarfile.open(fileobj=io.BytesIO(tar.stdout)).extractfile("api/app/_build_sha.txt").read().decode().strip()
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert body == head  # but only once the attribute is committed; HEAD of the committed tree


def test_the_dockerfile_image_reads_the_file_next_to_the_code():
    from pathlib import Path

    assert (Path(build_info.__file__).with_name("_build_sha.txt")).exists()
