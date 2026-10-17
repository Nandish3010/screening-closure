#!/usr/bin/env python3
"""No PHI in logs or URLs (F8, T-F10, T-F11).

Three parts, each its own sub-command:

  routes   Static review of the route table from the app's OpenAPI document (needs no stack). Every path
           parameter must be an opaque id (a UUID); every query parameter must be in QUERY_REVIEW with a
           reason it cannot hold patient data. A new query parameter fails until someone reviews it. `make test-security`.
  walk     The demo-like flow in process (FastAPI TestClient, in-memory stores) with marker values in every
           field that accepts them, while every logger is captured at DEBUG; the captured logs, including the
           HTTP client's request lines, are scanned. `make test-security` (tests/test_phi_scan.py).
  live     The same flow against the running stack (API_URL, default http://localhost:8000), then
           `docker compose logs api scheduler watchdog` (the uvicorn access log is in the api stream) is scanned,
           with postgres and fhir scanned and reported separately. `make test-phi-logs`. It writes a handful of
           synthetic marker cases into the stack's database (deterministic ids, so a rerun adds almost nothing), so
           run it against a throwaway stack or `make reset` afterwards.

What counts as a finding in a log line (after UUIDs, ISO timestamps, IP addresses and long hex digests are removed):
  * a marker: `PHIMARKER` (any case) or its base64 form (a dev token carries the display name);
  * an e-mail address;
  * a phone-like run: nine or more digits in one run, optionally with spaces, dashes or brackets between them.
The marker values go where the API accepts a free-ish string: the users table display name (login `name`, which
also allows `@`, digits and spaces), `patient_ref` and `result_ref`, and `payload` fields that the strict payload
schemas must refuse (A14). A positive control proves the markers really reached the system (they are in the case,
the actors map and the notification text) so a clean scan cannot be an empty one.

    python tools/check/phi_scan.py routes | walk | live [--api-url URL]
"""

from __future__ import annotations

import argparse
import base64
import logging
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKER = "PHIMARKER"
PHONE_DIGITS = "971501234567"
EMAIL = "marker.patient@example.org"
PATIENT_REF = f"Patient/{MARKER}-{PHONE_DIGITS}"
RESULT_REF = f"DiagnosticReport/{MARKER}-1"
FREE_TEXT = f"{MARKER} free text {PHONE_DIGITS} {EMAIL}"

# ---------------------------------------------------------------- the log scanner

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
ISO_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d(?:[ ()-]{0,2}\d){8,}(?![\w])")


def b64_needles(marker: str) -> list[str]:
    """The marker as it appears inside base64 text at each of the three byte alignments."""
    out = []
    for offset in range(3):
        encoded = base64.urlsafe_b64encode(b"\x00" * offset + marker.encode()).decode().rstrip("=")
        skip = -(-offset * 8 // 6)  # characters that mix in the padding bytes
        out.append(encoded[skip + 1: -1])  # drop the first mixed char and the last (possibly partial) one
    return out


@dataclass(frozen=True)
class Finding:
    kind: str  # marker, base64-marker, email, phone
    source: str
    line_no: int
    snippet: str  # the offending line with the match masked: never repeats the value

    def __str__(self) -> str:
        return f"{self.source}:{self.line_no}: {self.kind}: {self.snippet}"


def _mask(line: str, span: tuple[int, int]) -> str:
    return (line[:span[0]] + "<" + "*" * min(span[1] - span[0], 12) + ">" + line[span[1]:])[:200]


def scan_text(source: str, text: str, markers: tuple[str, ...] = (MARKER,)) -> list[Finding]:
    needles = [m.lower() for m in markers]
    b64 = [n for m in markers for n in b64_needles(m)]
    out: list[Finding] = []
    for no, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        for needle in needles:
            at = low.find(needle)
            if at >= 0:
                out.append(Finding("marker", source, no, _mask(line, (at, at + len(needle)))))
        for needle in b64:
            at = line.find(needle)
            if at >= 0:
                out.append(Finding("base64-marker", source, no, _mask(line, (at, at + len(needle)))))
        cleaned = line
        for pattern in (UUID_RE, ISO_TS_RE, ISO_DATE_RE, IP_RE, HEX_RE):
            cleaned = pattern.sub(" ", cleaned)
        for match in EMAIL_RE.finditer(cleaned):
            out.append(Finding("email", source, no, _mask(cleaned, match.span())))
        for match in PHONE_RE.finditer(cleaned):
            if sum(c.isdigit() for c in match.group()) >= 9:
                out.append(Finding("phone", source, no, _mask(cleaned, match.span())))
    return out


# ---------------------------------------------------------------- route table review (T-F11)

# Every query parameter in the API, reviewed. kind: `enum` / `bool` (a closed set, cannot hold a name),
# `opaque` (an id the system issued), `gap` (accepts something that can be an identifier: a documented finding).
QUERY_REVIEW: dict[tuple[str, str, str], tuple[str, str]] = {
    ("GET", "/cases", "state"): ("enum", "one of the pathway's state names"),
    ("GET", "/cases", "overdue"): ("bool", "true or false"),
    ("GET", "/cases", "flagged"): ("bool", "true or false"),
    ("GET", "/cases", "provider"): ("opaque", "a provider (organisation) key such as `endo-1`, not a person"),
    ("GET", "/cases", "owner"): (
        "opaque",
        "the owner's actor (`role:user_id`) or bare user id; a display name never matches (F8-1, fixed in api/app/readmodel.py `matches`)",
    ),
    ("GET", "/alerts", "active"): ("bool", "true or false"),
}
GAP_PARAMS = {key for key, (kind, _) in QUERY_REVIEW.items() if kind == "gap"}


def review_routes(openapi: dict | None = None) -> tuple[list[str], list[tuple[str, str, str, str, str]]]:
    """(problems, reviewed rows): rows are (method, path, kind of param, name, verdict)."""
    if openapi is None:
        sys.path.insert(0, str(ROOT / "api"))
        from app.main import app

        openapi = app.openapi()
    problems: list[str] = []
    rows = []
    seen_query = set()
    for path, item in sorted(openapi["paths"].items()):
        for method, op in sorted(item.items()):
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            m = method.upper()
            for param in op.get("parameters", []):
                where, name = param["in"], param["name"]
                schema = param.get("schema", {})
                if where == "path":
                    if schema.get("format") != "uuid":
                        problems.append(f"{m} {path}: path parameter {name!r} is not a UUID (schema {schema}); "
                                        "URLs may carry opaque ids only")
                    else:
                        rows.append((m, path, "path", name, "opaque id (UUID)"))
                elif where == "query":
                    seen_query.add((m, path, name))
                    review = QUERY_REVIEW.get((m, path, name))
                    if review is None:
                        problems.append(f"{m} {path}: query parameter {name!r} has not been reviewed; add it to QUERY_REVIEW "
                                        "in tools/check/phi_scan.py with why it cannot hold patient data, or remove it")
                    else:
                        rows.append((m, path, "query", name, f"{review[0]}: {review[1]}"))
                else:
                    problems.append(f"{m} {path}: parameter {name!r} in {where!r} (headers and cookies are not reviewed here)")
    for key in QUERY_REVIEW:
        if key not in seen_query:
            problems.append(f"QUERY_REVIEW names {key[0]} {key[1]} ?{key[2]}, which is not a query parameter of the API (stale)")
    return problems, rows


# ---------------------------------------------------------------- the demo-like flow


class Walk:
    """Drives the flow through anything with an httpx-style `.request(method, path, headers=, json=)`."""

    def __init__(self, client, *, wait_for_services=lambda: None, url_prefix: str = "", advance_days: int = 10) -> None:
        self.c = client
        self.advance_days = advance_days  # 0: leave the sim clock alone (the walk resets it to its start afterwards)
        self.prefix = url_prefix
        self.wait = wait_for_services
        self.tokens: dict[str, dict] = {}
        self.statuses: list[tuple[str, str, int]] = []
        self.responses: list[str] = []  # response bodies, for the positive control

    # ids are deterministic so a rerun against the same stack repeats the same case and events
    def uid(self, *parts: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "phi-scan/" + "/".join(parts)))

    def call(self, method, path, who=None, json=None, expect=None):
        headers = self.tokens[who] if who else {}
        resp = self.c.request(method, self.prefix + path, headers=headers, **({"json": json} if json is not None else {}))
        self.statuses.append((method, path.split("?")[0], resp.status_code))
        self.responses.append(resp.text)
        if expect is not None and resp.status_code not in (expect if isinstance(expect, tuple) else (expect,)):
            raise RuntimeError(f"{method} {path.split('?')[0]} gave {resp.status_code}, expected {expect}")
        return resp

    def login(self, key, role, name, patient_ref=None):
        body = {"role": role, "name": name, **({"patient_ref": patient_ref} if patient_ref else {})}
        resp = self.c.request("POST", self.prefix + "/auth/dev-login", json=body)
        assert resp.status_code == 200, resp.text
        self.tokens[key] = {"Authorization": f"Bearer {resp.json()['token']}"}

    def run(self, scheduler_token: str, watchdog_token: str | None = None) -> dict:
        self.login("coord", "coordinator", f"{MARKER} Coord")
        self.login("lab", "lab", f"{MARKER} Lab")
        self.login("pco", "primary_care_owner", f"{MARKER} Test")
        self.login("sup", "supervisor", f"{MARKER} Sup")
        self.login("patient", "patient", f"{MARKER} Patient {PHONE_DIGITS} {EMAIL}", patient_ref=PATIENT_REF)
        self.tokens["sched"] = {"Authorization": f"Bearer {scheduler_token}"}
        if watchdog_token:
            self.tokens["wdog"] = {"Authorization": f"Bearer {watchdog_token}"}

        case_id = self.uid("case")
        self.call("POST", "/cases", "coord", {"case_id": case_id, "patient_ref": PATIENT_REF, "result_ref": RESULT_REF,
                                              "contact_valid": {"sms": True}}, expect=(201, 409))

        def ev(step, who, type_, payload=None, expect=201):
            return self.call("POST", f"/cases/{case_id}/events", who,
                             {"type": type_, "payload": payload or {}, "event_id": self.uid("event", step)}, expect=expect)

        ev("ack", "lab", "acknowledge")
        ev("accept", "pco", "accept_ownership")
        ev("contact", "pco", "contact_patient")
        draft = self.call("POST", f"/cases/{case_id}/drafts", "coord",
                          {"purpose": "result_notification", "channel": "sms", "language": "en"}, expect=201).json()
        self.call("POST", f"/drafts/{draft['draft_id']}/approve", "pco", expect=200)
        ev("patient-ack", "patient", "patient_acknowledge")
        ev("consent", "patient", "consent_updated", {"channel_whatsapp": False})
        # reads, with the filters a worklist uses
        for path in ("/cases", "/cases?state=patient_acknowledged", "/cases?overdue=true", "/cases?flagged=true",
                     f"/cases/{case_id}", f"/cases/{case_id}/timeline", f"/cases/{case_id}/notifications",
                     f"/cases/{case_id}/fhir", f"/cases/{case_id}/drafts", "/metrics/closure", "/metrics/abstention",
                     "/heartbeats", "/alerts", "/pathways"):
            self.call("GET", path, "coord", expect=200)
        self.call("GET", "/me", "patient", expect=200)
        self.call("GET", f"/cases/{case_id}", "patient", expect=200)
        # refusals and bad input carrying marker text: none of it may be echoed into a log
        self.call("POST", f"/cases/{case_id}/events", "lab",
                  {"type": "close", "payload": {"note": FREE_TEXT}}, expect=403)
        self.call("POST", f"/cases/{case_id}/events", "pco",
                  {"type": "clinician_review", "payload": {"note": FREE_TEXT}}, expect=(409, 422))
        self.call("POST", f"/cases/{case_id}/events", "coord",
                  {"type": "contact_patient", "payload": {"free_text": FREE_TEXT}, "actor": f"coordinator:{MARKER}"}, expect=422)
        self.call("POST", "/cases", "coord", {"patient_ref": f"{MARKER} {PHONE_DIGITS} {EMAIL}"}, expect=422)
        # A16: the reject reason is a fixed taxonomy, so marker free text is a 422 before anything runs ...
        self.call("POST", f"/drafts/{draft['draft_id']}/reject", "pco", {"reason": FREE_TEXT}, expect=422)
        self.call("POST", f"/drafts/{draft['draft_id']}/reject", "pco",
                  {"reason": "other_with_note", "note": FREE_TEXT}, expect=409)  # ... and a note on an approved draft is a 409
        self.call("POST", "/auth/dev-login", None, {"role": "lab", "name": f"{MARKER}: {EMAIL}"}, expect=422)
        # time passes: the scheduler sweeps and the watchdog scans with the case in the database
        if self.advance_days:
            self.call("POST", "/clock/advance", "sup", {"days": self.advance_days}, expect=200)
        self.call("POST", "/scheduler/sweep", "sched", expect=200)
        if watchdog_token:
            self.call("POST", "/watchdog/scan", "wdog", expect=200)
        self.wait()
        self.call("GET", f"/cases/{case_id}/timeline", "coord", expect=200)
        if self.advance_days:
            self.call("POST", "/clock/reset", "sup", expect=200)
        return {"case_id": case_id, "draft_id": draft["draft_id"]}

    def positive_control(self) -> list[str]:
        """Problems if the marker values did not actually reach the system (a clean scan would then mean nothing)."""
        blob = "\n".join(self.responses)
        problems = []
        for what, needle in (("the patient_ref", PATIENT_REF), ("the result_ref", RESULT_REF), ("a display name", f"{MARKER} Test"),
                             ("the owner's name in the drafted text", f"{MARKER} Test"), ("a patient display name", PHONE_DIGITS)):
            if needle not in blob:
                problems.append(f"the marker for {what} never appeared in any response")
        return problems


# ---------------------------------------------------------------- walk in process (T-F10 without docker)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(f"{record.name} {record.levelname} {self.format(record)}")
            if record.exc_info:
                self.lines.append(logging.Formatter().formatException(record.exc_info))
        except Exception:  # noqa: BLE001 - a logging problem is not a finding
            pass


@dataclass
class WalkReport:
    findings: list[Finding] = field(default_factory=list)
    log_lines: int = 0
    requests: int = 0
    control_problems: list[str] = field(default_factory=list)


def walk_in_process() -> WalkReport:
    sys.path.insert(0, str(ROOT / "api"))
    from fastapi.testclient import TestClient

    from app import notify, ops, users
    from app.ai.draft_store import InMemoryDraftStore
    from app.clock import clock
    from app.config import settings
    from app.events import InMemoryEventStore
    from app.main import app
    from app.routers import cases, drafts

    saved = [(cases, "store", cases.store), (notify, "log", notify.log), (ops, "store", ops.store),
             (drafts, "store", drafts.store), (users, "directory", users.directory)]
    cases.store, notify.log, ops.store = InMemoryEventStore(), notify.InMemoryNotificationLog(), ops.InMemoryOpsStore()
    drafts.store, users.directory = InMemoryDraftStore(), users.InMemoryUserDirectory()
    clock.reset()
    capture = _Capture()
    root = logging.getLogger()
    old_level, old_handlers = root.level, root.handlers[:]
    root.handlers = [capture]  # only the capture: nothing is echoed to the terminal
    root.setLevel(logging.DEBUG)
    try:
        client = TestClient(app)
        walk = Walk(client)
        walk.run(settings.scheduler_service_token, settings.watchdog_service_token)
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)
        for module, attr, value in saved:
            setattr(module, attr, value)
        clock.reset()
    report = WalkReport(log_lines=len(capture.lines), requests=len(walk.statuses), control_problems=walk.positive_control())
    report.findings = scan_text("in-process logs", "\n".join(capture.lines))
    return report


# ---------------------------------------------------------------- live stack (T-F10, T-F11)


def compose_logs(services: list[str]) -> str:
    proc = subprocess.run(["docker", "compose", "logs", "--no-color", "--no-log-prefix", *services], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError(f"docker compose logs failed: {proc.stderr.strip()[:300]}")
    return proc.stdout + proc.stderr


def live(api_url: str, scheduler_token: str, watchdog_token: str, settle_seconds: float = 25.0, advance_days: int = 10) -> int:
    import httpx

    with httpx.Client(base_url=api_url, timeout=60) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"phi-scan live: the API is not reachable at {api_url}: {exc}", file=sys.stderr)
            return 2
        started = time.monotonic()
        walk = Walk(client, wait_for_services=lambda: time.sleep(settle_seconds), advance_days=advance_days)
        walk.run(scheduler_token, watchdog_token)
    problems = walk.positive_control()
    if problems:
        for p in problems:
            print(f"phi-scan live: CONTROL FAILED: {p}")
        return 1
    print(f"phi-scan live: walked {len(walk.statuses)} requests in {time.monotonic() - started:.0f} s; markers reached the system")
    failures = 0
    for services, gate in ((["api", "scheduler", "watchdog"], True), (["fhir", "postgres"], False)):
        for service in services:
            text = compose_logs([service])
            findings = scan_text(service, text)
            lines = len(text.splitlines())
            queries = sum(1 for ln in text.splitlines() if re.search(r'"(?:GET|POST|PUT|PATCH|DELETE) [^" ]*\?', ln))
            label = "gate" if gate else "informational"
            print(f"phi-scan live: {service:<10} {lines:>6} log lines, {len(findings)} findings ({label})"
                  + (f", {queries} access-log lines with a query string" if service == "api" else ""))
            for f in findings[:10]:
                print(f"    {f}")
            if gate:
                failures += len(findings)
    print("phi-scan live: " + ("FAIL" if failures else "PASS") + f" ({failures} findings in api, scheduler and watchdog logs)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("routes")
    sub.add_parser("walk")
    lv = sub.add_parser("live")
    lv.add_argument("--api-url", default=None)
    lv.add_argument("--no-clock", action="store_true",
                    help="do not advance (and afterwards reset) the sim clock; by default the walk advances it 10 days "
                         "so the sweep and escalation paths run, then resets it to its configured start")
    lv.add_argument("--settle", type=float, default=25.0, help="seconds to let the scheduler and watchdog loops run after the walk")
    args = ap.parse_args(argv)

    if args.cmd == "routes":
        problems, rows = review_routes()
        for p in problems:
            print(f"phi-scan routes: PROBLEM: {p}")
        path_params = sum(1 for r in rows if r[2] == "path")
        query = [r for r in rows if r[2] == "query"]
        gaps = [r for r in query if r[4].startswith("gap")]
        print(f"phi-scan routes: {'FAIL' if problems else 'PASS'} ({path_params} path parameters, all UUIDs; "
              f"{len(query)} query parameters reviewed, {len(gaps)} documented gap)")
        return 1 if problems else 0
    if args.cmd == "walk":
        report = walk_in_process()
        for p in report.control_problems:
            print(f"phi-scan walk: CONTROL FAILED: {p}")
        for f in report.findings[:20]:
            print(f"    {f}")
        ok = not report.findings and not report.control_problems
        print(f"phi-scan walk: {'PASS' if ok else 'FAIL'} ({report.requests} requests, {report.log_lines} log records scanned, "
              f"{len(report.findings)} findings)")
        return 0 if ok else 1
    sys.path.insert(0, str(ROOT / "api"))
    import os

    from app.config import settings

    api_url = args.api_url or os.environ.get("API_URL") or f"http://localhost:{os.environ.get('API_PORT', '8000')}"
    return live(api_url, os.environ.get("SCHEDULER_SERVICE_TOKEN", settings.scheduler_service_token),
                os.environ.get("WATCHDOG_SERVICE_TOKEN", settings.watchdog_service_token), args.settle,
                0 if args.no_clock else 10)


if __name__ == "__main__":
    sys.exit(main())
