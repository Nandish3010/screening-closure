#!/usr/bin/env python3
"""RBAC matrix (F7, T-F9): every route and event type, against every role, for a case of one's own and
another's, probed on the real app and compared with the policy tables.

The route list comes from the app's OpenAPI document. For each route, role and scope the script sends a
real request to the app (FastAPI TestClient, in-memory stores, nothing external) and classifies the
answer as an access decision:

    A  allowed past authorisation (the handler ran; its own 2xx, 409 or 422 is not an access decision)
    R  denied by role (403 from the route policy or the event policy)
    S  denied by case scope (a patient asking for someone else's case: 404; an owner on a case they do not own: 403)
    U  401, no valid identity

A cell with two letters, `A/S`, is "own case / other case". The expected letter for each cell is computed from
`app.policy` (ROUTE_POLICY, EVENT_POLICY, OWNER_SCOPED, OWN_CASE_ONLY) and the scope decisions in SCOPE_RULES
below. The run fails when:

  * an OpenAPI route has no ROUTE_POLICY entry, or a policy entry names no route (a stale decision);
  * a route with a path parameter has no scope decision in SCOPE_RULES;
  * the app serves a route that is neither in the OpenAPI document nor a known framework route;
  * any observed cell differs from the expected one, other than the documented KNOWN_GAPS (which must
    themselves still be observed: a fixed gap has to be removed from the list).

The result is rendered to docs/governance/rbac-matrix.md. `--check` (part of `make test-security`, hence
of `make check`) fails if the committed file differs from a fresh render.

    PYTHONPATH=api python tools/check/rbac_matrix.py [--write | --check]
"""

from __future__ import annotations

import argparse
import difflib
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "api"))

OUT_PATH = ROOT / "docs/governance/rbac-matrix.md"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, notify, ops, policy, users  # noqa: E402
from app.ai.draft_store import InMemoryDraftStore  # noqa: E402
from app.clock import clock  # noqa: E402
from app.config import settings  # noqa: E402
from app.events import CASE_CREATED, Event, InMemoryEventStore  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import cases, drafts  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

PATIENT = policy.PATIENT
OWNER_ROLES = sorted(policy.OWNER_SCOPED)
USER_ROLES = list(auth.USER_ROLES)
COLUMNS = ["anonymous", *USER_ROLES, auth.SCHEDULER_ROLE, auth.WATCHDOG_ROLE]
SHORT = {"anonymous": "anon", "lab": "lab", "primary_care_owner": "pco", "specialist_owner": "spec",
         "coordinator": "coord", "patient": "patient", "supervisor": "super", "scheduler": "sched", "watchdog": "wdog"}

EVENTS_ROUTE = ("POST", "/cases/{case_id}/events")
UNKNOWN_EVENT = "not_a_real_event"
# Routes the framework serves itself (public by FastAPI's defaults, not part of the API).
FRAMEWORK_ROUTES = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})

# The scope decision for every route that names a case or a draft: which kinds of caller are confined to
# "their" cases. `patient_own`: a patient sees and acts on their own case only (policy.authorize_case_access).
# `owner`: a primary_care_owner / specialist_owner acts only on cases they own (policy.authorize_owner_scope).
# A route with a path parameter that is not listed here fails the run: the decision has to be made, in this file.
SCOPE_RULES: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/cases/{case_id}"): frozenset({"patient_own"}),
    ("GET", "/cases/{case_id}/timeline"): frozenset({"patient_own"}),
    ("POST", "/cases/{case_id}/events"): frozenset({"patient_own", "owner"}),
    ("GET", "/cases/{case_id}/notifications"): frozenset({"owner"}),
    ("GET", "/cases/{case_id}/fhir"): frozenset({"owner"}),
    # decision-matrix.md: "a clinician only on a case they own".
    ("POST", "/cases/{case_id}/drafts"): frozenset({"owner"}),
    # Reading the drafts of a case (their text is the message to the patient) is owner-scoped too (SEC-2): a clinician
    # reads the drafts of cases they own; coordinators and supervisors read any.
    ("GET", "/cases/{case_id}/drafts"): frozenset({"owner"}),
    # Recording the colonoscopy result is the owning clinician's act (decision-matrix.md, E8 route).
    ("POST", "/cases/{case_id}/confirmatory-result"): frozenset({"owner"}),
    ("POST", "/drafts/{draft_id}/approve"): frozenset({"owner"}),
    ("POST", "/drafts/{draft_id}/reject"): frozenset({"owner"}),
}

# Observed behaviour that differs from SCOPE_RULES, with the finding that explains it. The run fails if a
# listed cell is no longer observed as written (the gap was fixed: delete the entry) or any other cell differs.
# key: (method, path, role, scope, event_type or "") -> observed letter
# G-1 (POST /cases/{id}/drafts without owner scoping) was fixed on main at the M4/M5 integration; the matrix now
# shows `S` for a clinician on another's case and this list is empty.
KNOWN_GAPS: dict[tuple[str, str, str, str, str], str] = {}
GAP_NOTES: list[str] = []


def actor_for(role: str, name: str) -> str:
    return f"{role}:{auth.opaque_user_id(role, name)}"


# ---------------------------------------------------------------- the world the probes run in


@contextmanager
def world():
    """A fresh in-memory API: stores, notification log, ops store and clock restored afterwards."""
    saved = [(cases, "store", cases.store), (cases, "pathway", cases.pathway), (notify, "log", notify.log),
             (ops, "store", ops.store), (drafts, "store", drafts.store), (users, "directory", users.directory)]
    cases.store = InMemoryEventStore()
    notify.log = notify.InMemoryNotificationLog()
    ops.store = ops.InMemoryOpsStore()
    drafts.store = InMemoryDraftStore()
    users.directory = users.InMemoryUserDirectory()
    clock.reset()
    try:
        yield TestClient(app)
    finally:
        for module, attr, value in saved:
            setattr(module, attr, value)
        clock.reset()


class Probe:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self._headers: dict[tuple[str, str | None], dict] = {}
        self.requests = 0

    def headers(self, role: str, patient_ref: str | None = None) -> dict:
        if role == "anonymous":
            return {}
        if role == auth.SCHEDULER_ROLE:
            return {"Authorization": f"Bearer {settings.scheduler_service_token}"}
        if role == auth.WATCHDOG_ROLE:
            return {"Authorization": f"Bearer {settings.watchdog_service_token}"}
        key = (role, patient_ref)
        if key not in self._headers:
            body = {"role": role, "name": f"rbac {role}"}
            if role == PATIENT:
                body["patient_ref"] = patient_ref or "Patient/rbac-own"
            resp = self.client.post("/auth/dev-login", json=body)
            assert resp.status_code == 200, resp.text
            self._headers[key] = {"Authorization": f"Bearer {resp.json()['token']}"}
        return self._headers[key]

    # -- fixtures built straight on the store: fast, and independent of the routes under test

    def make_case(self, owner: tuple[str, str] | None, patient_ref: str) -> UUID:
        store, pathway = cases.store, cases.pathway
        case_id = uuid4()
        store.create_case(case_id, pathway.name, Event(case_id, CASE_CREATED, clock.now(), "system:intake",
                                                       {"patient_ref": patient_ref}), pathway.initial_state)
        if owner is not None:
            store.append(Event(case_id, "acknowledge", clock.now(), actor_for("lab", "rbac lab")), pathway)
            store.append(Event(case_id, "accept_ownership", clock.now(), actor_for(*owner)), pathway)
        return case_id

    def case_for(self, role: str, scope: str) -> UUID:
        """A fresh case that is `role`'s own or someone else's. Roles with no case scope get the same kind for both."""
        if role in OWNER_ROLES:
            name = f"rbac {role}" if scope == "own" else f"rbac {role} other"
            return self.make_case((role, name), "Patient/rbac-own")
        if role == PATIENT:
            return self.make_case((policy.PCO, "rbac case owner"), "Patient/rbac-own" if scope == "own" else "Patient/rbac-other")
        return self.make_case((policy.PCO, "rbac case owner"), "Patient/rbac-own")

    def draft_on(self, case_id: UUID) -> UUID:
        resp = self.client.post(f"/cases/{case_id}/drafts", headers=self.headers(policy.COORD),
                                json={"purpose": "result_notification", "channel": "sms", "language": "en"})
        assert resp.status_code == 201, resp.text
        return UUID(resp.json()["draft_id"])

    # -- one request

    def call(self, method: str, path: str, role: str, scope: str, event_type: str | None = None) -> tuple[str, int]:
        url, kwargs = path, {}
        case_id = None
        if "{case_id}" in path or "{draft_id}" in path:
            case_id = self.case_for(role, scope)
            url = path.replace("{case_id}", str(case_id))
            if "{draft_id}" in path:
                url = path.replace("{draft_id}", str(self.draft_on(case_id)))
        body = BODIES.get((method, path))
        if (method, path) == EVENTS_ROUTE:
            body = {"type": event_type, "payload": {}}
        if body is not None:
            kwargs["json"] = body
        patient_ref = "Patient/rbac-own" if role == PATIENT else None
        self.requests += 1
        resp = self.client.request(method, url, headers=self.headers(role, patient_ref), **kwargs)
        return classify(resp), resp.status_code


BODIES = {
    ("POST", "/cases"): {"patient_ref": "Patient/rbac-new"},
    ("POST", "/auth/dev-login"): {"role": "lab", "name": "rbac probe"},
    ("POST", "/clock/advance"): {"days": 0.001},
    ("POST", "/scheduler/heartbeat"): {"sweep_ok": True},
    ("POST", "/cases/{case_id}/drafts"): {"purpose": "result_notification", "channel": "sms", "language": "en"},
    ("POST", "/drafts/{draft_id}/reject"): {"reason": "not_needed"},
}


def classify(resp) -> str:
    try:
        detail = str(resp.json().get("detail", "")) if resp.headers.get("content-type", "").startswith("application/json") else ""
    except (ValueError, AttributeError):
        detail = ""
    if resp.status_code == 401:
        return "U"
    if resp.status_code == 403:
        if "may not call" in detail or "no access policy" in detail or "may not emit" in detail or "no policy for event type" in detail:
            return "R"
        if "may only act on cases they own" in detail:
            return "S"
        return "?"  # some other 403: not an access decision this matrix knows about
    if resp.status_code == 404 and detail == "case not found":
        return "S"  # a patient and someone else's case: indistinguishable from an unknown case, by design
    return "A"


# ---------------------------------------------------------------- what the policy says should happen


def expected(method: str, path: str, role: str, scope: str, event_type: str | None = None) -> str:
    entry = policy.ROUTE_POLICY[(method, path)]
    if entry == policy.PUBLIC:
        return "A"
    if role == "anonymous":
        return "U"
    if entry != policy.ANY_IDENTITY and role not in entry:
        return "R"
    rules = SCOPE_RULES.get((method, path), frozenset())
    if (method, path) == EVENTS_ROUTE:
        allowed = policy.EVENT_POLICY.get(event_type)
        if allowed is None or role not in allowed:
            return "R"
    if scope == "other":
        if role in policy.OWN_CASE_ONLY and "patient_own" in rules:
            return "S"
        if role in policy.OWNER_SCOPED and "owner" in rules:
            return "S"
    return "A"


# ---------------------------------------------------------------- the run


@dataclass
class Cell:
    own: str
    other: str
    own_status: int
    other_status: int
    gap: bool = False  # a documented gap (KNOWN_GAPS) is part of this cell

    def text(self) -> str:
        return (self.own if self.own == self.other else f"{self.own}/{self.other}") + ("\\*" if self.gap else "")


@dataclass
class Result:
    routes: list[tuple[str, str]] = field(default_factory=list)
    route_cells: dict[tuple[str, str, str], Cell] = field(default_factory=dict)
    event_types: list[str] = field(default_factory=list)
    event_cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    gaps_seen: list[str] = field(default_factory=list)
    probes: int = 0
    requests: int = 0


def openapi_routes() -> list[tuple[str, str]]:
    out = []
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                out.append((method.upper(), path))
    return sorted(out, key=lambda r: (r[1], r[0]))


def structural_problems(routes: list[tuple[str, str]]) -> list[str]:
    problems = []
    for route in routes:
        if route not in policy.ROUTE_POLICY:
            problems.append(f"route {route[0]} {route[1]} has no ROUTE_POLICY decision")
        if ("{" in route[1]) and route not in SCOPE_RULES:
            problems.append(f"route {route[0]} {route[1]} names a case or draft but has no scope decision in SCOPE_RULES")
    for entry in policy.ROUTE_POLICY:
        if entry not in routes:
            problems.append(f"ROUTE_POLICY entry {entry[0]} {entry[1]} names no route in the OpenAPI document (stale decision)")
    for entry in SCOPE_RULES:
        if entry not in routes:
            problems.append(f"SCOPE_RULES entry {entry[0]} {entry[1]} names no route")
    served = {getattr(r, "path", None) for r in app.routes if getattr(r, "path", None)}
    documented = {p for _, p in routes}
    for path in sorted(served - documented - FRAMEWORK_ROUTES):
        problems.append(f"the app serves {path}, which is neither in the OpenAPI document nor a framework route")
    return problems


def run() -> Result:
    result = Result()
    result.routes = openapi_routes()
    result.problems += structural_problems(result.routes)
    if result.problems:  # probing needs a decision for every route; report these first
        return result
    result.event_types = sorted(policy.EVENT_POLICY) + [UNKNOWN_EVENT]
    with world() as client:
        probe = Probe(client)

        def check(method, path, role, event_type=None) -> Cell:
            scopes = ("own", "other") if ((role in policy.OWN_CASE_ONLY or role in policy.OWNER_SCOPED)
                                           and ("{" in path)) else ("own",)
            seen = {}
            gap = False
            for scope in scopes:
                letter, status = probe.call(method, path, role, scope, event_type)
                want = expected(method, path, role, scope, event_type)
                gap_key = (method, path, role, scope, event_type or "")
                if gap_key in KNOWN_GAPS:
                    want_gap = KNOWN_GAPS[gap_key]
                    if letter != want_gap:
                        result.problems.append(f"{method} {path} {role} {scope}: the known gap is no longer observed "
                                               f"(got {letter}, listed as {want_gap}); remove it from KNOWN_GAPS")
                    result.gaps_seen.append(f"{method} {path} {role} {scope}")
                    gap = True
                elif letter != want:
                    suffix = f" {event_type}" if event_type else ""
                    result.problems.append(f"{method} {path}{suffix} as {role} ({scope} case): policy says {want}, "
                                           f"observed {letter} (HTTP {status})")
                seen[scope] = (letter, status)
                result.probes += 1
            own, other = seen["own"], seen.get("other", seen["own"])
            return Cell(own[0], other[0], own[1], other[1], gap)

        for method, path in result.routes:
            if (method, path) == EVENTS_ROUTE:
                continue
            for role in COLUMNS:
                result.route_cells[(method, path, role)] = check(method, path, role)
        for event_type in result.event_types:
            for role in COLUMNS:
                result.event_cells[(event_type, role)] = check(*EVENTS_ROUTE, role, event_type)
        # The events route itself: the gate lets every role try; the event rows decide. Summarise it per role.
        for role in COLUMNS:
            result.route_cells[(*EVENTS_ROUTE, role)] = Cell(
                _route_gate(role), _route_gate(role), 0, 0)
        result.requests = probe.requests
    return result


def _route_gate(role: str) -> str:
    entry = policy.ROUTE_POLICY[EVENTS_ROUTE]
    if role == "anonymous":
        return "U"
    return "A" if role in entry else "R"


# ---------------------------------------------------------------- the document


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def render(result: Result) -> str:
    head = ["", *(SHORT[c] for c in COLUMNS)]
    route_rows = []
    for method, path in result.routes:
        label = f"`{method} {path}`" + (" (the event rows below decide)" if (method, path) == EVENTS_ROUTE else "")
        route_rows.append([label, *(result.route_cells[(method, path, c)].text() for c in COLUMNS)])
    event_rows = []
    for event_type in result.event_types:
        label = f"`{event_type}`" + (" (no such event: the policy refuses it)" if event_type == UNKNOWN_EVENT else "")
        event_rows.append([label, *(result.event_cells[(event_type, c)].text() for c in COLUMNS)])
    scoped = [(m, p) for m, p in result.routes if "{" in p]
    lines = [
        "# RBAC matrix",
        "",
        "Generated by `tools/check/rbac_matrix.py` (ticket F7, test case T-F9). Do not edit by hand: "
        "`make test-security` regenerates it in memory and fails if this file differs "
        "(`PYTHONPATH=api python tools/check/rbac_matrix.py --write` updates it).",
        "",
        "Every cell is what the running API did, not what the policy table says: the script sends a real request for each "
        "route, role and case scope to the app and classifies the answer. Each observed cell is also compared with the "
        "policy tables in `api/app/policy.py` (`ROUTE_POLICY`, `EVENT_POLICY`, the owner and own-case scoping), "
        "so a difference fails the build. See [decision-matrix.md](decision-matrix.md) for who may do what and why.",
        "",
        "## Legend",
        "",
        "- `A` allowed past authorisation. The handler ran; whatever it then answered (2xx, 409, 422) is not an access decision.",
        "- `R` denied by role: 403 from the route policy or the event policy.",
        "- `S` denied by case scope: a patient asking for someone else's case (404, indistinguishable from an unknown case), "
        "or a `primary_care_owner` / `specialist_owner` acting on a case they do not own (403).",
        "- `U` 401, no valid identity.",
        "- `X/Y` two letters: `X` for the caller's own case, `Y` for another case. One letter: scope makes no difference "
        "(the role has no case scope, or the route does not name a case).",
        "- `*` after a letter: the cell is a documented gap, observed behaviour that differs from the intended rule (see Findings).",
        "- Columns: `anon` no token; the six user roles (`pco` = `primary_care_owner`, `spec` = `specialist_owner`, "
        "`coord` = `coordinator`, `super` = `supervisor`); `sched` the scheduler service token; `wdog` the watchdog service token.",
        "- \"Own case\": for a clinician, a case they own; for a patient, the case with their `patient_ref`. "
        "\"Other\": a case owned by another clinician, or another patient's.",
        "",
        "## Routes by role",
        "",
        *table(head, route_rows),
        "",
        "Framework routes `/openapi.json`, `/docs`, `/docs/oauth2-redirect` and `/redoc` are served by FastAPI, are not in the "
        "OpenAPI document and need no token. They carry no patient data; the route list they expose is this document. "
        "Whether to switch them off outside a development environment is a deployment decision.",
        "",
        "## `POST /cases/{case_id}/events` by event type and role",
        "",
        "The route admits every role and both service tokens; the event policy then decides per event type. "
        "`R` on a row means the role may never emit that event through this route. The state machine decides separately "
        "whether an allowed event is valid in the case's state (see [decision-matrix.md](decision-matrix.md)).",
        "",
        *table(head, event_rows),
        "",
        "## Case-scoped routes",
        "",
        "Routes that name a case or a draft, with the scope rule the matrix enforces (decided in `SCOPE_RULES` in the script; "
        "a new route with a path parameter fails the run until it has one):",
        "",
        *table(["Route", "Scope rule"], [[f"`{m} {p}`", _rule_text(SCOPE_RULES[(m, p)])] for m, p in scoped]),
        "",
        "## Findings",
        "",
        *([f"- {note}" for note in GAP_NOTES] if GAP_NOTES else ["None."]),
        "",
        "## What was checked",
        "",
        f"- {len(result.routes)} routes in the OpenAPI document, {len(policy.ROUTE_POLICY)} entries in `ROUTE_POLICY`: "
        "every route has a decision and no decision is stale.",
        f"- {len(policy.EVENT_POLICY)} event types in `EVENT_POLICY`, plus one unknown type that must be refused for every role.",
        f"- {result.probes} role, route and scope combinations probed with real requests; "
        f"{len(result.problems)} differ from the policy tables{' (listed below)' if result.problems else ''}; "
        f"{len(result.gaps_seen)} are documented gaps (see Findings).",
        "",
    ]
    return "\n".join(lines)


def _rule_text(rules: frozenset[str]) -> str:
    parts = []
    if "patient_own" in rules:
        parts.append("a patient only for their own case")
    if "owner" in rules:
        parts.append("a clinician only for a case they own")
    return "; ".join(parts) if parts else "none (the role gate is the whole rule)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help=f"write {OUT_PATH.relative_to(ROOT)}")
    mode.add_argument("--check", action="store_true", help="fail if the committed file differs from a fresh render")
    args = ap.parse_args(argv)

    result = run()
    text = render(result)
    for problem in result.problems:
        print(f"rbac-matrix: PROBLEM: {problem}")
    if result.problems:
        print(f"rbac-matrix: FAIL ({len(result.problems)} problems)")
        return 1
    if args.write:
        OUT_PATH.write_text(text, encoding="utf-8")
        print(f"rbac-matrix: wrote {OUT_PATH.relative_to(ROOT)}")
    elif args.check:
        current = OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else ""
        if current != text:
            diff = list(difflib.unified_diff(current.splitlines(), text.splitlines(), "committed", "fresh", lineterm="", n=1))
            print("\n".join(diff[:40]))
            print(f"rbac-matrix: FAIL, {OUT_PATH.relative_to(ROOT)} is out of date; run "
                  "`PYTHONPATH=api python tools/check/rbac_matrix.py --write` and commit it")
            return 1
    print(f"rbac-matrix: PASS ({len(result.routes)} routes, {len(result.event_types)} event types, "
          f"{result.probes} combinations probed, {len(result.gaps_seen)} documented gap cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
