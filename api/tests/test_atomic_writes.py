"""SEC-2 fix 8: the draft row and `message_drafted` are one transaction; the notification row and
`message_sent` are one transaction. A fault injected between the two writes leaves neither (Postgres), or
at least never the event without its row (in memory, which has one lock but no rollback)."""

import os
from uuid import UUID, uuid4

import pytest

from api_helpers import login_headers, make_client
from app import notify, outbox
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.events import InMemoryEventStore
from app.routers import cases, drafts

HAS_DB = bool(os.environ.get("DATABASE_URL"))
client = make_client()
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
NAME = "Dr Atomic"


class Injected(RuntimeError):
    """The fault injected between the two writes."""


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


def H(role, name=None):
    return login_headers(client, role, name)


def owned_case():
    cid = client.post("/cases", json={"patient_ref": f"Patient/at-{uuid4().hex[:8]}"}, headers=H("coordinator")).json()["case_id"]
    for type_, role, name in [("acknowledge", "lab", None), ("accept_ownership", "primary_care_owner", NAME)]:
        assert client.post(f"/cases/{cid}/events", json={"type": type_}, headers=H(role, name)).status_code == 201
    return cid


def types(cid):
    return [e.type for e in cases.store.events_for(UUID(cid))]


def test_a_fault_between_the_draft_row_and_the_event_leaves_neither(world, monkeypatch):
    cid = owned_case()
    ds = drafts.draft_store()
    real_add = ds.add

    def add_then_fail(*args, **kwargs):
        real_add(*args, **kwargs)  # the draft row IS written ...
        raise Injected("... and then the process fails before the transaction commits")

    monkeypatch.setattr(ds, "add", add_then_fail)
    with pytest.raises(Injected):
        make_client().post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam"))
    assert "message_drafted" not in types(cid)
    if world == "postgres":  # one transaction: the draft row and its prompt rolled back with the event
        assert ds.list_for_case(UUID(cid)) == []
    # and the same request, once the fault is gone, makes both
    monkeypatch.setattr(ds, "add", real_add)
    r = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam"))
    assert r.status_code == 201
    assert types(cid).count("message_drafted") == 1
    rows = [str(d.draft_id) for d in ds.list_for_case(UUID(cid))]
    assert rows == [r.json()["draft_id"]] if world == "postgres" else r.json()["draft_id"] in rows
    # (in memory, which has one lock but no rollback, the faulted attempt's row stays; it has no event)


def test_a_fault_before_the_draft_row_leaves_no_event(world, monkeypatch):
    cid = owned_case()
    ds = drafts.draft_store()

    def boom(*args, **kwargs):
        raise Injected("the draft insert failed")

    monkeypatch.setattr(ds, "add", boom)
    with pytest.raises(Injected):
        make_client().post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam"))
    assert "message_drafted" not in types(cid)  # it used to stay: an event for a draft that does not exist


def test_a_fault_between_the_notification_row_and_message_sent_leaves_neither(world, monkeypatch):
    cid = owned_case()
    did = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam")).json()["draft_id"]
    real_add = notify.log.add

    def add_then_fail(*args, **kwargs):
        real_add(*args, **kwargs)  # the notification row IS written ...
        raise Injected("... and then the process fails before the transaction commits")

    monkeypatch.setattr(notify.log, "add", add_then_fail)
    r = client.post(f"/drafts/{did}/approve", headers=H("primary_care_owner", NAME))
    assert r.status_code == 200 and r.json()["sent"] is False and r.json()["blocked_reason"] == "pending"
    assert "message_approved" in types(cid) and "message_sent" not in types(cid)
    if world == "postgres":
        assert notify.log.for_case(UUID(cid)) == []  # rolled back with the event
    assert drafts.outbox_store().get(UUID(did)).status == outbox.PENDING
    # the next drain, with the fault gone, writes both, once
    monkeypatch.setattr(notify.log, "add", real_add)
    assert [o.status for o in drafts.drain_outbox()] == [outbox.SENT]
    assert types(cid).count("message_sent") == 1
    rows = notify.log.for_case(UUID(cid))
    assert [n.status for n in rows] == ["sent"] and rows[0].event_id is not None


def test_a_message_sent_refused_by_the_machine_leaves_no_notification_row(world, monkeypatch):
    cid = owned_case()
    did = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam")).json()["draft_id"]
    store = cases.store
    real_append = store.append

    def withdraw_first(event, pathway, **kw):
        if event.type == "message_sent":  # consent withdrawn in the instant between the check and the write
            real_append(type(event)(event.case_id, "consent_updated", event.at, "patient:p", {"withdrawn": True}), pathway)
        return real_append(event, pathway, **kw)

    monkeypatch.setattr(store, "append", withdraw_first)
    r = client.post(f"/drafts/{did}/approve", headers=H("primary_care_owner", NAME))
    assert r.json()["sent"] is False and r.json()["blocked_reason"] == "case_state"
    assert "message_sent" not in types(cid) and notify.log.for_case(UUID(cid)) == []
