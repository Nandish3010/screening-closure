"""A21 (ADR-032): human-effort accounting. Who wrote each event (`actor_kind`), what a person's action costs (the assumed
minutes table the simulator imports), and whether the automation cuts human labour by more than 40%. T-A47."""

from __future__ import annotations

import pytest

from api_helpers import login_headers, make_client
from app import auth, effort
from app.main import app, pathway
from app.state_machine import AUTOMATION_ACTOR, AUTOMATION_ALLOWED_EVENTS
from test_automation import (  # noqa: F401  (the world fixture and the walk helpers)
    KARIM,
    LAYLA_LOGIN,
    appointment_passed,
    booked_case,
    evidence,
    fresh,
    found,
    login,
    new_case,
    post,
    scan,
    evidence_scan,
)

client = make_client()


@pytest.fixture(autouse=True)
def own_copy_of_the_minutes(monkeypatch):
    """The figures below are the module's own copy. Whether the simulator's table is importable depends on the checkout, not
    on the test: pin the table the accounting reads to the own copy (the loader has its own tests, further down)."""
    monkeypatch.setattr(effort, "HUMAN_ACTION_MINUTES", dict(effort.ASSUMED_MINUTES))
    monkeypatch.setattr(effort, "MINUTES_SOURCE", effort.OWN_COPY)
    monkeypatch.setattr(effort, "FROM_SIMULATOR", ())


OWNERS_FIGURES = {
    "coordinator_phone_call": 6, "clinician_review": 3, "referral_handling": 8, "sign_off": 4,
    "escalation_handling": 5, "message_approval": 2,
}


def ev(type_, actor):
    return {"type": type_, "actor": actor}


HUMAN = "primary_care_owner:o1"
SPEC = "specialist_owner:s1"
COORD = "coordinator:c1"
AUTO = AUTOMATION_ACTOR


# ---- classification ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("actor,kind", [
    ("lab:abc", "human"), ("primary_care_owner:abc", "human"), ("specialist_owner:abc", "human"),
    ("coordinator:abc", "human"), ("patient:abc", "human"), ("supervisor:abc", "human"),
    ("system:automation", "automation"), ("model:template", "model"), ("model:azure_openai", "model"),
    ("system:scheduler", "service"), ("system:watchdog", "service"), ("system:notifier", "service"),
    ("system:consent", "service"), ("system:intake", "service"), ("scheduler", "service"), ("watchdog", "service"),
    ("", "service"), (None, "service"), ("clinician:dr_who", "service"),
])
def test_actor_kind(actor, kind):
    assert effort.actor_kind(actor) == kind and kind in effort.ACTOR_KINDS


def test_the_module_agrees_with_auth_and_the_state_machine():
    assert effort.HUMAN_ROLES == auth.USER_ROLES
    assert effort.AUTOMATION_ACTOR == AUTOMATION_ACTOR
    # every event the automation may emit has an accounting row (None = it replaces no manual step)
    assert set(effort.AUTOMATION_REPLACES) == set(AUTOMATION_ALLOWED_EVENTS)
    assert set(effort.HUMAN_EVENT_ACTION) <= set(pathway.event_types) | {"message_approved", "message_rejected"}


# ---- the table ------------------------------------------------------------------------------------------------------


def test_the_minutes_table_is_a_plain_dict_with_the_owners_six_figures_and_is_labelled_assumed():
    assert type(effort.HUMAN_ACTION_MINUTES) is dict
    for action, minutes in OWNERS_FIGURES.items():
        assert effort.HUMAN_ACTION_MINUTES[action] == minutes
    assert all(isinstance(k, str) and isinstance(v, (int, float)) and v > 0 for k, v in effort.HUMAN_ACTION_MINUTES.items())
    # every action an event maps to is priced
    priced = set(effort.HUMAN_ACTION_MINUTES)
    assert set(effort.HUMAN_EVENT_ACTION.values()) <= priced
    assert {v for v in effort.AUTOMATION_REPLACES.values() if v} <= priced
    a = effort.assumptions()
    assert a["status"] == "assumed; to source" and a["minutes"] == effort.HUMAN_ACTION_MINUTES
    assert a["minutes"] is not effort.HUMAN_ACTION_MINUTES  # a copy: the page cannot edit the table
    assert effort.ASSUMED in effort.__doc__ and "to source" in effort.__doc__


# ---- one case -------------------------------------------------------------------------------------------------------


def test_counts_split_by_actor_kind_and_the_share_is_automated_over_automated_plus_staff():
    events = [ev("case_created", "system:intake"), ev("acknowledge", AUTO), ev("assign_ownership", AUTO),
              ev("accept_ownership", HUMAN), ev("message_sent", AUTO), ev("message_drafted", "model:template"),
              ev("message_approved", HUMAN), ev("message_sent", "system:notifier"),
              ev("patient_acknowledge", "patient:p1"), ev("mark_overdue", "system:scheduler")]
    e = effort.case_effort(events)
    assert (e["human_actions"], e["automated_actions"], e["patient_actions"], e["model_actions"], e["service_actions"]) == (2, 3, 1, 1, 2)
    assert e["automation_share"] == 0.6  # 3 / (3 + 2): the patient, the model and the services are not in it
    assert e["human_minutes"] == 1 + 2  # accept_ownership + message_approved
    assert e["minutes_saved"] == 2 + 2 + 3  # acknowledge + assignment + one manual message
    assert e["baseline_minutes"] == e["human_minutes"] + e["minutes_saved"] == 10
    assert e["reduction_pct"] == 70.0 and e["unpriced_human_actions"] == 0
    assert effort.human_minutes_per_case(events) == 3


def test_an_empty_case_has_no_share_and_no_percentage():
    e = effort.case_effort([ev("case_created", "system:intake")])
    assert e["automation_share"] is None and e["reduction_pct"] is None and e["baseline_minutes"] == 0


def test_a_human_action_with_no_row_is_counted_but_not_priced():
    e = effort.case_effort([ev("mark_lost_contact", COORD), ev("callback_requested", COORD)])
    assert e["human_actions"] == 2 and e["unpriced_human_actions"] == 2 and e["human_minutes"] == 0


def test_a_review_request_hands_a_decision_to_a_person_and_saves_nothing():
    e = effort.case_effort([ev("review_requested", AUTO)])
    assert e["automated_actions"] == 1 and e["minutes_saved"] == 0


def test_events_may_be_objects_or_dicts():
    class E:
        def __init__(self, type, actor):
            self.type, self.actor = type, actor

    assert effort.case_effort([E("acknowledge", AUTO)]) == effort.case_effort([ev("acknowledge", AUTO)])


# ---- the 40% ----------------------------------------------------------------------------------------------------------

HAPPY_HUMAN = [ev("accept_ownership", HUMAN), ev("contact_patient", HUMAN), ev("book_confirmatory", SPEC),
               ev("handoff_requested", HUMAN), ev("clinician_review", SPEC), ev("close", SPEC)]
HAPPY_AUTO = [ev("acknowledge", AUTO), ev("assign_ownership", AUTO), ev("message_sent", AUTO), ev("message_sent", AUTO),
              ev("message_sent", AUTO), ev("complete_confirmatory", AUTO), ev("closure_proposed", AUTO)]


def test_the_happy_path_cuts_human_minutes_by_more_than_forty_percent_on_the_assumed_table():
    e = effort.case_effort(HAPPY_AUTO + HAPPY_HUMAN)
    # 27 minutes of people (1 + 6 + 5 + 8 + 3 + 4) against 47 with no automation (+ 2 + 2 + 3x3 + 5 + 2 replaced)
    assert e["human_minutes"] == 27 and e["minutes_saved"] == 20 and e["baseline_minutes"] == 47
    assert e["reduction_pct"] == 42.6 and e["reduction_pct"] > effort.TARGET_REDUCTION_PCT
    assert e["automation_share"] == round(7 / 13, 4)


def test_an_automatic_reroute_adds_to_the_saving_and_a_cohort_report_totals_the_cases():
    rerouted = HAPPY_AUTO + [ev("handoff_requested", AUTO)] + HAPPY_HUMAN
    report = effort.effort_report({"a": HAPPY_AUTO + HAPPY_HUMAN, "b": rerouted})
    t = report["totals"]
    assert t["human_minutes"] == 54 and t["minutes_saved"] == 20 + 28 and t["baseline_minutes"] == 102
    assert t["reduction_pct"] == 47.1 and t["meets_target"] is True and t["target_pct"] == 40.0
    assert t["human_actions"] == 12 and t["automated_actions"] == 15


def test_the_target_is_not_met_by_a_pathway_that_only_looks_busy():
    # automation that replaces little: a case acknowledged by the automation and then done entirely by people
    thin = effort.effort_report({"x": [ev("acknowledge", AUTO)] + HAPPY_HUMAN})["totals"]
    assert thin["reduction_pct"] < effort.TARGET_REDUCTION_PCT and thin["meets_target"] is False
    nothing = effort.effort_report({"x": HAPPY_HUMAN})["totals"]
    assert nothing["reduction_pct"] == 0.0 and nothing["meets_target"] is False
    assert effort.effort_report({})["totals"]["reduction_pct"] is None


def test_the_claim_rests_on_the_assumptions_and_the_margin_is_stated(monkeypatch):
    """Sensitivity, so nobody mistakes the happy-path 42.6% for a measured result: with a manual message priced at 2
    minutes instead of 3 the same case is below the target. Kept as a test so the margin cannot silently grow."""
    monkeypatch.setitem(effort.HUMAN_ACTION_MINUTES, "manual_message", 2)
    assert effort.case_effort(HAPPY_AUTO + HAPPY_HUMAN)["reduction_pct"] < effort.TARGET_REDUCTION_PCT


# ---- through the API ------------------------------------------------------------------------------------------------


def test_the_effort_route_reports_a_real_case_walk(fresh):
    cid = booked_case()
    found(cid, f"Procedure/colonoscopy-{cid}")
    appointment_passed()
    scan()
    evidence_scan()
    post(cid, "clinician_review", login(*KARIM))
    body = client.get("/metrics/effort", headers=login_headers(client, "supervisor")).json()
    assert body["cases"] == 1 and body["assumptions"]["status"] == "assumed; to source"
    row = body["per_case"][0]
    assert row["case_id"] == cid and row["automated_actions"] >= 5 and row["human_actions"] >= 6
    t = body["totals"]
    assert t["minutes_saved"] > 0 and t["baseline_minutes"] == t["human_minutes"] + t["minutes_saved"]
    assert t["target_pct"] == 40.0 and isinstance(t["meets_target"], bool)
    page = client.get(f"/cases/{cid}", headers=login_headers(client, "supervisor")).json()
    assert page["effort"]["automated_actions"] == row["automated_actions"]
    assert page["effort"]["human_actions"] == row["human_actions"]
    assert [e["actor_kind"] for e in page["events"]][:3] == ["service", "automation", "automation"]


def test_the_effort_route_is_for_coordinators_and_supervisors_and_limits_its_rows(fresh):
    for _ in range(3):
        new_case()
    assert client.get("/metrics/effort", headers=login_headers(client, "coordinator")).status_code == 200
    for role in ("lab", "primary_care_owner", "specialist_owner", "patient"):
        assert client.get("/metrics/effort", headers=login_headers(client, role)).status_code == 403
    assert client.get("/metrics/effort").status_code == 401
    body = client.get("/metrics/effort?limit=2", headers=login_headers(client, "coordinator")).json()
    assert body["cases"] == 3 and len(body["per_case"]) == 2 and body["per_case_truncated"] is True
    assert client.get("/metrics/effort?limit=0", headers=login_headers(client, "coordinator")).status_code == 422
    assert "/metrics/effort" in app.openapi()["paths"]


# ---- the minutes: the simulator's table when importable, else the module's own assumed copy ---------------------------


class FakeSimulatorTable:
    """What `simulator.sim.effort` exposes: `MINUTES`, keyed by action id."""

    MINUTES = {"M_PHONE_ATTEMPT": 7.2, "M_CLINICIAN_REVIEW": 5.0, "O_SIGN_CLOSURE": 5.0, "O_APPROVE_DRAFT": 1.5,
               "M_SMS_ATTEMPT": 1.5, "X_BOOK_APPT": 8.0}


def test_without_the_simulator_the_own_copy_is_used_and_marked_assumed():
    class NoMinutes:
        pass

    for module in (NoMinutes, type("Bad", (), {"MINUTES": 7}), type("Empty", (), {"MINUTES": {}}),
                   type("Junk", (), {"MINUTES": {"M_PHONE_ATTEMPT": "seven", "O_SIGN_CLOSURE": True, "M_SMS_ATTEMPT": -1}})):
        minutes, source, priced = effort.load_minutes(module)
        assert minutes == effort.ASSUMED_MINUTES and source == effort.OWN_COPY and priced == ()
    assert effort.OWN_COPY.endswith("(own copy, assumed)")
    assumed = effort.assumptions()
    assert assumed["status"] == effort.ASSUMED and assumed["source"] == effort.OWN_COPY and assumed["illustrative"] is True
    assert assumed["minutes"] == effort.ASSUMED_MINUTES and "behaviour simulator" in assumed["note"]


def test_the_simulators_table_is_read_when_it_is_there_action_by_action(monkeypatch):
    minutes, source, priced = effort.load_minutes(FakeSimulatorTable)
    assert source == "simulator/sim/effort.py"
    assert minutes["coordinator_phone_call"] == 7.2 and minutes["sign_off"] == 5.0 and minutes["message_approval"] == 1.5
    assert minutes["appointment_booking"] == 8.0 and minutes["manual_message"] == 1.5
    # what the simulator does not price keeps the own copy
    assert minutes["referral_handling"] == effort.ASSUMED_MINUTES["referral_handling"]
    assert minutes["accept_ownership"] == effort.ASSUMED_MINUTES["accept_ownership"]
    assert priced == tuple(sorted(a for a, i in effort.SIMULATOR_ACTION.items() if i in FakeSimulatorTable.MINUTES))
    monkeypatch.setattr(effort, "HUMAN_ACTION_MINUTES", minutes)
    monkeypatch.setattr(effort, "MINUTES_SOURCE", source)
    monkeypatch.setattr(effort, "FROM_SIMULATOR", priced)
    assumed = effort.assumptions()
    assert assumed["source"] == "simulator/sim/effort.py" and assumed["status"] != effort.ASSUMED
    assert effort.minutes_for("coordinator_phone_call") == 7.2


def test_every_simulator_row_the_accounting_names_is_an_action_of_the_accounting():
    assert set(effort.SIMULATOR_ACTION) <= set(effort.ASSUMED_MINUTES)
    assert len(set(effort.SIMULATOR_ACTION.values())) == len(effort.SIMULATOR_ACTION)


def test_the_module_says_it_is_illustrative_and_where_the_submission_number_comes_from():
    assert "ILLUSTRATIVE" in effort.__doc__ and "simulator/sim/effort.py" in effort.__doc__
    assert "NOT where the submission's number comes from" in effort.__doc__


def test_the_api_starts_when_the_simulator_cannot_be_imported(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "simulator.sim.effort", None)  # an import of it raises ImportError
    minutes, source, priced = effort.load_minutes()
    assert (minutes, source, priced) == (effort.ASSUMED_MINUTES, effort.OWN_COPY, ())
