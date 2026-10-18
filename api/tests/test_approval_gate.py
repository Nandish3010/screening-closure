"""D5: the approval gate (T-D1, T-D2, T-D13), in-memory stores. The Postgres half (one
transaction, the draft triggers, concurrent approvals) is in test_approval_gate_pg.py.

The rule under test: a patient message reaches the notification mock only through
`POST /drafts/{id}/approve`, by the case's owner (or a supervisor), once per draft, never on a
frozen or closed case, and the whole flow leaves its provenance behind.
"""

import hashlib
import re
from dataclasses import replace
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import app as app_package
from api_helpers import ROLES, actor_of, login_headers, make_client, user_id_of
from app import notify, payloads, policy
from app.ai.draft_store import InMemoryDraftStore
from app.ai.provider import SYSTEM_PROMPT
from app.clock import clock
from app.config import settings as app_settings
from app.events import Event, InMemoryEventStore
from app.main import app
from app.notify import InMemoryNotificationLog
from app.pathway import load_pathway
from app.routers import cases, drafts
from app.state_machine import NOTIFIER_ACTOR, InvalidTransition, replay_model

client = make_client()
PATHWAY = load_pathway(app_settings.pathway_path)
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
OWNER_NAME = "Dr Sara Khan"
OWNER_ACTOR = actor_of("primary_care_owner", OWNER_NAME)  # A14: the actor holds an opaque id


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


@contextmanager
def world():
    """Fresh in-memory event store, draft store, notification log and clock."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cases, "store", InMemoryEventStore())
        mp.setattr(drafts, "store", InMemoryDraftStore())
        mp.setattr(notify, "log", InMemoryNotificationLog())
        mp.delenv("LLM_PROVIDER", raising=False)
        clock.reset()
        try:
            yield
        finally:
            clock.reset()


@pytest.fixture
def fresh():
    with world():
        yield


def post_event(cid, type_, role, payload=None, name=None, patient_ref=None):
    if name is None and role in ("primary_care_owner", "specialist_owner"):
        name = OWNER_NAME
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}},
                    headers=H(role, name, patient_ref))
    assert r.status_code == 201, r.text
    return r.json()


def owned_case(contact_valid=None, role="primary_care_owner"):
    body = {"patient_ref": "Patient/1"}
    if contact_valid is not None:
        body["contact_valid"] = contact_valid
    cid = client.post("/cases", json=body, headers=H("coordinator")).json()["case_id"]
    post_event(cid, "acknowledge", "lab")
    post_event(cid, "accept_ownership", role)
    return cid


def make_draft(cid, **over):
    r = client.post(f"/cases/{cid}/drafts", json={**BODY, **over}, headers=H("coordinator", "Maryam"))
    assert r.status_code == 201, r.text
    return r.json()["draft_id"]


def approve(draft_id, role="primary_care_owner", name=OWNER_NAME):
    return client.post(f"/drafts/{draft_id}/approve", headers=H(role, name))


def reject(draft_id, reason="tone", role="primary_care_owner", name=OWNER_NAME, note=None):
    body = {"reason": reason} if note is None else {"reason": reason, "note": note}
    return client.post(f"/drafts/{draft_id}/reject", json=body, headers=H(role, name))


def log_of(cid):
    return cases.store.events_for(UUID(cid))


def types(cid):
    return [e.type for e in log_of(cid)]


def sent_events(cid):
    return [e for e in log_of(cid) if e.type == "message_sent"]


def drafted_case(**kw):
    cid = owned_case(**kw)
    return cid, make_draft(cid)


# ---------------------------------------------------------------- the happy path


def test_approval_records_the_approver_then_the_notifier_sends(fresh):
    cid, did = drafted_case()
    r = approve(did)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"draft_id", "approved_by", "approved_at", "sent", "blocked_reason", "notification_id"}
    assert out["draft_id"] == did and out["approved_by"] == OWNER_ACTOR
    assert out["sent"] is True and out["blocked_reason"] is None
    # the log: drafted by the model, approved by the human, sent by the notifier, in that order
    tail = log_of(cid)[-3:]
    assert [(e.type, e.actor) for e in tail] == [
        ("message_drafted", "model:template"),
        ("message_approved", OWNER_ACTOR),
        ("message_sent", NOTIFIER_ACTOR),
    ]
    approved, sent = tail[1], tail[2]
    assert approved.payload == {"draft_id": did}
    assert sent.payload["draft_id"] == did and sent.payload["notification_id"] == out["notification_id"]
    assert out["approved_at"] == approved.at.isoformat()
    # the notification row is the mock "delivery"
    (row,) = notify.log.for_case(UUID(cid))
    assert str(row.id) == out["notification_id"] and row.status == "sent" and row.event_id == sent.event_id
    assert (row.channel, row.language, row.purpose) == ("sms", "en", "result_notification")
    assert row.body == drafts.draft_store().get(UUID(did)).text
    # the case itself did not change state
    assert client.get(f"/cases/{cid}", headers=H("coordinator")).json()["state"] == "owner_accepted"


def test_a_supervisor_may_approve_any_case(fresh):
    cid, did = drafted_case()
    r = approve(did, "supervisor", "sam")
    assert r.status_code == 200 and r.json()["approved_by"] == actor_of("supervisor", "sam") and r.json()["sent"] is True
    assert [e.actor for e in log_of(cid) if e.type == "message_approved"] == [actor_of("supervisor", "sam")]


def test_the_specialist_who_owns_the_case_may_approve(fresh):
    cid = owned_case(role="specialist_owner")
    did = make_draft(cid)
    assert approve(did, "specialist_owner", OWNER_NAME).status_code == 200


def test_arabic_and_other_channels_go_through_the_same_gate(fresh):
    cid = owned_case()
    for channel, language in (("whatsapp", "ar"), ("letter", "en"), ("sms", "ar")):
        did = make_draft(cid, channel=channel, language=language, purpose="reminder")
        assert approve(did).json()["sent"] is True
    assert [(n.channel, n.language) for n in notify.log.for_case(UUID(cid))] == [
        ("whatsapp", "ar"), ("letter", "en"), ("sms", "ar")]


# ---------------------------------------------------------------- T-D13: who may approve


def test_t_d13_policy_entries_exclude_every_other_role():
    for path in ("/drafts/{draft_id}/approve", "/drafts/{draft_id}/reject"):
        assert policy.ROUTE_POLICY[("POST", path)] == frozenset(
            {"primary_care_owner", "specialist_owner", "supervisor"})


@pytest.mark.parametrize("role", ["lab", "coordinator", "patient"])
def test_t_d13_roles_that_never_approve_get_403_and_nothing_is_written(fresh, role):
    cid, did = drafted_case()
    before = types(cid)
    r = approve(did, role, "someone") if role != "patient" else client.post(
        f"/drafts/{did}/approve", headers=H("patient", "pat", "Patient/1"))
    assert r.status_code == 403
    r = client.post(f"/drafts/{did}/reject", json={"reason": "tone"},
                    headers=H(role, "someone", "Patient/1" if role == "patient" else None))
    assert r.status_code == 403
    assert types(cid) == before and notify.log.for_case(UUID(cid)) == []
    assert drafts.draft_store().get(UUID(did)).decided is False


def test_t_d13_a_clinician_who_does_not_own_the_case_is_refused(fresh):
    cid, did = drafted_case()
    for role, name in (("primary_care_owner", "Dr Other"), ("specialist_owner", "Dr Spec")):
        assert approve(did, role, name).status_code == 403
        assert reject(did, role=role, name=name).status_code == 403
    assert sent_events(cid) == [] and "message_approved" not in types(cid)


def test_t_d13_a_case_without_an_owner_cannot_be_approved_by_a_clinician(fresh):
    cid = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=H("coordinator")).json()["case_id"]
    did = make_draft(cid)  # a coordinator may draft for an unowned case
    assert approve(did, "primary_care_owner", "anyone").status_code == 403
    assert approve(did, "supervisor", "sam").status_code == 200  # a supervisor may


def test_t_d13_unauthenticated_and_automated_callers_are_refused(fresh):
    cid, did = drafted_case()
    assert client.post(f"/drafts/{did}/approve").status_code == 401
    assert client.post(f"/drafts/{did}/reject", json={"reason": "tone"}).status_code == 401
    sched = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
    assert client.post(f"/drafts/{did}/approve", headers=sched).status_code == 403
    # a model or system actor cannot even obtain a token
    for role in ("model", "system", "watchdog", "scheduler"):
        r = client.post("/auth/dev-login", json={"role": role, "name": "x"})
        assert r.status_code in (400, 401, 403, 422), (role, r.status_code)
    assert sent_events(cid) == []


def test_unknown_and_malformed_draft_ids(fresh):
    assert approve(str(uuid4())).status_code == 404
    assert reject(str(uuid4())).status_code == 404
    assert client.post("/drafts/not-a-uuid/approve", headers=H("supervisor", "sam")).status_code == 422


# ---------------------------------------------------------------- T-D1: exactly once, only here


def test_a_draft_cannot_be_approved_twice(fresh):
    cid, did = drafted_case()
    assert approve(did).status_code == 200
    n = len(log_of(cid))
    for role, name in (("primary_care_owner", OWNER_NAME), ("supervisor", "sam")):
        r = approve(did, role, name)
        assert r.status_code == 409 and "already approved" in r.json()["detail"]
    assert len(log_of(cid)) == n  # no second message_approved, no second message_sent
    assert len(sent_events(cid)) == 1 and len(notify.log.for_case(UUID(cid))) == 1


def test_concurrent_approvals_send_exactly_once(fresh):
    cid, did = drafted_case()
    headers = H("primary_care_owner", OWNER_NAME)
    results, barrier = [], threading.Barrier(8)

    def go():
        c = make_client()
        barrier.wait()
        results.append(c.post(f"/drafts/{did}/approve", headers=headers).status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200] + [409] * 7
    assert types(cid).count("message_approved") == 1 and len(sent_events(cid)) == 1
    assert len(notify.log.for_case(UUID(cid))) == 1


def test_approval_and_event_commit_together_or_not_at_all(fresh):
    """If the event is refused, the draft stays unapproved; if the draft step fails, no event."""
    cid, did = drafted_case()
    store, ds = cases.store, drafts.draft_store()
    event = Event(UUID(cid), "message_approved", clock.now(), OWNER_ACTOR, {"draft_id": did})

    def boom(_cur):
        raise RuntimeError("the draft update failed")

    n = len(log_of(cid))
    with pytest.raises(RuntimeError):
        store.append(event, PATHWAY, within=boom)
    assert len(log_of(cid)) == n  # the event was not stored
    # and the other way round: a refused event leaves the draft alone
    post_event(cid, "consent_updated", "coordinator", {"withdrawn": True})
    n = len(log_of(cid))
    with pytest.raises(InvalidTransition):
        store.append(event, PATHWAY, within=lambda cur: ds.approve(UUID(did), OWNER_ACTOR, clock.now(), cur=cur))
    assert ds.get(UUID(did)).approved_at is None and len(log_of(cid)) == n


def test_message_sent_is_unreachable_through_any_event_route(fresh):
    cid, did = drafted_case()
    every_role = [H(r, "x", "Patient/1" if r == "patient" else None) for r in ROLES]
    every_role.append({"Authorization": f"Bearer {app_settings.scheduler_service_token}"})
    for h in every_role:
        for type_ in ("message_sent", "message_approved"):
            r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": {"draft_id": did}}, headers=h)
            assert r.status_code == 403, (type_, r.status_code)
    assert sent_events(cid) == [] and "message_approved" not in types(cid)
    # and the approval is still possible afterwards, through the gate
    assert approve(did).json()["sent"] is True


def test_only_the_approve_route_leads_to_notify_send():
    """Structural: the sources that can write message_sent. Nothing but the gate calls send."""
    root = Path(app_package.__file__).parent
    allowed_send_callers = {"routers/drafts.py"}  # the gate
    allowed_notifier_refs = {"notify.py", "state_machine.py", "routers/drafts.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root))
        text = path.read_text()
        if re.search(r"\bnotify\.send\(|\bemit_message_sent\(|from app\.notify import[^\n]*\bsend\b", text):
            if rel not in allowed_send_callers | {"notify.py"}:
                offenders.append((rel, "calls notify.send / emit_message_sent"))
        if re.search(r"NOTIFIER_ACTOR|system:notifier", text) and rel not in allowed_notifier_refs:
            offenders.append((rel, "uses the notifier actor"))
    assert offenders == []
    # the events route cannot be talked into it either
    assert policy.EVENT_POLICY["message_sent"] == frozenset()
    assert policy.EVENT_POLICY["message_approved"] == frozenset()


def test_the_only_post_routes_that_touch_drafts_are_the_documented_ones():
    routes = sorted((m.upper(), path) for path, item in app.openapi()["paths"].items() if "draft" in path
                    for m in item)
    assert routes == [
        ("GET", "/cases/{case_id}/drafts"),
        ("POST", "/cases/{case_id}/drafts"),
        ("POST", "/drafts/{draft_id}/approve"),
        ("POST", "/drafts/{draft_id}/reject"),
    ]


# ---------------------------------------------------------------- frozen, closed, blocked


def test_approval_on_a_consent_frozen_case_is_409_and_emits_nothing(fresh):
    cid, did = drafted_case()
    post_event(cid, "consent_updated", "patient", {"withdrawn": True}, patient_ref="Patient/1")
    before = types(cid)
    r = approve(did)
    assert r.status_code == 409 and "consent" in r.json()["detail"]
    assert types(cid) == before  # no message_approved, no message_sent
    assert notify.log.for_case(UUID(cid)) == []
    assert drafts.draft_store().get(UUID(did)).approved_at is None
    # a supervisor is stopped too
    assert approve(did, "supervisor", "sam").status_code == 409
    # once consent is restored the same draft can be approved
    post_event(cid, "consent_updated", "coordinator", {"withdrawn": False})
    assert approve(did).status_code == 200


def test_approval_on_a_closed_case_is_409(fresh):
    cid = owned_case(role="specialist_owner")
    did = make_draft(cid)
    for type_, role, payload in [
        ("contact_patient", "specialist_owner", {}),
        ("patient_acknowledge", "patient", {}),
        ("book_confirmatory", "specialist_owner", {}),
        ("complete_confirmatory", "specialist_owner", {}),
        ("clinician_review", "specialist_owner", {}),
        ("close", "specialist_owner", {"evidence_ref": "DiagnosticReport/1", "signed_by": f"Practitioner/{user_id_of('specialist_owner', OWNER_NAME)}"}),
    ]:
        post_event(cid, type_, role, payload, patient_ref="Patient/1" if role == "patient" else None)
    before = types(cid)
    r = approve(did, "specialist_owner")
    assert r.status_code == 409 and "closed" in r.json()["detail"]
    assert types(cid) == before and notify.log.for_case(UUID(cid)) == []


def test_a_blocked_channel_approves_but_sends_nothing(fresh):
    cid = owned_case()
    post_event(cid, "consent_updated", "patient", {"channel_sms": False}, patient_ref="Patient/1")
    did = make_draft(cid)  # sms
    r = approve(did)
    assert r.status_code == 200
    out = r.json()
    assert out["sent"] is False and out["blocked_reason"] == "consent"
    assert types(cid).count("message_approved") == 1 and sent_events(cid) == []
    (row,) = notify.log.for_case(UUID(cid))
    assert str(row.id) == out["notification_id"] and row.status == "blocked_consent" and row.event_id is None
    assert drafts.draft_store().get(UUID(did)).approved_by == OWNER_ACTOR
    # another channel on the same case still goes out
    assert approve(make_draft(cid, channel="whatsapp")).json()["sent"] is True
    assert len(sent_events(cid)) == 1


def test_an_invalid_contact_blocks_the_send(fresh):
    cid = owned_case(contact_valid={"sms": False})
    out = approve(make_draft(cid)).json()
    assert out["sent"] is False and out["blocked_reason"] == "contact" and out["notification_id"]
    assert sent_events(cid) == []
    assert notify.log.for_case(UUID(cid))[0].status == "blocked_contact"
    cid2 = owned_case(contact_valid=False)
    assert approve(make_draft(cid2)).json()["blocked_reason"] == "contact"


# ---------------------------------------------------------------- reject


def test_reject_records_the_decision_and_appends_message_rejected(fresh):
    """A16: the rejection is on the draft row AND on the case log, as the rejecting human."""
    cid, did = drafted_case()
    before = types(cid)
    r = reject(did, "tone")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["draft_id"] == did and out["rejected_by"] == OWNER_ACTOR
    assert out["reason"] == "tone" and out["note"] is None and out["rejected_at"]
    assert types(cid) == before + ["message_rejected"]
    ev = log_of(cid)[-1]
    assert ev.actor == OWNER_ACTOR and ev.payload == {"draft_id": did, "reason": "tone"}
    assert ev.at.isoformat() == out["rejected_at"]
    row = drafts.draft_store().get(UUID(did))
    assert row.rejected_by == OWNER_ACTOR and row.rejection_reason == "tone"
    assert row.approved_at is None
    listed = client.get(f"/cases/{cid}/drafts", headers=H("coordinator")).json()[0]
    assert listed["rejected_by"] == OWNER_ACTOR and listed["rejection_reason"] == "tone"
    assert listed["rejected_at"] == out["rejected_at"] and listed["approved_at"] is None
    # it is on the timeline too
    tl = client.get(f"/cases/{cid}/timeline", headers=H("coordinator")).json()["items"]
    assert [i["payload"] for i in tl if i["kind"] == "event" and i["type"] == "message_rejected"] == [ev.payload]


@pytest.mark.parametrize("reason", payloads.MESSAGE_REJECT_REASONS[:-1])
def test_every_taxonomy_reason_is_accepted(fresh, reason):
    cid, did = drafted_case()
    assert reject(did, reason).status_code == 200
    assert log_of(cid)[-1].payload == {"draft_id": did, "reason": reason}


def test_other_with_note_keeps_the_note_on_the_draft_and_out_of_the_log(fresh):
    cid, did = drafted_case()
    r = reject(did, "other_with_note", note="  patient prefers a call  ")
    assert r.status_code == 200, r.text
    assert r.json()["reason"] == "other_with_note" and r.json()["note"] == "patient prefers a call"
    assert log_of(cid)[-1].payload == {"draft_id": did, "reason": "other_with_note"}  # no free text in the log
    assert drafts.draft_store().get(UUID(did)).rejection_reason == "other_with_note: patient prefers a call"


def test_a_rejected_draft_cannot_be_approved_and_an_approved_one_cannot_be_rejected(fresh):
    cid = owned_case()
    rejected, approved = make_draft(cid), make_draft(cid)
    assert reject(rejected).status_code == 200
    n = len(log_of(cid))
    r = approve(rejected)
    assert r.status_code == 409 and "rejected" in r.json()["detail"]
    assert reject(rejected).status_code == 409  # one decision only
    assert approve(approved).status_code == 200
    r = reject(approved)
    assert r.status_code == 409 and "approved" in r.json()["detail"]
    assert len(sent_events(cid)) == 1 and len(log_of(cid)) == n + 2  # approved + sent only


@pytest.mark.parametrize("body", [
    {}, {"reason": ""}, {"reason": "   "}, {"reason": "too formal for this patient"}, {"reason": "Tone"},
    {"reason": "tone", "rejected_by": "someone"}, {"reason": 5},
    {"reason": "other_with_note"}, {"reason": "other_with_note", "note": "  "},
    {"reason": "other_with_note", "note": "x" * 201}, {"reason": "tone", "note": "why not"},
])
def test_reject_needs_a_taxonomy_reason(fresh, body):
    cid, did = drafted_case()
    r = client.post(f"/drafts/{did}/reject", json=body, headers=H("primary_care_owner", OWNER_NAME))
    assert r.status_code == 422
    assert drafts.draft_store().get(UUID(did)).decided is False
    assert "message_rejected" not in types(cid)


def test_a_draft_may_be_rejected_on_a_frozen_case(fresh):
    cid, did = drafted_case()
    post_event(cid, "consent_updated", "patient", {"withdrawn": True}, patient_ref="Patient/1")
    assert reject(did).status_code == 200  # rejecting can only stop outreach
    assert types(cid)[-1] == "message_rejected"


def test_a_draft_on_a_closed_case_cannot_be_rejected_and_nothing_is_written(fresh):
    cid, did = drafted_case()
    n = len(log_of(cid))
    real = cases.store.read_model
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cases.store, "read_model", lambda c, pw: replace(real(c, pw), state=pw.terminal_state))
        r = reject(did)
    assert r.status_code == 409 and "closed" in r.json()["detail"]
    assert len(log_of(cid)) == n and drafts.draft_store().get(UUID(did)).decided is False


def test_message_rejected_has_no_poster_and_is_human_only(fresh):
    """A16: nobody may post it through the events route; the state machine refuses automated actors."""
    assert policy.EVENT_POLICY["message_rejected"] == frozenset()
    cid, did = drafted_case()
    for role in ROLES:
        r = client.post(f"/cases/{cid}/events", headers=H(role, OWNER_NAME if "owner" in role else None, "Patient/1" if role == "patient" else None),
                        json={"type": "message_rejected", "payload": {"draft_id": did, "reason": "tone"}})
        assert r.status_code in (403, 404), (role, r.status_code)
    assert "message_rejected" in PATHWAY.human_only_events
    ev = Event(UUID(cid), "message_rejected", clock.now(), "system:notifier", {"draft_id": did, "reason": "tone"})
    with pytest.raises(InvalidTransition):
        replay_model([*log_of(cid), ev], PATHWAY)
    model_actor = Event(UUID(cid), "message_rejected", clock.now(), "model:template", {"draft_id": did, "reason": "tone"})
    with pytest.raises(InvalidTransition):
        replay_model([*log_of(cid), model_actor], PATHWAY)


# ---------------------------------------------------------------- provenance


def test_the_flow_leaves_the_provenance_intact(fresh):
    cid, did = drafted_case()
    approve(did)
    d = drafts.draft_store().get(UUID(did))
    prompt = drafts.draft_store().get_prompt(d.prompt_hash)
    # the stored prompt still hashes to the recorded hash, and is the system prompt we ship
    assert hashlib.sha256((prompt.system_prompt + "\n---\n" + prompt.prompt_text).encode()).hexdigest() == d.prompt_hash
    assert prompt.system_prompt == SYSTEM_PROMPT
    # who: requester, approver; when: sim time; what: draft text, provider, hash
    assert d.created_by == actor_of("coordinator", "Maryam") and d.approved_by == OWNER_ACTOR
    assert d.approved_at is not None and d.approved_at >= d.created_at
    events = log_of(cid)
    drafted = next(e for e in events if e.type == "message_drafted")
    approved = next(e for e in events if e.type == "message_approved")
    sent = next(e for e in events if e.type == "message_sent")
    assert drafted.payload["prompt_hash"] == d.prompt_hash and drafted.payload["draft_id"] == did
    assert drafted.actor == f"model:{d.provider}"
    assert approved.actor == d.approved_by and approved.at == d.approved_at
    assert sent.payload["draft_id"] == did and sent.actor == NOTIFIER_ACTOR
    assert drafted.seq < approved.seq < sent.seq
    # the listing exposes the same approver and time
    listed = client.get(f"/cases/{cid}/drafts", headers=H("coordinator")).json()[0]
    assert listed["approved_by"] == OWNER_ACTOR and listed["approved_at"] == d.approved_at.isoformat()
    assert listed["prompt_hash"] == d.prompt_hash


# ---------------------------------------------------------------- T-D2: model actors


MODEL_ACTORS = ["model:template", "model:azure_openai", "model:jais", "model:drafter", "model:"]


def test_t_d2_a_model_actor_can_only_produce_message_drafted():
    from app.state_machine import CaseModel

    events = set(PATHWAY.event_types) | {"case_created", "made_up_event", "message_sent", "message_approved"}
    states = list(PATHWAY.open_states) + [PATHWAY.terminal_state]
    accepted = []
    for actor in MODEL_ACTORS:
        for state in states:
            for kind in sorted(events):
                for payload in ({}, {"withdrawn": True}, {"state": state}, {"reason": "x", "to_provider": "p"}):
                    model = CaseModel(state=state, owner=OWNER_ACTOR)
                    try:
                        from app.state_machine import step

                        step(model, Event(uuid4(), kind, clock.now(), actor, payload), PATHWAY)
                    except InvalidTransition:
                        continue
                    accepted.append((actor, state, kind))
    assert {kind for _a, _s, kind in accepted} <= {"message_drafted"}
    assert {s for _a, s, _k in accepted} <= set(PATHWAY.open_states)  # and never on a closed case


def test_t_d2_model_events_are_refused_by_both_stores(fresh):
    cid = owned_case()
    for kind in sorted(set(PATHWAY.event_types) - {"message_drafted"}):
        with pytest.raises(InvalidTransition):
            cases.store.append(Event(UUID(cid), kind, clock.now(), "model:drafter", {"state": "owner_accepted"}), PATHWAY)
    assert types(cid) == ["case_created", "acknowledge", "accept_ownership"]
    cases.store.append(Event(UUID(cid), "message_drafted", clock.now(), "model:drafter", {}), PATHWAY)
    assert types(cid)[-1] == "message_drafted"


def test_t_d2_the_draft_route_creates_nothing_but_a_message_drafted_event(fresh):
    cid = owned_case()
    before = types(cid)
    make_draft(cid)
    assert types(cid) == before + ["message_drafted"]
    assert log_of(cid)[-1].actor.startswith("model:")


# ---------------------------------------------------------------- T-D1 as a property

ACTION_ROLES = ["lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor", "other_pco"]
POSTABLE = sorted(set(PATHWAY.event_types) | {"message_sent", "message_approved", "bogus"})

actions = st.one_of(
    st.tuples(st.just("draft"), st.sampled_from(["sms", "whatsapp", "letter"]),
              st.sampled_from(["en", "ar"]), st.sampled_from(["coordinator", "primary_care_owner"])),
    st.tuples(st.just("approve"), st.integers(0, 3), st.sampled_from(ACTION_ROLES)),
    st.tuples(st.just("reject"), st.integers(0, 3), st.sampled_from(ACTION_ROLES)),
    st.tuples(st.just("event"), st.sampled_from(POSTABLE), st.sampled_from(ACTION_ROLES)),
    st.tuples(st.just("consent"), st.sampled_from([
        {"withdrawn": True}, {"withdrawn": False}, {"channel_sms": False}, {"channel_sms": True},
        {"channel_whatsapp": False}, {"channel_letter": False}])),
    st.tuples(st.just("tick"), st.integers(1, 72)),
)


def as_role(role):
    if role == "other_pco":
        return H("primary_care_owner", "Dr Other")
    if role == "patient":
        return H("patient", "pat", "Patient/1")
    name = OWNER_NAME if role in ("primary_care_owner", "specialist_owner") else None
    return H(role, name)


def check_gate_invariant(cid):
    events = log_of(cid)
    ds = drafts.draft_store()
    approved_ids = [e.payload["draft_id"] for e in events if e.type == "message_approved"]
    sent = [e for e in events if e.type == "message_sent"]
    assert len(approved_ids) == len(set(approved_ids)), "a draft was approved twice"
    assert len({e.payload["draft_id"] for e in sent}) == len(sent), "a draft was sent twice"
    for i, e in enumerate(events):
        if e.type == "message_approved":
            d = ds.get(UUID(e.payload["draft_id"]))
            assert d is not None and d.case_id == UUID(cid)
            assert d.approved_by == e.actor and d.approved_at == e.at
            assert e.actor.split(":", 1)[0] in ("primary_care_owner", "specialist_owner", "supervisor")
        if e.type == "message_sent":
            assert e.actor == NOTIFIER_ACTOR
            prior = [a for a in events[:i] if a.type == "message_approved"
                     and a.payload["draft_id"] == e.payload["draft_id"]]
            assert len(prior) == 1, "message_sent without a prior approval of that draft"
            model = replay_model(events[:i], PATHWAY)
            assert not model.consent_frozen
            assert model.consent[f"channel_{e.payload['channel']}"] is True
    for d in ds.list_for_case(UUID(cid)):
        if d.rejected_at is not None:
            assert d.approved_at is None and str(d.draft_id) not in approved_ids
            assert str(d.draft_id) not in {e.payload["draft_id"] for e in sent}
        if d.approved_at is not None:
            assert approved_ids.count(str(d.draft_id)) == 1
    rows = notify.log.for_case(UUID(cid))
    assert [r.event_id for r in rows if r.status == "sent"] == [e.event_id for e in sent]
    assert all(r.event_id is None for r in rows if r.status != "sent")


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(st.lists(actions, min_size=1, max_size=14))
def test_t_d1_no_message_is_sent_without_a_prior_human_approval(steps):
    with world():
        cid = owned_case()
        made: list[str] = []
        for step_ in steps:
            kind = step_[0]
            if kind == "draft":
                _k, channel, language, role = step_
                r = client.post(f"/cases/{cid}/drafts", json={"purpose": "reminder", "channel": channel, "language": language},
                                headers=as_role(role))
                if r.status_code == 201:
                    made.append(r.json()["draft_id"])
            elif kind in ("approve", "reject") and made:
                _k, idx, role = step_
                did = made[idx % len(made)]
                if kind == "approve":
                    client.post(f"/drafts/{did}/approve", headers=as_role(role))
                else:
                    client.post(f"/drafts/{did}/reject", json={"reason": "not_needed"}, headers=as_role(role))
            elif kind == "event":
                _k, type_, role = step_
                client.post(f"/cases/{cid}/events", json={"type": type_, "payload": {"draft_id": made[0] if made else "x"}},
                            headers=as_role(role))
            elif kind == "consent":
                client.post(f"/cases/{cid}/events", json={"type": "consent_updated", "payload": step_[1]},
                            headers=as_role("patient"))
            elif kind == "tick":
                client.post("/clock/advance", json={"hours": step_[1]}, headers=H("supervisor", "sam"))
            check_gate_invariant(cid)


def test_the_invariant_checker_catches_a_send_that_skipped_the_gate(fresh):
    """Guards the property test against passing vacuously: forge the violation and see it fail."""
    cid, did = drafted_case()
    check_gate_invariant(cid)
    # SEC-2 / ADR-030: the state machine itself now refuses a send that skipped the approval ...
    with pytest.raises(InvalidTransition, match="not an approved, unsent draft"):
        notify.send(UUID(cid), "sms", "en", "reminder", "forged", draft_id=did)  # bypasses the route
    # ... so to prove the checker is not vacuous, forge the violation into the log behind the machine's back
    case = cases.store._cases[UUID(cid)]
    case.events.append(Event(UUID(cid), "message_sent", clock.now(), NOTIFIER_ACTOR,
                             {"notification_id": str(uuid4()), "channel": "sms", "language": "en",
                              "purpose": "reminder", "draft_id": did}, seq=len(case.events) + 1))
    with pytest.raises(AssertionError, match="without a prior approval"):
        check_gate_invariant(cid)
    cid2, did2 = drafted_case()
    approve(did2)
    check_gate_invariant(cid2)  # and the honest path passes
