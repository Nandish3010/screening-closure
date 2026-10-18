"""D13: POST/GET /cases/{id}/replies with a fake classifier (in-memory stores). The Postgres half is test_replies_pg.py.

What these pin down: for each intent the one automatic effect and no other; that the text never reaches the event log;
that nothing closes a case or changes its state; that a person is asked to look whenever the system is not sure or the
intent is one that always goes to a person; that the model being absent is the same as the model being unsure; and who
may call the route and which events nobody but the triage may write.
"""

import json
from uuid import UUID, uuid4

import pytest

from api_helpers import ROLES, login_headers, make_client, user_id_of
from app import consent, payloads, policy
from app.events import Event
from app.routers import cases, replies
from app.state_machine import TRIAGE_ACTOR, InvalidTransition, replay_model
from app.triage import classify as triage
from app.triage.labels import INTENTS
from app.triage.reply_store import InMemoryReplyStore

client = make_client()
CAL = triage.Calibration(temperature=1.0, threshold=0.8, mass_floor=0.5, model_id="fake-model-1")


class KeywordScorer:
    """Scores by a keyword in the text: `intent:<name>` picks that intent, `unsure` spreads the probability."""

    def score(self, text):
        if "unsure" in text:
            return {"logits": [0.2 if i == "confirm" else 0.0 for i in INTENTS], "mass": 0.9}
        want = next((i for i in INTENTS if f"intent:{i}" in text), "other")
        return {"logits": [10.0 if i == want else 0.0 for i in INTENTS], "mass": 0.99}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(replies, "store", InMemoryReplyStore())
    triage.set_backend(KeywordScorer(), CAL)
    yield
    triage.reset()


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def new_case(patient_ref="Patient/1"):
    r = client.post("/cases", json={"patient_ref": patient_ref}, headers=H("coordinator"))
    assert r.status_code == 201
    return r.json()["case_id"]


def post_event(cid, type_, role, payload=None, name=None, patient_ref=None):
    if name is None and role in ("primary_care_owner", "specialist_owner"):
        name = "Dr Sara Khan"
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=H(role, name, patient_ref))
    assert r.status_code == 201, r.text
    return r.json()


def reply(cid, text, channel="whatsapp", role="coordinator", **extra):
    return client.post(f"/cases/{cid}/replies", json={"channel": channel, "text": text, **extra}, headers=H(role))


def log(cid):
    return cases.store.events_for(UUID(cid))


def kinds_after_creation(cid):
    return [e.type for e in log(cid)][1:]


def state(cid):
    return cases.store.current_state(UUID(cid))


# ------------------------------------------------------------------------------------------ per intent
EFFECT = {
    "confirm": None,
    "reschedule": ("callback_requested", {"reason": "reschedule"}),
    "cannot_attend": ("callback_requested", {"reason": "cannot_attend"}),
    "dont_understand": ("callback_requested", {"reason": "patient_does_not_understand"}),
    "wrong_number": ("review_requested", {"reason": "wrong_number"}),
    "already_done": ("review_requested", {"reason": "already_done"}),
    "opt_out": ("review_requested", {"reason": "opt_out"}),
    "question": ("review_requested", {"reason": "triage_uncertain"}),
    "other": ("review_requested", {"reason": "triage_uncertain"}),
}


@pytest.mark.parametrize("intent", INTENTS)
def test_each_intent_has_exactly_its_documented_effect(intent):
    cid = new_case()
    before = state(cid)
    r = reply(cid, f"intent:{intent} please")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["intent"] == intent and body["model_id"] == "fake-model-1" and body["logged"] is True
    effect = EFFECT[intent]
    expected = ["reply_received", "reply_classified"] + ([effect[0]] if effect else [])
    assert kinds_after_creation(cid) == expected and body["applied"] == expected
    events = log(cid)
    assert {e.actor for e in events[1:]} == {TRIAGE_ACTOR}
    if effect:
        assert events[-1].payload["reason"] == effect[1]["reason"]
        assert events[-1].payload["source_event_id"] == str(events[2].event_id)  # traceable to the classification
    assert state(cid) == before  # nothing the triage does moves a case


def test_needs_human_follows_the_rule_in_the_response_and_the_log():
    cid = new_case()
    for intent, human in [("confirm", False), ("reschedule", False), ("question", True), ("opt_out", True), ("other", True)]:
        assert reply(cid, f"intent:{intent}").json()["needs_human"] is human
    classified = [e.payload for e in log(cid) if e.type == "reply_classified"]
    assert [p["needs_human"] for p in classified] == [False, False, True, True, True]
    assert all(set(p) == {"intent", "confidence", "model_id", "needs_human", "text_ref"} for p in classified)


def test_the_text_is_stored_but_never_in_the_event_log():
    cid = new_case()
    secret = "intent:reschedule my colonoscopy because of my chemotherapy ZEBRA-SENTINEL"
    r = reply(cid, secret, channel="sms")
    assert r.status_code == 201
    assert "ZEBRA-SENTINEL" not in json.dumps(r.json())  # not echoed either
    dumped = json.dumps([{"p": e.payload, "t": e.type, "a": e.actor} for e in log(cid)])
    assert "ZEBRA-SENTINEL" not in dumped and "chemotherapy" not in dumped
    received = next(e for e in log(cid) if e.type == "reply_received")
    assert received.payload == {"channel": "sms", "text_ref": r.json()["reply_id"]}
    stored = client.get(f"/cases/{cid}/replies", headers=H("coordinator")).json()
    assert [row["text"] for row in stored] == [secret] and stored[0]["reply_id"] == r.json()["reply_id"]


def test_low_confidence_asks_a_person_instead_of_guessing():
    cid = new_case()
    body = reply(cid, "unsure about this").json()
    assert body["needs_human"] is True
    assert kinds_after_creation(cid) == ["reply_received", "reply_classified", "review_requested"]
    assert log(cid)[-1].payload["reason"] == "triage_uncertain"
    assert replay_model(log(cid), cases.pathway).triage_review_reason == "triage_uncertain"


def test_no_model_means_every_reply_goes_to_a_person():
    triage.set_backend(None)
    cid = new_case()
    for text in ("intent:confirm yes", "intent:reschedule", "intent:opt_out stop"):
        body = reply(cid, text).json()
        assert body["needs_human"] is True and body["model_id"] == "unavailable"
    assert kinds_after_creation(cid).count("callback_requested") == 0
    reasons = [e.payload["reason"] for e in log(cid) if e.type == "review_requested"]
    assert reasons == ["triage_uncertain"] * 3


def test_an_opt_out_never_withdraws_consent_and_never_touches_the_state():
    cid = new_case()
    reply(cid, "intent:opt_out stop contacting me")
    model = replay_model(log(cid), cases.pathway)
    assert model.consent["withdrawn"] is False and model.consent_frozen is False
    assert model.triage_review_reason == "opt_out"
    assert not any(e.type == "consent_updated" for e in log(cid))
    assert state(cid) == "result_received"


def test_a_triage_flag_does_not_hide_a_later_consent_review():
    cid = new_case()
    reply(cid, "intent:opt_out stop")
    post_event(cid, "consent_updated", "coordinator", {"withdrawn": True})
    types = [e.type for e in log(cid)]
    assert types.count("review_requested") == 2  # the triage one and the consent one
    model = replay_model(log(cid), cases.pathway)
    assert model.review_reason == "consent_withdrawn" and model.triage_review_reason == "opt_out"
    assert consent.ensure_review_requested(cases.store, cases.pathway, UUID(cid), uuid4(), log(cid)[-1].at) is None


def test_replies_do_not_count_as_human_activity_for_the_idle_clock():
    cid = new_case()
    before = replay_model(log(cid), cases.pathway).last_human_activity_at
    reply(cid, "intent:confirm yes")
    after = replay_model(log(cid), cases.pathway)
    assert after.last_human_activity_at == before


def test_a_reply_never_closes_or_advances_a_case_in_any_state():
    cid = new_case()
    post_event(cid, "acknowledge", "lab")
    post_event(cid, "accept_ownership", "primary_care_owner")
    post_event(cid, "contact_patient", "primary_care_owner")
    before = state(cid)
    for intent in INTENTS:
        reply(cid, f"intent:{intent}")
    assert state(cid) == before == "patient_contacted"
    assert "close" not in [e.type for e in log(cid)]


# ----------------------------------------------------------------------------------- retries and shapes
def test_a_retry_with_the_same_reply_id_writes_nothing_twice():
    cid = new_case()
    rid = str(uuid4())
    first = reply(cid, "intent:reschedule monday", reply_id=rid)
    n = len(log(cid))
    again = reply(cid, "intent:reschedule monday", reply_id=rid)
    assert again.status_code == 201 and again.json() == first.json() and len(log(cid)) == n
    assert len(client.get(f"/cases/{cid}/replies", headers=H("coordinator")).json()) == 1


def test_a_retry_after_a_crash_between_the_events_heals_the_log(monkeypatch):
    cid = new_case()
    rid = uuid4()
    first = reply(cid, "intent:cannot_attend sorry", reply_id=str(rid))
    # the process died after writing reply_received only: rebuild the log with just that event
    events = log(cid)
    crashed = type(cases.store)()
    crashed.create_case(UUID(cid), cases.pathway.name, events[0], cases.pathway.initial_state)
    crashed.append(events[1], cases.pathway)
    monkeypatch.setattr(cases, "store", crashed)
    assert kinds_after_creation(cid) == ["reply_received"]
    again = reply(cid, "intent:cannot_attend sorry", reply_id=str(rid))
    assert again.status_code == 201 and kinds_after_creation(cid) == ["reply_received", "reply_classified", "callback_requested"]
    assert again.json()["intent"] == first.json()["intent"]


def test_the_same_reply_id_on_another_case_is_refused():
    a, b = new_case(), new_case("Patient/2")
    rid = str(uuid4())
    assert reply(a, "intent:confirm", reply_id=rid).status_code == 201
    assert reply(b, "intent:confirm", reply_id=rid).status_code == 409


def test_unknown_case_and_bad_bodies():
    assert reply(str(uuid4()), "intent:confirm").status_code == 404
    cid = new_case()
    h = H("coordinator")
    assert client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": ""}, headers=h).status_code == 422
    assert client.post(f"/cases/{cid}/replies", json={"channel": "pigeon", "text": "hi"}, headers=h).status_code == 422
    assert client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": "hi", "actor": "x"}, headers=h).status_code == 422
    assert client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": "x" * 4001}, headers=h).status_code == 422
    assert log(cid)[1:] == []


def test_arabic_arabizi_and_mixed_text_round_trip():
    cid = new_case()
    texts = ["تمام بجي بكرة intent:confirm", "ma a2dar ajee intent:cannot_attend", "ok شكرا intent:confirm 👍"]
    for t in texts:
        assert reply(cid, t, language_hint="ar").status_code == 201
    stored = client.get(f"/cases/{cid}/replies", headers=H("supervisor")).json()
    assert [r["text"] for r in stored] == texts and all(r["language_hint"] == "ar" for r in stored)


def test_a_closed_case_stores_the_reply_for_a_person_and_logs_nothing():
    signed = {"evidence_ref": "DiagnosticReport/1", "signed_by": f"Practitioner/{user_id_of('specialist_owner', 'Dr Sara Khan')}"}
    cid = new_case()
    post_event(cid, "acknowledge", "lab")
    post_event(cid, "accept_ownership", "specialist_owner")
    for type_, role, payload in [("contact_patient", "specialist_owner", {}), ("patient_acknowledge", "patient", {"lang": "en"}),
                                 ("book_confirmatory", "specialist_owner", {}), ("complete_confirmatory", "specialist_owner", {}),
                                 ("clinician_review", "specialist_owner", {}), ("close", "specialist_owner", signed)]:
        post_event(cid, type_, role, payload, patient_ref="Patient/1" if role == "patient" else None)
    assert state(cid) == "closed"
    n = len(log(cid))
    r = reply(cid, "intent:opt_out stop")
    assert r.status_code == 201 and r.json()["logged"] is False and r.json()["applied"] == [] and "closed" in r.json()["note"]
    assert len(log(cid)) == n
    stored = client.get(f"/cases/{cid}/replies", headers=H("coordinator")).json()
    assert stored[0]["logged"] is False and stored[0]["needs_human"] is True


# --------------------------------------------------------------------------------------------- policy
def test_only_the_coordinator_may_post_a_reply():
    cid = new_case()
    for role in ROLES:
        r = reply(cid, "intent:confirm", role=role)
        assert r.status_code == (201 if role == "coordinator" else 403), (role, r.status_code)
    assert client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": "x"}).status_code == 401
    sched = {"Authorization": "Bearer " + __import__("app.config", fromlist=["settings"]).settings.scheduler_service_token}
    assert client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": "x"}, headers=sched).status_code == 403


def test_only_coordinator_and_supervisor_may_read_the_stored_text():
    cid = new_case()
    reply(cid, "intent:confirm")
    for role in ROLES:
        r = client.get(f"/cases/{cid}/replies", headers=H(role))
        assert r.status_code == (200 if role in ("coordinator", "supervisor") else 403), role


def test_route_policy_entries_exist_and_event_policy_blocks_the_reply_events():
    assert policy.ROUTE_POLICY[("POST", "/cases/{case_id}/replies")] == frozenset({policy.COORD})
    assert policy.ROUTE_POLICY[("GET", "/cases/{case_id}/replies")] == frozenset({policy.COORD, policy.SUPER})
    cid = new_case()
    payload_for = {"reply_received": {"channel": "sms", "text_ref": str(uuid4())},
                   "reply_classified": {"intent": "confirm", "confidence": 0.9, "model_id": "m", "needs_human": False,
                                        "text_ref": str(uuid4())},
                   "review_requested": {"reason": "opt_out", "source_event_id": str(uuid4())}}
    for kind, payload in payload_for.items():
        assert policy.EVENT_POLICY[kind] == frozenset()
        for role in ROLES:
            r = client.post(f"/cases/{cid}/events", json={"type": kind, "payload": payload}, headers=H(role))
            assert r.status_code == 403, (kind, role)
    assert kinds_after_creation(cid) == []


# --------------------------------------------------------------------------------------- state machine
def _event(cid, kind, actor, payload):
    return Event(UUID(cid), kind, log(cid)[-1].at, actor, payload)


def test_only_the_triage_actor_writes_reply_events_and_it_writes_nothing_else():
    cid = new_case()
    ref = str(uuid4())
    received = {"channel": "sms", "text_ref": ref}
    for actor in ("coordinator:abc", "system:scheduler", "system:consent", "model:template", "watchdog", "system:notifier"):
        with pytest.raises(InvalidTransition):
            cases.store.append(_event(cid, "reply_received", actor, received), cases.pathway)
    for kind, payload in [("close", {"evidence_ref": "Procedure/1", "signed_by": "supervisor:abc"}),
                          ("acknowledge", {}), ("consent_updated", {"withdrawn": True}), ("message_sent", {}),
                          ("escalation_accepted", {}), ("reopen", {"reason": "other"}), ("mark_lost_contact", {"failed_attempts": 3}),
                          ("contact_patient", {}), ("accept_ownership", {})]:
        with pytest.raises(InvalidTransition):
            cases.store.append(_event(cid, kind, TRIAGE_ACTOR, payload), cases.pathway)
    assert kinds_after_creation(cid) == []


def test_review_reasons_are_tied_to_their_actor():
    cid = new_case()
    src = str(uuid4())
    for actor, reason in [(TRIAGE_ACTOR, "consent_withdrawn"), ("system:consent", "opt_out"), ("system:consent", "triage_uncertain"),
                          ("coordinator:abc", "opt_out"), ("system:scheduler", "wrong_number")]:
        with pytest.raises(InvalidTransition):
            cases.store.append(_event(cid, "review_requested", actor, {"reason": reason, "source_event_id": src}), cases.pathway)
    assert kinds_after_creation(cid) == []


def test_triage_may_relay_only_a_callback_the_patient_asked_for_and_traceably():
    cid = new_case()
    src = str(uuid4())
    for payload in [{"reason": "wants_to_speak_to_clinician", "source_event_id": src}, {"reason": "other", "source_event_id": src},
                    {"reason": "reschedule"}, {}]:
        with pytest.raises(InvalidTransition):
            cases.store.append(_event(cid, "callback_requested", TRIAGE_ACTOR, payload), cases.pathway)
    for actor in ("system:scheduler", "model:template", "watchdog"):  # still human only for everyone else
        with pytest.raises(InvalidTransition):
            cases.store.append(_event(cid, "callback_requested", actor, {"reason": "reschedule", "source_event_id": src}), cases.pathway)
    cases.store.append(_event(cid, "callback_requested", TRIAGE_ACTOR, {"reason": "reschedule", "source_event_id": src}), cases.pathway)
    assert kinds_after_creation(cid) == ["callback_requested"]


def test_payload_schemas_refuse_text_and_unknown_keys():
    ref = str(uuid4())
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("reply_received", {"channel": "sms", "text_ref": ref, "text": "hello"})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("reply_received", {"channel": "sms", "text_ref": "hello there, I am Ahmed"})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("reply_classified", {"intent": "confirm", "confidence": 1.5, "model_id": "m", "needs_human": False, "text_ref": ref})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("reply_classified", {"intent": "I will come", "confidence": 0.5, "model_id": "m", "needs_human": False, "text_ref": ref})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("reply_classified", {"intent": "confirm", "confidence": 0.5, "model_id": "m", "needs_human": False,
                                               "text_ref": ref, "reason": "free text"})
    assert set(payloads.allowed_keys("reply_received")) == {"channel", "text_ref"}
    assert set(payloads.allowed_keys("reply_classified")) == {"intent", "confidence", "model_id", "needs_human", "text_ref"}


def test_the_pathway_lists_the_two_events_as_annotations():
    assert {"reply_received", "reply_classified"} <= set(cases.pathway.annotations)
    assert not {"reply_received", "reply_classified"} & set(cases.pathway.human_only_events)
