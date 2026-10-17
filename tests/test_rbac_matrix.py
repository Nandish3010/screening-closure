"""T-F9 (F7): the RBAC matrix. Every route and event type against every role, own case and other case,
probed on the real app and compared with the policy tables; a route without a policy decision fails;
docs/governance/rbac-matrix.md is the rendering and must not drift. Run by `make test-security`."""

from __future__ import annotations

import pytest

import rbac_matrix as m
from app import policy


@pytest.fixture(scope="module")
def result():
    return m.run()


def test_every_openapi_route_has_a_policy_decision_and_no_decision_is_stale():
    assert m.structural_problems(m.openapi_routes()) == []
    assert set(m.openapi_routes()) == set(policy.ROUTE_POLICY)


def test_every_role_route_and_scope_behaves_as_the_policy_tables_say(result):
    assert result.problems == []
    # 24 routes + 25 event types x 9 callers, own and other where scope applies: a sanity floor, not a count to maintain
    assert result.probes > 400 and len(result.routes) == len(policy.ROUTE_POLICY)


def test_the_committed_matrix_document_is_current(result):
    committed = m.OUT_PATH.read_text(encoding="utf-8")
    assert committed == m.render(result), "docs/governance/rbac-matrix.md is stale: run tools/check/rbac_matrix.py --write"


def test_the_matrix_covers_every_event_type_and_an_unknown_one_is_refused_for_everyone(result):
    assert set(result.event_types) == set(policy.EVENT_POLICY) | {m.UNKNOWN_EVENT}
    for role in m.COLUMNS:
        assert result.event_cells[(m.UNKNOWN_EVENT, role)].text() in {"R", "U"}


def test_nobody_but_the_gate_can_post_message_sent_message_approved_or_review_requested(result):
    for event_type in ("message_sent", "message_approved", "review_requested"):
        assert {result.event_cells[(event_type, role)].text() for role in m.COLUMNS if role != "anonymous"} == {"R"}


def test_service_tokens_reach_only_their_own_routes_and_events(result):
    for (method, path, role), cell in result.route_cells.items():
        if role in ("scheduler", "watchdog") and cell.text() == "A":
            entry = policy.ROUTE_POLICY[(method, path)]
            assert entry in (policy.PUBLIC, policy.ANY_IDENTITY) or role in entry
    allowed_events = {role: {e for e in policy.EVENT_POLICY if result.event_cells[(e, role)].text() == "A"} for role in ("scheduler", "watchdog")}
    assert allowed_events["scheduler"] == {"mark_overdue", "escalate", "handoff_rejected"}
    assert allowed_events["watchdog"] == {"watchdog_flagged"}


# ---- the check itself catches what it is for


def test_a_route_without_a_policy_entry_fails(monkeypatch):
    table = dict(policy.ROUTE_POLICY)
    del table[("GET", "/metrics/closure")]
    monkeypatch.setattr(policy, "ROUTE_POLICY", table)
    assert any("GET /metrics/closure has no ROUTE_POLICY decision" in p for p in m.run().problems)


def test_a_stale_policy_entry_fails(monkeypatch):
    monkeypatch.setattr(policy, "ROUTE_POLICY", {**policy.ROUTE_POLICY, ("GET", "/ghost"): policy.PUBLIC})
    assert any("/ghost" in p and "stale" in p for p in m.run().problems)


def test_a_route_with_a_path_parameter_needs_a_scope_decision(monkeypatch):
    rules = dict(m.SCOPE_RULES)
    del rules[("GET", "/cases/{case_id}/fhir")]
    monkeypatch.setattr(m, "SCOPE_RULES", rules)
    assert any("no scope decision" in p for p in m.run().problems)


def test_a_route_outside_the_openapi_document_is_caught(monkeypatch):
    monkeypatch.setattr(m, "FRAMEWORK_ROUTES", frozenset())
    assert any("/docs" in p for p in m.structural_problems(m.openapi_routes()))


def test_removing_owner_scoping_from_a_handler_is_caught(monkeypatch):
    monkeypatch.setattr(policy, "authorize_owner_scope", lambda *a, **k: None)
    problems = m.run().problems
    assert any("/fhir" in p and "primary_care_owner" in p for p in problems)


def test_removing_the_patient_own_case_rule_is_caught(monkeypatch):
    monkeypatch.setattr(policy, "authorize_case_access", lambda *a, **k: None)
    assert any("as patient (other case)" in p for p in m.run().problems)


def test_a_known_gap_that_is_fixed_must_be_removed(monkeypatch):
    monkeypatch.setattr(m, "KNOWN_GAPS", {**m.KNOWN_GAPS, ("GET", "/cases/{case_id}/fhir", "primary_care_owner", "other", ""): "A"})
    assert any("known gap is no longer observed" in p for p in m.run().problems)
