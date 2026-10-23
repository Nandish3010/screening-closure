"""Hardening of the automation (A21, ADR-032) after the engineering review.

T-A48 replay does not depend on live configuration (the outreach policy is an append-time guard, never read when a log is
replayed); T-A49 consent and reply flags (opt-out, wrong number) stop the automation until a person clears them, and the
weekly cap counts every outbound message; T-A50 the evidence scan is kind to HAPI (appointment date, negative-result TTL,
first outage ends the scan, a SQL filter on the state) and the scheduler's heartbeat covers the scans. (The actor table
is test_actor_table.py; the automation token on the events route is in test_automation.py.)"""

from __future__ import annotations

import urllib.error
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

import test_automation as base
from app import automation, evidence, notify, outreach_policy, state_machine
from app.config import settings as app_settings
from app.events import Event, InMemoryEventStore
from app.state_machine import AUTOMATION_ACTOR, TRIAGE_ACTOR, InvalidTransition, replay_model
from app.watchdog import run_idle_scan
from test_automation import (  # noqa: F401  (the world fixture and the walk helpers)
    AUTO,
    KARIM,
    LAYLA_LOGIN,
    PATHWAY,
    accepted_case_store,
    appointment_passed,
    append_auto,
    auto_msg,
    booked_case,
    client,
    clock,
    cases,
    default_calendar,
    evidence_scan,
    found,
    fresh,
    log,
    login,
    login_headers,
    model,
    new_case,
    owner_accepted_case,
    post,
    referral_pending_case,
    scan,
    sent,
    types,
)

# ---------------------------------------------------------------------------------------------------------------------
# T-A48 (A1): replay does not depend on live configuration
# ---------------------------------------------------------------------------------------------------------------------


def case_with_two_automatic_messages():
    cid = owner_accepted_case()
    scan()  # the result notice
    post(cid, "contact_patient", login(*LAYLA_LOGIN))
    clock.advance(days=3)
    scan()  # the reminder, two working days later
    assert [e.payload["template_id"] for e in sent(cid)] == ["result_notification.sms", "reminder.sms"]
    return cid


def policy_variants(tmp_path, monkeypatch):
    """(label, apply) for a different, a missing and a broken policy file, and one that explodes if it is ever asked."""
    import yaml

    raw = yaml.safe_load(open(app_settings.outreach_policy_path))
    other = {**raw, "max_auto_messages_per_week": 0, "templates": raw["templates"][:1], "rules": raw["rules"][:1],
             "template_set_version": 99}
    different = tmp_path / "different.yaml"
    different.write_text(yaml.safe_dump(other))
    broken = tmp_path / "broken.yaml"
    broken.write_text("templates: [this is: not, valid: yaml\n")

    def use(path):
        def apply():
            outreach_policy._cached.cache_clear()
            monkeypatch.setattr(app_settings, "outreach_policy_path", str(path))
        return apply

    def explode():
        def boom(path=None):
            raise AssertionError("the outreach policy was consulted")
        monkeypatch.setattr(outreach_policy, "get_policy", boom)

    return [("different", use(different)), ("missing", use(tmp_path / "does-not-exist.yaml")),
            ("broken", use(broken)), ("never consulted", explode)]


def test_replay_of_automatic_messages_gives_the_same_model_with_a_different_missing_or_broken_policy(
        fresh, tmp_path, monkeypatch):
    cid = case_with_two_automatic_messages()
    events = log(cid)
    baseline = replay_model(events, PATHWAY).to_json()
    assert baseline["auto_messages_sent"] == 2 and baseline["outbound_messages_sent"] == 2
    for label, apply in policy_variants(tmp_path, monkeypatch):
        apply()
        assert replay_model(events, PATHWAY).to_json() == baseline, label
        # and a store that has to re-apply the whole log (a rebuild, a new replica) accepts it too: no guard, no policy
        rebuilt = InMemoryEventStore()
        rebuilt.create_case(UUID(cid), PATHWAY.name, events[0], PATHWAY.initial_state)
        for e in events[1:]:
            rebuilt.append(e, PATHWAY)
        assert rebuilt.read_model(UUID(cid), PATHWAY).to_json() == baseline, label


def test_a_case_with_automatic_messages_stays_in_the_sweep_and_the_watchdog_whatever_the_policy(
        fresh, tmp_path, monkeypatch):
    from app.sweep import run_sweep

    cid = case_with_two_automatic_messages()
    events = log(cid)
    for label, apply in policy_variants(tmp_path, monkeypatch):
        apply()
        rebuilt = InMemoryEventStore()
        rebuilt.create_case(UUID(cid), PATHWAY.name, events[0], PATHWAY.initial_state)
        for e in events[1:]:
            rebuilt.append(e, PATHWAY)
        swept = run_sweep(rebuilt, PATHWAY, default_calendar(), clock.now() + timedelta(days=60))
        assert swept.cases_evaluated == 1 and swept.errors == [], label
        watched = run_idle_scan(rebuilt, PATHWAY, clock.now() + timedelta(days=60), idle_days=7)
        assert watched.cases_evaluated == 1 and watched.errors == [] and watched.flagged == 1, label


def test_an_edited_policy_changes_what_is_sent_from_now_on_not_what_replays(fresh, monkeypatch, tmp_path):
    """The policy is live configuration for NEW messages: a template version bump makes the append guard refuse a message
    at the old version, while the log that holds the old messages still replays."""
    cid = owner_accepted_case()
    scan()
    events = log(cid)
    pol = outreach_policy.get_policy()
    bumped = {**pol.templates, "reminder.sms": outreach_policy.AutoTemplate(
        "reminder.sms", "reminder", "sms", 2)}
    import dataclasses

    monkeypatch.setattr(outreach_policy, "get_policy", lambda path=None: dataclasses.replace(pol, templates=bumped))
    store, other = accepted_case_store()
    with pytest.raises(InvalidTransition, match="approved at version 2"):
        append_auto(store, auto_msg(other, "reminder.sms", "sms", "reminder", version=1))
    assert replay_model(events, PATHWAY).auto_message_times  # the old log is untouched by the new policy


def test_the_machine_alone_accepts_an_automatic_message_with_the_fields_and_no_policy(monkeypatch):
    """The step function takes an automated `message_sent` that names its template and version, without a word from the
    policy: that is the guard's job, at append time."""
    def boom(path=None):
        raise AssertionError("the state machine consulted the outreach policy")

    monkeypatch.setattr(outreach_policy, "get_policy", boom)
    with base.world():
        store, cid = accepted_case_store()
        store.append(auto_msg(cid, template_id="never_approved.sms", purpose="never_approved", version=41), PATHWAY)
        assert replay_model(store.events_for(cid), PATHWAY).auto_message_times
        for bad, why in ((auto_msg(cid, template_id="reminder.whatsapp"), "template_id is"),  # id != purpose.channel
                         (auto_msg(cid, draft_id=str(uuid4())), "no draft_id")):
            with pytest.raises(InvalidTransition, match=why):
                store.append(bad, PATHWAY)


# ---------------------------------------------------------------------------------------------------------------------
# T-A49 (A5): consent and reply flags, and the weekly cap over every outbound message
# ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def triage():
    """The triage actor's row of the actor table holds `review_requested` (D13, merged at M5 final): nothing to set."""
    assert "review_requested" in state_machine.ACTOR_EVENTS[TRIAGE_ACTOR]


def flag(cid, reason, actor=TRIAGE_ACTOR):
    cases.store.append(Event(UUID(cid), "review_requested", clock.now(), actor,
                             {"reason": reason, "source_event_id": str(uuid4())}), PATHWAY)


def test_the_triage_flags_are_written_in_process_by_the_triage_actor_and_never_posted(fresh):
    """Before the merge of cut-triage the triage row was an empty placeholder; now only `system:triage` writes these
    flags, in process, and nobody can post them through the events route."""
    cid = owner_accepted_case()
    for reason in ("opt_out", "wrong_number"):
        post(cid, "review_requested", login_headers(client, "supervisor"),
             {"reason": reason, "source_event_id": str(uuid4())}, expect=403)
    assert model(cid).outreach_holds == ()
    flag(cid, "opt_out")
    assert model(cid).outreach_holds == ("opt_out",)


def test_each_review_reason_has_one_emitter(fresh, triage, monkeypatch):
    cid = owner_accepted_case()
    monkeypatch.setitem(state_machine.ACTOR_EVENTS, "system:consent", frozenset({"review_requested"}))
    for reason in ("opt_out", "wrong_number"):
        for who in (AUTOMATION_ACTOR, "system:consent"):
            with pytest.raises(InvalidTransition, match="emitted only by 'system:triage'"):
                flag(cid, reason, who)
    for reason in ("consent_withdrawn", "reroute_exhausted"):  # and triage may not use the other reasons
        with pytest.raises(InvalidTransition, match="emitted only by"):
            flag(cid, reason, TRIAGE_ACTOR)


@pytest.mark.parametrize("reason,clearing", [("opt_out", "consent_updated"), ("wrong_number", "contact_updated")])
def test_automation_sends_nothing_after_a_flag_until_a_person_clears_it(fresh, triage, reason, clearing):
    cid = owner_accepted_case()
    flag(cid, reason)
    m = model(cid)
    assert m.outreach_holds == (reason,) and m.to_json()["outreach_holds"] == [reason]
    # the worklist shows the flag; the model keeps it apart from a consent review (D13)
    assert m.triage_review_reason == reason and m.to_json()["review_reason"] == reason and m.review_reason is None
    out = scan()
    assert sent(cid) == [] and notify.log.for_case(UUID(cid)) == [] and out["outreach_held"] == 1
    # the machine itself refuses it too, whatever the caller read earlier (a stale scan racing the flag)
    with pytest.raises(InvalidTransition, match="held"):
        notify.send(UUID(cid), "sms", "en", "result_notification", "x", store=cases.store, pathway=PATHWAY,
                    auto={"template_id": "result_notification.sms", "template_version": 1})
    with pytest.raises(InvalidTransition, match="held"):
        cases.store.append(auto_msg(UUID(cid)), PATHWAY)
    clock.advance(days=30)
    assert scan().get("outreach_sent") is None and sent(cid) == []  # time does not clear it
    # the event that does NOT clear it leaves it open
    other = "contact_updated" if clearing == "consent_updated" else "consent_updated"
    other_payload = {"contact_valid": True} if other == "contact_updated" else {"channel_sms": True}
    post(cid, other, login_headers(client, "coordinator"), other_payload)
    assert model(cid).outreach_holds == (reason,)
    assert scan().get("outreach_sent") is None
    # a person clears it
    payload = {"contact_valid": True} if clearing == "contact_updated" else {"channel_sms": True}
    post(cid, clearing, login_headers(client, "coordinator"), payload)
    m = model(cid)
    assert m.outreach_holds == () and m.review_reason is None and m.triage_review_reason is None
    out = scan()
    assert out["outreach_sent"] == 1 and len(sent(cid)) == 1


def test_both_flags_must_be_cleared_one_by_one(fresh, triage):
    cid = owner_accepted_case()
    flag(cid, "opt_out")
    flag(cid, "wrong_number")
    flag(cid, "wrong_number")  # a second report does not stack
    assert model(cid).outreach_holds == ("opt_out", "wrong_number")
    post(cid, "consent_updated", login_headers(client, "coordinator"), {"channel_sms": True})
    assert model(cid).outreach_holds == ("wrong_number",) and scan().get("outreach_sent") is None
    post(cid, "contact_updated", login_headers(client, "coordinator"), {"contact_valid": True})
    assert model(cid).outreach_holds == () and scan()["outreach_sent"] == 1


def test_a_hold_survives_the_case_moving_on_and_a_replay(fresh, triage):
    cid = owner_accepted_case()
    flag(cid, "opt_out")
    post(cid, "contact_patient", login(*LAYLA_LOGIN))  # the owner phones the patient: still no automatic message
    clock.advance(days=3)
    assert scan().get("outreach_sent") is None and sent(cid) == []
    assert replay_model(log(cid), PATHWAY).outreach_holds == ("opt_out",)
    assert replay_model(log(cid), PATHWAY).to_json() == model(cid).to_json()


def test_the_hold_does_not_stop_the_rest_of_the_automation(fresh, triage):
    cid = referral_pending_case()
    flag(cid, "wrong_number")
    base.reject(cid, KARIM)
    assert scan()["rerouted"] == 1  # a reroute is not a message to the patient


def test_contact_updated_is_a_person_s_event_and_says_only_that_the_details_are_valid(fresh):
    cid = owner_accepted_case()
    body = {"contact_valid": True}
    for headers in (AUTO, base.SCHED, login_headers(client, "lab"), login("patient", "pat", patient_ref="Patient/1")):
        post(cid, "contact_updated", headers, body, expect=403)
    post(cid, "contact_updated", login_headers(client, "coordinator"), {"contact_valid": False}, expect=422)
    post(cid, "contact_updated", login_headers(client, "coordinator"), {}, expect=422)
    post(cid, "contact_updated", login_headers(client, "coordinator"), {"contact_valid": True, "number": "0500000000"}, expect=422)
    other_owner = login("primary_care_owner", "Dr. Somebody Else", "Elsewhere")
    post(cid, "contact_updated", other_owner, body, expect=403)  # owner scoping applies to clinicians
    post(cid, "contact_updated", login(*LAYLA_LOGIN), body)
    post(cid, "contact_updated", login_headers(client, "supervisor"), {**body, "note": "rang the number"})
    assert model(cid).contact_confirmed_at is not None and model(cid).state == "owner_accepted"
    # it changes no state and is a human action in the accounting
    page = client.get(f"/cases/{cid}", headers=login_headers(client, "coordinator")).json()
    assert [e["actor_kind"] for e in page["events"] if e["type"] == "contact_updated"] == ["human", "human"]


def test_a_person_who_confirmed_the_contact_details_overrides_a_contact_valid_false_on_the_case(fresh):
    cid = owner_accepted_case(contact_valid=False)
    post(cid, "contact_updated", login_headers(client, "coordinator"), {"contact_valid": True})
    out = scan()
    assert out["outreach_sent"] == 1 and out.get("outreach_blocked") is None
    (row,) = notify.log.for_case(UUID(cid))
    assert row.status == "sent"
    genesis = log(cid)[0].payload
    assert notify.contact_blocked(genesis, "sms") == "contact details flagged invalid"  # the flag is still the log's
    assert notify.contact_blocked(genesis, "sms", model(cid)) is None


def approved_send(store, cid, n, channel="sms"):
    """A draft approved by the owner and sent by the notifier (the human path), the way the outbox writes it."""
    draft = str(uuid4())
    store.append(Event(cid, "message_approved", clock.now(), "primary_care_owner:o1", {"draft_id": draft}), PATHWAY)
    store.append(Event(cid, "message_sent", clock.now(), state_machine.NOTIFIER_ACTOR,
                       {"notification_id": str(uuid4()), "channel": channel, "language": "en", "purpose": "first_contact",
                        "draft_id": draft}), PATHWAY)


def test_the_weekly_cap_counts_every_outbound_message_not_only_the_automatic_ones():
    with base.world():
        store, cid = accepted_case_store()
        approved_send(store, cid, 1)
        approved_send(store, cid, 2)
        m = store.read_model(cid, PATHWAY)
        assert len(m.outbound_message_times) == 2 and m.auto_message_times == ()
        with pytest.raises(InvalidTransition, match="2 outbound messages"):
            append_auto(store, auto_msg(cid))
        # a week later both have left the window
        append_auto(store, auto_msg(cid, at=clock.now() + timedelta(days=7, minutes=1)))
        assert len(store.read_model(cid, PATHWAY).outbound_message_times) == 3
        # and the automatic ones count for the next: two in the new week fill it
        append_auto(store, auto_msg(cid, "reminder.sms", "sms", "reminder", at=clock.now() + timedelta(days=7, hours=2)))
        with pytest.raises(InvalidTransition, match="2 outbound messages"):
            append_auto(store, auto_msg(cid, "reminder.whatsapp", "whatsapp", "reminder",
                                        at=clock.now() + timedelta(days=7, hours=3)))


def test_the_scan_holds_back_a_message_when_approved_drafts_filled_the_week(fresh):
    cid = owner_accepted_case()
    owner = base.actor("primary_care_owner", base.LAYLA)
    for _ in range(2):
        draft = str(uuid4())
        cases.store.append(Event(UUID(cid), "message_approved", clock.now(), owner, {"draft_id": draft}), PATHWAY)
        cases.store.append(Event(UUID(cid), "message_sent", clock.now(), state_machine.NOTIFIER_ACTOR,
                                 {"notification_id": str(uuid4()), "channel": "sms", "language": "en",
                                  "purpose": "first_contact", "draft_id": draft}), PATHWAY)
    out = scan()
    assert out["outreach_capped"] == 1 and sent(cid)[0].actor == state_machine.NOTIFIER_ACTOR and len(sent(cid)) == 2
    clock.advance(days=8)
    assert scan()["outreach_sent"] == 1  # the week has passed: still due, now sent


# ---------------------------------------------------------------------------------------------------------------------
# T-A50 (A5): the scans are kind to HAPI and to the scheduler
# ---------------------------------------------------------------------------------------------------------------------


class FakeHapi:
    """A stand-in for the FHIR server behind `FhirFinder`: every search answers an empty Bundle (nothing yet), or the
    connection fails. Counts the requests it was asked."""

    def __init__(self, fail=False):
        self.fail, self.requests = fail, []

    def urlopen(self, request, timeout=None):
        self.requests.append(request.full_url)
        if self.fail:
            raise urllib.error.URLError("connection refused")
        return base._Resp({"resourceType": "Bundle", "type": "searchset", "entry": []})


@pytest.fixture
def hapi(fresh, monkeypatch):
    fake = FakeHapi()
    monkeypatch.setattr(evidence.urllib.request, "urlopen", fake.urlopen)
    monkeypatch.setattr(evidence, "finder", evidence.FhirFinder("http://hapi/fhir"))
    return fake


def booked_without_a_date():
    cid = referral_pending_case()
    post(cid, "handoff_accepted", login(*KARIM))
    post(cid, "book_confirmatory", login(*KARIM), {})
    return cid


def test_the_first_unavailable_answer_ends_the_evidence_scan(hapi):
    cids = [booked_case() for _ in range(3)]
    appointment_passed()
    hapi.fail = True
    out = evidence_scan()
    assert out["unavailable"] == 1 and "asked" not in out
    assert len(hapi.requests) == 1  # one question to a server that is down, not one per case
    hapi.fail = False
    assert evidence_scan()["asked"] == 3  # the outage was not remembered as "no evidence"
    assert all("closure_proposed" not in types(c) for c in cids)


def test_a_case_with_no_evidence_yet_is_not_asked_again_for_six_simulated_hours(hapi):
    cid = booked_case()
    appointment_passed()
    first = evidence_scan()
    assert first["asked"] == 1
    asked = len(hapi.requests)
    assert asked > 0
    again = evidence_scan()
    assert again["skipped_recent_no"] == 1 and "asked" not in again and len(hapi.requests) == asked
    clock.advance(hours=5, )
    assert evidence_scan()["skipped_recent_no"] == 1 and len(hapi.requests) == asked
    clock.advance(hours=1, )  # six hours since the question
    assert evidence_scan()["asked"] == 1 and len(hapi.requests) > asked
    # evidence that turns up after the TTL is found
    found(cid, f"Procedure/colonoscopy-{cid}")
    clock.advance(hours=7)
    assert evidence_scan()["closure_proposed"] == 1


def test_the_negative_cache_is_per_case_and_a_restart_only_costs_a_question(hapi):
    a, b = booked_case(), booked_case()
    appointment_passed()
    evidence_scan()
    n = len(hapi.requests)
    evidence_scan()
    assert len(hapi.requests) == n
    automation.evidence_negatives.clear()  # a new process
    assert evidence_scan()["asked"] == 2 and len(hapi.requests) == 2 * n
    cache = automation.NegativeCache(ttl=timedelta(hours=1))
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    cache.remember(UUID(a), t0)
    assert cache.fresh(UUID(a), t0 + timedelta(minutes=59)) and not cache.fresh(UUID(a), t0 + timedelta(hours=1))
    assert not cache.fresh(UUID(a), t0 - timedelta(minutes=1)) and not cache.fresh(UUID(b), t0)  # clock went back; other case
    cache.prune(t0 + timedelta(hours=2))
    assert len(cache) == 0


def test_hapi_is_asked_only_once_the_booked_appointment_has_begun(hapi):
    future = booked_case()  # booked for 20 January 2026; the clock is at 5 January
    undated = booked_without_a_date()
    out = evidence_scan()
    assert out["not_yet_due"] == 1 and out["asked"] == 1  # the undated booking cannot be told apart: asked
    asked_before = len(hapi.requests)
    clock.advance(days=14)  # 19 January: still the day before for the dated booking (the undated one's "no" has expired)
    out = evidence_scan()
    assert out["not_yet_due"] == 1 and out["asked"] == 1
    clock.advance(days=1)  # 20 January 08:00 UTC: the appointment day has begun
    out = evidence_scan()
    assert out.get("not_yet_due") is None and out["asked"] == 2
    assert len(hapi.requests) > asked_before
    assert future != undated


def test_a_timed_appointment_has_begun_at_its_time(fresh):
    ev = [Event(uuid4(), "book_confirmatory", datetime(2026, 1, 1, tzinfo=UTC), "x:1",
                {"appointment_at": "2026-01-20T14:30:00+04:00"})]
    assert automation._appointment_begun(ev, datetime(2026, 1, 20, 10, 29, tzinfo=UTC)) is False
    assert automation._appointment_begun(ev, datetime(2026, 1, 20, 10, 30, tzinfo=UTC)) is True
    assert automation._appointment_begun([Event(uuid4(), "book_confirmatory", datetime(2026, 1, 1, tzinfo=UTC), "x:1",
                                                {"appointment_date": "not a date"})], datetime(2026, 1, 1, tzinfo=UTC))


class SpyStore:
    """Delegates to a store and records the `states` filter handed to `iter_open_case_models`."""

    def __init__(self, inner):
        self._inner, self.filters = inner, []

    def iter_open_case_models(self, pathway, only=None, **kw):
        self.filters.append(kw.get("states"))
        return self._inner.iter_open_case_models(pathway, only=only, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_the_scans_ask_the_store_only_for_the_states_they_can_act_on(fresh):
    done = referral_pending_case()  # patient_acknowledged: no rule, no intake, nothing to reroute
    waiting = booked_case()
    spy = SpyStore(cases.store)
    out = automation.scan_evidence(spy, PATHWAY, clock.now())
    assert set(spy.filters[0]) == set(automation.EVIDENCE_SCAN_STATES) and out.get("asked") is None
    out = automation.run_automation_scan(spy, PATHWAY, clock.now(), default_calendar())
    states = set(spy.filters[1])
    assert {"result_received", "acknowledged", "referral_rejected", "owner_accepted", "patient_contacted",
            "confirmatory_booked"} <= states
    assert "patient_acknowledged" not in states and "clinician_reviewed" not in states and "closed" not in states
    assert out["cases_evaluated"] == 1  # only `waiting` (booked) is a candidate: `done` was never replayed or looked at
    assert done != waiting


def test_a_rule_that_applies_in_any_state_turns_the_state_filter_off(fresh, monkeypatch):
    import dataclasses

    pol = outreach_policy.get_policy()
    anywhere = dataclasses.replace(pol.rules[0], state=None)
    monkeypatch.setattr(outreach_policy, "get_policy", lambda path=None: dataclasses.replace(pol, rules=(anywhere,)))
    spy = SpyStore(cases.store)
    automation.run_automation_scan(spy, PATHWAY, clock.now(), default_calendar())
    assert spy.filters == [None]


def test_the_in_memory_store_filters_by_state_like_the_database(fresh):
    a, b = owner_accepted_case(), booked_case()
    got = {str(c) for c, _, _ in cases.store.iter_open_case_models(PATHWAY, states=["owner_accepted"])}
    assert got == {a}
    got = {str(c) for c, _, _ in cases.store.iter_open_case_models(PATHWAY, states=("confirmatory_booked", "closed"))}
    assert got == {b}
    assert {str(c) for c, _, _ in cases.store.iter_open_case_models(PATHWAY, states=[])} == set()
    assert {str(c) for c, _, _ in cases.store.iter_open_case_models(PATHWAY)} == {a, b}


# -- the scheduler's heartbeat covers the scans -----------------------------------------------------------------------

import importlib.util
import time
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "scheduler" / "main.py"


@pytest.fixture
def sched(monkeypatch):
    import threading

    spec = importlib.util.spec_from_file_location("scheduler_main_hardening", MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.calls = []
    lock = threading.Lock()

    def record(name, *a, **k):
        with lock:
            mod.calls.append((name, a, k))

    monkeypatch.setattr(mod, "post_heartbeat", lambda *a, **k: record("beat", *a, **k))
    monkeypatch.setattr(mod, "write_heartbeat", lambda *a, **k: record("write"))
    monkeypatch.setattr(mod, "drain_outbox", lambda: None)
    monkeypatch.setattr(mod, "run_sweep", lambda: {"sim_time": "2026-01-05T08:00:00+00:00", "cases_evaluated": 0,
                                                   "mark_overdue": 0, "escalate": 0, "rejected": 0, "errors": []})
    monkeypatch.setattr(mod, "BEAT_SECONDS", 0.05)
    monkeypatch.setattr(mod, "acquire_lease", lambda: {"leader": True, "token": 1})  # A31: the leader's loop
    return mod


def test_heartbeats_keep_coming_while_a_long_automation_scan_runs(sched, monkeypatch):
    """A scan over a large cohort, or a slow HAPI, can outlast the watchdog's 60 s: a busy scheduler must not look dead."""
    def slow_scan(path):
        sched.calls.append((f"scan-start:{path}", (), {}))
        time.sleep(0.4)  # several beat periods
        sched.calls.append((f"scan-end:{path}", (), {}))
        return {"enabled": True, "asked": 1, "errors": []}

    monkeypatch.setattr(sched, "run_automation", slow_scan)
    sched.loop_once(sched.LoopState())
    names = [c[0] for c in sched.calls]
    start, end = names.index("scan-start:automation-scan"), names.index("scan-end:evidence-scan")
    inside = [c for c in sched.calls[start:end] if c[0] == "beat"]
    assert len(inside) >= 4, names  # beats during two 0.4 s scans, 0.05 s apart
    assert all(c[2] == {"sweeping": True} for c in inside)
    # and the thread is stopped when the scans are over: the next call after `scan-end` is the loop's own final beat
    after = sched.calls[end + 1:]
    assert [c[0] for c in after if c[0] == "beat"].__len__() == 1 and after[-1][2].get("sweeping") is not True


def test_a_failing_scan_still_stops_the_heartbeat_thread_and_the_loop_goes_on(sched, monkeypatch):
    def broken(path):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(sched, "run_automation", broken)
    with pytest.raises(RuntimeError):  # run_automation catches its own HTTP errors; anything else is a bug that surfaces
        sched.loop_once(sched.LoopState())
    time.sleep(0.2)
    n = len([c for c in sched.calls if c[0] == "beat"])
    time.sleep(0.2)
    assert len([c for c in sched.calls if c[0] == "beat"]) == n  # BeatWhile's __exit__ ran: no beat thread left behind
