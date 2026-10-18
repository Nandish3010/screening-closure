"""T-A36 (A14, ADR-019): opaque actors, a users table for display names, and a strict payload
schema per event type so free text cannot enter the immutable log."""

import json
from uuid import UUID, uuid4

import pytest

from api_helpers import DRAFT_ID, DRAFTED, actor_of, login_headers, make_client, user_id_of
from app import payloads, policy, users
from app.auth import opaque_user_id
from app.config import settings as app_settings
from app.events import Event
from app.pathway import load_pathway
from app.routers import cases

PATHWAY = load_pathway(app_settings.pathway_path)
client = make_client()
MARKER = "Zaphod Marker"  # a name that must never appear in the log


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def new_case(patient_ref="Patient/1"):
    r = client.post("/cases", json={"patient_ref": patient_ref}, headers=H("coordinator"))
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def post(case_id, type_, headers, payload=None, **extra):
    return client.post(f"/cases/{case_id}/events",
                       json={"type": type_, "payload": payload or {}, **extra}, headers=headers)


def stored(case_id):
    return cases.store.events_for(UUID(case_id))


# ---- opaque actors and the users table -------------------------------------------------------------


def test_the_actor_is_role_and_an_opaque_id_never_the_name():
    cid = new_case()
    r = post(cid, "acknowledge", H("lab", MARKER))
    actor = r.json()["events"][-1]["actor"]
    role, _, uid = actor.partition(":")
    assert role == "lab" and len(uid) == 12 and all(c in "0123456789abcdef" for c in uid)
    assert uid == opaque_user_id("lab", MARKER) == user_id_of("lab", MARKER)
    assert "Zaphod" not in actor and "Marker" not in actor
    # the same person gets the same id; a different role or name gets a different one
    assert opaque_user_id("lab", MARKER) == opaque_user_id("lab", MARKER)
    assert len({opaque_user_id("lab", "a"), opaque_user_id("lab", "b"), opaque_user_id("coordinator", "a")}) == 3


def test_dev_login_writes_the_display_name_to_the_users_table_and_me_resolves_it():
    login = client.post("/auth/dev-login", json={"role": "primary_care_owner", "name": MARKER, "provider": "endo-1"})
    assert login.status_code == 200
    body = login.json()
    uid = opaque_user_id("primary_care_owner", MARKER)
    assert body["user_id"] == uid and body["display_name"] == MARKER
    rec = users.directory.get(uid)
    assert rec.role == "primary_care_owner" and rec.display_name == MARKER and rec.provider == "endo-1"
    me = client.get("/me", headers={"Authorization": f"Bearer {body['token']}"}).json()
    assert me == {"user_id": uid, "role": "primary_care_owner", "display_name": MARKER, "provider": "endo-1"}


def test_a_valid_token_always_has_a_users_row_even_after_the_table_is_wiped():
    h = H("lab", "Wiped Away")
    users.directory._rows.clear()
    client.get("/me", headers=h)
    assert users.directory.get(user_id_of("lab", "Wiped Away")).display_name == "Wiped Away"


def test_the_case_resolves_the_owner_for_the_ui():
    cid = new_case()
    post(cid, "acknowledge", H("lab"))
    r = post(cid, "accept_ownership", H("primary_care_owner", "Dr Resolve"))
    body = r.json()
    me = actor_of("primary_care_owner", "Dr Resolve")
    assert body["read_model"]["owner"] == me  # the actor, unchanged
    assert body["owner"] == {"actor": me, "display_name": "Dr Resolve", "role": "primary_care_owner"}
    assert body["actors"][me] == body["owner"]
    assert body["actors"]["system:intake"] == {"actor": "system:intake", "display_name": "intake", "role": "system"}
    assert client.get(f"/cases/{cid}", headers=H("coordinator")).json()["owner"] == body["owner"]


def test_an_unknown_user_id_still_resolves_to_something_showable():
    assert users.resolve_actor("coordinator:deadbeef0000") == {
        "actor": "coordinator:deadbeef0000", "display_name": "deadbeef0000", "role": "coordinator"}
    assert users.resolve_actor(None) is None


# ---- the schema table -------------------------------------------------------------------------------

UUID_A = "11111111-2222-4333-8444-555555555555"
# A valid sample payload for every event type.
SAMPLES = {
    "case_created": {"patient_ref": "Patient/1", "result_ref": "DiagnosticReport/r1", "contact_valid": {"sms": False}},
    "acknowledge": {}, "accept_ownership": {"provider": "endo-1"},
    "assign_ownership": {"owner": "primary_care_owner:abc123def456", "provider": "Riverside Primary Care", "auto": True},
    "closure_proposed": {"evidence_ref": "Procedure/colonoscopy-1", "auto": True},
    "contact_patient": {"channel": "sms", "draft_id": UUID_A},
    "patient_acknowledge": {"lang": "ar"}, "book_confirmatory": {"appointment_ref": "Appointment/a1"},
    "complete_confirmatory": {"result_ref": "Procedure/p1"}, "clinician_review": {"signed_by": "Practitioner/abc"},
    "close": {"evidence_ref": "DiagnosticReport/r1", "signed_by": "Practitioner/abc"},
    "reopen": {"reason": "new_finding"},
    "mark_overdue": {"state": "acknowledged", "due_at": "2026-01-07T08:00:00+00:00"},
    "escalate": {"rung": "reminder", "state": "acknowledged"},
    "escalation_accepted": {"rung": "reminder"}, "handoff_requested": {"to_provider": "endo-1"},
    "handoff_accepted": {"provider": "endo-1"}, "handoff_rejected": {"reason": "capacity"},
    "reassign": {"owner": "primary_care_owner:abc123def456"}, "consent_updated": {"withdrawn": True},
    "contact_updated": {"contact_valid": True},
    "mark_lost_contact": {"failed_attempts": 3},
    "review_requested": {"reason": "consent_withdrawn", "source_event_id": UUID_A},
    "message_drafted": DRAFTED, "message_approved": {"draft_id": UUID_A},
    "message_rejected": {"draft_id": UUID_A, "reason": "tone"},
    "message_sent": {"notification_id": UUID_A, "channel": "sms", "language": "en", "purpose": "first_contact"},
    "callback_requested": {"reason": "patient_does_not_understand", "lang": "en"},
    "watchdog_flagged": {"reason": "idle", "idle_days": 7},
}


def test_every_event_type_the_system_knows_has_a_schema():
    known = set(PATHWAY.event_types) | set(policy.EVENT_POLICY) | {"case_created"}
    assert known <= set(payloads.SCHEMAS), sorted(known - set(payloads.SCHEMAS))
    assert set(SAMPLES) == set(payloads.SCHEMAS)


@pytest.mark.parametrize("event_type", sorted(SAMPLES))
def test_the_sample_is_accepted_and_an_extra_key_is_not(event_type):
    payloads.validate(event_type, SAMPLES[event_type])
    for extra in ({"patient_name": "Fatima Al Zaabi"}, {"text": "hello"}, {"x": 1}):
        with pytest.raises(payloads.PayloadSchemaError, match="Extra inputs"):
            payloads.validate(event_type, {**SAMPLES[event_type], **extra})


def test_an_event_type_without_a_schema_is_refused():
    with pytest.raises(payloads.PayloadSchemaError, match="no payload schema"):
        payloads.validate("bogus_event", {})


@pytest.mark.parametrize("event_type", ["acknowledge", "accept_ownership", "close", "reopen", "handoff_requested",
                                        "reassign", "consent_updated", "contact_updated", "mark_lost_contact",
                                        "callback_requested", "escalation_accepted", "message_approved"])
def test_human_events_take_a_note_of_at_most_200_characters(event_type):
    ok = {**SAMPLES[event_type], "note": "n" * 200}
    payloads.validate(event_type, ok)
    with pytest.raises(payloads.PayloadSchemaError, match="at most 200"):
        payloads.validate(event_type, {**SAMPLES[event_type], "note": "n" * 201})


@pytest.mark.parametrize("reason", payloads.MESSAGE_REJECT_REASONS)
def test_message_rejected_takes_the_taxonomy_and_nothing_else(reason):
    payloads.validate("message_rejected", {"draft_id": UUID_A, "reason": reason})


@pytest.mark.parametrize("bad", [
    {"draft_id": UUID_A, "reason": "too formal"}, {"draft_id": UUID_A, "reason": "Tone"}, {"draft_id": UUID_A},
    {"reason": "tone"}, {"draft_id": "not-a-uuid", "reason": "tone"},
    {"draft_id": UUID_A, "reason": "tone", "note": "n"},  # no free text, not even a note
])
def test_message_rejected_refuses_free_text_and_malformed_payloads(bad):
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("message_rejected", bad)


@pytest.mark.parametrize("event_type", ["mark_overdue", "review_requested", "message_drafted", "message_sent", "watchdog_flagged"])
def test_automated_events_take_no_note(event_type):
    with pytest.raises(payloads.PayloadSchemaError, match="Extra inputs"):
        payloads.validate(event_type, {**SAMPLES[event_type], "note": "hi"})


@pytest.mark.parametrize("event_type,field,bad", [
    ("reopen", "reason", "Dr Who disagreed with the result"),
    ("reopen", "reason", ""),
    ("callback_requested", "reason", "she is worried about her husband"),
    ("watchdog_flagged", "reason", "nobody looked"),
    ("handoff_rejected", "reason", "Patient Fatima declined"),
    ("review_requested", "reason", "because"),
    ("close", "evidence_ref", "Patient/1"),
    ("close", "evidence_ref", "DiagnosticReport/has space"),
    ("close", "signed_by", "Dr Huda Rahman"),
    ("reassign", "owner", "Dr Kamal"),
    ("handoff_requested", "to_provider", ""),
    ("mark_lost_contact", "failed_attempts", "3"),
    ("consent_updated", "withdrawn", "true"),
    ("contact_updated", "contact_valid", False),  # it says the details are fine; there is no "contact_updated: invalid"
    ("contact_updated", "contact_valid", "yes"),
    ("review_requested", "reason", "opt_out please"),
    ("message_drafted", "prompt_hash", "not a hash"),
    ("message_drafted", "language", "fr"),
    ("mark_overdue", "due_at", "next week"),
])
def test_reasons_are_taxonomies_and_values_have_the_shape_of_ids_not_prose(event_type, field, bad):
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate(event_type, {**SAMPLES[event_type], field: bad})


def test_message_drafted_carries_no_text_only_the_draft_pointer():
    assert set(payloads.allowed_keys("message_drafted")) == {
        "draft_id", "provider", "fallback_used", "prompt_hash", "language"}
    for key in ("text", "text_en", "text_ar", "body"):
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("message_drafted", {**DRAFTED, key: "Dear Fatima, your result ..."})


# ---- over HTTP: 422 and nothing written ---------------------------------------------------------------


def test_a_payload_with_free_text_is_422_and_writes_nothing():
    cid = new_case()
    before = len(stored(cid))
    for type_, who, payload in [
        ("acknowledge", "lab", {"patient_name": "Fatima Al Zaabi"}),
        ("acknowledge", "lab", {"note": "x" * 201}),
        ("accept_ownership", "primary_care_owner", {"owner": "Dr Kamal"}),
    ]:
        r = post(cid, type_, H(who), payload)
        assert r.status_code == 422, (type_, r.text)
    assert len(stored(cid)) == before
    assert post(cid, "acknowledge", H("lab"), {"note": "called twice, no answer"}).status_code == 201


def test_creating_a_case_checks_the_genesis_payload_too():
    for body in ({"patient_ref": "Fatima Al Zaabi"}, {"result_ref": "see the lab"}):
        assert client.post("/cases", json=body, headers=H("coordinator")).status_code == 422, body
    assert client.post("/cases", json={"patient_ref": "Patient/1", "contact_valid": False},
                       headers=H("coordinator")).status_code == 201


def test_scheduler_and_consent_and_notify_events_pass_the_schema_too():
    """Scan stored events: whatever the in-process emitters wrote must validate."""
    import app.notify as notify
    from datetime import timedelta
    from app.clock import clock
    from app.calendar import default_calendar
    from app.sweep import run_sweep

    cid = new_case("Patient/77")
    post(cid, "acknowledge", H("lab"))
    owner = H("primary_care_owner", "Dr Scan")
    post(cid, "accept_ownership", owner)
    post(cid, "consent_updated", H("patient", "pat", "Patient/77"), {"withdrawn": True})
    post(cid, "consent_updated", H("coordinator"), {"withdrawn": False})
    post(cid, "contact_patient", owner)
    draft = "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11"
    cases.store.append(Event(UUID(cid), "message_approved", clock.now(), actor_of("primary_care_owner", "Dr Scan"),
                             {"draft_id": draft}), cases.pathway)
    notify.send(UUID(cid), "sms", "en", "first_contact", "text stays in the notifications log", draft_id=draft)
    run_sweep(cases.store, cases.pathway, default_calendar(), clock.now() + timedelta(days=60), only=[UUID(cid)])
    types = {e.type for e in stored(cid)}
    assert {"mark_overdue", "escalate", "review_requested", "message_sent"} <= types
    for e in stored(cid):
        payloads.validate(e.type, e.payload)  # raises if any emitter let something unlisted through


def test_no_name_ever_reaches_the_log():
    """Walk a case with marker display names through many event types, then scan the whole log."""
    cid = new_case("Patient/5")
    names = {role: f"{MARKER} {role}" for role in ("lab", "primary_care_owner", "specialist_owner", "coordinator", "supervisor")}
    pat = H("patient", f"{MARKER} patient", "Patient/5")
    post(cid, "acknowledge", H("lab", names["lab"]), {"note": "received"})
    post(cid, "accept_ownership", H("primary_care_owner", names["primary_care_owner"]))
    post(cid, "contact_patient", H("primary_care_owner", names["primary_care_owner"]))
    post(cid, "patient_acknowledge", pat, {"lang": "en"})
    post(cid, "callback_requested", pat, {"reason": "patient_does_not_understand"})
    post(cid, "consent_updated", pat, {"channel_sms": False})
    post(cid, "consent_updated", H("coordinator", names["coordinator"]), {"channel_sms": True})
    post(cid, "handoff_requested", H("primary_care_owner", names["primary_care_owner"]), {"to_provider": "endo-1"})
    spec = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": names["specialist_owner"], "provider": "endo-1"}).json()
    r = post(cid, "handoff_accepted", {"Authorization": f"Bearer {spec['token']}"})
    assert r.status_code == 201, r.text
    blob = json.dumps([[e.actor, e.payload, e.type] for e in stored(cid)])
    assert len(stored(cid)) >= 9
    assert "Zaphod" not in blob and "Marker" not in blob
    # ... but the names are resolvable, from the users table
    body = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    assert {v["display_name"] for v in body["actors"].values() if v["role"] != "system"} >= set(names.values()) - {names["supervisor"]}


# ---- SEC-1: reassign.owner is a staff actor ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["primary_care_owner", "specialist_owner", "coordinator", "supervisor", "lab"])
def test_reassign_accepts_a_staff_actor(role):
    payloads.validate("reassign", {"owner": f"{role}:abc123def456"})


@pytest.mark.parametrize(
    "owner",
    ["patient:abc123def456", "system:scheduler", "system:intake", "scheduler:x", "watchdog:x", "model:gpt", "system:consent",
     "doctor:abc", "primary_care_owner", "primary_care_owner:", "primary_care_owner:a b", "PRIMARY_CARE_OWNER:abc",
     "primary_care_owner:abc:def", ""],
)
def test_reassign_refuses_a_patient_an_automated_actor_or_a_malformed_one(owner):
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("reassign", {"owner": owner})


def test_the_reassign_role_list_is_the_user_roles_without_the_patient():
    from app import auth

    allowed = {r for r in auth.USER_ROLES if r != "patient"}
    for role in auth.USER_ROLES:
        ok = True
        try:
            payloads.validate("reassign", {"owner": f"{role}:abc"})
        except payloads.PayloadSchemaError:
            ok = False
        assert ok == (role in allowed), role
    for name in auth.AUTOMATED_ROLE_NAMES:
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("reassign", {"owner": f"{name}:abc"})


@pytest.mark.parametrize("reason", ["consent_withdrawn", "reroute_exhausted", "opt_out", "wrong_number"])
def test_review_requested_takes_the_four_reasons_and_the_machine_vocabulary_is_the_same(reason):
    from app import state_machine

    payloads.validate("review_requested", {"reason": reason, "source_event_id": UUID_A})
    assert tuple(payloads.REVIEW_REASONS) == tuple(state_machine.REVIEW_REASONS)
