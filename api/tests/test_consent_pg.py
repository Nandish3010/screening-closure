"""T-A23 and T-A24 against Postgres (`make test-db`): the ledger replays from the stored log,
the freeze refuses outreach and writes nothing, and the review flag is written once."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import settings as app_settings
from app.consent import ensure_review_requested
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.state_machine import InvalidTransition, PayloadError
from app.store_pg import PostgresEventStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
OWNER = "primary_care_owner:dr_a"


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=4)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


def contacted_case(store):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {"patient_ref": "Patient/1"}),
                      PATHWAY.initial_state)
    for i, (type_, actor) in enumerate([("acknowledge", "lab:l"), ("accept_ownership", OWNER),
                                        ("contact_patient", OWNER)], start=1):
        store.append(Event(cid, type_, at(0, i), actor), PATHWAY)
    return cid


def put(store, cid, type_, actor, payload=None, when=at(1)):
    return store.append(Event(cid, type_, when, actor, payload or {}), PATHWAY)


def test_t_a24_the_ledger_is_the_replay_of_an_append_only_history(store, pool):
    cid = contacted_case(store)
    put(store, cid, "consent_updated", "patient:p", {"channel_sms": False}, at(1))
    put(store, cid, "consent_updated", "coordinator:co", {"channel_whatsapp": False, "share_with_specialist": False}, at(2))
    put(store, cid, "consent_updated", "patient:p", {"channel_sms": True}, at(3))
    m = store.read_model(cid, PATHWAY)
    assert m.consent["channel_sms"] is True and m.consent["channel_whatsapp"] is False
    assert m.consent["share_with_specialist"] is False and m.consent["updated_by"] == "patient:p"
    assert [h["changes"] for h in m.consent_history] == [
        {"channel_sms": False}, {"channel_whatsapp": False, "share_with_specialist": False}, {"channel_sms": True}]
    # the history lives in the append-only event log: it cannot be edited or deleted
    with pool.connection() as conn:
        n = conn.execute("SELECT count(*) FROM events WHERE case_id=%s AND type='consent_updated'", (cid,)).fetchone()[0]
        assert n == 3
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM events WHERE case_id=%s AND type='consent_updated'", (cid,))
    with pytest.raises(PayloadError):
        put(store, cid, "consent_updated", "patient:p", {"channel_sms": "no"})


def test_t_a23_withdrawal_freezes_outreach_and_the_flag_is_written_once(store):
    cid = contacted_case(store)
    trigger = uuid4()
    store.append(Event(cid, "consent_updated", at(1), "patient:p", {"withdrawn": True}, trigger), PATHWAY)
    assert store.current_state(cid) == "consent_withdrawn"
    assert ensure_review_requested(store, PATHWAY, cid, trigger, at(1)) is not None
    assert ensure_review_requested(store, PATHWAY, cid, trigger, at(1)) is None
    m = store.read_model(cid, PATHWAY)
    assert m.consent_frozen and m.review_reason == "consent_withdrawn" and m.resume_state == "patient_contacted"
    n = len(store.events_for(cid))
    for kind, actor, payload in [
        ("contact_patient", OWNER, {}), ("message_drafted", OWNER, {}), ("message_approved", OWNER, {}),
        ("message_sent", "system:notifier", {}), ("handoff_requested", OWNER, {"to_provider": "endo-1"}),
    ]:
        with pytest.raises(InvalidTransition, match="consent withdrawn"):
            put(store, cid, kind, actor, payload, at(2))
    assert len(store.events_for(cid)) == n  # nothing written
    # restoring puts the case back where it was
    assert put(store, cid, "consent_updated", "coordinator:co", {"withdrawn": False}, at(3)) == "patient_contacted"
    m = store.read_model(cid, PATHWAY)
    assert not m.consent_frozen and m.review_reason is None
    assert put(store, cid, "message_drafted", OWNER, {}, at(4)) == "patient_contacted"
