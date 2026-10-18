"""SEC-2 fix 7: a patient sees a redacted case.

Staff notes, handoff and reopen reasons, escalation and SLA internals, the watchdog's flags, review flags and
staff display names are not for the patient: the timeline, the case page, the worklist row and the answer to
posting an event all show the visible steps only, and the other party by role. Staff are unchanged."""

import json
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from api_helpers import actor_of, login_headers, make_client, user_id_of
from app import notify, readmodel
from app.ai.draft_store import InMemoryDraftStore
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases, drafts
from app.sweep import run_sweep
from app.watchdog import run_idle_scan

PATHWAY = load_pathway(app_settings.pathway_path)
HAS_DB = bool(os.environ.get("DATABASE_URL"))
client = make_client()
REF = "Patient/pv-1"
NAMES = {"lab": "Lab Tech Zorbax", "pco": "Dr Quillon Marsh", "spec": "Dr Fenwick Ostrander", "coord": "Coordinator Wibble",
         "sup": "Supervisor Hobart"}
NOTE = "STAFFNOTE-do-not-show"
REJECT_NOTE = "REJECTNOTE-capacity-is-full"
PROVIDER = "endo-pv-clinic"


@pytest.fixture(params=["memory", pytest.param("postgres", marks=[pytest.mark.integration, pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")])])
def world(request, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    pool = None
    if request.param == "memory":
        monkeypatch.setattr(cases, "store", InMemoryEventStore())
        monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
        monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    else:
        from app.db import create_pool, run_migrations
        from app.store_pg import PostgresEventStore, PostgresNotificationLog

        pool = create_pool(os.environ["DATABASE_URL"], max_size=6)
        run_migrations(pool)
        monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
        monkeypatch.setattr(notify, "log", PostgresNotificationLog(pool))
        monkeypatch.setattr(drafts, "_pg", None)
        monkeypatch.setattr(drafts, "_pg_outbox", None)
    clock.reset()
    yield request.param
    clock.reset()
    if pool is not None:
        pool.close()


def login(role, name, patient_ref=None, provider=None):
    body = {"role": role, "name": name, **({"patient_ref": patient_ref} if patient_ref else {}), **({"provider": provider} if provider else {})}
    return {"Authorization": f"Bearer {client.post('/auth/dev-login', json=body).json()['token']}"}


def post(cid, type_, headers, payload=None):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == 201, (type_, r.text)
    return r


def busy_case(ref=None):
    """A case with everything a patient must not see in it: notes, a refused handoff with its reason, an
    overdue breach with escalation rungs, a watchdog flag, a drafted-and-approved message, a review flag."""
    ref = ref or f"Patient/pv-{uuid4().hex[:8]}"
    h = {"lab": login("lab", NAMES["lab"]), "pco": login("primary_care_owner", NAMES["pco"]),
         "spec": login("specialist_owner", NAMES["spec"], provider=PROVIDER), "coord": login("coordinator", NAMES["coord"]),
         "sup": login("supervisor", NAMES["sup"]), "pat": login("patient", "Patient Person", ref)}
    cid = client.post("/cases", json={"patient_ref": ref}, headers=h["coord"]).json()["case_id"]
    post(cid, "acknowledge", h["lab"], {"note": NOTE})
    post(cid, "accept_ownership", h["pco"], {"note": NOTE})
    post(cid, "contact_patient", h["pco"], {"channel": "sms", "note": NOTE})
    post(cid, "patient_acknowledge", h["pat"], {"lang": "en"})
    post(cid, "handoff_requested", h["pco"], {"to_provider": PROVIDER, "note": NOTE})
    post(cid, "handoff_rejected", h["spec"], {"reason": "capacity", "note": REJECT_NOTE})
    clock.advance(days=30)
    run_sweep(cases.store, PATHWAY, default_calendar(), clock.now(), only=[UUID(cid)])  # mark_overdue + rungs
    run_idle_scan(cases.store, PATHWAY, clock.now(), 7, only=[UUID(cid)])  # a watchdog flag
    post(cid, "escalation_accepted", h["coord"], {"note": NOTE})
    did = client.post(f"/cases/{cid}/drafts", json={"purpose": "reminder", "channel": "sms", "language": "en"}, headers=h["coord"]).json()["draft_id"]
    assert client.post(f"/drafts/{did}/approve", headers=h["pco"]).json()["sent"] is True  # message_drafted/approved/sent
    post(cid, "callback_requested", h["pat"], {"reason": "wants_to_speak_to_clinician", "lang": "en"})
    post(cid, "consent_updated", h["pat"], {"channel_whatsapp": False})
    return cid, h, ref


FORBIDDEN_TEXT = [
    NOTE, REJECT_NOTE, *NAMES.values(), "Zorbax", "Quillon", "Fenwick", "Wibble", "Hobart", "Patient Person",
    PROVIDER, "capacity", "wants_to_speak_to_clinician", "mark_overdue", "escalat", "sla_", "overdue", "bounce",
    "watchdog", "idle", "supervisor", "handoff", "reassign", "review", "message_drafted", "message_approved",
    "pending_handoff", "due_at", "prompt_hash", "draft_id",
]


def assert_clean(body, staff_ids):
    blob = json.dumps(body, ensure_ascii=False)
    for needle in FORBIDDEN_TEXT:
        assert needle.lower() not in blob.lower(), f"{needle!r} reached the patient: {blob[:400]}"
    for opaque in staff_ids:
        assert opaque not in blob, f"a staff id reached the patient: {opaque}"


def staff_ids():
    return [user_id_of("lab", NAMES["lab"]), user_id_of("primary_care_owner", NAMES["pco"]),
            user_id_of("specialist_owner", NAMES["spec"]), user_id_of("coordinator", NAMES["coord"]),
            user_id_of("supervisor", NAMES["sup"])]


def test_the_patients_timeline_is_the_visible_steps_with_roles_instead_of_names(world):
    cid, h, _ = busy_case()
    r = client.get(f"/cases/{cid}/timeline", headers=h["pat"])
    assert r.status_code == 200
    body = r.json()
    assert_clean(body, staff_ids())
    assert {i["kind"] for i in body["items"]} == {"event"}  # no sla_started / sla_breached / escalation / ...
    types = [i["type"] for i in body["items"]]
    assert types == ["case_created", "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge",
                     "message_sent", "callback_requested", "consent_updated"]
    by_type = {i["type"]: i for i in body["items"]}
    assert by_type["acknowledge"]["actor"] == "lab" and by_type["accept_ownership"]["actor"] == "primary_care_owner"
    assert by_type["patient_acknowledge"]["actor"] == "patient" and by_type["message_sent"]["actor"] == "system"
    assert by_type["contact_patient"]["payload"] == {"channel": "sms"}  # the note is gone
    assert by_type["message_sent"]["payload"] == {"channel": "sms", "language": "en", "purpose": "reminder"}
    assert by_type["callback_requested"]["payload"] == {"lang": "en"}  # their own request, without the reason code
    assert by_type["consent_updated"]["payload"] == {"channel_whatsapp": False}
    assert set(body["actors"]) == {"lab", "primary_care_owner", "patient", "system"}
    assert all(a == {"actor": role, "display_name": role, "role": role} for role, a in body["actors"].items())


def test_staff_still_see_everything(world):
    cid, h, _ = busy_case()
    body = client.get(f"/cases/{cid}/timeline", headers=h["coord"]).json()
    blob = json.dumps(body)
    for must in (NOTE, REJECT_NOTE, "capacity", "mark_overdue", "escalate", "handoff_rejected", "watchdog_flagged", NAMES["pco"]):
        assert must in blob, must
    kinds = {i["kind"] for i in body["items"]}
    assert {"sla_breached", "escalation", "watchdog_flag", "event"} <= kinds
    full = client.get(f"/cases/{cid}", headers=h["coord"]).json()
    assert "risk" in full and "events" in full  # the staff shape is unchanged
    assert full["read_model"]["last_handoff"]["reason"] == "capacity" and full["owner"]["display_name"] == NAMES["pco"]


def test_the_patients_case_page_is_redacted_too(world):
    cid, h, ref = busy_case()
    body = client.get(f"/cases/{cid}", headers=h["pat"]).json()
    assert_clean(body, staff_ids())
    assert body["state"] == "referral_rejected" or body["state"] == "patient_acknowledged"
    assert body["owner"] == {"actor": "primary_care_owner", "display_name": "primary_care_owner", "role": "primary_care_owner"}
    assert body["patient_ref"] == ref
    assert body["risk"] is None and body["risk_score"] is None  # D9: null for a patient, as ever
    for key in ("due_at", "overdue", "escalation_level", "pending_handoff", "provider", "watchdog_flagged"):
        assert key not in body, key
    rm = body["read_model"]
    assert set(rm) == {"state", "consent_frozen", "consent", "last_event_at"}
    assert rm["consent"]["channel_whatsapp"] is False and "updated_by" not in rm["consent"]
    assert [e["type"] for e in body["events"]] == [
        "case_created", "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge", "message_sent",
        "callback_requested", "consent_updated"]


def test_what_a_patient_gets_back_from_posting_an_event_is_redacted(world):
    cid, h, _ = busy_case()
    r = client.post(f"/cases/{cid}/events", json={"type": "consent_updated", "payload": {"channel_sms": False}}, headers=h["pat"])
    assert r.status_code == 201
    assert_clean(r.json(), staff_ids())
    assert r.json()["events"][-1]["type"] == "consent_updated"


def test_the_patients_worklist_row_is_redacted_and_staff_rows_are_not(world):
    cid, h, ref = busy_case()
    rows = client.get("/cases", headers=h["pat"]).json()
    mine = [r for r in rows if r["case_id"] == cid]
    assert len(mine) == 1
    assert_clean(mine, staff_ids())
    assert set(mine[0]) == {"case_id", "pathway", "state", "created_at", "updated_at", "patient_ref", "owner", "consent_frozen", "last_event_at", "risk_score"}
    assert mine[0]["risk_score"] is None
    staff_row = next(r for r in client.get("/cases", headers=h["coord"]).json() if r["case_id"] == cid)
    assert staff_row["owner"]["display_name"] == NAMES["pco"] and staff_row["overdue"] is True and "due_at" in staff_row


def test_a_withdrawal_hides_the_review_flag_from_the_patient_but_keeps_the_state(world):
    cid, h, _ = busy_case()
    r = client.post(f"/cases/{cid}/events", json={"type": "consent_updated", "payload": {"withdrawn": True}}, headers=h["pat"])
    body = r.json()
    assert body["state"] == "consent_withdrawn" and body["read_model"]["consent_frozen"] is True
    assert "review_reason" not in body["read_model"] and "review_requested" not in json.dumps(body)
    assert_clean(body, staff_ids())


def test_another_patient_still_gets_a_404(world):
    cid, _, _ = busy_case()
    other = login("patient", "Someone Else", "Patient/not-this-one")
    assert client.get(f"/cases/{cid}/timeline", headers=other).status_code == 404
    assert client.get(f"/cases/{cid}", headers=other).status_code == 404


def test_the_allow_list_is_what_hides_new_fields(world):
    """A payload key nobody listed is hidden by default, and an event type nobody listed does not appear."""
    from app.events import Event

    e = Event(UUID(int=1), "contact_patient", clock.now(), "primary_care_owner:abc", {"channel": "sms", "note": "x", "new_field": 1})
    assert readmodel.patient_event_json(e)["payload"] == {"channel": "sms"}
    assert readmodel.patient_event_json(Event(UUID(int=1), "brand_new_event", clock.now(), "lab:x", {})) is None
    assert set(readmodel.PATIENT_EVENT_FIELDS) <= set(PATHWAY.event_types) | {"case_created"}
