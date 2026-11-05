"""ADR-057: the host side of the web reset (deploy/gcp/vm/demo-reset-watch.sh), run against a stub of the real reset."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "deploy/gcp/vm/demo-reset-watch.sh"


def run(tmp_path, *args, status=None, request=None):
    repo = tmp_path / "repo"
    (repo / "deploy/gcp/vm").mkdir(parents=True, exist_ok=True)
    stub = repo / "deploy/gcp/vm/demo-reset-on-vm.sh"
    stub.write_text(f"#!/bin/bash\necho reset >> {tmp_path}/resets.log\n")
    stub.chmod(0o755)
    ctl = tmp_path / "ctl"
    ctl.mkdir(exist_ok=True)
    if status is not None:
        (ctl / "status.json").write_text(json.dumps(status))
    if request is not None:
        (ctl / "reset-request.json").write_text(json.dumps(request))
    (ctl / "baseline.json").write_text("{}")
    env = {**os.environ, "SC_REPO": str(repo), "DEMO_CONTROL_DIR": str(ctl)}
    out = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    resets = (tmp_path / "resets.log").read_text().count("reset") if (tmp_path / "resets.log").exists() else 0
    st = json.loads((ctl / "status.json").read_text()) if (ctl / "status.json").exists() else {}
    return resets, st, ctl


def test_no_request_means_no_reset(tmp_path):
    assert run(tmp_path)[0] == 0


def test_a_request_resets_once_records_who_and_when_and_clears_the_request(tmp_path):
    resets, st, ctl = run(tmp_path, request={"requested_at": "x", "requested_by": "Noura (supervisor)"},
                          status={"last_request_at": "2026-11-08T05:00:00+00:00"})
    assert resets == 1
    assert st["last_reset_by"] == "Noura (supervisor)" and st["last_reset_reason"] == "manual"
    assert st["last_request_at"] == "2026-11-08T05:00:00+00:00"  # the API's rate-limit field survives
    assert datetime.fromisoformat(st["last_reset_at"]) > datetime.now(UTC) - timedelta(minutes=2)
    assert not (ctl / "reset-request.json").exists() and not (ctl / "baseline.json").exists()


def test_the_scheduled_reset_runs_unless_one_just_happened_or_a_request_is_waiting(tmp_path):
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    resets, st, _ = run(tmp_path, "--scheduled", status={"last_reset_at": old})
    assert resets == 1 and st["last_reset_by"] == "the schedule" and st["last_reset_reason"] == "scheduled"
    again, _, _ = run(tmp_path, "--scheduled")  # moments later
    assert again == 1  # still the one: skipped
    third = tmp_path / "t3"
    third.mkdir()
    r, st2, _ = run(third, "--scheduled", status={"last_reset_at": old}, request={"requested_by": "x"})
    assert r == 0  # a pending manual request wins; this pass does nothing
