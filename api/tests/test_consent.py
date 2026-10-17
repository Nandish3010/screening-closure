"""T-A23 and T-A24 (in-memory store; Postgres versions in test_consent_pg.py): the consent
ledger derived by replay, the outreach freeze, and the human-review flag."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from api_helpers import APPROVED, DRAFTED, actor_of, login_headers, make_client
from app.clock import clock
from app.config import settings as app_settings
from app.consent import channel_allowed, ensure_review_requested, review_event_id
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import (
    CONSENT_FROZEN_EVENTS,
    CONSENT_KEYS,
    InvalidTransition,
    PayloadError,
    step,
)
from test_state_machine import OWNER, TO_CONTACTED, drive, make_event

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
FROZEN = sorted(CONSENT_FROZEN_EVENTS)


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


def consent_event(payload, actor="patient:p", when=None):
    return make_event("consent_updated", actor, payload, when or at(5))


# ---- the ledger, derived by replay --------------------------------------------------------------


def test_defaults_before_anyone_says_anything():
    m = drive(*TO_CONTACTED)
    assert m.consent == {
        "channel_sms": True, "channel_whatsapp": True, "channel_phone": True,
        "channel_letter": True, "share_with_specialist": True, "withdrawn": False,
        "updated_at": None, "updated_by": None,
    }
    assert m.consent_history == () and not m.consent_frozen


def test_partial_updates_merge_and_every_change_is_kept():
    m = drive(*TO_CONTACTED)
    m = step(m, consent_event({"channel_sms": False}, when=at(5)), PATHWAY)
    m = step(m, consent_event({"channel_whatsapp": False, "share_with_specialist": False}, "coordinator:co", at(6)), PATHWAY)
    m = step(m, consent_event({"channel_sms": True}, when=at(7)), PATHWAY)
    assert (m.consent["channel_sms"], m.consent["channel_whatsapp"]) == (True, False)
    assert m.consent["share_with_specialist"] is False and m.consent["channel_phone"] is True
    assert m.consent["updated_by"] == "patient:p" and m.consent["updated_at"] == at(7).isoformat()
    assert [h["changes"] for h in m.consent_history] == [
        {"channel_sms": False},
        {"channel_whatsapp": False, "share_with_specialist": False},
        {"channel_sms": True},
    ]
    assert [h["actor"] for h in m.consent_history] == ["patient:p", "coordinator:co", "patient:p"]
    assert m.state == "patient_contacted"  # channel choices change no state


def test_the_current_value_is_the_replay_of_the_history():
    events = [consent_event({"channel_sms": False}, when=at(5)),
              consent_event({"channel_sms": True, "channel_letter": False}, when=at(6)),
              consent_event({"channel_phone": False}, when=at(7))]
    m = drive(*TO_CONTACTED)
    for ev in events:
        m = step(m, ev, PATHWAY)
    folded = {}
    for h in m.consent_history:
        folded.update(h["changes"])
    assert all(m.consent[k] is v for k, v in folded.items())


@pytest.mark.parametrize(
    "payload",
    [{}, {"channel": "sms"}, {"channel_sms": "false"}, {"channel_sms": 0}, {"withdrawn": None},
     {"share_with_specialist": 1}, {"channel_fax": False}],
)
def test_malformed_consent_payloads_are_payload_errors(payload):
    with pytest.raises(PayloadError):
        step(drive(*TO_CONTACTED), consent_event(payload), PATHWAY)


def test_withdrawal_freezes_and_restoring_unfreezes_with_the_history_kept():
    m = drive(*TO_CONTACTED)
    w = step(m, consent_event({"withdrawn": True, "channel_sms": False}, when=at(5)), PATHWAY)
    assert w.state == "consent_withdrawn" and w.consent_frozen and w.consent["withdrawn"] is True
    assert w.resume_state == "patient_contacted" and w.consent["channel_sms"] is False
    back = step(w, consent_event({"withdrawn": False}, "coordinator:co", at(6)), PATHWAY)
    assert back.state == "patient_contacted" and not back.consent_frozen
    assert back.consent["withdrawn"] is False and back.consent["channel_sms"] is False  # kept
    assert len(back.consent_history) == 2


def test_frozen_case_still_accepts_channel_changes_but_stays_frozen():
    w = step(drive(*TO_CONTACTED), consent_event({"withdrawn": True}), PATHWAY)
    again = step(w, consent_event({"channel_phone": False}, when=at(6)), PATHWAY)
    assert again.state == "consent_withdrawn" and again.consent["withdrawn"] is True


def test_consent_updates_come_from_humans_only():
    with pytest.raises(InvalidTransition):
        step(drive(*TO_CONTACTED), consent_event({"channel_sms": False}, "system:scheduler"), PATHWAY)


# ---- the freeze ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", FROZEN)
def test_a_frozen_case_refuses_outreach_with_the_reason(kind):
    owned = drive(*TO_CONTACTED)
    frozen = step(owned, consent_event({"withdrawn": True}), PATHWAY)
    payload = {"to_provider": "endo-1"} if kind == "handoff_requested" else {}
    actor = "system:notifier" if kind == "message_sent" else OWNER
    with pytest.raises(InvalidTransition, match="consent withdrawn"):
        step(frozen, make_event(kind, actor, payload, at(6)), PATHWAY)


def test_the_same_events_are_fine_while_consent_stands():
    m = drive(*TO_CONTACTED)
    for kind in FROZEN:
        payload = {"to_provider": "endo-1"} if kind == "handoff_requested" else {}
        if kind == "contact_patient":
            continue  # a state transition, not valid from patient_contacted
        step(m, make_event(kind, OWNER if kind != "message_sent" else "system:notifier", payload, at(6)), PATHWAY)


def test_freeze_does_not_stop_the_things_that_protect_the_patient():
    frozen = step(drive(*TO_CONTACTED), consent_event({"withdrawn": True}), PATHWAY)
    for kind, actor, payload in [
        ("callback_requested", "patient:p", {}),
        ("watchdog_flagged", "watchdog", {}),
        ("callback_requested", "coordinator:co", {}),
    ]:
        assert step(frozen, make_event(kind, actor, payload, at(6)), PATHWAY).state == "consent_withdrawn"


# ---- the review flag -----------------------------------------------------------------------------


def review(actor="system:consent", reason="consent_withdrawn"):
    return make_event("review_requested", actor, {"reason": reason}, at(5, 1))


def test_review_requested_belongs_to_system_consent_alone():
    frozen = step(drive(*TO_CONTACTED), consent_event({"withdrawn": True}), PATHWAY)
    m = step(frozen, review(), PATHWAY)
    assert m.review_reason == "consent_withdrawn" and m.review_requested_at == at(5, 1)
    assert m.state == "consent_withdrawn"
    for actor in ("system:scheduler", "watchdog", "model:drafter", "supervisor:su", "coordinator:co", "patient:p"):
        with pytest.raises(InvalidTransition):
            step(frozen, review(actor), PATHWAY)
    with pytest.raises(PayloadError):
        step(frozen, review(reason="idle"), PATHWAY)
    # and system:consent can emit nothing else
    for kind in ("watchdog_flagged", "callback_requested", "message_sent", "escalate", "close"):
        with pytest.raises(InvalidTransition):
            step(frozen, make_event(kind, "system:consent", {}, at(6)), PATHWAY)


def test_restoring_consent_clears_the_review():
    frozen = step(drive(*TO_CONTACTED), consent_event({"withdrawn": True}), PATHWAY)
    flagged = step(frozen, review(), PATHWAY)
    back = step(flagged, consent_event({"withdrawn": False}, "coordinator:co", at(7)), PATHWAY)
    assert back.review_reason is None and back.review_requested_at is None


def test_channel_allowed():
    m = drive(*TO_CONTACTED)
    assert channel_allowed(m, "sms") == (True, "ok")
    off = step(m, consent_event({"channel_whatsapp": False}), PATHWAY)
    assert channel_allowed(off, "whatsapp")[0] is False and channel_allowed(off, "sms")[0] is True
    assert channel_allowed(m, "pigeon")[0] is False
    frozen = step(m, consent_event({"withdrawn": True}), PATHWAY)
    assert channel_allowed(frozen, "sms") == (False, "consent withdrawn")


def test_ensure_review_requested_is_idempotent():
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
    assert ensure_review_requested(store, PATHWAY, cid, uuid4(), at(1)) is None  # not frozen
    trigger = uuid4()
    store.append(Event(cid, "consent_updated", at(1), "patient:p", {"withdrawn": True}, trigger), PATHWAY)
    flag = ensure_review_requested(store, PATHWAY, cid, trigger, at(1))
    assert flag.event_id == review_event_id(trigger) and flag.actor == "system:consent"
    assert ensure_review_requested(store, PATHWAY, cid, trigger, at(1)) is None
    assert [e.type for e in store.events_for(cid)].count("review_requested") == 1


def test_consent_keys_match_the_brief():
    assert CONSENT_KEYS == ("channel_sms", "channel_whatsapp", "channel_phone", "channel_letter",
                            "share_with_specialist", "withdrawn")


# ---- over HTTP: T-A23 and T-A24 --------------------------------------------------------------------

client = make_client()
LAB = login_headers(client, "lab", "lab one")
COORD = login_headers(client, "coordinator", "bea")
SUP = login_headers(client, "supervisor", "sam")
PATIENT = login_headers(client, "patient", "pat", "Patient/1")
OTHER_PATIENT = login_headers(client, "patient", "pat2", "Patient/2")
PCO = login_headers(client, "primary_care_owner", "dr a")
SPEC = login_headers(client, "specialist_owner", "dr s")


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    clock.reset()
    yield
    clock.reset()


def post(case_id, type_, headers, payload=None, **extra):
    return client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}, **extra}, headers=headers)


def new_case(patient_ref="Patient/1"):
    cid = client.post("/cases", json={"patient_ref": patient_ref}, headers=COORD).json()["case_id"]
    for type_, headers in [("acknowledge", LAB), ("accept_ownership", PCO), ("contact_patient", PCO)]:
        assert post(cid, type_, headers).status_code == 201
    return cid


def case(cid):
    return client.get(f"/cases/{cid}", headers=COORD).json()


def test_t_a24_patient_and_coordinator_may_change_consent_everyone_else_gets_403():
    cid = new_case()
    assert post(cid, "consent_updated", PATIENT, {"channel_sms": False}).status_code == 201
    assert post(cid, "consent_updated", COORD, {"channel_whatsapp": False}).status_code == 201
    n = len(case(cid)["events"])
    for headers in (SUP, PCO, SPEC, LAB):
        assert post(cid, "consent_updated", headers, {"channel_sms": True}).status_code == 403
    assert post(cid, "consent_updated", OTHER_PATIENT, {"channel_sms": True}).status_code == 404  # not theirs
    assert len(case(cid)["events"]) == n
    rm = case(cid)["read_model"]
    assert rm["consent"]["channel_sms"] is False and rm["consent"]["channel_whatsapp"] is False
    assert [(h["actor"], h["changes"]) for h in rm["consent_history"]] == [
        (actor_of("patient", "pat"), {"channel_sms": False}), (actor_of("coordinator", "bea"), {"channel_whatsapp": False})]


def test_t_a24_history_is_in_the_timeline_and_matches_the_derived_value():
    cid = new_case()
    for payload in ({"channel_sms": False}, {"channel_sms": True}, {"share_with_specialist": False}):
        assert post(cid, "consent_updated", PATIENT, payload).status_code == 201
    events = [e for e in case(cid)["events"] if e["type"] == "consent_updated"]
    assert [e["payload"] for e in events] == [{"channel_sms": False}, {"channel_sms": True}, {"share_with_specialist": False}]
    rm = case(cid)["read_model"]
    assert rm["consent"]["channel_sms"] is True and rm["consent"]["share_with_specialist"] is False
    assert len(rm["consent_history"]) == 3


@pytest.mark.parametrize("payload", [{}, {"channel_sms": "no"}, {"withdrawn": 1}])
def test_bad_consent_payloads_are_422_and_write_nothing(payload):
    cid = new_case()
    n = len(case(cid)["events"])
    assert post(cid, "consent_updated", PATIENT, payload).status_code == 422
    assert len(case(cid)["events"]) == n


def test_t_a23_withdrawal_freezes_outreach_and_flags_the_case_for_review():
    cid = new_case()
    r = post(cid, "consent_updated", PATIENT, {"withdrawn": True})
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "consent_withdrawn"
    rm = body["read_model"]
    assert rm["consent_frozen"] is True and rm["review_reason"] == "consent_withdrawn"
    assert [(e["type"], e["actor"]) for e in body["events"][-2:]] == [
        ("consent_updated", actor_of("patient", "pat")), ("review_requested", "system:consent")]
    # it is on the human-review list
    listed = client.get("/cases", params={"state": "consent_withdrawn"}, headers=COORD).json()
    assert cid in [c["case_id"] for c in listed]
    # outreach and new handoffs are refused with the reason; nothing is written
    n = len(case(cid)["events"])
    for type_, headers, payload in [
        ("contact_patient", PCO, {}),
        ("message_drafted", COORD, DRAFTED),
        ("message_drafted", PCO, DRAFTED),
        ("message_approved", PCO, APPROVED),
        ("handoff_requested", PCO, {"to_provider": "endo-1"}),
        ("handoff_requested", SUP, {"to_provider": "endo-1"}),
    ]:
        r = post(cid, type_, headers, payload)
        assert r.status_code == 409 and "consent withdrawn" in r.json()["detail"], (type_, r.text)
    assert post(cid, "message_sent", PCO).status_code == 403  # nobody posts it
    assert len(case(cid)["events"]) == n
    # a human can still act on what matters: ask for a callback
    assert post(cid, "callback_requested", PATIENT, {"reason": "wants_to_speak_to_clinician"}).status_code == 201


def test_the_review_flag_cannot_be_posted_by_anyone():
    cid = new_case()
    post(cid, "consent_updated", PATIENT, {"withdrawn": True})
    for headers in (PATIENT, COORD, SUP, PCO, SPEC, LAB):
        assert post(cid, "review_requested", headers, {"reason": "consent_withdrawn"}).status_code == 403


def test_a_retried_withdrawal_adds_no_second_flag():
    cid = new_case()
    eid = str(uuid4())
    for _ in range(3):
        assert post(cid, "consent_updated", PATIENT, {"withdrawn": True}, event_id=eid).status_code == 201
    assert [e["type"] for e in case(cid)["events"]].count("review_requested") == 1
    assert [e["type"] for e in case(cid)["events"]].count("consent_updated") == 1


def test_restoring_consent_unfreezes_and_clears_the_review():
    cid = new_case()
    post(cid, "consent_updated", PATIENT, {"withdrawn": True})
    r = post(cid, "consent_updated", COORD, {"withdrawn": False})  # recorded on the patient's behalf
    assert r.status_code == 201 and r.json()["state"] == "patient_contacted"
    rm = r.json()["read_model"]
    assert rm["consent_frozen"] is False and rm["review_reason"] is None
    assert [e["type"] for e in r.json()["events"]].count("review_requested") == 1  # history stays
    assert post(cid, "message_drafted", PCO, DRAFTED).status_code == 201
