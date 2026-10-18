#!/usr/bin/env python3
"""Offline verification (F10, T-F14): the demo flow with outbound network blocked.

Brings up the compose stack in its own project (`closure_offline`, own volumes, ports 28000/28080) with
docker-compose.offline.yml: api, HAPI, Postgres, scheduler and watchdog sit on a Docker network declared `internal`
(no gateway, no outside DNS), `LLM_NETWORK_ENABLED=false`. No iptables, no host firewall. Then:

  1. proves the block from inside the api, scheduler and watchdog containers (a TCP connect to 1.1.1.1:443 and
     8.8.8.8:53 and a DNS lookup of example.com all fail), and that the same probe run from the `edge` container,
     which is on an ordinary bridge, is reported as a control (it succeeds when the host itself is online);
  2. seeds the demo patient (the Patient and positive FIT report in HAPI, the case in the API) and runs the E8 demo
     scenario, tools/demo/scenario.py, against the stack: the same 20 steps as the live demo (escalation ladder,
     Arabic template draft approved and sent, a declined and a bounced hand-off, booking, the colonoscopy Procedure in
     HAPI, review and `close` with that evidence, which the API verifies in HAPI over the internal network); the
     scheduler's heartbeat is fresh and the FHIR projection queue drains;
  3. recreates the api with `LLM_PROVIDER=azure_openai` and still no network: the draft route must answer quickly with
     the template text, `fallback_used`, an abstention reason, and the abstention is counted;
  4. scans the api, scheduler and watchdog logs for connection errors (none may affect the run), then tears everything down.

Needs docker and a free ports; takes a few minutes (HAPI starts slowly). Exit code 0 only if every step holds.

    PYTHONPATH=api python tools/check/offline_check.py [--keep] [--no-build]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import uuid
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "closure_offline"
API_PORT = int(os.environ.get("OFFLINE_API_PORT", "28000"))
FHIR_PORT = int(os.environ.get("OFFLINE_FHIR_PORT", "28080"))
API = f"http://localhost:{API_PORT}"
FHIR = f"http://localhost:{FHIR_PORT}/fhir"

PROBE = r"""
import socket, sys
reached = []
for target in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    try:
        socket.create_connection(target, timeout=4).close()
        reached.append(target)
    except OSError as exc:
        print("connect", target, "failed:", type(exc).__name__)
try:
    socket.getaddrinfo("example.com", 443)
    reached.append("dns")
except OSError as exc:
    print("dns failed:", type(exc).__name__)
print("REACHED" if reached else "BLOCKED", reached)
sys.exit(1 if reached else 0)
"""
# What an attempt to leave an internal network looks like: no DNS, no route, or a timeout. "Connection refused" is not on
# the list: the scheduler gets one from the api while the api is recreated in step 3, which is an internal restart.
CONNECTION_ERRORS = re.compile(r"ConnectError|ConnectTimeout|Name or service not known|Temporary failure in name resolution|"
                               r"Network is unreachable|getaddrinfo failed|NameResolutionError", re.I)

results: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"offline: {'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def compose(*args: str, env_extra: dict | None = None, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
    env = {**os.environ, "COMPOSE_PROJECT_NAME": PROJECT, "API_PORT": str(API_PORT), "FHIR_PORT": str(FHIR_PORT), **(env_extra or {})}
    cmd = ["docker", "compose", "-p", PROJECT, "-f", "docker-compose.yml", "-f", "docker-compose.offline.yml", *args]
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, check=check, timeout=timeout)


def wait_healthy(seconds: int = 240) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{API}/health", timeout=3).status_code == 200 and httpx.get(f"{FHIR}/metadata", timeout=5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(3)
    return False


class Api:
    def __init__(self) -> None:
        self.c = httpx.Client(base_url=API, timeout=60)
        self.tokens: dict[str, dict] = {}
        self.me: dict[str, dict] = {}

    def login(self, key, role, name, patient_ref=None, provider=None):
        body = {"role": role, "name": name, **({"patient_ref": patient_ref} if patient_ref else {}), **({"provider": provider} if provider else {})}
        r = self.c.post("/auth/dev-login", json=body)
        r.raise_for_status()
        self.me[key] = r.json()
        self.tokens[key] = {"Authorization": f"Bearer {r.json()['token']}"}

    def call(self, method, path, who, json=None, expect=(200, 201)):
        r = self.c.request(method, path, headers=self.tokens[who], **({"json": json} if json is not None else {}))
        if r.status_code not in (expect if isinstance(expect, tuple) else (expect,)):
            raise RuntimeError(f"{method} {path} as {who}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def event(self, case_id, who, type_, payload=None):
        return self.call("POST", f"/cases/{case_id}/events", who, {"type": type_, "payload": payload or {}}, expect=201)


CASE_NAMESPACE = uuid.UUID("0b8e6d5a-3c1f-4a52-8a77-9d4e2f6b1c33")  # simulator/create_cases.py: case id = uuid5(DiagnosticReport/<id>)


def seed_demo_patient(api: "Api") -> tuple[Path, str]:
    """Put a demo patient for tools/demo/scenario.py into this empty stack: the Patient and the positive FIT report in
    HAPI, and the case in the API, as `make demo-seed` does for the full cohort. The patient is synthetic and gets a
    one-row manifest in a temp directory (DEMO_MANIFEST): the repository's own manifest names a Synthea patient whose
    numeric id HAPI assigned in the dev stack and cannot be created under that id here, and a fresh clone has no
    manifest at all (data/ is not tracked). Returns (manifest path, case id)."""
    first = {"patient_id": "offline-demo-1", "report_id": str(uuid.uuid5(CASE_NAMESPACE, "offline-demo-report")),
             "observation_id": "offline-demo-obs", "effective_date": "2025-12-31T10:00:00Z", "seed": 0}
    manifest = Path(tempfile.mkdtemp(prefix="offline-manifest-")) / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "sim_clock_start": "2026-01-05T08:00:00Z", "results": [first]}))
    pid, rid = first["patient_id"], first["report_id"]
    resources = [
        ("Patient", pid, {"resourceType": "Patient", "id": pid, "active": True, "gender": "female", "birthDate": "1975-03-02",
                          "name": [{"family": "Offline", "given": ["Demo"]}]}),
        ("Patient", "offline-1", {"resourceType": "Patient", "id": "offline-1", "active": True}),
        ("DiagnosticReport", rid, {"resourceType": "DiagnosticReport", "id": rid, "status": "final", "subject": {"reference": f"Patient/{pid}"},
                                   "code": {"text": "Faecal immunochemical test (offline test)"}}),
    ]
    for kind, rid_, body in resources:
        r = httpx.put(f"{FHIR}/{kind}/{rid_}", json=body, timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"PUT {kind}/{rid_} gave HTTP {r.status_code}: {r.text[:200]}")
    case_id = str(uuid.uuid5(CASE_NAMESPACE, f"DiagnosticReport/{rid}"))
    api.call("POST", "/cases", "coord", {"case_id": case_id, "patient_ref": f"Patient/{pid}", "result_ref": f"DiagnosticReport/{rid}"}, expect=201)
    return manifest, case_id


def probe(service: str) -> tuple[bool, str]:
    out = compose("exec", "-T", service, "python", "-c", PROBE, check=False, timeout=60)
    return out.returncode == 0, out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr.strip()[:200]


def run(keep: bool, build: bool) -> int:
    started = time.monotonic()
    try:
        print(f"offline: bringing up project {PROJECT} on an internal network (api :{API_PORT}, fhir :{FHIR_PORT})", flush=True)
        t0 = time.monotonic()
        compose("up", "-d", *(["--build"] if build else []), "--remove-orphans")
        step("stack up", wait_healthy(), f"{time.monotonic() - t0:.0f} s to healthy")
        if not results[-1][1]:
            return 1

        # 1. the block, from inside
        for service in ("api", "scheduler", "watchdog"):
            ok, line = probe(service)
            step(f"{service} cannot reach the internet", ok, line)
        ok, line = probe("edge")
        print(f"offline: control: the same probe from `edge` (ordinary bridge): {line} "
              f"({'the probe does detect connectivity' if not ok else 'host looks offline too, so the control is inconclusive'})")
        peers = compose("exec", "-T", "api", "python", "-c",
                        "import urllib.request,socket;urllib.request.urlopen('http://fhir:8080/fhir/metadata',timeout=10);"
                        "socket.create_connection(('postgres',5432),timeout=5);print('peers ok')", check=False, timeout=60)
        step("api still reaches HAPI and Postgres by name", peers.returncode == 0, peers.stdout.strip() or peers.stderr.strip()[:200])

        # 2. the demo scenario (tools/demo/scenario.py, the E8 story) with template drafting, against this empty stack
        api = Api()
        for key, role, name, extra in (("coord", "coordinator", "Offline Coord", {}), ("lab", "lab", "Offline Lab", {}),
                                       ("pco", "primary_care_owner", "Dr Offline Referrer", {})):
            api.login(key, role, name, **extra)
        manifest, case_id = seed_demo_patient(api)
        step("demo patient, result report and case seeded in the offline stack", True, f"case {case_id}")
        t0 = time.monotonic()
        env = {**os.environ, "API_URL": API, "FHIR_BASE_URL": FHIR, "DEMO_MANIFEST": str(manifest), "PYTHONPATH": str(ROOT / "api")}
        out = subprocess.run([sys.executable, "tools/demo/scenario.py", "--json"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
        summary = json.loads(out.stdout[out.stdout.index("{"):]) if out.returncode == 0 and "{" in out.stdout else {}
        step("tools/demo/scenario.py runs end to end offline (20 steps, escalation ladder, hand-offs, booking, close with Procedure evidence)",
             out.returncode == 0 and summary.get("state") == "closed",
             f"exit {out.returncode}, state {summary.get('state')}, {summary.get('events')} events, {time.monotonic() - t0:.1f} s"
             + ("" if out.returncode == 0 else f", stderr: {out.stderr.strip()[-300:]}"))
        if out.returncode != 0:
            raise RuntimeError("the demo scenario failed offline")
        cid = summary["case_id"]
        drafts = api.call("GET", f"/cases/{cid}/drafts", "coord", expect=200)
        step("template drafting works offline", bool(drafts) and all(d["provider"] == "template" and d["fallback_used"] is False and len(d["text"]) > 20 for d in drafts),
             f"{len(drafts)} draft(s), providers {sorted({d['provider'] for d in drafts})}")
        types = [e["type"] for e in api.call("GET", f"/cases/{cid}", "coord", expect=200)["events"]]
        step("approval sent through the notification mock", "message_approved" in types and "message_sent" in types)
        # a template reference draft for step 3 (same owner as the case used there, so the texts can be compared)
        ref_case = api.call("POST", "/cases", "coord", {"patient_ref": "Patient/offline-1"}, expect=201)["case_id"]
        api.event(ref_case, "lab", "acknowledge")
        api.event(ref_case, "pco", "accept_ownership")
        draft = api.call("POST", f"/cases/{ref_case}/drafts", "coord", {"purpose": "result_notification", "channel": "sms", "language": "en"}, expect=201)
        step("a plain template draft is available as the reference", draft["provider"] == "template" and not draft["fallback_used"])
        deadline = time.monotonic() + 60
        waiting = -1
        while time.monotonic() < deadline:
            waiting = api.call("GET", f"/cases/{cid}/fhir", "coord", expect=200)["events_waiting"]
            if waiting == 0:
                break
            time.sleep(3)
        step("FHIR projection queue drained", waiting == 0, f"{waiting} events waiting")
        time.sleep(12)
        beat = api.call("GET", "/heartbeats", "coord", expect=200)["scheduler"]
        step("the scheduler is sweeping", bool(beat) and beat["sweep_ok"] is True and beat["age_seconds"] < 30, str(beat and beat["age_seconds"]))

        # 3. a model provider, no network
        # --no-deps: only the api is recreated. Without it compose runs the one-shot `migrate` again first (SEC-1), which
        # lengthens the window in which the api is down enough for a scheduler sweep to fail inside it.
        compose("up", "-d", "--no-deps", "api", env_extra={"OFFLINE_LLM_PROVIDER": "azure_openai"})
        step("api restarted with LLM_PROVIDER=azure_openai", wait_healthy(120))
        api = Api()
        for key, role, name in (("coord", "coordinator", "Offline Coord"), ("pco", "primary_care_owner", "Dr Offline Referrer"), ("lab", "lab", "Offline Lab")):
            api.login(key, role, name)
        case2 = api.call("POST", "/cases", "coord", {"patient_ref": "Patient/offline-1"}, expect=201)["case_id"]
        api.event(case2, "lab", "acknowledge")
        api.event(case2, "pco", "accept_ownership")
        t0 = time.monotonic()
        d2 = api.call("POST", f"/cases/{case2}/drafts", "coord", {"purpose": "result_notification", "channel": "sms", "language": "en"}, expect=201)
        took = time.monotonic() - t0
        step("model provider offline falls back to the template quickly",
             d2["fallback_used"] is True and bool(d2["abstention_reason"]) and d2["text"] == draft["text"]
             and took < 5, f"{took:.2f} s, reason {d2['abstention_reason']}")
        metric = api.call("GET", "/metrics/abstention", "coord", expect=200)
        step("the abstention is counted", metric["abstentions"] >= 1, f"{metric['abstentions']} of {metric['attempts']}")
        sent2 = api.call("POST", f"/drafts/{d2['draft_id']}/approve", "pco", expect=200)
        step("the fallback draft is sent once approved", sent2["sent"] is True)

        # 4. logs
        bad = []
        for service in ("api", "scheduler", "watchdog"):
            text = compose("logs", "--no-color", "--no-log-prefix", service, check=False).stdout
            bad += [f"{service}: {ln.strip()[:160]}" for ln in text.splitlines() if CONNECTION_ERRORS.search(ln)]
        step("no connection errors in api, scheduler or watchdog logs", not bad, f"{len(bad)} lines" + (f", first: {bad[0]}" if bad else ""))
    except Exception as exc:  # noqa: BLE001 - reported as a failed step
        step("offline run completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        if not keep:
            compose("down", "-v", "--remove-orphans", check=False)
    failed = [n for n, ok, _ in results if not ok]
    print(f"offline: {'FAIL' if failed else 'PASS'} ({len(results) - len(failed)} of {len(results)} steps, {time.monotonic() - started:.0f} s)")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="leave the stack running afterwards")
    ap.add_argument("--no-build", action="store_true", help="do not rebuild the images")
    args = ap.parse_args(argv)
    return run(args.keep, not args.no_build)


if __name__ == "__main__":
    sys.exit(main())
