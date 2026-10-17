"""D4: POST/GET /cases/{id}/drafts (T-D5 unit half, in-memory stores).

The Postgres half (rows, prompts, triggers) is in test_drafts_pg.py.
"""

import dataclasses
import hashlib
import json
from uuid import UUID

import pytest

from api_helpers import ROLES, actor_of, login_headers, make_client, user_id_of
from app import payloads, policy
from app.ai import provider as prov
from app.ai.draft_store import InMemoryDraftStore
from app.ai.provider import SYSTEM_PROMPT, DraftResult
from app.routers import cases, drafts

client = make_client()
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
# A15: the closer signs as themselves (the specialist owner "Dr Sara Khan" is who post_event acts as).
SIGNED = {
    "evidence_ref": "DiagnosticReport/1",
    "signed_by": f"Practitioner/{user_id_of('specialist_owner', 'Dr Sara Khan')}",
}
ALLOWED = ["primary_care_owner", "specialist_owner", "coordinator", "supervisor"]
FORBIDDEN = ["lab", "patient"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_NETWORK_ENABLED", raising=False)


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def new_case(patient_ref="Patient/1"):
    r = client.post("/cases", json={"patient_ref": patient_ref}, headers=H("coordinator"))
    assert r.status_code == 201
    return r.json()["case_id"]


def post_event(case_id, type_, role, payload=None, name=None, patient_ref=None):
    if name is None and role in ("primary_care_owner", "specialist_owner"):
        name = "Dr Sara Khan"  # clinicians act only on cases they own (A5), and this is the owner
    r = client.post(
        f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}},
        headers=H(role, name, patient_ref),
    )
    assert r.status_code == 201, r.text
    return r.json()


def owned_case(owner="Dr Sara Khan", role="primary_care_owner"):
    cid = new_case()
    post_event(cid, "acknowledge", "lab")
    post_event(cid, "accept_ownership", role, name=owner)
    return cid


def booked_case(payload):
    cid = owned_case()
    post_event(cid, "contact_patient", "primary_care_owner")
    post_event(cid, "patient_acknowledge", "patient", {"lang": "en"}, patient_ref="Patient/1")
    post_event(cid, "book_confirmatory", "primary_care_owner", payload)
    return cid


def create(cid, body=None, role="coordinator", name=None):
    return client.post(f"/cases/{cid}/drafts", json=body or BODY, headers=H(role, name))


def events(cid):
    return cases.store.events_for(UUID(cid))


# -- roles and policy (fail closed) ----------------------------------------------------------


def test_routes_have_policy_entries_with_the_right_roles():
    assert policy.ROUTE_POLICY[("POST", "/cases/{case_id}/drafts")] == frozenset(ALLOWED)
    assert policy.ROUTE_POLICY[("GET", "/cases/{case_id}/drafts")] == frozenset(ALLOWED)


@pytest.mark.parametrize("role", ALLOWED)
def test_allowed_roles_can_draft(role):
    cid = owned_case()
    r = create(cid, role=role)
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("role", FORBIDDEN)
def test_forbidden_roles_get_403_and_nothing_is_written(role):
    cid = owned_case()
    before = len(events(cid))
    assert create(cid, role=role).status_code == 403
    assert len(events(cid)) == before
    assert drafts.store.list_for_case(UUID(cid)) == []


def test_unknown_role_set_is_exactly_covered():
    assert set(ALLOWED) | set(FORBIDDEN) == set(ROLES)


def test_unauthenticated_is_401_and_scheduler_is_403():
    cid = owned_case()
    assert client.post(f"/cases/{cid}/drafts", json=BODY).status_code == 401
    from test_rbac import auth_service_headers

    assert client.post(f"/cases/{cid}/drafts", json=BODY, headers=auth_service_headers()).status_code == 403


def test_a_route_without_a_policy_entry_is_denied(monkeypatch):
    monkeypatch.delitem(policy.ROUTE_POLICY, ("POST", "/cases/{case_id}/drafts"))
    cid = owned_case()
    assert create(cid, role="supervisor").status_code == 403


# -- happy path ------------------------------------------------------------------------------


def test_response_shape_and_template_draft():
    cid = owned_case("Dr Sara Khan")
    r = create(cid)
    assert r.status_code == 201
    out = r.json()
    assert set(out) == {
        "draft_id", "text", "text_ar", "text_en", "language", "provider", "fallback_used",
        "abstention_reason", "prompt_hash",
    }
    UUID(out["draft_id"])
    assert out["provider"] == "template" and out["fallback_used"] is False
    assert out["abstention_reason"] is None
    assert out["language"] == "en" and out["text"] == out["text_en"] and out["text_ar"] is None
    assert "Dr Sara Khan" in out["text"] and "{" not in out["text"]
    assert len(out["prompt_hash"]) == 64


def test_arabic_draft_puts_text_in_text_ar():
    cid = owned_case()
    out = create(cid, {**BODY, "language": "ar"}).json()
    assert out["text"] == out["text_ar"] and out["text_en"] is None and out["language"] == "ar"


def test_message_drafted_event_is_an_annotation_by_the_model_actor():
    cid = owned_case()
    state_before = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    out = create(cid).json()
    e = events(cid)[-1]
    assert e.type == "message_drafted" and e.actor == "model:template"
    # The strict A14 schema and docs/governance/ai-boundary.md: ids and hashes, never the draft text.
    assert e.payload == {
        "draft_id": out["draft_id"], "provider": "template", "fallback_used": False,
        "prompt_hash": out["prompt_hash"], "language": "en",
    }
    assert set(e.payload) == set(payloads.allowed_keys("message_drafted"))
    payloads.validate(e.type, e.payload)
    assert out["text"] not in json.dumps(e.payload)
    after = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    assert after["state"] == state_before["state"]  # drafting never moves a case
    assert after["read_model"]["owner"] == state_before["read_model"]["owner"]
    assert after["events"][-1]["actor"] == "model:template"


def test_requester_is_stored_from_the_token():
    cid = owned_case()
    out = create(cid, role="supervisor", name="Sam").json()
    row = drafts.store.get(UUID(out["draft_id"]))
    assert row.created_by == actor_of("supervisor", "Sam")
    assert (row.purpose, row.channel, row.language) == ("result_notification", "sms", "en")
    assert row.approved_at is None and row.approved_by is None
    assert row.abstention_reason is None and row.abstained_at is None


def test_the_prompt_is_stored_and_the_hash_is_reproducible():
    cid = owned_case("Dr Sara Khan")
    out = create(cid).json()
    prompt = drafts.store.get_prompt(out["prompt_hash"])
    assert prompt.system_prompt == SYSTEM_PROMPT
    assert "Dr Sara Khan" in prompt.prompt_text and "a colonoscopy" in prompt.prompt_text
    digest = hashlib.sha256((prompt.system_prompt + "\n---\n" + prompt.prompt_text).encode()).hexdigest()
    assert digest == out["prompt_hash"]


def test_every_message_drafted_event_has_a_stored_draft_and_prompt():
    cid = owned_case()
    for lang in ("en", "ar", "en"):
        create(cid, {**BODY, "language": lang})
    drafted = [e for e in events(cid) if e.type == "message_drafted"]
    assert len(drafted) == 3
    for e in drafted:
        row = drafts.store.get(UUID(e.payload["draft_id"]))
        assert row is not None and row.prompt_hash == e.payload["prompt_hash"]
        assert drafts.store.get_prompt(row.prompt_hash) is not None


def test_list_drafts_in_order_and_empty_for_a_new_case():
    cid = owned_case()
    assert client.get(f"/cases/{cid}/drafts", headers=H("coordinator")).json() == []
    a = create(cid).json()["draft_id"]
    b = create(cid, {**BODY, "channel": "letter"}).json()["draft_id"]
    listed = client.get(f"/cases/{cid}/drafts", headers=H("primary_care_owner")).json()
    assert [d["draft_id"] for d in listed] == [a, b]
    assert listed[1]["channel"] == "letter" and listed[0]["provider"] == "template"
    assert {"prompt_hash", "created_by", "created_at", "approved_at", "approved_by"} <= set(listed[0])


def test_list_is_scoped_to_the_case_and_closed_to_patients():
    c1, c2 = owned_case(), owned_case()
    create(c1)
    assert len(client.get(f"/cases/{c2}/drafts", headers=H("coordinator")).json()) == 0
    assert client.get(f"/cases/{c1}/drafts", headers=H("patient", patient_ref="Patient/1")).status_code == 403


# -- request building from the read model ------------------------------------------------------


def test_owner_comes_from_the_read_model():
    cid = owned_case("Dr Omar")
    assert "Dr Omar" in create(cid, role="supervisor", name="Sam").json()["text"]


def test_case_without_an_owner_is_drafted_in_the_requesters_name():
    cid = new_case()
    out = create(cid, role="coordinator", name="Maryam").json()
    assert "Maryam" in out["text"]


def test_overlong_owner_name_is_422_not_truncated():
    cid = owned_case("N" * 50)  # allowed as a login name (64), too long for a message (40)
    r = create(cid)
    assert r.status_code == 422 and "cannot build a message" in r.json()["detail"]
    assert drafts.store.list_for_case(UUID(cid)) == []


@pytest.mark.parametrize(
    "payload,expect_en",
    [
        ({"appointment_ref": "A1", "appointment_date": "2026-11-03"}, "Tuesday 3 November 2026"),
        ({"appointment_ref": "A1", "appointment_at": "2026-11-03T20:30:00Z"}, "Wednesday 4 November 2026"),
    ],
)
def test_appointment_date_is_used_when_booked(payload, expect_en):
    cid = booked_case(payload)
    en = create(cid, {**BODY, "purpose": "appointment_confirmation"}).json()["text"]
    assert expect_en in en  # 20:30Z is already the next day in Dubai
    ar = create(cid, {**BODY, "purpose": "appointment_confirmation", "language": "ar"}).json()["text"]
    assert "نوفمبر 2026" in ar


def test_appointment_confirmation_without_a_date_is_409():
    cid = owned_case()
    r = create(cid, {**BODY, "purpose": "appointment_confirmation"})
    assert r.status_code == 409 and "appointment" in r.json()["detail"]
    cid = booked_case({"appointment_ref": "A1"})  # booked, but no date recorded
    assert create(cid, {**BODY, "purpose": "appointment_confirmation"}).status_code == 409


def test_a_reminder_includes_the_date_when_there_is_one():
    cid = booked_case({"appointment_ref": "A1", "appointment_date": "2026-11-03"})
    assert "3 November 2026" in create(cid, {**BODY, "purpose": "reminder"}).json()["text"]


# -- refusals ---------------------------------------------------------------------------------


def test_consent_frozen_case_is_409_and_writes_nothing():
    cid = owned_case()
    post_event(cid, "consent_updated", "coordinator", {"withdrawn": True})
    assert client.get(f"/cases/{cid}", headers=H("coordinator")).json()["read_model"]["consent_frozen"]
    before = len(events(cid))
    r = create(cid)
    assert r.status_code == 409 and "consent" in r.json()["detail"]
    assert len(events(cid)) == before and drafts.store.list_for_case(UUID(cid)) == []
    # and again once consent is restored
    post_event(cid, "consent_updated", "coordinator", {"withdrawn": False})
    assert create(cid).status_code == 201


def test_closed_case_is_409():
    cid = owned_case(role="specialist_owner")  # complete_confirmatory is a specialist event
    for type_, role, payload in [
        ("contact_patient", "specialist_owner", {}),
        ("patient_acknowledge", "patient", {}),
        ("book_confirmatory", "specialist_owner", {}),
        ("complete_confirmatory", "specialist_owner", {}),
        ("clinician_review", "specialist_owner", {}),
        ("close", "specialist_owner", SIGNED),
    ]:
        post_event(cid, type_, role, payload, patient_ref="Patient/1" if role == "patient" else None)
    assert client.get(f"/cases/{cid}", headers=H("coordinator")).json()["state"] == "closed"
    assert create(cid).status_code == 409


def test_unknown_case_is_404():
    assert create("00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/cases/00000000-0000-0000-0000-000000000000/drafts", headers=H("coordinator")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"purpose": "diagnosis", "channel": "sms", "language": "en"},
        {"purpose": "reminder", "channel": "email", "language": "en"},
        {"purpose": "reminder", "channel": "sms", "language": "fr"},
        {"purpose": "reminder", "channel": "sms"},
        {**BODY, "requested_by": "someone"},  # identity is never taken from the body
        {**BODY, "actor": "model:x"},
    ],
)
def test_bad_bodies_are_422(body):
    cid = owned_case()
    before = len(events(cid))
    assert create(cid, body).status_code == 422
    assert len(events(cid)) == before


# -- model providers and fallback ---------------------------------------------------------------


class FakeModel:
    name = "azure_openai"
    model_id = "fake-model"

    def __init__(self, text=None, exc=None):
        self.text, self.exc = text, exc

    def draft(self, req):
        if self.exc:
            raise self.exc
        return DraftResult(self.text, req.language, self.name, self.model_id, False, None, "ignored")


def use_model(monkeypatch, fake):
    monkeypatch.setitem(prov._REGISTRY, "azure_openai", lambda: fake)
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")


def test_a_valid_model_draft_is_used_and_attributed_to_the_model(monkeypatch):
    cid = owned_case("Dr Sara Khan")
    text = "Your screening test found blood. Dr Sara Khan will arrange a colonoscopy and the care team will contact you."
    use_model(monkeypatch, FakeModel(text))
    out = create(cid).json()
    assert out["provider"] == "azure_openai" and out["fallback_used"] is False and out["text"] == text
    assert events(cid)[-1].actor == "model:azure_openai"
    row = drafts.store.get(UUID(out["draft_id"]))
    assert row.model_id == "fake-model" and row.abstention_reason is None
    # the stored hash is ours (of the prompt we built), not whatever the provider claims
    assert row.prompt_hash == out["prompt_hash"] != "ignored"


def test_a_diagnosis_falls_back_to_the_template_and_the_abstention_is_stored(monkeypatch):
    cid = owned_case("Dr Sara Khan")
    use_model(monkeypatch, FakeModel("You have cancer. Dr Sara Khan will arrange a colonoscopy."))
    out = create(cid).json()
    assert out["provider"] == "template" and out["fallback_used"] is True
    assert out["abstention_reason"].startswith("validation_failed:") and "cancer" in out["abstention_reason"]
    assert "cancer" not in out["text"].lower()
    row = drafts.store.get(UUID(out["draft_id"]))
    assert row.fallback_used and row.abstention_reason == out["abstention_reason"]
    assert row.abstained_at == row.created_at
    assert events(cid)[-1].actor == "model:template" and events(cid)[-1].payload["fallback_used"] is True


def test_provider_failure_never_reaches_the_caller(monkeypatch):
    cid = owned_case()
    use_model(monkeypatch, FakeModel(exc=TimeoutError("slow")))
    r = create(cid)
    assert r.status_code == 201
    assert r.json()["fallback_used"] and r.json()["abstention_reason"] == "provider_error:TimeoutError"


def test_model_providers_with_no_cache_and_no_network_use_the_template(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "jais")
    cid = owned_case()
    out = create(cid).json()
    assert out["provider"] == "template" and out["abstention_reason"] == "cache_miss"


def test_misconfigured_provider_is_503_and_writes_nothing(monkeypatch):
    cid = owned_case()
    monkeypatch.setenv("LLM_PROVIDER", "gpt-nine")
    before = len(events(cid))
    assert create(cid).status_code == 503
    monkeypatch.setenv("LLM_PROVIDER", "jais")
    monkeypatch.setenv("LLM_CACHE_MODE", "sideways")
    assert create(cid).status_code == 503
    assert len(events(cid)) == before


def test_a_model_actor_still_cannot_post_anything_else():
    """The draft route is the model's only door: its events are annotations, nothing else."""
    from app.state_machine import is_automated

    cid = owned_case()
    create(cid)
    assert all(
        e.type == "message_drafted" for e in events(cid) if is_automated(e.actor) and e.actor.startswith("model:")
    )


def test_the_draft_route_refuses_a_payload_the_strict_schema_would_refuse(monkeypatch):
    """T-A36 / ai-boundary: the route goes through payloads.validate, so a provider name outside the
    allowed shape (or any extra key) is refused with nothing written, not stored in the log."""
    cid = owned_case()
    n_events = len(events(cid))
    n_drafts = len(drafts.store.list_for_case(UUID(cid)))
    real = drafts.draft_with_fallback

    def bad_provider(req, provider):
        result = real(req, provider)
        return dataclasses.replace(result, provider="not a valid provider name!")

    monkeypatch.setattr(drafts, "draft_with_fallback", bad_provider)
    r = create(cid)
    assert r.status_code == 503
    assert len(events(cid)) == n_events and len(drafts.store.list_for_case(UUID(cid))) == n_drafts
