"""T-F10 / T-F11 (F8): the PHI scanner, the route-table review and the in-process demo walk with markers.
The live scan of a running stack's logs is `make test-phi-logs` (tools/check/phi_scan.py live)."""

from __future__ import annotations

import base64
import json
import logging

import phi_scan as ps
import pytest


# ---- the scanner finds what it is for

@pytest.mark.parametrize("line,kind", [
    ("INFO app: created case for PHIMARKER Test", "marker"),
    ("INFO app: created case for phimarker test", "marker"),
    ("POST /cases/Patient/PHIMARKER-1 HTTP/1.1", "marker"),
    ("WARN contact owner marker.patient@example.org failed", "email"),
    ("WARN sms to +971 50 123 4567 failed", "phone"),
    ("WARN sms to 0501234567 failed", "phone"),
    ("WARN sms to 971-50-1234567 failed", "phone"),
    ("WARN sms to (050) 123 4567 failed", "phone"),
    ("WARN patient 784199012345671 not found", "phone"),
])
def test_scanner_flags_markers_emails_and_phone_like_runs(line, kind):
    found = ps.scan_text("t", line)
    assert [f.kind for f in found] == [kind]
    assert found[0].line_no == 1


@pytest.mark.parametrize("line", [
    'INFO:     172.20.0.5:55234 - "POST /watchdog/scan HTTP/1.1" 200 OK',
    'POST /cases/4b7a2316-8d2a-4c47-9e10-2a7f4b9d3c11/events HTTP/1.1 201',
    "2026-10-17 21:59:25,005 scheduler INFO sweep sim_time=2026-01-05T08:00:00+00:00 workers=4 evaluated=100000 mark_overdue=4301",
    "INFO app.db: migration applied: 006_fhir_projection.sql",
    "prompt_hash=" + "ab" * 32,
    "GET /cases?state=patient_acknowledged&overdue=true HTTP/1.1 200",
    "case 00000000-0000-0000-0000-000012345678 replayed in 12 ms (3 events)",
    "event_id 123456 seq 42",
])
def test_scanner_ignores_ids_timestamps_hashes_and_counts(line):
    assert ps.scan_text("t", line) == []


def test_a_finding_never_repeats_the_value():
    (f,) = ps.scan_text("t", "dial +971501234567 now")
    assert "971501234567" not in str(f) and "dial" in f.snippet
    (m,) = ps.scan_text("t", "name=PHIMARKER Test")
    assert "PHIMARKER" not in str(m)


@pytest.mark.parametrize("prefix", ["", "a", "ab", "abc", "name=", "x:"])
def test_a_marker_inside_base64_is_found_at_every_alignment(prefix):
    token = base64.urlsafe_b64encode(json.dumps({"r": "x", "n": prefix + ps.MARKER + " Test"}).encode()).decode().rstrip("=")
    assert ps.MARKER.lower() not in token.lower()
    assert any(f.kind == "base64-marker" for f in ps.scan_text("t", f"Authorization: Bearer {token}.sig"))


def test_a_real_dev_token_carrying_the_marker_name_is_found():
    from app import auth

    token = auth.issue_token("lab", f"{ps.MARKER} Test", None)
    assert any(f.kind == "base64-marker" for f in ps.scan_text("t", f"header {token}"))


# ---- T-F11: the route table

def test_every_path_parameter_is_a_uuid_and_every_query_parameter_is_reviewed():
    problems, rows = ps.review_routes()
    assert problems == []
    assert {r[3] for r in rows if r[2] == "path"} == {"case_id", "draft_id"}
    assert {(r[0], r[1], r[3]) for r in rows if r[2] == "query"} == set(ps.QUERY_REVIEW)


def test_the_owner_filter_is_the_one_documented_gap():
    assert ps.GAP_PARAMS == {("GET", "/cases", "owner")}


def _spec(**params):
    return {"paths": {"/x/{item}": {"get": {"parameters": [
        {"in": "path", "name": "item", "schema": params.get("path_schema", {"type": "string", "format": "uuid"})},
        *({"in": "query", "name": q, "schema": {"type": "string"}} for q in params.get("queries", ()))]}}}}


def test_a_new_query_parameter_must_be_reviewed(monkeypatch):
    monkeypatch.setattr(ps, "QUERY_REVIEW", {})
    problems, _ = ps.review_routes(_spec(queries=["patient_name"]))
    assert any("patient_name" in p and "not been reviewed" in p for p in problems)


def test_a_path_parameter_that_is_not_a_uuid_fails(monkeypatch):
    monkeypatch.setattr(ps, "QUERY_REVIEW", {})
    problems, _ = ps.review_routes(_spec(path_schema={"type": "string"}))
    assert any("not a UUID" in p for p in problems)


def test_a_stale_review_entry_fails(monkeypatch):
    monkeypatch.setattr(ps, "QUERY_REVIEW", {("GET", "/gone", "q"): ("bool", "x")})
    problems, _ = ps.review_routes(_spec())
    assert any("stale" in p for p in problems)


# ---- T-F10: the demo walk with markers, logs captured

def test_the_demo_walk_with_markers_leaves_no_marker_phone_or_email_in_any_log():
    report = ps.walk_in_process()
    assert report.control_problems == []  # the markers did reach the system, so a clean scan means something
    assert report.requests >= 30 and report.log_lines > 20
    assert report.findings == [], [str(f) for f in report.findings]


def test_the_walk_catches_a_handler_that_logs_a_display_name(monkeypatch):
    from app import policy

    real = policy.authorize_event

    def leaky(identity, event_type):
        logging.getLogger("app.leaky").info("event %s by %s", event_type, identity.name)
        return real(identity, event_type)

    monkeypatch.setattr(policy, "authorize_event", leaky)
    report = ps.walk_in_process()
    assert any(f.kind == "marker" for f in report.findings)


def test_the_walk_catches_a_logged_free_text_payload(monkeypatch):
    from app.routers import cases

    real = cases.payloads.validate

    def leaky(type_, payload):
        logging.getLogger("app.leaky").warning("payload %s", payload)
        return real(type_, payload)

    monkeypatch.setattr(cases.payloads, "validate", leaky)
    kinds = {f.kind for f in ps.walk_in_process().findings}
    assert {"marker", "email", "phone"} <= kinds
