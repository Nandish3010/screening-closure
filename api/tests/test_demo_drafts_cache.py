"""The committed Claude replay cache covers the demo's drafts (A35, ADR-043, D6).

The hosted demo and the video run with `LLM_PROVIDER=anthropic`, `LLM_CACHE_MODE=replay` and the network off. A draft is a
real recorded Claude Haiku output only if the cache holds an entry for the request's prompt hash. The hash covers the
purpose, channel, language, the care owner's ROLE and the pathway name (names, next step and date are placeholders), so the
demo's coverage is: every purpose x channel x language for the four staff roles that can draft, on the colorectal pathway.
These tests read the committed files (no network, no recording) and fail if a prompt change leaves the demo on templates
(abstention reason `cache_miss`), and prove a miss is still the template.
"""

import json
from uuid import UUID

import pytest

from api_helpers import login_headers, make_client
from app.ai.cache import DEFAULT_CACHE_DIR, read_entry
from app.ai.provider import CHANNELS, LANGUAGES, PURPOSES, DraftRequest, get_provider, prompt_hash
from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES
from app.ai.draft_store import InMemoryDraftStore
from app.ai.validate import draft_with_fallback
from app.routers import cases, drafts

DRAFTING_ROLES = ("primary_care_owner", "specialist_owner", "coordinator", "supervisor")
DEMO_OWNERS = {"en": "Dr. Huda Rahman", "ar": "Dr. Huda Rahman"}  # the owner the demo scenario drafts as
PATHWAY = "colorectal_fit"  # the pathway of the demo snapshot, the guided path and the video
MODEL = "claude-haiku-5-5"


@pytest.fixture(autouse=True)
def _replay_only(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    monkeypatch.delenv("LLM_CACHE_DIR", raising=False)
    monkeypatch.delenv("LLM_NETWORK_ENABLED", raising=False)


def _requests(role: str, pathway: str = PATHWAY):
    for language in LANGUAGES:
        for purpose in PURPOSES:
            for channel in CHANNELS:
                date = SAMPLE_VALUES[language]["appointment_date"] if purpose == "appointment_confirmation" else None
                yield DraftRequest(purpose, channel, language, DEMO_OWNERS[language], role, DEFAULT_NEXT_STEP[language], pathway, date)


@pytest.mark.parametrize("role", DRAFTING_ROLES)
def test_every_demo_prompt_has_a_recorded_model_output(role):
    reqs = list(_requests(role))
    assert len(reqs) == len(PURPOSES) * len(CHANNELS) * len(LANGUAGES)
    for req in reqs:
        entry = read_entry("anthropic", prompt_hash(req))
        assert entry is not None, f"no recording for {role}/{req.purpose}/{req.channel}/{req.language}"
        assert entry.model_id == MODEL and entry.language == req.language and entry.usage  # a real call, with its token counts


@pytest.mark.parametrize("role", DRAFTING_ROLES)
def test_the_replayed_drafts_pass_the_validator_and_carry_provenance(role):
    provider = get_provider()
    for req in _requests(role):
        out = draft_with_fallback(req, provider)
        where = f"{role}/{req.purpose}/{req.channel}/{req.language}"
        assert not out.fallback_used and out.abstention_reason is None, where
        assert out.provider == "anthropic" and out.model_id == MODEL and out.prompt_hash == prompt_hash(req), where
        assert req.owner_name in out.text and req.next_step in out.text, where  # placeholders were filled by the system
        assert "{" not in out.text and "}" not in out.text, where
        if req.appointment_date:
            assert req.appointment_date in out.text, where


def test_the_cache_holds_no_secret_or_contact_detail():
    blob = "".join(p.read_text(encoding="utf-8") for p in (DEFAULT_CACHE_DIR / "anthropic").glob("*.json"))
    for needle in ("sk-ant", "Bearer", "@", "http://", "https://"):
        assert needle not in blob, needle
    for p in (DEFAULT_CACHE_DIR / "anthropic").glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["prompt_hash"] == p.stem and "{owner_name}" in data["text"]  # placeholders, never a name


def test_a_prompt_with_no_recording_is_still_the_template_with_cache_miss():
    other_pathway = DraftRequest("reminder", "sms", "en", "Dr. Huda Rahman", "primary_care_owner", "a camera test", "no_such_pathway")
    out = draft_with_fallback(other_pathway, get_provider())
    assert out.fallback_used and out.provider == "template" and out.abstention_reason == "cache_miss"
    assert out.text and "{" not in out.text  # a template message is produced, never an empty draft


def test_miss_in_an_empty_cache_dir_is_the_template(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    req = next(_requests("primary_care_owner"))
    out = draft_with_fallback(req, get_provider())
    assert out.fallback_used and out.abstention_reason == "cache_miss" and out.provider == "template"


def test_the_api_serves_a_recorded_claude_draft_on_a_demo_case(monkeypatch):
    """End to end through POST /cases/{id}/drafts: the draft row says anthropic, the model, and that nothing is approved yet."""
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    client = make_client()
    h = lambda role, name=None: login_headers(client, role, name)  # noqa: E731
    cid = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=h("coordinator")).json()["case_id"]
    for etype, role, name in (("acknowledge", "lab", None), ("accept_ownership", "specialist_owner", "Dr. Huda Rahman")):
        r = client.post(f"/cases/{cid}/events", json={"type": etype, "payload": {}}, headers=h(role, name))
        assert r.status_code == 201, r.text
    body = {"purpose": "reminder", "channel": "sms", "language": "ar"}  # the demo's step 10: an Arabic reminder
    out = client.post(f"/cases/{cid}/drafts", json=body, headers=h("specialist_owner", "Dr. Huda Rahman"))
    assert out.status_code == 201, out.text
    d = out.json()
    assert d["provider"] == "anthropic" and d["fallback_used"] is False and d["abstention_reason"] is None
    assert "Dr. Huda Rahman" in d["text"]
    listed = client.get(f"/cases/{cid}/drafts", headers=h("specialist_owner", "Dr. Huda Rahman")).json()
    assert listed[0]["model_id"] == MODEL and listed[0]["approved_by"] is None  # GET carries the model for the UI's provenance line
    assert cases.store.events_for(UUID(cid))[-1].payload["provider"] == "anthropic"
