"""ADR-058: the host side of the web reset (deploy/gcp/vm/demo-reset-watch.sh), run against a stub of the real reset."""

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
    env = {**os.environ, "SC_REPO": str(repo), "DEMO_CONTROL_DIR": str(ctl), "DEMO_API_UID": str(os.getuid())}
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


def test_the_request_is_data_never_code(tmp_path):
    """Shell metacharacters in the requester's name are kept as text; nothing in the file is ever run."""
    pwned = tmp_path / "pwned"
    evil = f"$(touch {pwned}) `touch {pwned}` ; touch {pwned} #"
    resets, st, _ = run(tmp_path, request={"requested_at": "x", "requested_by": evil})
    assert resets == 1 and not pwned.exists()
    assert "touch" in st["last_reset_by"]  # recorded as text


def test_a_symlinked_or_foreign_request_is_ignored_and_removed(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"requested_by": "x"}))
    ctl = tmp_path / "ctl"
    ctl.mkdir()
    os.symlink(target, ctl / "reset-request.json")
    repo = tmp_path / "repo"
    (repo / "deploy/gcp/vm").mkdir(parents=True)
    stub = repo / "deploy/gcp/vm/demo-reset-on-vm.sh"
    stub.write_text(f"#!/bin/bash\necho reset >> {tmp_path}/resets.log\n")
    stub.chmod(0o755)
    env = {**os.environ, "SC_REPO": str(repo), "DEMO_CONTROL_DIR": str(ctl), "DEMO_API_UID": str(os.getuid())}
    assert subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60).returncode == 0
    assert not (tmp_path / "resets.log").exists() and not (ctl / "reset-request.json").is_symlink()
    # a plain file owned by someone other than the API user (here: the API uid is declared to be a different one)
    (ctl / "reset-request.json").write_text(json.dumps({"requested_by": "x"}))
    env["DEMO_API_UID"] = str(os.getuid() + 12345)
    subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert not (tmp_path / "resets.log").exists()


def test_the_watcher_runs_only_the_fixed_restore():
    text = "\n".join(l for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith("#"))
    for banned in ("eval ", "bash -c", "sh -c", "source ", "xargs", "$(cat", "`cat"):
        assert banned not in text, banned
    assert text.count("demo-reset-on-vm.sh") == 1 and '"$SC_REPO/deploy/gcp/vm/demo-reset-on-vm.sh"' in text


def test_the_seed_snapshot_is_recorded_as_the_first_reset_point_without_resetting(tmp_path):
    resets, st, ctl = run(tmp_path, "--record-seed", status={"last_request_at": "2026-11-08T05:00:00+00:00"})
    assert resets == 0  # nothing is restored
    assert st["last_reset_by"] == "the seed" and st["last_reset_reason"] == "seed"
    assert datetime.fromisoformat(st["last_reset_at"]) > datetime.now(UTC) - timedelta(minutes=2)
    assert st["last_request_at"] == "2026-11-08T05:00:00+00:00" and not (ctl / "baseline.json").exists()


def test_the_scheduled_pass_records_when_the_timer_fired_even_when_it_skips(tmp_path):
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    resets, st, _ = run(tmp_path, "--scheduled", status={"last_reset_at": recent})
    assert resets == 0 and st["last_reset_at"] == recent  # skipped: a reset just happened
    assert datetime.fromisoformat(st["last_scheduled_at"]) > datetime.now(UTC) - timedelta(minutes=2)
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    second = tmp_path / "b"
    second.mkdir()
    resets, st, _ = run(second, "--scheduled", status={"last_reset_at": old})
    assert resets == 1 and st["last_scheduled_at"] and st["last_reset_at"] != old


def run_failing(tmp_path, *args, request=None, status=None, recover_ok=True):
    """The real reset fails (exit 3) half way. A stub of demo-recover.sh records that the stack was brought back."""
    repo = tmp_path / "repo"
    (repo / "deploy/gcp/vm").mkdir(parents=True, exist_ok=True)
    stub = repo / "deploy/gcp/vm/demo-reset-on-vm.sh"
    stub.write_text(f"#!/bin/bash\necho reset >> {tmp_path}/resets.log\nexit 3\n")
    stub.chmod(0o755)
    rec = repo / "deploy/gcp/vm/demo-recover.sh"
    rec.write_text(f"#!/bin/bash\necho recovered >> {tmp_path}/recover.log\n{'' if recover_ok else 'exit 1'}\n")
    rec.chmod(0o755)
    ctl = tmp_path / "ctl"
    ctl.mkdir(exist_ok=True)
    if status is not None:
        (ctl / "status.json").write_text(json.dumps(status))
    if request is not None:
        (ctl / "reset-request.json").write_text(json.dumps(request))
    env = {**os.environ, "SC_REPO": str(repo), "DEMO_CONTROL_DIR": str(ctl), "DEMO_API_UID": str(os.getuid())}
    out = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    st = json.loads((ctl / "status.json").read_text()) if (ctl / "status.json").exists() else {}
    recovered = (tmp_path / "recover.log").read_text().count("recovered") if (tmp_path / "recover.log").exists() else 0
    return out, st, ctl, recovered


def test_a_failed_reset_brings_the_stack_back_and_records_why(tmp_path):
    before = "2026-11-08T05:00:00+00:00"
    out, st, ctl, recovered = run_failing(tmp_path, request={"requested_at": "x", "requested_by": "Noura (supervisor)"},
                                          status={"last_reset_at": before, "last_request_at": before})
    assert out.returncode == 3 and "RESET FAILED" in out.stderr  # the failure is not swallowed
    assert recovered == 1  # the stack that the reset stopped was started again
    assert "manual reset by Noura (supervisor) failed (exit 3)" in st["last_reset_error"]
    assert datetime.fromisoformat(st["last_reset_error_at"]) > datetime.now(UTC) - timedelta(minutes=2)
    assert st["last_reset_at"] == before and st["last_request_at"] == before  # the failed reset is not recorded as a reset
    assert not (ctl / ".lock").exists()  # and the lock is released for the next pass


def test_a_failed_scheduled_reset_is_recorded_too_and_a_failed_recovery_does_not_hide_it(tmp_path):
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    out, st, _, recovered = run_failing(tmp_path, "--scheduled", status={"last_reset_at": old}, recover_ok=False)
    assert out.returncode == 3 and recovered == 1 and "recovery also failed" in out.stderr
    assert "scheduled reset by the schedule failed" in st["last_reset_error"]


def test_the_next_good_reset_clears_the_error(tmp_path):
    resets, st, _ = run(tmp_path, request={"requested_at": "x", "requested_by": "Noura (supervisor)"},
                        status={"last_reset_error": "manual reset by x failed (exit 3)", "last_reset_error_at": "2026-11-08T05:00:00+00:00"})
    assert resets == 1 and not st["last_reset_error"] and not st["last_reset_error_at"]


def test_a_failure_before_the_reset_starts_does_not_run_the_recovery(tmp_path):
    out, st, _, recovered = run_failing(tmp_path)  # no request: nothing to reset, nothing to recover
    assert out.returncode == 0 and recovered == 0 and "last_reset_error" not in st
