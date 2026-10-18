"""A21 (ADR-032): the automation actor `system:automation`. Every step that is not a human decision is done by it, and it
can do nothing else: acknowledge and name the owner at intake, re-route a rejected referral, send pre-approved templates,
propose a closure from evidence it found. In-memory stores; the Postgres half is test_automation_pg.py.

T-A42 intake (acknowledge, assign, the owner still accepts), T-A43 outreach (template allow-list, consent, freeze, weekly
cap), T-A44 reroute (next provider, exhausted list), T-A45 evidence and closure proposal (closure stays human), T-A46 the
automation token and its limits."""

from __future__ import annotations

import dataclasses
import json
import urllib.error
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from api_helpers import login_headers, make_client
from app import auth, automation, evidence, notify, outreach_policy, policy, providers, users
from app.ai.draft_store import InMemoryDraftStore
from app.auth import opaque_user_id
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import Event, InMemoryEventStore
from app.main import app
from app.notify import InMemoryNotificationLog
from app.pathway import load_pathway
from app.routers import cases, drafts
from app.state_machine import AUTOMATION_ACTOR, InvalidTransition, replay_model
from app.sweep import run_sweep

client = make_client()
PATHWAY = load_pathway(app_settings.pathway_path)
LAYLA = "Dr. Layla Haddad"  # the default owner of Riverside Primary Care (config/providers.yaml)
HUDA = "Dr. Huda Rahman"  # the default owner of Marina Digestive Health
KARIM = ("specialist_owner", "Dr. Karim Nasser", "Corniche Gastro Unit")
RANA = ("specialist_owner", "Dr. Rana Farouk", "Al Noor Endoscopy Centre")
HUDA_LOGIN = ("primary_care_owner", HUDA, "Marina Digestive Health")
LAYLA_LOGIN = ("primary_care_owner", LAYLA, "Riverside Primary Care")
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
AUTO = {"Authorization": f"Bearer {app_settings.automation_service_token}"}


def actor(role, name):
    return f"{role}:{opaque_user_id(role, name)}"


@contextmanager
def world(**settings):
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cases, "store", InMemoryEventStore())
        mp.setattr(drafts, "store", InMemoryDraftStore())
        mp.setattr(notify, "log", InMemoryNotificationLog())
        mp.setattr(users, "directory", users.InMemoryUserDirectory())
        mp.setattr(app_settings, "automation_enabled", settings.get("enabled", True))
        mp.setattr(evidence, "finder", evidence.StaticFinder())
        mp.setattr(evidence, "verifier", evidence.AcceptAllVerifier())
        clock.reset()
        try:
            yield
        finally:
            clock.reset()


@pytest.fixture
def fresh():
    with world():
        yield


def login(role, name, provider=None, patient_ref=None):
    body = {"role": role, "name": name}
    if provider:
        body["provider"] = provider
    if patient_ref:
        body["patient_ref"] = patient_ref
    r = client.post("/auth/dev-login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def new_case(registered_provider=None, **extra):
    body = {"patient_ref": "Patient/1", **extra}
    if registered_provider is not None:
        body["registered_provider"] = registered_provider
    r = client.post("/cases", json=body, headers=login_headers(client, "coordinator"))
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def post(cid, type_, headers, payload=None, expect=201):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == expect, (type_, r.status_code, r.text)
    return r.json()


def log(cid):
    return cases.store.events_for(UUID(cid))


def types(cid):
    return [e.type for e in log(cid)]


def model(cid):
    return cases.store.read_model(UUID(cid), PATHWAY)


def scan():
    return automation.run_automation_scan(cases.store, PATHWAY, clock.now(), default_calendar())


def evidence_scan():
    return automation.scan_evidence(cases.store, PATHWAY, clock.now())


def owner_accepted_case(registered_provider=None, **extra):
    cid = new_case(registered_provider, **extra)
    name = HUDA if registered_provider == "Marina Digestive Health" else LAYLA
    prov = "Marina Digestive Health" if name == HUDA else "Riverside Primary Care"
    post(cid, "accept_ownership", login("primary_care_owner", name, prov))
    return cid


def referral_pending_case():
    """patient_acknowledged, with a referral to Corniche Gastro Unit pending (requested by the owner, a person)."""
    cid = owned_to_patient_acknowledged()
    post(cid, "handoff_requested", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]), {"to_provider": KARIM[2]})
    return cid


def owned_to_patient_acknowledged():
    cid = owner_accepted_case()
    owner = login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2])
    post(cid, "contact_patient", owner)
    post(cid, "patient_acknowledge", login("patient", "pat", patient_ref="Patient/1"))
    return cid


def booked_case():
    cid = referral_pending_case()
    post(cid, "handoff_accepted", login(*KARIM[:2], KARIM[2]))
    post(cid, "book_confirmatory", login(*KARIM[:2], KARIM[2]), {"appointment_date": "2026-01-20"})
    assert model(cid).state == "confirmatory_booked"
    return cid


def reject(cid, who, reason="capacity"):
    post(cid, "handoff_rejected", login(*who), {"reason": reason})


# ---------------------------------------------------------------------------------------------------------------------
# configuration files
# ---------------------------------------------------------------------------------------------------------------------


def test_the_shipped_providers_file_names_primary_care_owners_and_a_reroute_order():
    p = providers.get_providers()
    assert p.default_provider in p.owners
    assert all(o.role == "primary_care_owner" for o in p.owners.values())
    assert p.owner_for("Marina Digestive Health").actor == actor("primary_care_owner", HUDA)
    assert p.owner_for(None).actor == actor("primary_care_owner", LAYLA)  # the default
    assert p.owner_for("Nowhere Clinic").actor == actor("primary_care_owner", LAYLA)  # unknown: still a route to a human
    assert p.reroute_list(PATHWAY.name) == ("Corniche Gastro Unit", "Al Noor Endoscopy Centre", "Marina Digestive Health")
    assert p.next_provider(PATHWAY.name, ["corniche gastro unit"]) == "Al Noor Endoscopy Centre"
    assert p.next_provider(PATHWAY.name, list(p.reroute_list(PATHWAY.name))) is None
    assert p.next_provider("some_other_pathway", []) is None


def test_the_cohort_registration_is_deterministic_and_uses_every_provider():
    p = providers.get_providers()
    picks = {providers.provider_for_patient(f"patient-{i}", p.registration_providers) for i in range(60)}
    assert picks == set(p.registration_providers)
    assert providers.provider_for_patient("abc", p.registration_providers) == providers.provider_for_patient(
        "abc", reversed(p.registration_providers))


@pytest.mark.parametrize("edit,needle", [
    (lambda r: r["registration"][r["default_provider"]]["owner"].update(role="supervisor"), "primary_care_owner"),
    (lambda r: r.update(default_provider="Nowhere"), "default_provider"),
    (lambda r: r["referral"]["reroute_order"]["gastroenterology"].append("Corniche Gastro Unit"), "distinct"),
    (lambda r: r["referral"]["specialty_by_pathway"].update(colorectal_fit="dermatology"), "no reroute order"),
])
def test_a_bad_providers_file_is_refused_at_load(edit, needle):
    import yaml

    raw = yaml.safe_load(open(app_settings.providers_path))
    edit(raw)
    with pytest.raises(providers.ProvidersError, match=needle):
        providers.parse_providers(raw)


def test_the_shipped_outreach_policy_is_approved_versioned_and_matches_the_templates():
    pol = outreach_policy.get_policy()
    assert pol.max_auto_messages_per_week == 2 and pol.default_language == "en"
    assert pol.templates and all(t.approved_by and t.version >= 1 for t in pol.templates.values())
    assert "placeholder" in next(iter(pol.templates.values())).approved_by  # H1: nobody has signed yet
    assert {r.trigger for r in pol.rules} == {"entered_state", "reminder", "escalation_rung"}
    for rule in pol.rules:
        for channel in rule.channels:
            assert pol.template_id(rule.purpose, channel) in pol.templates


@pytest.mark.parametrize("edit,needle", [
    (lambda r: r["templates"][0].update(approved_by=""), "approved_by"),
    (lambda r: r["templates"][0].update(version=0), "version"),
    (lambda r: r["rules"][0].update(template="escalation_notice"), "not in the approved list"),
    (lambda r: r.update(template_set_version=99), "re-approve"),
    (lambda r: r.update(max_auto_messages_per_week=-1), "max_auto_messages_per_week"),
    (lambda r: r["rules"][1].pop("after_working_days"), "after_working_days"),
    (lambda r: r["templates"].append(dict(r["templates"][0])), "twice"),
])
def test_a_bad_outreach_policy_is_refused_at_load(edit, needle):
    import yaml

    raw = yaml.safe_load(open(app_settings.outreach_policy_path))
    edit(raw)
    with pytest.raises(outreach_policy.OutreachPolicyError, match=needle):
        outreach_policy.parse_policy(raw)


# ---------------------------------------------------------------------------------------------------------------------
# T-A42 intake: acknowledge and assign
# ---------------------------------------------------------------------------------------------------------------------


def test_ingest_acknowledges_and_names_the_owner_by_automation(fresh):
    cid = new_case()
    events = log(cid)
    assert [e.type for e in events] == ["case_created", "acknowledge", "assign_ownership"]
    ack, assign = events[1], events[2]
    assert ack.actor == AUTOMATION_ACTOR and ack.payload == {"auto": True}
    assert assign.actor == AUTOMATION_ACTOR
    assert assign.payload == {"owner": actor("primary_care_owner", LAYLA), "provider": "Riverside Primary Care", "auto": True}
    m = model(cid)
    # acknowledged, owner named, but nobody has accepted: the accept_ownership clock (1 working day) is running
    assert m.state == "acknowledged" and m.owner == actor("primary_care_owner", LAYLA)
    assert m.provider == "Riverside Primary Care"
    assert m.clock_started_at is not None
    # the owner is shown by name: the directory row exists though they never logged in
    assert users.resolve_actor(m.owner)["display_name"] == LAYLA


def test_the_registered_provider_picks_the_owner(fresh):
    cid = new_case("Marina Digestive Health")
    assert model(cid).owner == actor("primary_care_owner", HUDA)
    assert log(cid)[0].payload["registered_provider"] == "Marina Digestive Health"
    assert model(new_case("Unknown Practice")).owner == actor("primary_care_owner", LAYLA)


def test_the_case_page_says_who_did_what(fresh):
    cid = new_case()
    page = client.get(f"/cases/{cid}", headers=login_headers(client, "coordinator")).json()
    kinds = {e["type"]: e["actor_kind"] for e in page["events"]}
    assert kinds == {"case_created": "service", "acknowledge": "automation", "assign_ownership": "automation"}
    assert page["effort"]["automated_actions"] == 2 and page["effort"]["human_actions"] == 0
    assert page["owner"]["display_name"] == LAYLA


def test_with_automation_off_nothing_changes_and_manual_acknowledge_still_works():
    with world(enabled=False):
        cid = new_case()
        assert types(cid) == ["case_created"]
        post(cid, "acknowledge", login_headers(client, "lab"))
        assert model(cid).state == "acknowledged"
        assert scan() == {"enabled": False} and evidence_scan() == {"enabled": False}
        assert types(cid) == ["case_created", "acknowledge"]


def test_manual_acknowledge_after_automatic_acknowledge_is_refused_not_duplicated(fresh):
    cid = new_case()
    post(cid, "acknowledge", login_headers(client, "lab"), expect=409)
    assert types(cid).count("acknowledge") == 1


def test_a_failed_automatic_intake_never_fails_the_ingest_and_the_scan_heals_it(fresh, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("providers file unreadable")

    monkeypatch.setattr(automation, "get_providers", broken)
    cid = new_case()  # 201: asserted by new_case
    assert types(cid) == ["case_created", "acknowledge"]  # the acknowledge went; the assignment did not
    monkeypatch.undo()
    out = scan()
    assert out["assigned"] == 1 and model(cid).owner == actor("primary_care_owner", LAYLA)
    assert scan().get("assigned") is None  # idempotent


def test_the_scan_acknowledges_and_assigns_cases_created_before_automation_was_on():
    with world(enabled=False):
        cid = new_case()
        app_settings.automation_enabled = True
        out = scan()
        assert out["acknowledged"] == 1 and out["assigned"] == 1
        assert types(cid) == ["case_created", "acknowledge", "assign_ownership"]
        assert scan().get("acknowledged") is None


def test_the_assigned_owner_accepts_and_keeps_the_provider_nobody_else_can_accept(fresh):
    cid = new_case()
    other = login("primary_care_owner", "Dr. Somebody Else", "Elsewhere")
    post(cid, "accept_ownership", other, expect=403)  # a case with an owner is that owner's
    post(cid, "accept_ownership", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]))
    m = model(cid)
    assert m.state == "owner_accepted" and m.owner == actor("primary_care_owner", LAYLA)
    assert m.provider == "Riverside Primary Care"  # not lost when the accept names no provider
    assert [e.actor for e in log(cid) if e.type == "accept_ownership"] == [m.owner]


def test_accepting_with_no_prior_assignment_behaves_as_before():
    with world(enabled=False):
        cid = new_case()
        post(cid, "acknowledge", login_headers(client, "lab"))
        post(cid, "accept_ownership", login("primary_care_owner", "Dr A"))
        assert model(cid).provider is None


def test_if_the_owner_does_nothing_the_ladder_reaches_them(fresh):
    """The clock starts regardless: assignment is not acceptance, so the 1 working day accept_ownership SLA breaches and
    the escalation ladder notifies the owner (the first rung's target)."""
    cid = new_case()
    clock.advance(days=3)
    result = run_sweep(cases.store, PATHWAY, default_calendar(), clock.now())
    assert result.mark_overdue == 1 and result.escalate >= 1
    rungs = [e for e in log(cid) if e.type == "escalate"]
    assert rungs[0].payload["rung"] == "reminder" and rungs[0].payload["target"] == "owner"
    assert model(cid).overdue is True


def test_assign_ownership_is_one_time_and_only_before_acceptance(fresh):
    cid = new_case()
    again = Event(UUID(cid), "assign_ownership", clock.now(), AUTOMATION_ACTOR,
                  {"owner": actor("primary_care_owner", HUDA), "auto": True})
    with pytest.raises(InvalidTransition, match="already has an owner"):
        cases.store.append(again, PATHWAY)
    post(cid, "accept_ownership", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]))
    late = dataclasses.replace(again, event_id=uuid4())
    with pytest.raises(InvalidTransition):
        cases.store.append(late, PATHWAY)


def test_assign_ownership_by_anyone_else_is_refused(fresh):
    cid = new_case()
    for headers in (login_headers(client, "supervisor"), login_headers(client, "coordinator"), SCHED):
        post(cid, "assign_ownership", headers, {"owner": actor("primary_care_owner", HUDA), "auto": True}, expect=403)
    # and by the machine, whatever the route: a person's actor is refused
    store = InMemoryEventStore()
    other = uuid4()
    store.create_case(other, PATHWAY.name, Event(other, "case_created", clock.now(), "system:intake", {}), PATHWAY.initial_state)
    with pytest.raises(InvalidTransition):
        store.append(Event(other, "assign_ownership", clock.now(), "supervisor:su",
                           {"owner": actor("primary_care_owner", HUDA), "auto": True}), PATHWAY)


# ---------------------------------------------------------------------------------------------------------------------
# T-A46 the automation token and its limits
# ---------------------------------------------------------------------------------------------------------------------


def test_the_automation_token_is_system_automation_and_cannot_log_in_as_a_user(fresh):
    me = client.get("/me", headers=AUTO).json()
    assert me["user_id"] == AUTOMATION_ACTOR and me["role"] == "automation"
    assert client.post("/auth/dev-login", json={"role": "automation", "name": "x"}).status_code == 403
    assert client.post("/auth/dev-login", json={"role": "system", "name": "automation"}).status_code == 403


def test_the_automation_token_may_emit_exactly_the_events_of_its_row(fresh):
    allowed = {t for t, roles in policy.EVENT_POLICY.items() if "automation" in roles}
    assert allowed == {"acknowledge", "assign_ownership", "complete_confirmatory", "closure_proposed", "handoff_requested"}
    cid = new_case()
    for type_ in set(policy.EVENT_POLICY) - allowed:
        post(cid, type_, AUTO, {}, expect=403)
    # nobody can post these two, the automation included: they are written in process only
    for type_ in ("message_sent", "review_requested"):
        assert policy.EVENT_POLICY[type_] == frozenset()
        post(cid, type_, AUTO, {}, expect=403)
    assert client.get("/cases", headers=AUTO).status_code == 403
    assert client.post("/cases", json={}, headers=AUTO).status_code == 403
    assert client.get("/metrics/effort", headers=AUTO).status_code == 403


def test_the_automation_token_can_never_close_a_case(fresh):
    cid = new_case()
    signed = {"evidence_ref": "Procedure/1", "signed_by": AUTOMATION_ACTOR}
    post(cid, "close", AUTO, signed, expect=403)
    # and the machine refuses it even if a caller got past the route
    with pytest.raises(InvalidTransition):
        cases.store.append(Event(UUID(cid), "close", clock.now(), AUTOMATION_ACTOR, signed), PATHWAY)
    assert model(cid).state != "closed"


def test_the_machine_limits_the_automation_to_its_events_whatever_the_route(fresh):
    cid = new_case()
    for type_ in ("accept_ownership", "contact_patient", "book_confirmatory", "clinician_review", "close", "reassign",
                  "escalation_accepted", "handoff_accepted", "consent_updated", "message_approved", "reopen",
                  "mark_overdue", "escalate", "watchdog_flagged"):
        with pytest.raises(InvalidTransition):
            cases.store.append(Event(UUID(cid), type_, clock.now(), AUTOMATION_ACTOR, {"owner": "supervisor:x"}), PATHWAY)


def test_the_auto_flag_is_the_automations_alone(fresh):
    cid = new_case()
    lab = login_headers(client, "lab")
    post(cid, "acknowledge", lab, {"auto": True}, expect=422)
    post(cid, "handoff_requested", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]), {"to_provider": "X", "auto": True}, expect=422)
    # and the automation always says so
    other = new_case()
    with pytest.raises(Exception):
        cases.store.append(Event(UUID(other), "closure_proposed", clock.now(), AUTOMATION_ACTOR,
                                 {"evidence_ref": "Procedure/1"}), PATHWAY)


def test_the_scan_routes_are_for_the_scheduler_and_the_automation_token_only(fresh):
    for path in ("/scheduler/automation-scan", "/scheduler/evidence-scan"):
        assert client.post(path, headers=SCHED).status_code == 200
        assert client.post(path, headers=AUTO).status_code == 200
        assert client.post(path).status_code == 401
        for role in ("coordinator", "supervisor", "lab", "primary_care_owner"):
            assert client.post(path, headers=login_headers(client, role)).status_code == 403
        assert client.post(path, headers={"Authorization": f"Bearer {app_settings.watchdog_service_token}"}).status_code == 403


def test_service_tokens_outside_dev_must_be_distinct_long_and_not_the_default(monkeypatch):
    good = {"scheduler": "s" * 40, "watchdog": "w" * 40, "automation": "a" * 40}
    monkeypatch.setattr(app_settings, "app_env", "prod")
    monkeypatch.setattr(app_settings, "dev_login_enabled", False)
    monkeypatch.setattr(app_settings, "dev_auth_secret", "x" * 40)
    monkeypatch.setattr(app_settings, "scheduler_service_token", good["scheduler"])
    monkeypatch.setattr(app_settings, "watchdog_service_token", good["watchdog"])
    monkeypatch.setattr(app_settings, "automation_service_token", good["automation"])
    assert auth.secure_config_problems() == []
    monkeypatch.setattr(app_settings, "automation_service_token", "a" * 31)
    assert any("AUTOMATION_SERVICE_TOKEN is shorter than 32" in p for p in auth.secure_config_problems())
    monkeypatch.setattr(app_settings, "automation_service_token", good["watchdog"])
    assert any("WATCHDOG_SERVICE_TOKEN and AUTOMATION_SERVICE_TOKEN are the same value" in p for p in auth.secure_config_problems())
    monkeypatch.setattr(app_settings, "automation_service_token", "")
    assert any("AUTOMATION_SERVICE_TOKEN is empty" in p for p in auth.secure_config_problems())
    from app.config import AUTOMATION_TOKEN_DEFAULT

    monkeypatch.setattr(app_settings, "automation_service_token", AUTOMATION_TOKEN_DEFAULT)
    assert any("AUTOMATION_SERVICE_TOKEN is the built-in" in p for p in auth.secure_config_problems())


# ---------------------------------------------------------------------------------------------------------------------
# T-A43 outreach with pre-approved templates
# ---------------------------------------------------------------------------------------------------------------------


def sent(cid):
    return [e for e in log(cid) if e.type == "message_sent"]


def test_an_accepted_owner_triggers_the_result_notice_with_no_draft_and_no_approval(fresh):
    cid = owner_accepted_case()
    out = scan()
    assert out["outreach_sent"] == 1
    (event,) = sent(cid)
    assert event.actor == AUTOMATION_ACTOR
    assert event.payload["auto"] is True and event.payload["template_id"] == "result_notification.sms"
    assert event.payload["template_version"] == 1 and "draft_id" not in event.payload
    assert event.payload["purpose"] == "result_notification" and event.payload["channel"] == "sms"
    assert "message_drafted" not in types(cid) and "message_approved" not in types(cid)
    (row,) = notify.log.for_case(UUID(cid))
    assert row.status == "sent" and LAYLA in row.body and row.event_id == event.event_id
    assert "colonoscopy" not in row.body.lower() and "cancer" not in row.body.lower()  # non-disclosure (E14)
    assert scan().get("outreach_sent") is None  # once
    assert len(sent(cid)) == 1


def test_the_consent_ledger_picks_the_channel_and_a_withdrawal_stops_everything(fresh):
    cid = owner_accepted_case()
    post(cid, "consent_updated", login("patient", "pat", patient_ref="Patient/1"), {"channel_sms": False})
    scan()
    assert sent(cid)[0].payload["channel"] == "whatsapp"
    cid2 = owner_accepted_case()
    post(cid2, "consent_updated", login("coordinator", "Maryam"), {"withdrawn": True})
    out = scan()
    assert sent(cid2) == [] and notify.log.for_case(UUID(cid2)) == []
    assert out.get("outreach_sent", 0) == 0


def test_invalid_contact_details_move_the_message_to_the_next_channel_or_log_it_blocked(fresh):
    cid = owner_accepted_case(contact_valid={"sms": False})
    scan()
    assert sent(cid)[0].payload["channel"] == "whatsapp"
    cid2 = owner_accepted_case(contact_valid=False)
    out = scan()
    assert sent(cid2) == [] and out["outreach_blocked"] == 1
    (row,) = notify.log.for_case(UUID(cid2))
    assert row.status == "blocked_contact"
    assert scan().get("outreach_blocked") is None  # not retried every loop


def test_a_reminder_after_two_working_days_and_the_weekly_cap(fresh):
    cid = owner_accepted_case()
    scan()  # result notice, Mon 5 Jan
    post(cid, "contact_patient", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]))
    assert scan().get("outreach_sent") is None  # not due yet
    clock.advance(days=3)  # Thu 8 Jan: two working days since entering patient_contacted
    out = scan()
    assert out["outreach_sent"] == 1
    assert [e.payload["template_id"] for e in sent(cid)] == ["result_notification.sms", "reminder.sms"]
    # two automatic messages in the week: the cap (2) holds back a third, however due it is
    post(cid, "patient_acknowledge", login("patient", "pat", patient_ref="Patient/1"))
    post(cid, "handoff_requested", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]), {"to_provider": KARIM[2]})
    post(cid, "handoff_accepted", login(*KARIM[:2], KARIM[2]))
    post(cid, "book_confirmatory", login(*KARIM[:2], KARIM[2]), {"appointment_date": "2026-01-20"})
    out = scan()
    assert out["outreach_capped"] == 1 and len(sent(cid)) == 2
    clock.advance(days=5)  # Tue 13 Jan: the 5 Jan message has left the seven-day window
    out = scan()
    assert out["outreach_sent"] == 1
    last = sent(cid)[-1]
    assert last.payload["template_id"] == "appointment_confirmation.sms"
    assert "Tuesday 20 January 2026" in notify.log.for_case(UUID(cid))[-1].body


def test_an_escalation_rung_can_trigger_a_reminder(fresh):
    cid = owner_accepted_case()
    post(cid, "contact_patient", login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2]))
    clock.advance(days=10)
    run_sweep(cases.store, PATHWAY, default_calendar(), clock.now())
    assert model(cid).escalation_level >= 1
    out = scan()
    assert out["outreach_sent"] >= 1
    assert "reminder.sms" in [e.payload["template_id"] for e in sent(cid)]


# -- the gate is the state machine's, not this module's ----------------------------------------------------------------


def accepted_case_store():
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, "case_created", clock.now(), "system:intake", {}), PATHWAY.initial_state)
    for type_, who, payload in (("acknowledge", "lab:l1", {}), ("accept_ownership", "primary_care_owner:o1", {})):
        store.append(Event(cid, type_, clock.now(), who, payload), PATHWAY)
    return store, cid


def auto_msg(cid, template_id="result_notification.sms", channel="sms", purpose="result_notification", version=1,
             at=None, **over):
    payload = {"notification_id": str(uuid4()), "channel": channel, "language": "en", "purpose": purpose, "auto": True,
               "template_id": template_id, "template_version": version, **over}
    return Event(cid, "message_sent", at or clock.now(), AUTOMATION_ACTOR, payload)


def test_the_machine_accepts_a_listed_template_and_nothing_else():
    with world():
        store, cid = accepted_case_store()
        store.append(auto_msg(cid), PATHWAY)
        for bad, why in (
            (auto_msg(cid, template_id="made_up.sms"), "not in the approved"),
            (auto_msg(cid, version=2), "approved at version 1"),
            (auto_msg(cid, template_id="reminder.sms"), "purpose and channel"),  # purpose says result_notification
            (auto_msg(cid, template_id="reminder.letter", channel="letter", purpose="reminder"), "not in the approved"),
            (auto_msg(cid, channel="whatsapp"), "purpose and channel"),
            (auto_msg(cid, template_id="escalation_notice.sms", purpose="escalation_notice"), "not in the approved"),
        ):
            with pytest.raises(InvalidTransition, match=why):
                store.append(bad, PATHWAY)
        assert len(replay_model(store.events_for(cid), PATHWAY).auto_message_times) == 1


def test_an_automatic_message_needs_the_flag_the_template_and_no_draft():
    with world():
        store, cid = accepted_case_store()
        for payload in ({"auto": True}, {"template_id": "reminder.sms", "template_version": 1},
                        {"draft_id": str(uuid4())}):
            event = auto_msg(cid)
            bad = dataclasses.replace(event, payload={k: v for k, v in event.payload.items()
                                                      if k not in ("auto", "template_id", "template_version")} | payload)
            with pytest.raises(InvalidTransition):
                store.append(bad, PATHWAY)
        # a draft_id with an automatic message: refused
        with pytest.raises(InvalidTransition, match="no draft_id"):
            store.append(auto_msg(cid, draft_id=str(uuid4())), PATHWAY)
        # and a person's actor can never send a template
        for who in ("supervisor:s1", "system:scheduler", "model:drafter"):
            with pytest.raises(InvalidTransition):
                store.append(dataclasses.replace(auto_msg(cid), actor=who), PATHWAY)
        # the notifier still needs a draft a human approved (ADR-030)
        with pytest.raises(InvalidTransition):
            store.append(dataclasses.replace(auto_msg(cid), actor="system:notifier"), PATHWAY)


def test_consent_freeze_and_an_off_channel_block_an_automatic_message_at_apply_time():
    with world():
        store, cid = accepted_case_store()
        store.append(Event(cid, "consent_updated", clock.now(), "patient:p1", {"channel_sms": False}), PATHWAY)
        with pytest.raises(InvalidTransition, match="not consented"):
            store.append(auto_msg(cid), PATHWAY)
        store.append(Event(cid, "consent_updated", clock.now(), "patient:p1", {"withdrawn": True}), PATHWAY)
        with pytest.raises(InvalidTransition, match="consent withdrawn"):
            store.append(auto_msg(cid, "reminder.whatsapp", "whatsapp", "reminder"), PATHWAY)


def test_at_most_max_auto_messages_per_week_enforced_in_the_machine():
    with world():
        store, cid = accepted_case_store()
        t0 = clock.now()
        store.append(auto_msg(cid, at=t0), PATHWAY)
        store.append(auto_msg(cid, "reminder.sms", "sms", "reminder", at=t0 + timedelta(days=1)), PATHWAY)
        with pytest.raises(InvalidTransition, match="2 automatic messages in the last 7 days"):
            store.append(auto_msg(cid, "reminder.whatsapp", "whatsapp", "reminder", at=t0 + timedelta(days=6)), PATHWAY)
        # the first leaves the window at day 7
        store.append(auto_msg(cid, "reminder.whatsapp", "whatsapp", "reminder", at=t0 + timedelta(days=7, minutes=1)), PATHWAY)


def test_the_cap_is_read_from_the_policy(monkeypatch):
    with world():
        pol = outreach_policy.get_policy()
        monkeypatch.setattr(outreach_policy, "get_policy", lambda path=None: dataclasses.replace(pol, max_auto_messages_per_week=0))
        store, cid = accepted_case_store()
        with pytest.raises(InvalidTransition, match="the limit is 0"):
            store.append(auto_msg(cid), PATHWAY)


def test_an_unusable_policy_sends_nothing_but_the_rest_of_the_automation_runs(fresh, monkeypatch):
    def broken(path=None):
        raise outreach_policy.OutreachPolicyError("template set moved on")

    cid = owner_accepted_case()
    monkeypatch.setattr(outreach_policy, "get_policy", broken)
    out = scan()
    assert sent(cid) == [] and any("outreach policy unusable" in e for e in out["errors"])
    store, cid2 = accepted_case_store()
    with pytest.raises(InvalidTransition, match="not usable"):
        store.append(auto_msg(cid2), PATHWAY)


def test_message_sent_still_has_no_poster_and_model_drafts_still_need_a_human(fresh):
    cid = owner_accepted_case()
    payload = {"notification_id": str(uuid4()), "channel": "sms", "language": "en", "purpose": "reminder", "auto": True,
               "template_id": "reminder.sms", "template_version": 1}
    for headers in (AUTO, SCHED, login_headers(client, "supervisor")):
        post(cid, "message_sent", headers, payload, expect=403)
    r = client.post(f"/cases/{cid}/drafts", json={"purpose": "reminder", "channel": "sms", "language": "en"},
                    headers=login_headers(client, "coordinator"))
    assert r.status_code == 201
    assert sent(cid) == []  # drafted, not sent: the approval gate is untouched


# ---------------------------------------------------------------------------------------------------------------------
# T-A44 reroute
# ---------------------------------------------------------------------------------------------------------------------


def requests_of(cid):
    return [(e.actor, e.payload["to_provider"], e.payload.get("auto")) for e in log(cid) if e.type == "handoff_requested"]


def test_a_rejected_referral_is_re_requested_from_the_next_provider(fresh):
    cid = referral_pending_case()
    reject(cid, KARIM)
    assert model(cid).state == "referral_rejected"
    out = scan()
    assert out["rerouted"] == 1
    assert requests_of(cid)[-1] == (AUTOMATION_ACTOR, "Al Noor Endoscopy Centre", True)
    assert model(cid).pending_handoff["to_provider"] == "Al Noor Endoscopy Centre"
    assert scan().get("rerouted") is None  # once per rejection
    # the next provider accepts: back on the path with a new specialist owner
    post(cid, "handoff_accepted", login(*RANA))
    assert model(cid).state == "owner_accepted" and model(cid).provider == "Al Noor Endoscopy Centre"


def test_an_unanswered_request_bounces_and_is_re_routed_again(fresh):
    cid = referral_pending_case()
    reject(cid, KARIM, "wrong_service")
    scan()  # to Al Noor
    clock.advance(days=6)
    run_sweep(cases.store, PATHWAY, default_calendar(), clock.now())  # the bounce (timeout) of the unanswered request
    bounces = [e for e in log(cid) if e.type == "handoff_rejected" and e.payload.get("bounced")]
    assert len(bounces) == 1 and model(cid).pending_handoff is None
    scan()
    assert [p for _, p, _ in requests_of(cid)] == ["Corniche Gastro Unit", "Al Noor Endoscopy Centre", "Marina Digestive Health"]


def test_an_exhausted_list_goes_to_a_person_once_and_a_reassign_clears_it(fresh):
    cid = referral_pending_case()
    reject(cid, KARIM)
    scan()
    reject(cid, RANA, "capacity")
    scan()
    assert [p for _, p, _ in requests_of(cid)][-1] == "Marina Digestive Health"
    reject(cid, HUDA_LOGIN, "capacity")
    out = scan()
    assert out["reroute_exhausted"] == 1
    flag = [e for e in log(cid) if e.type == "review_requested"]
    assert len(flag) == 1 and flag[0].actor == AUTOMATION_ACTOR and flag[0].payload["reason"] == "reroute_exhausted"
    assert model(cid).to_json()["review_reason"] == "reroute_exhausted"
    assert model(cid).consent_frozen is False  # a review flag is not a consent freeze
    scan()
    assert len([e for e in log(cid) if e.type == "review_requested"]) == 1 and len(requests_of(cid)) == 3
    # a coordinator handles it: the flag clears when the case leaves referral_rejected
    post(cid, "reassign", login_headers(client, "coordinator"), {"owner": actor("primary_care_owner", LAYLA)})
    assert model(cid).state == "owner_accepted" and model(cid).to_json()["review_reason"] is None


def test_the_exhausted_flag_is_independent_of_the_consent_review(fresh):
    cid = referral_pending_case()
    reject(cid, KARIM)
    scan()
    reject(cid, RANA)
    scan()
    reject(cid, HUDA_LOGIN)
    scan()
    patient = login("patient", "pat", patient_ref="Patient/1")
    post(cid, "consent_updated", patient, {"withdrawn": True})
    m = model(cid)
    # consent takes precedence while it is withdrawn, and the reroute flag is neither lost nor overwritten ...
    assert m.consent_frozen and m.to_json()["review_reason"] == "consent_withdrawn" and m.reroute_exhausted_at is not None
    scan()
    assert len(requests_of(cid)) == 3  # nothing is sent while consent is withdrawn
    post(cid, "consent_updated", patient, {"withdrawn": False})
    m = model(cid)
    assert m.state == "referral_rejected" and m.to_json()["review_reason"] == "reroute_exhausted"  # ... and is back after
    scan()
    assert len([e for e in log(cid) if e.type == "review_requested" and e.actor == AUTOMATION_ACTOR]) == 1


def test_reasons_that_need_a_person_are_not_re_routed(fresh):
    cid = referral_pending_case()
    reject(cid, KARIM, "patient_declined")
    out = scan()
    assert out["reroute_left_to_humans"] == 1 and len(requests_of(cid)) == 1
    assert "review_requested" not in types(cid)  # the reassign SLA and the ladder carry it


def test_the_machine_lets_the_automation_request_a_handoff_only_to_re_route(fresh):
    cid = owner_accepted_case()
    with pytest.raises(InvalidTransition, match="reroute"):
        cases.store.append(Event(UUID(cid), "handoff_requested", clock.now(), AUTOMATION_ACTOR,
                                 {"to_provider": "Corniche Gastro Unit", "auto": True}), PATHWAY)
    with pytest.raises(InvalidTransition, match="reroute_exhausted"):
        cases.store.append(Event(UUID(cid), "review_requested", clock.now(), AUTOMATION_ACTOR,
                                 {"reason": "reroute_exhausted", "source_event_id": str(uuid4())}), PATHWAY)
    # the consent actor may not use the new reason, nor the automation the old one
    for who, reason in (("system:consent", "reroute_exhausted"), (AUTOMATION_ACTOR, "consent_withdrawn")):
        with pytest.raises(InvalidTransition):
            cases.store.append(Event(UUID(cid), "review_requested", clock.now(), who,
                                     {"reason": reason, "source_event_id": str(uuid4())}), PATHWAY)


# ---------------------------------------------------------------------------------------------------------------------
# T-A45 evidence and the closure proposal
# ---------------------------------------------------------------------------------------------------------------------


def found(cid, *refs):
    evidence.finder = evidence.StaticFinder({cid: list(refs)})


def test_evidence_found_completes_the_step_and_proposes_the_closure(fresh):
    cid = booked_case()
    found(cid, f"Procedure/colonoscopy-{cid}")
    out = evidence_scan()
    assert out["found"] == 1 and out["completed"] == 1 and out["closure_proposed"] == 1
    tail = log(cid)[-2:]
    assert [(e.type, e.actor) for e in tail] == [("complete_confirmatory", AUTOMATION_ACTOR), ("closure_proposed", AUTOMATION_ACTOR)]
    assert tail[0].payload == {"result_ref": f"Procedure/colonoscopy-{cid}", "auto": True}
    assert tail[1].payload == {"evidence_ref": f"Procedure/colonoscopy-{cid}", "auto": True}
    m = model(cid)
    assert m.state == "confirmatory_completed"  # the clinician review is still ahead
    assert m.closure_proposal["evidence_ref"] == f"Procedure/colonoscopy-{cid}"
    assert m.to_json()["closure_proposal"]["event_id"] == str(tail[1].event_id)
    assert evidence_scan().get("found") is None  # it has its proposal: not asked again
    assert types(cid).count("closure_proposed") == 1


def test_the_closure_stays_human_the_clinician_reviews_and_signs_with_the_evidence_prefilled(fresh):
    cid = booked_case()
    ref = f"Procedure/colonoscopy-{cid}"
    found(cid, ref)
    evidence_scan()
    karim = login(*KARIM)
    # the automation cannot close, and neither can any token
    post(cid, "close", AUTO, {"evidence_ref": ref, "signed_by": AUTOMATION_ACTOR}, expect=403)
    post(cid, "close", karim, {"evidence_ref": ref, "signed_by": actor(KARIM[0], KARIM[1])}, expect=409)  # review first
    post(cid, "clinician_review", karim)
    assert model(cid).closure_proposal is not None  # review does not consume it
    page = post(cid, "close", karim, {"evidence_ref": ref, "signed_by": actor(KARIM[0], KARIM[1])})
    assert page["state"] == "closed" and page["read_model"]["closure_proposal"] is None
    assert [e.actor for e in log(cid) if e.type == "close"] == [actor(KARIM[0], KARIM[1])]


def test_a_reopen_withdraws_the_proposal(fresh):
    cid = booked_case()
    ref = f"Procedure/colonoscopy-{cid}"
    found(cid, ref)
    evidence_scan()
    karim = login(*KARIM)
    post(cid, "clinician_review", karim)
    post(cid, "close", karim, {"evidence_ref": ref, "signed_by": actor(KARIM[0], KARIM[1])})
    post(cid, "reopen", karim, {"reason": "evidence_inadequate"})
    assert model(cid).closure_proposal is None
    assert evidence_scan().get("found") is None  # `reopened` is not scanned: the evidence was judged inadequate


def test_when_a_person_completed_the_step_only_the_proposal_is_added(fresh):
    cid = booked_case()
    karim = login(*KARIM)
    post(cid, "complete_confirmatory", karim)
    found(cid, f"DiagnosticReport/colonoscopy-report-{cid}")
    out = evidence_scan()
    assert out.get("completed") is None and out["closure_proposed"] == 1
    assert types(cid)[-1] == "closure_proposed"


def test_evidence_that_is_not_this_cases_is_never_proposed(fresh):
    cid = booked_case()
    mine, theirs = f"Procedure/colonoscopy-{cid}", "Procedure/colonoscopy-someone-else"
    evidence.verifier = evidence.StaticVerifier({mine, theirs}, bindings={theirs: uuid4(), mine: cid})
    found(cid, theirs)
    out = evidence_scan()
    assert out.get("found") is None and "closure_proposed" not in types(cid)
    found(cid, theirs, mine)  # the first that belongs to the case wins
    evidence_scan()
    assert model(cid).closure_proposal["evidence_ref"] == mine


def test_an_unreachable_fhir_server_is_counted_and_changes_nothing(fresh):
    cid = booked_case()
    before = types(cid)
    evidence.finder = evidence.StaticFinder(fail="connection refused")
    out = evidence_scan()
    assert out["unavailable"] == 1 and types(cid) == before


def test_a_case_that_is_not_booked_is_not_asked_about(fresh):
    cid = referral_pending_case()
    finder = evidence.StaticFinder({cid: [f"Procedure/colonoscopy-{cid}"]})
    evidence.finder = finder
    evidence_scan()
    assert finder.calls == [] and "closure_proposed" not in types(cid)


def test_closure_proposed_is_an_annotation_that_changes_no_state_and_needs_the_automation(fresh):
    cid = booked_case()
    with pytest.raises(InvalidTransition, match="closure_proposed: not allowed from state"):
        cases.store.append(Event(UUID(cid), "closure_proposed", clock.now(), AUTOMATION_ACTOR,
                                 {"evidence_ref": "Procedure/1", "auto": True}), PATHWAY)
    post(cid, "complete_confirmatory", login(*KARIM))
    for headers in (login(*KARIM), login_headers(client, "supervisor"), SCHED):
        post(cid, "closure_proposed", headers, {"evidence_ref": "Procedure/1", "auto": True}, expect=403)
    post(cid, "closure_proposed", AUTO, {"evidence_ref": "Procedure/1", "auto": True})
    assert model(cid).state == "confirmatory_completed"


# -- the HAPI search itself, with a fake HTTP layer --------------------------------------------------------------------


class _Resp:
    def __init__(self, body, status=200):
        self.status, self._body = status, json.dumps(body).encode()

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def bundle(*resources):
    return {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": r} for r in resources]}


def test_the_fhir_finder_searches_by_case_identifier_and_by_code_after_the_booking(monkeypatch):
    cid = uuid4()
    booking = datetime(2026, 1, 10, tzinfo=UTC)
    procs = bundle(
        {"resourceType": "Procedure", "id": "by-identifier", "identifier": [{"system": evidence.CASE_IDENTIFIER_SYSTEM, "value": str(cid)}],
         "performedDateTime": "2025-01-01T00:00:00Z"},   # named: counts whatever its date
        {"resourceType": "Procedure", "id": "old-colonoscopy", "performedDateTime": "2020-03-01T00:00:00Z"},  # before booking
        {"resourceType": "Procedure", "id": "after-booking", "performedDateTime": "2026-01-12T09:00:00+00:00"},
        {"resourceType": "Procedure", "id": "bad.id", "performedDateTime": "2026-01-12T09:00:00Z"},  # dots: never a reference
    )
    reports = bundle({"resourceType": "DiagnosticReport", "id": "rep-1", "effectiveDateTime": "2026-01-11T00:00:00Z"})
    urls = []

    def fake_urlopen(request, timeout=None):
        urls.append(request.full_url)
        return _Resp(procs if "/Procedure?" in request.full_url else reports)

    monkeypatch.setattr(evidence.urllib.request, "urlopen", fake_urlopen)
    refs = evidence.FhirFinder("http://hapi/fhir").find(case_id=cid, patient_ref="Patient/abc-1", after=booking)
    assert refs == ["Procedure/by-identifier", "Procedure/after-booking", "DiagnosticReport/rep-1"]
    assert any("identifier=urn%3Ascreening-closure%3Acase%7C" + str(cid) in u for u in urls)
    assert any("subject=Patient%2Fabc-1" in u and "73761001" in u for u in urls)
    assert any("18746-8" in u for u in urls)
    # a patient reference that is not a clean Patient/<id> is never put in a URL
    urls.clear()
    evidence.FhirFinder("http://hapi/fhir").find(case_id=cid, patient_ref="Patient/../x", after=booking)
    assert urls and all("subject=" not in u for u in urls)


def test_the_fhir_finder_reports_an_unreachable_server_as_unavailable(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(evidence.urllib.request, "urlopen", boom)
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.FhirFinder("http://hapi/fhir").find(case_id=uuid4(), patient_ref=None, after=None)


def test_the_finder_is_bound_by_configure():
    class S:
        fhir_verify_evidence = False
        fhir_projection = False
        fhir_base_url = "http://x/fhir"
        fhir_timeout_seconds = 1.0

    saved = (evidence.finder, evidence.verifier)
    try:
        evidence.configure(S)
        assert isinstance(evidence.finder, evidence.NullFinder)
        S.fhir_verify_evidence = True
        evidence.configure(S)
        assert isinstance(evidence.finder, evidence.FhirFinder)
    finally:
        evidence.finder, evidence.verifier = saved


# ---------------------------------------------------------------------------------------------------------------------
# what a patient sees, and the replay
# ---------------------------------------------------------------------------------------------------------------------


def test_a_patient_sees_neither_the_assignment_nor_the_proposal_nor_the_template_internals(fresh):
    cid = booked_case()
    found(cid, f"Procedure/colonoscopy-{cid}")
    scan()
    evidence_scan()
    pat = login("patient", "pat", patient_ref="Patient/1")
    page = client.get(f"/cases/{cid}", headers=pat).json()
    seen = {e["type"] for e in page["events"]}
    assert "assign_ownership" not in seen and "closure_proposed" not in seen
    for e in page["events"]:
        assert "actor_kind" not in e and "template_id" not in e["payload"] and "auto" not in e["payload"]
    timeline = client.get(f"/cases/{cid}/timeline", headers=pat).json()
    assert "closure_proposed" not in json.dumps(timeline) and "template_id" not in json.dumps(timeline)


def test_the_whole_log_replays_to_the_same_model_and_the_timeline_marks_the_actor_kind(fresh):
    cid = booked_case()
    found(cid, f"Procedure/colonoscopy-{cid}")
    scan()
    evidence_scan()
    again = replay_model(log(cid), PATHWAY)
    assert again.to_json() == model(cid).to_json()
    tl = client.get(f"/cases/{cid}/timeline", headers=login_headers(client, "supervisor")).json()
    kinds = {i["type"]: i["actor_kind"] for i in tl["items"] if i["kind"] == "event"}
    assert kinds["acknowledge"] == "automation" and kinds["accept_ownership"] == "human"
    assert kinds["message_sent"] == "automation" and kinds["case_created"] == "service"


def test_scans_are_idempotent_and_concurrent_scans_write_each_event_once(fresh):
    import threading

    cid = referral_pending_case()
    reject(cid, KARIM)
    threads = [threading.Thread(target=scan) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(requests_of(cid)) == 2
    assert len(sent(cid)) <= 2
    assert len({e.event_id for e in log(cid)}) == len(log(cid))
