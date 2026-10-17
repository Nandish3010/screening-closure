"""D1: provider interface and LLM_PROVIDER selection (T-D6)."""

import pytest

from app.ai import provider as prov
from app.ai.provider import (
    DraftRequest,
    DraftResult,
    Provider,
    ProviderUnavailable,
    TemplateProvider,
    UnknownProvider,
    build_prompt,
    get_provider,
    prompt_hash,
)


def make_req(**overrides) -> DraftRequest:
    base = dict(
        purpose="result_notification",
        channel="sms",
        language="en",
        owner_name="Dr Sara Khan",
        owner_role="primary_care_owner",
        next_step="a colonoscopy",
        pathway_name="colorectal_fit",
    )
    base.update(overrides)
    return DraftRequest(**base)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def test_default_is_template_provider():
    p = get_provider()
    assert isinstance(p, TemplateProvider)
    assert p.name == "template"


def test_env_selects_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "template")
    assert isinstance(get_provider(), TemplateProvider)
    for name in ("azure_openai", "jais"):
        monkeypatch.setenv("LLM_PROVIDER", name)
        p = get_provider()
        assert p.name == name


def test_env_value_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", " Template ")
    assert isinstance(get_provider(), TemplateProvider)


@pytest.mark.parametrize("name", ["azure_openai", "jais"])
def test_unimplemented_providers_are_unavailable(name):
    p = get_provider(name)
    assert isinstance(p, Provider)
    with pytest.raises(ProviderUnavailable):
        p.draft(make_req())


def test_unknown_provider_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gpt-nine")
    with pytest.raises(UnknownProvider, match="gpt-nine"):
        get_provider()
    with pytest.raises(UnknownProvider):
        get_provider("nope")


def test_register_provider_overrides(monkeypatch):
    class Fake:
        name = "jais"
        model_id = "fake"

        def draft(self, req):
            return DraftResult("x", req.language, "jais", "fake", False, None, "h")

    monkeypatch.setitem(prov._REGISTRY, "jais", Fake)
    assert get_provider("jais").draft(make_req()).text == "x"


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "diagnosis"},
        {"channel": "email"},
        {"language": "fr"},
        {"owner_name": ""},
        {"owner_name": "   "},
        {"next_step": ""},
        {"owner_name": "Dr {owner_name}"},
        {"owner_name": "x" * 61},
        {"purpose": "appointment_confirmation", "appointment_date": None},
    ],
)
def test_draft_request_rejects_bad_fields(overrides):
    with pytest.raises(ValueError):
        make_req(**overrides)


def test_prompt_hash_is_deterministic_and_input_sensitive():
    a, b = make_req(), make_req()
    assert prompt_hash(a) == prompt_hash(b)
    assert len(prompt_hash(a)) == 64
    assert prompt_hash(a) != prompt_hash(make_req(language="ar"))
    assert prompt_hash(a) != prompt_hash(make_req(owner_name="Dr Omar"))


def test_prompt_states_the_boundary():
    text = build_prompt(make_req())
    assert "screening test found blood" in text
    assert "Do not diagnose" in text
    assert "Dr Sara Khan" in text and "a colonoscopy" in text
