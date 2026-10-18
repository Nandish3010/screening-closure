"""T-A25 (and the notification half of T-A23): the notification mock writes a log row per message,
honours consent and the contact flag, and never touches the network."""

import ast
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from api_helpers import APPROVED, DRAFT_ID, DRAFTED, login_headers, make_client
from app import notify
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, CaseNotFound, Event, InMemoryEventStore
from app.notify import InMemoryNotificationLog, contact_blocked, send
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import InvalidTransition

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
OWNER = "primary_care_owner:dr_a"


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a connection or resolve a name fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("the notification mock tried to use the network")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def make_case(store, genesis=None, upto="patient_contacted"):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", genesis or {"patient_ref": "Patient/1"}),
                      PATHWAY.initial_state)
    steps = [("acknowledge", "lab:l"), ("accept_ownership", OWNER), ("contact_patient", OWNER)]
    for i, (type_, actor) in enumerate(steps, start=1):
        store.append(Event(cid, type_, at(0, i), actor), PATHWAY)
    return cid


def approve_draft(store, cid, draft_id, when):
    """A human approved `draft_id` (ADR-030: the state machine accepts a message_sent only for such a draft).
    On a case with consent withdrawn the approval is itself refused; the send then reports blocked_consent."""
    try:
        store.append(Event(cid, "message_approved", when, OWNER, {"draft_id": draft_id}), PATHWAY)
    except InvalidTransition:
        pass


def mock_send(store, cid, log, channel="sms", **kw):
    when = kw.pop("at", at(1))
    draft_id = kw.pop("draft_id", None) or str(uuid4())
    if kw.pop("approved", True):
        approve_draft(store, cid, draft_id, when)
    return send(cid, channel, kw.pop("language", "en"), kw.pop("purpose", "result_reminder"),
                kw.pop("body", "Please call the clinic."), store=store, pathway=PATHWAY,
                notification_log=log, at=when, draft_id=draft_id, **kw)


# ---- sending ---------------------------------------------------------------------------------------


def test_a_message_is_logged_and_message_sent_is_written(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    n = mock_send(store, cid, log, "whatsapp", language="ar", purpose="first_contact", draft_id=DRAFT_ID)
    assert (n.status, n.reason, n.channel, n.language, n.purpose) == ("sent", None, "whatsapp", "ar", "first_contact")
    assert log.for_case(cid) == [n]
    [sent] = [e for e in store.events_for(cid) if e.type == "message_sent"]
    assert n.event_id == sent.event_id and sent.actor == "system:notifier"
    assert sent.payload == {"notification_id": str(n.id), "channel": "whatsapp", "language": "ar",
                            "purpose": "first_contact", "draft_id": DRAFT_ID}
    assert n.created_at == at(1) and n.body == "Please call the clinic."
    assert store.current_state(cid) == "patient_contacted"  # a message changes no state


def test_one_row_per_message_in_order(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    rows = [mock_send(store, cid, log, ch, body=f"m{i}") for i, ch in enumerate(["sms", "phone", "letter", "sms"])]
    assert [n.body for n in log.for_case(cid)] == ["m0", "m1", "m2", "m3"]
    assert len({n.id for n in rows}) == 4 and len({n.event_id for n in rows}) == 4
    assert [e.type for e in store.events_for(cid)].count("message_sent") == 4
    assert log.for_case(uuid4()) == []


def test_a_channel_the_patient_switched_off_is_blocked_and_logged_without_a_message_sent(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    store.append(Event(cid, "consent_updated", at(1), "patient:p", {"channel_whatsapp": False}), PATHWAY)
    n = mock_send(store, cid, log, "whatsapp")
    assert n.status == "blocked_consent" and "whatsapp" in n.reason and n.event_id is None
    assert [e.type for e in store.events_for(cid)].count("message_sent") == 0
    assert mock_send(store, cid, log, "sms").status == "sent"  # other channels are unaffected
    assert [x.status for x in log.for_case(cid)] == ["blocked_consent", "sent"]


def test_a_withdrawn_case_sends_nothing_on_any_channel(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    mock_send(store, cid, log, "sms")  # before the withdrawal
    store.append(Event(cid, "consent_updated", at(2), "patient:p", {"withdrawn": True}), PATHWAY)
    results = [mock_send(store, cid, log, ch, at=at(3)) for ch in ("sms", "whatsapp", "phone", "letter")]
    assert {r.status for r in results} == {"blocked_consent"} and all(r.reason == "consent withdrawn" for r in results)
    outbound = [n for n in log.for_case(cid) if n.status in ("sent", "queued")]
    assert len(outbound) == 1 and outbound[0].created_at < at(2)  # none after the withdrawal event
    after = [e for e in store.events_for(cid) if e.type == "message_sent" and e.at > at(2)]
    assert after == []
    # the engine itself refuses a forged message_sent too
    with pytest.raises(InvalidTransition, match="consent withdrawn"):
        store.append(Event(cid, "message_sent", at(3), "system:notifier"), PATHWAY)


def test_consent_is_read_back_after_restoring(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    store.append(Event(cid, "consent_updated", at(2), "patient:p", {"withdrawn": True}), PATHWAY)
    assert mock_send(store, cid, log, at=at(3)).status == "blocked_consent"
    store.append(Event(cid, "consent_updated", at(4), "coordinator:co", {"withdrawn": False}), PATHWAY)
    assert mock_send(store, cid, log, at=at(5)).status == "sent"


@pytest.mark.parametrize(
    "flag,channel,status",
    [(False, "sms", "blocked_contact"), (False, "letter", "blocked_contact"),
     ({"sms": False}, "sms", "blocked_contact"), ({"sms": False}, "whatsapp", "sent"),
     ({"sms": True}, "sms", "sent"), (True, "sms", "sent"), (None, "sms", "sent")],
)
def test_contact_validity_flag_in_the_genesis_payload(flag, channel, status, no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store, {"patient_ref": "Patient/1", "contact_valid": flag})
    n = mock_send(store, cid, log, channel)
    assert n.status == status
    assert (n.event_id is None) == (status != "sent")
    if status == "blocked_contact":
        assert "invalid" in n.reason and [e.type for e in store.events_for(cid)].count("message_sent") == 0


def test_consent_is_checked_before_the_contact_flag(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store, {"contact_valid": False})
    store.append(Event(cid, "consent_updated", at(1), "patient:p", {"withdrawn": True}), PATHWAY)
    assert mock_send(store, cid, log).status == "blocked_consent"


def test_contact_blocked_helper():
    assert contact_blocked({}, "sms") is None
    assert contact_blocked({"contact_valid": None}, "sms") is None
    assert contact_blocked({"contact_valid": False}, "phone")
    assert contact_blocked({"contact_valid": {"phone": False}}, "phone")
    assert contact_blocked({"contact_valid": {"phone": False}}, "sms") is None


def test_bad_input_and_unknown_case(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    for kwargs in ({"channel": "fax"}, {"language": "fr"}, {"purpose": ""}, {"body": ""}):
        args = {"channel": "sms", "language": "en", "purpose": "p", "body": "b", **kwargs}
        with pytest.raises(ValueError):
            send(cid, args["channel"], args["language"], args["purpose"], args["body"],
                 store=store, pathway=PATHWAY, notification_log=log, at=at(1))
    with pytest.raises(CaseNotFound):
        mock_send(store, uuid4(), log)
    assert log.for_case(cid) == []


def test_a_closed_case_cannot_be_messaged_and_logs_nothing(no_network):
    store, log = InMemoryEventStore(), InMemoryNotificationLog()
    cid = make_case(store)
    for i, (type_, actor, payload) in enumerate([
        ("patient_acknowledge", "patient:p", {}), ("book_confirmatory", OWNER, {}),
        ("complete_confirmatory", "specialist_owner:s", {}), ("clinician_review", OWNER, {}),
        ("close", OWNER, {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}),
    ], start=2):
        store.append(Event(cid, type_, at(0, i * 3), actor, payload), PATHWAY)
    assert store.current_state(cid) == "closed"
    with pytest.raises(InvalidTransition):
        mock_send(store, cid, log)
    assert log.for_case(cid) == []


# ---- nothing leaves the process --------------------------------------------------------------------


def test_the_module_imports_nothing_that_can_open_a_connection():
    tree = ast.parse(Path(notify.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "smtplib",
              "ftplib", "telnetlib", "websockets", "twilio", "boto3", "subprocess", "asyncio"}
    assert not imported & banned, imported & banned


def test_message_sent_cannot_be_written_by_anyone_else():
    store = InMemoryEventStore()
    cid = make_case(store)
    for actor in (OWNER, "supervisor:su", "coordinator:co", "system:scheduler", "model:drafter", "watchdog",
                  "system:consent", "clinician:dr_a"):
        with pytest.raises(InvalidTransition, match="system:notifier|may only emit"):
            store.append(Event(cid, "message_sent", at(1), actor), PATHWAY)
    assert [e.type for e in store.events_for(cid)].count("message_sent") == 0


# ---- the route and the log over HTTP ----------------------------------------------------------------

client = make_client()
LAB = login_headers(client, "lab", "lab one")
COORD = login_headers(client, "coordinator", "bea")
SUP = login_headers(client, "supervisor", "sam")
PATIENT = login_headers(client, "patient", "pat", "Patient/1")
PCO = login_headers(client, "primary_care_owner", "dr a")
PCO_B = login_headers(client, "primary_care_owner", "dr b")
SPEC = login_headers(client, "specialist_owner", "dr s")


@pytest.fixture
def http_case(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(notify, "log", InMemoryNotificationLog())
    clock.reset()
    cid = client.post("/cases", json={"patient_ref": "Patient/1", "contact_valid": {"letter": False}},
                      headers=COORD).json()["case_id"]
    for type_, h in [("acknowledge", LAB), ("accept_ownership", PCO), ("contact_patient", PCO)]:
        assert client.post(f"/cases/{cid}/events", json={"type": type_}, headers=h).status_code == 201
    yield cid
    clock.reset()


def send_approved(cid, channel, language, purpose, body, **kw):
    """`send` for a draft a human approved first (the state machine accepts no other, ADR-030)."""
    draft = str(uuid4())
    try:
        cases.store.append(Event(UUID(cid), "message_approved", kw.get("at") or clock.now(), OWNER, {"draft_id": draft}), PATHWAY)
    except InvalidTransition:
        pass  # consent is withdrawn: the approval is refused too, and the send reports blocked_consent
    return send(cid, channel, language, purpose, body, draft_id=draft, **kw)


def test_the_notifications_route_lists_the_log(http_case, no_network):
    cid = http_case
    send_approved(cid, "sms", "en", "reminder", "Hello")
    send_approved(cid, "letter", "ar", "reminder", "مرحبا")
    r = client.get(f"/cases/{cid}/notifications", headers=COORD)
    assert r.status_code == 200
    rows = r.json()
    assert [(x["channel"], x["status"]) for x in rows] == [("sms", "sent"), ("letter", "blocked_contact")]
    assert set(rows[0]) == {"id", "case_id", "channel", "language", "purpose", "body", "status", "reason", "event_id", "created_at"}
    assert rows[1]["body"] == "مرحبا" and rows[1]["event_id"] is None
    # the event it points at is in the timeline
    events = client.get(f"/cases/{cid}", headers=COORD).json()["events"]
    assert rows[0]["event_id"] in [e["event_id"] for e in events if e["type"] == "message_sent"]


def test_who_may_read_the_log(http_case):
    cid = http_case
    url = f"/cases/{cid}/notifications"
    for h in (COORD, SUP, PCO):
        assert client.get(url, headers=h).status_code == 200
    for h in (LAB, PATIENT, PCO_B, SPEC):  # not allowed, or not the owner of this case
        assert client.get(url, headers=h).status_code == 403
    assert client.get(url).status_code == 401
    assert client.get(f"/cases/{uuid4()}/notifications", headers=COORD).status_code == 404


def test_nobody_can_post_message_sent_over_http(http_case):
    for h in (LAB, COORD, SUP, PCO, SPEC, PATIENT):
        r = client.post(f"/cases/{http_case}/events", json={"type": "message_sent", "payload": {}}, headers=h)
        assert r.status_code == 403
    sched = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
    assert client.post(f"/cases/{http_case}/events", json={"type": "message_sent"}, headers=sched).status_code == 403


def test_t_a23_after_a_withdrawal_over_http_no_outbound_row_exists(http_case, no_network):
    cid = http_case
    send_approved(cid, "sms", "en", "reminder", "before", at=clock.now())
    client.post("/clock/advance", json={"hours": 1}, headers=SUP)
    r = client.post(f"/cases/{cid}/events", json={"type": "consent_updated", "payload": {"withdrawn": True}}, headers=PATIENT)
    assert r.status_code == 201 and r.json()["state"] == "consent_withdrawn"
    withdrawn_at = r.json()["events"][-2]["at"]
    for ch in ("sms", "whatsapp", "phone", "letter"):
        send_approved(cid, ch, "en", "reminder", "after")  # at the sim clock
    rows = client.get(f"/cases/{cid}/notifications", headers=COORD).json()
    assert [x["status"] for x in rows] == ["sent"] + ["blocked_consent"] * 4
    assert [x for x in rows if x["status"] in ("sent", "queued") and x["created_at"] >= withdrawn_at] == []
    r = client.post(
        f"/cases/{cid}/events", json={"type": "message_drafted", "payload": DRAFTED}, headers=COORD
    )
    assert r.status_code == 409
    # message_approved is not postable at all (only POST /drafts/{id}/approve writes it, D5)
    r = client.post(
        f"/cases/{cid}/events", json={"type": "message_approved", "payload": APPROVED}, headers=PCO
    )
    assert r.status_code == 403
    listed = client.get("/cases", params={"state": "consent_withdrawn"}, headers=COORD).json()
    assert cid in [c["case_id"] for c in listed]
    assert client.get(f"/cases/{cid}", headers=COORD).json()["read_model"]["review_reason"] == "consent_withdrawn"
