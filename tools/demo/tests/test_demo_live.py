"""T-E9 and T-E10 against the running stack (`make test-demo`). They need the compose stack up, seeded, and a snapshot
(`make demo-snapshot`); they stop and start the services and change the demo state, and leave it reset.

T-E9  the scripted scenario runs end to end for the named patient and, after `make demo-reset`, produces the same event
      log hash again.
T-E10 `make demo-reset` after a half-run scenario returns the database, the FHIR store and the sim clock to exactly
      the snapshot's fingerprint; the demo is usable (the API answers) in under 30 seconds. HAPI, a JVM, needs 20 to 40 s
      more or less depending on the load of the machine, so it is allowed 90.
"""

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
API = os.environ.get("API_URL", "http://localhost:8000")
SNAPSHOT = ROOT / "tools" / "demo" / "snapshot.sh"
FINGERPRINT = ROOT / "data" / "demo-snapshot.fingerprint"
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else "python3"


def _stack_problem() -> str | None:
    try:
        urllib.request.urlopen(f"{API}/health", timeout=3)
    except Exception:
        return f"the API at {API} is not reachable (make up)"
    if not FINGERPRINT.exists():
        return "no demo snapshot (make demo-snapshot on a clean seeded stack)"
    status = subprocess.run([str(SNAPSHOT), "status"], cwd=ROOT, capture_output=True, text=True)
    # `status` prints "snapshot: none" without one; with one (SEC-1: two databases) "... and hapi_snapshot exist"
    ok = status.returncode == 0 and status.stdout.startswith("snapshot: ") and "snapshot: none" not in status.stdout
    return None if ok else "no demo snapshot database (make demo-snapshot)"


pytestmark = [pytest.mark.live, pytest.mark.skipif(_stack_problem() is not None, reason=str(_stack_problem()))]


def reset() -> tuple[float, float]:
    """Run `make demo-reset`'s script; returns (seconds until the API answers, seconds until HAPI answers too)."""
    out = subprocess.run([str(SNAPSHOT), "reset"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "equals the snapshot's" in out.stdout, out.stdout
    m = re.search(r"API answering after ([\d.]+) s, HAPI after ([\d.]+) s", out.stdout)
    assert m, out.stdout
    return float(m.group(1)), float(m.group(2))


def scenario(*args) -> dict:
    out = subprocess.run([PY, "tools/demo/scenario.py", "--json", *args], cwd=ROOT, capture_output=True, text=True, timeout=900)
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout[out.stdout.index("{"):])


def fingerprint() -> dict:
    out = subprocess.run([str(SNAPSHOT), "hash"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def test_t_e9_the_scenario_runs_end_to_end_and_repeats_identically_after_a_reset():
    reset()
    first = scenario()
    assert first["state"] == "closed" and first["patient"]
    reset()
    second = scenario()
    assert second["case_id"] == first["case_id"] and second["patient"] == first["patient"]
    assert second["event_log_hash"] == first["event_log_hash"], "the event log differs between two runs"
    assert second["events"] == first["events"]
    reset()


def test_t_e10_reset_returns_exactly_the_snapshots_state_within_30_seconds():
    reset()
    snapshot = dict(line.split("=", 1) for line in FINGERPRINT.read_text().splitlines() if "=" in line)
    scenario("--until", "overdue")
    changed = fingerprint()
    assert changed["events"] != snapshot["events"] and changed["sim_clock"] != snapshot["sim_clock"]
    api_s, hapi_s = reset()
    after = fingerprint()
    assert after == snapshot  # every count, every md5, the clock and the FHIR resource counts
    print(f"\nT-E10: API answering {api_s:.1f} s after the reset started, HAPI {hapi_s:.1f} s")
    assert api_s < 30, f"the demo was not usable (API) within 30 s: {api_s:.1f} s"
    assert hapi_s < 90, f"HAPI took {hapi_s:.1f} s to come back"
