"""SEC-2 fix 3: owner scope is re-evaluated under the case lock (TOCTOU).

A clinician acts only on cases they own. The route checks that on an early read; between that read and the
write the case can change hands (a handoff accepted, a reassignment). The store now runs an authorisation
guard on the replayed model inside the append's transaction: 403 and no row. `faults.AppendGate` parks the
request between its pre-check and its append, the test hands the case over through the inner store, releases
the request, and asserts it was refused and wrote nothing. Memory and Postgres."""

import os
from uuid import UUID

import pytest

import faults
from api_helpers import actor_of, login_headers, make_client
from app import notify
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.config import settings as app_settings
from app.events import Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases, drafts

HAS_DB = bool(os.environ.get("DATABASE_URL"))
PATHWAY = load_pathway(app_settings.pathway_path)
client = make_client()
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
ALICE, ALICE_ACTOR = "Dr Alice", actor_of("primary_care_owner", "Dr Alice")


@pytest.fixture(params=["memory", pytest.param("postgres", marks=[pytest.mark.integration, pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")])])
def world(request, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    if request.param == "memory":
        inner = InMemoryEventStore()
        monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
        monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
        pool = None
    else:
        from app.db import create_pool, run_migrations
        from app.store_pg import PostgresEventStore, PostgresNotificationLog

        pool = create_pool(os.environ["DATABASE_URL"], max_size=8)
        run_migrations(pool)
        inner = PostgresEventStore(pool)
        monkeypatch.setattr(notify, "log", PostgresNotificationLog(pool))
        monkeypatch.setattr(drafts, "_pg", None)
    monkeypatch.setattr(cases, "store", inner)
    clock.reset()
    yield inner
    clock.reset()
    if pool is not None:
        pool.close()


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post(cid, type_, role, payload=None, name=None, patient_ref=None):
    return client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=H(role, name, patient_ref))


def log(cid):
    return [e.type for e in cases.store.events_for(UUID(cid))]


def case_owned_by_alice(handoff=False):
    cid = client.post("/cases", json={"patient_ref": "Patient/toctou"}, headers=H("coordinator")).json()["case_id"]
    for type_, role, name, extra in [("acknowledge", "lab", None, {}), ("accept_ownership", "primary_care_owner", ALICE, {}),
                                     ("contact_patient", "primary_care_owner", ALICE, {}),
                                     ("patient_acknowledge", "patient", "pat", {})]:
        r = post(cid, type_, role, extra, name, patient_ref="Patient/toctou" if role == "patient" else None)
        assert r.status_code == 201, r.text
    if handoff:
        assert post(cid, "handoff_requested", "primary_care_owner", {"to_provider": "endo-9"}, ALICE).status_code == 201
    return cid


def hand_over_to_specialist(inner, cid):
    """The specialist for endo-9 accepts the pending referral, straight through the inner store: Alice no
    longer owns the case."""
    spec_actor = "specialist_owner:" + "deadbeef0001"
    inner.append(Event(UUID(cid), "handoff_accepted", clock.now(), spec_actor, {}), PATHWAY)
    assert inner.read_model(UUID(cid), PATHWAY).owner == spec_actor


def test_an_event_posted_after_the_case_changed_hands_is_refused_and_leaves_no_row(world):
    cid = case_owned_by_alice(handoff=True)
    gate = faults.AppendGate(world, matches=lambda evs: evs[0].type == "book_confirmatory")
    cases.store = gate
    before = log(cid)
    thread, out = faults.run_parked(lambda: post(cid, "book_confirmatory", "primary_care_owner", {}, ALICE))
    assert gate.arrived.wait(15), "the request never reached the append"  # its pre-check has passed: Alice owned it
    hand_over_to_specialist(world, cid)
    gate.release()
    thread.join(15)
    (r,) = out
    assert r.status_code == 403 and "may only act on cases they own" in r.json()["detail"], r.text
    assert log(cid) == [*before, "handoff_accepted"]  # only the hand-over; Alice's event wrote nothing
    assert world.current_state(UUID(cid)) == "patient_acknowledged"


def test_without_the_hand_over_the_same_parked_request_succeeds(world):
    cid = case_owned_by_alice(handoff=True)
    gate = faults.AppendGate(world, matches=lambda evs: evs[0].type == "book_confirmatory")
    cases.store = gate
    thread, out = faults.run_parked(lambda: post(cid, "book_confirmatory", "primary_care_owner", {}, ALICE))
    assert gate.arrived.wait(15)
    gate.release()
    thread.join(15)
    assert out[0].status_code == 201, out[0].text
    assert log(cid)[-1] == "book_confirmatory"


def test_a_draft_is_refused_when_the_case_changed_hands_after_the_pre_check(world):
    cid = case_owned_by_alice(handoff=True)
    gate = faults.AppendGate(world, matches=lambda evs: evs[0].type == "message_drafted")
    cases.store = gate
    thread, out = faults.run_parked(
        lambda: client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("primary_care_owner", ALICE)))
    assert gate.arrived.wait(15)
    hand_over_to_specialist(world, cid)
    gate.release()
    thread.join(15)
    assert out[0].status_code == 403, out[0].text
    assert "message_drafted" not in log(cid)
    assert drafts.draft_store().list_for_case(UUID(cid)) == []


def test_an_approval_is_refused_when_the_case_changed_hands_after_the_pre_check(world):
    cid = case_owned_by_alice(handoff=True)
    made = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam"))
    assert made.status_code == 201, made.text
    did = made.json()["draft_id"]
    gate = faults.AppendGate(world, matches=lambda evs: evs[0].type == "message_approved")
    cases.store = gate
    thread, out = faults.run_parked(lambda: client.post(f"/drafts/{did}/approve", headers=H("primary_care_owner", ALICE)))
    assert gate.arrived.wait(15)
    hand_over_to_specialist(world, cid)
    gate.release()
    thread.join(15)
    assert out[0].status_code == 403, out[0].text
    assert "message_approved" not in log(cid) and "message_sent" not in log(cid)
    assert drafts.draft_store().get(UUID(did)).approved_at is None  # the draft is still undecided
    assert notify.log.for_case(UUID(cid)) == []


def test_a_rejection_is_refused_when_the_case_changed_hands_after_the_pre_check(world):
    cid = case_owned_by_alice(handoff=True)
    did = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam")).json()["draft_id"]
    gate = faults.AppendGate(world, matches=lambda evs: evs[0].type == "message_rejected")
    cases.store = gate
    thread, out = faults.run_parked(
        lambda: client.post(f"/drafts/{did}/reject", json={"reason": "tone"}, headers=H("primary_care_owner", ALICE)))
    assert gate.arrived.wait(15)
    hand_over_to_specialist(world, cid)
    gate.release()
    thread.join(15)
    assert out[0].status_code == 403, out[0].text
    assert "message_rejected" not in log(cid) and drafts.draft_store().get(UUID(did)).rejected_at is None
