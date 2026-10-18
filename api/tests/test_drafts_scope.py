"""SEC-2 fix 9: GET /cases/{id}/drafts is owner-scoped. The drafts carry the message text meant for the
patient; a clinician reads them for the cases they own, coordinators and supervisors for any."""

from uuid import uuid4

import pytest

from api_helpers import login_headers, make_client
from app import notify
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.events import InMemoryEventStore
from app.routers import cases, drafts

client = make_client()
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    clock.reset()
    yield
    clock.reset()


def H(role, name=None):
    return login_headers(client, role, name)


def case_with_draft(owner_role="primary_care_owner", owner_name="Dr Owner"):
    cid = client.post("/cases", json={"patient_ref": f"Patient/ds-{uuid4().hex[:6]}"}, headers=H("coordinator")).json()["case_id"]
    for type_, role, name in [("acknowledge", "lab", None), ("accept_ownership", owner_role, owner_name)]:
        assert client.post(f"/cases/{cid}/events", json={"type": type_}, headers=H(role, name)).status_code == 201
    made = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H("coordinator", "Maryam"))
    assert made.status_code == 201
    return cid, made.json()["draft_id"]


def test_a_non_owner_pco_is_refused_and_the_owner_is_not():
    cid, did = case_with_draft()
    other = client.get(f"/cases/{cid}/drafts", headers=H("primary_care_owner", "Dr Not The Owner"))
    assert other.status_code == 403 and "may only act on cases they own" in other.json()["detail"]
    assert "text" not in other.text and did not in other.text  # nothing of the draft leaks in the refusal
    mine = client.get(f"/cases/{cid}/drafts", headers=H("primary_care_owner", "Dr Owner"))
    assert mine.status_code == 200 and [d["draft_id"] for d in mine.json()] == [did] and mine.json()[0]["text"]


def test_a_specialist_who_does_not_own_the_case_is_refused_too():
    cid, _ = case_with_draft()
    assert client.get(f"/cases/{cid}/drafts", headers=H("specialist_owner", "Dr Spec")).status_code == 403


def test_a_case_with_no_owner_yet_is_not_open_to_a_clinician():
    cid = client.post("/cases", json={"patient_ref": "Patient/ds-unowned"}, headers=H("coordinator")).json()["case_id"]
    assert client.get(f"/cases/{cid}/drafts", headers=H("primary_care_owner", "Dr Eager")).status_code == 403


@pytest.mark.parametrize("role", ["coordinator", "supervisor"])
def test_coordinators_and_supervisors_read_any_case(role):
    cid, did = case_with_draft()
    r = client.get(f"/cases/{cid}/drafts", headers=H(role, "Reader"))
    assert r.status_code == 200 and [d["draft_id"] for d in r.json()] == [did]


@pytest.mark.parametrize("role", ["lab", "patient"])
def test_roles_that_may_not_draft_still_may_not_read(role):
    cid, _ = case_with_draft()
    h = login_headers(client, role, "x", "Patient/ds-1" if role == "patient" else None)
    assert client.get(f"/cases/{cid}/drafts", headers=h).status_code == 403


def test_unknown_case_is_404_and_no_token_is_401():
    assert client.get(f"/cases/{uuid4()}/drafts", headers=H("coordinator")).status_code == 404
    assert client.get(f"/cases/{uuid4()}/drafts").status_code == 401
