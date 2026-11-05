"""The GKE load test script (deploy/gke/loadtest.sh) against a FAKE gcloud: no cloud, no cluster, no Docker. What it proves:
refuses without sign-in, without billing and above the budget; creates nothing when "yes" is not typed; ALWAYS runs the teardown after
a failure that happened once resources existed; `teardown` deletes by label and verifies; the cost arithmetic. The real run
is documented in docs/governance/gke-loadtest.md and has not happened (needs a signed-in gcloud)."""

from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import sys
import time

import pytest

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "deploy" / "gke" / "loadtest.sh"
FAKE = REPO_ROOT / "tests" / "fakes" / "fake_gcloud.py"
sys.path.insert(0, str(REPO_ROOT / "deploy" / "gke"))
import cost  # noqa: E402


@pytest.fixture()
def shims(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("gcloud", "docker", "kubectl", "helm", "gke-gcloud-auth-plugin"):
        (bindir / name).symlink_to(FAKE)
    (bindir / "python3").symlink_to(sys.executable)
    # PATH is ONLY the shim directory and the system directories: the real gcloud (which may be signed in on this machine) must be unreachable,
    # and so is its configuration directory
    path = f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin"
    assert shutil.which("gcloud", path=path) == str(bindir / "gcloud") and os.access(FAKE, os.X_OK)
    env = dict(os.environ, PATH=path, CLOUDSDK_CONFIG=str(tmp_path / "no-gcloud-config"), FAKE_LOG=str(tmp_path / "calls.log"), FAKE_STATE=str(tmp_path / "state"),
               OUT_ROOT=str(tmp_path / "out"), RUN_ID="test", PROJECT="fake-project", WALL_CAP_HOURS="4", SC_ALLOW_SPEND="1")
    (tmp_path / "calls.log").write_text("")
    return env, tmp_path


def run_tty(env, answer, *args, timeout=60):
    """Run the script with a pseudo-terminal as stdin (the script insists the confirmation is typed) and answer the prompt."""
    master, slave = pty.openpty()
    p = subprocess.Popen(["bash", str(SCRIPT), *args], stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    os.close(slave)
    out, t0, sent = [], time.time(), False
    while p.poll() is None and time.time() - t0 < timeout:
        if not sent and select.select([p.stdout], [], [], 0.2)[0]:
            pass
        if not sent and time.time() - t0 > 1.5:
            os.write(master, (answer + "\n").encode())
            sent = True
        time.sleep(0.1)
    if p.poll() is None:
        p.kill()
    out = p.stdout.read()
    os.close(master)
    return p.returncode, out


def calls(tmp_path):
    return (tmp_path / "calls.log").read_text().splitlines()


def created(tmp_path):
    return [c for c in calls(tmp_path) if " create" in c or "services enable" in c or "buildx build" in c]


def test_refuses_without_the_spend_switch_before_any_cloud_call(shims):
    env, tmp = shims
    env = {k: v for k, v in env.items() if k != "SC_ALLOW_SPEND"}
    rc, out = run_tty(env, "yes", "run")
    assert rc == 3 and "owner has not approved spend" in out
    assert calls(tmp) == []


def test_refuses_without_signin(shims):
    env, tmp = shims
    rc, out = run_tty(dict(env, FAKE_NO_AUTH="1"), "yes", "run")
    assert rc != 0 and "gcloud auth login" in out
    assert created(tmp) == []


def test_refuses_without_billing(shims):
    env, tmp = shims
    rc, out = run_tty(dict(env, FAKE_NO_BILLING="1"), "yes", "run")
    assert rc != 0 and "billing is not enabled" in out
    assert created(tmp) == []


def test_refuses_above_budget(shims):
    env, tmp = shims
    rc, out = run_tty(dict(env, WALL_CAP_HOURS="400"), "yes", "run")
    assert rc != 0 and "OVER BUDGET" in out
    assert created(tmp) == []


def test_creates_nothing_unless_yes_is_typed(shims):
    env, tmp = shims
    rc, out = run_tty(env, "no", "run")
    assert rc != 0 and "nothing was created" in out
    assert created(tmp) == []
    assert "Expected" in out and "Worst case" in out  # the estimate was printed first


def test_failure_after_yes_runs_the_teardown(shims):
    env, tmp = shims
    rc, out = run_tty(dict(env, FAKE_FAIL_CLUSTER="1", ZONE_SUFFIXES="a b"), "yes", "run", timeout=90)
    assert rc != 0
    c = calls(tmp)
    assert any(x.startswith("gcloud artifacts repositories create") for x in c)      # something existed
    assert sum(1 for x in c if x.startswith("gcloud container clusters create")) == 2  # both zones tried
    assert any(x.startswith("gcloud artifacts repositories delete") for x in c)       # ... and the repository was deleted by the trap
    assert not (tmp / "state" / "repo").exists()
    assert "teardown verified" in out


def test_wall_clock_cap_ends_a_hung_run_with_the_teardown(shims):
    env, tmp = shims
    # a load generator that never finishes; the cap is 0.004 h (14 s)
    rc, out = run_tty(dict(env, FAKE_HANG="1", WALL_CAP_HOURS="0.004", KEEP_RESULTS_EVERY="2"), "yes", "run", timeout=150)
    assert rc != 0
    assert "terminated (SIGTERM)" in out and "teardown verified" in out
    assert "WALL-CLOCK CAP" in (tmp / "out" / "test" / "logs" / "cap-watchdog.log").read_text()
    c = calls(tmp)
    assert any(x.startswith("gcloud container clusters delete") for x in c) and any(x.startswith("gcloud artifacts repositories delete") for x in c)
    assert not (tmp / "state" / "cluster").exists() and not (tmp / "state" / "repo").exists()


def test_teardown_deletes_by_label_and_verifies(shims):
    env, tmp = shims
    st = tmp / "state"
    st.mkdir()
    (st / "cluster").write_text("sc-lt-x me-central1-a")
    (st / "disk").write_text("pvc-abc us-central1-a")
    (st / "repo").write_text("projects/fake-project/locations/me-central1/repositories/sc-lt-x")
    rc, out = run_tty(env, "yes", "teardown", "--yes", "--project", "fake-project")
    assert rc == 0 and "teardown verified" in out
    for f in ("cluster", "disk", "repo"):
        assert not (st / f).exists()
    c = calls(tmp)
    i_cluster = next(i for i, x in enumerate(c) if x.startswith("gcloud container clusters delete"))
    i_repo = next(i for i, x in enumerate(c) if x.startswith("gcloud artifacts repositories delete"))
    assert i_cluster < i_repo
    assert any("labels.sc-loadtest=true" in x for x in c)  # found by label


def test_cost_arithmetic_and_guard():
    r = cost.compute(cost.config_from_env())
    assert r["worst_case_usd"] <= 30
    assert abs(r["usd_per_hour"] - (sum(p["usd_per_hour"] for p in r["pools"]) + sum(x["usd_per_hour"] for x in r["other_lines"]))) < 1e-3
    cfg = cost.config_from_env() | {"cap_hours": 100.0}
    assert cost.compute(cfg)["worst_case_usd"] > 30
    assert cost.main(["--max-usd", "1"]) == 2
