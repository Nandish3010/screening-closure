"""D7: Azure OpenAI and Jais adapters, mocked HTTP only (T-D4).

No test here opens a socket: every request goes to an `httpx.MockTransport`, and a fixture
makes any real connection attempt fail the test.
"""

import json
import socket

import httpx
import pytest

from app.ai.adapters import AzureOpenAIProvider, JaisProvider, ProviderResponseError, network_enabled
from app.ai.provider import (
    SYSTEM_PROMPT,
    DraftRequest,
    ProviderUnavailable,
    TemplateProvider,
    build_prompt,
    get_provider,
    prompt_hash,
)
from app.ai.validate import AbstentionLog, draft_with_fallback

GOOD = "Your screening test found blood. Dr Sara Khan will arrange a colonoscopy and the care team will contact you."
SECRET = "k3y-do-not-leak-0123"

AZURE_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://res.openai.azure.com/",
    "AZURE_OPENAI_API_KEY": SECRET,
    "AZURE_OPENAI_DEPLOYMENT": "gpt-draft",
}
JAIS_ENV = {
    "JAIS_ENDPOINT": "https://jais.example.org/v1/chat/completions",
    "JAIS_API_KEY": SECRET,
    "JAIS_MODEL": "jais-chat",
}


def make_req(**overrides) -> DraftRequest:
    base = dict(purpose="result_notification", channel="sms", language="en", owner_name="Dr Sara Khan",
                owner_role="primary_care_owner", next_step="a colonoscopy", pathway_name="colorectal_fit")
    base.update(overrides)
    return DraftRequest(**base)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for key in (*AZURE_ENV, *JAIS_ENV, "AZURE_OPENAI_API_VERSION", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    monkeypatch.delenv("LLM_NETWORK_ENABLED", raising=False)

    def refuse(*a, **k):
        raise AssertionError("real outbound connection attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def configure(monkeypatch, env, network=True):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LLM_NETWORK_ENABLED", "true" if network else "false")


class Recorder:
    """A mock transport that records requests and answers with `handler`."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        return self._handler(request)


def ok(text=GOOD, **extra):
    body = {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], **extra}
    return Recorder(lambda r: httpx.Response(200, json=body))


# -- network switch ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "maybe"])
def test_network_is_off_unless_explicitly_true(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("LLM_NETWORK_ENABLED", value)
    assert network_enabled() is False


@pytest.mark.parametrize("cls,env", [(AzureOpenAIProvider, AZURE_ENV), (JaisProvider, JAIS_ENV)])
def test_network_off_raises_before_any_request(monkeypatch, cls, env):
    configure(monkeypatch, env, network=False)
    rec = ok()
    with pytest.raises(ProviderUnavailable, match="network"):
        cls(transport=rec.transport).draft(make_req())
    assert rec.requests == []


@pytest.mark.parametrize("cls", [AzureOpenAIProvider, JaisProvider])
def test_default_registry_providers_make_no_call_by_default(cls):
    # no env at all: the registry providers are unavailable, with no mock needed
    with pytest.raises(ProviderUnavailable):
        get_provider(cls.name).draft(make_req())


@pytest.mark.parametrize("cls", [AzureOpenAIProvider, JaisProvider])
def test_unconfigured_is_unavailable(monkeypatch, cls):
    monkeypatch.setenv("LLM_NETWORK_ENABLED", "true")
    rec = ok()
    with pytest.raises(ProviderUnavailable, match="not configured"):
        cls(transport=rec.transport).draft(make_req())
    assert rec.requests == []


# -- request shape -----------------------------------------------------------------------------


def test_azure_request_shape(monkeypatch):
    configure(monkeypatch, AZURE_ENV)
    rec = ok()
    res = AzureOpenAIProvider(transport=rec.transport).draft(make_req(appointment_date="Tuesday 3 November 2026"))
    (request,) = rec.requests
    assert request.method == "POST"
    assert str(request.url) == (
        "https://res.openai.azure.com/openai/deployments/gpt-draft/chat/completions?api-version=2024-06-01"
    )
    assert request.headers["api-key"] == SECRET and "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["temperature"] == 0 and body["max_tokens"] <= 1500
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert body["messages"][1]["content"] == build_prompt(make_req(appointment_date="Tuesday 3 November 2026"))
    assert res.text == GOOD and res.provider == "azure_openai" and res.model_id == "gpt-draft"
    assert res.fallback_used is False and res.language == "en"
    assert res.prompt_hash == prompt_hash(make_req(appointment_date="Tuesday 3 November 2026"))


def test_azure_api_version_is_configurable(monkeypatch):
    configure(monkeypatch, {**AZURE_ENV, "AZURE_OPENAI_API_VERSION": "2025-01-01"})
    rec = ok()
    AzureOpenAIProvider(transport=rec.transport).draft(make_req())
    assert rec.requests[0].url.params["api-version"] == "2025-01-01"


def test_jais_request_shape(monkeypatch):
    configure(monkeypatch, JAIS_ENV)
    rec = ok()
    res = JaisProvider(transport=rec.transport).draft(make_req(language="ar", owner_name="الدكتورة سارة", next_step="تنظير القولون"))
    (request,) = rec.requests
    assert str(request.url) == JAIS_ENV["JAIS_ENDPOINT"]
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(request.content)
    assert body["model"] == "jais-chat" and body["temperature"] == 0
    assert "Write in Arabic" in body["messages"][1]["content"]
    assert res.provider == "jais" and res.model_id == "jais-chat" and res.language == "ar"


def test_the_prompt_forbids_diagnosis_and_advice_and_names_the_three_values():
    for needle in ("Never diagnose", "never give medical advice", "grade-6", "language the user message asks for",
                   "care owner name, next step and appointment date"):
        assert needle in SYSTEM_PROMPT
    user = build_prompt(make_req(appointment_date="Tuesday 3 November 2026"))
    for value in ("Dr Sara Khan", "a colonoscopy", "Tuesday 3 November 2026", "Do not diagnose", "grade-6"):
        assert value in user


def test_no_patient_data_is_in_the_request(monkeypatch):
    configure(monkeypatch, AZURE_ENV)
    rec = ok()
    AzureOpenAIProvider(transport=rec.transport).draft(make_req())
    raw = rec.requests[0].content.decode()
    # the request type has no patient fields at all; check the wire text for the usual suspects
    for needle in ("Patient/", "mrn", "emirates", "@"):
        assert needle not in raw.lower()


def test_endpoint_must_be_https_except_localhost(monkeypatch):
    configure(monkeypatch, {**JAIS_ENV, "JAIS_ENDPOINT": "http://jais.example.org/v1/chat/completions"})
    rec = ok()
    with pytest.raises(ProviderUnavailable, match="https"):
        JaisProvider(transport=rec.transport).draft(make_req())
    assert rec.requests == []
    configure(monkeypatch, {**JAIS_ENV, "JAIS_ENDPOINT": "http://localhost:8123/v1/chat/completions"})
    JaisProvider(transport=rec.transport).draft(make_req())
    assert len(rec.requests) == 1


# -- failures become fallbacks (T-D4) -----------------------------------------------------------

FAILURES = {
    "timeout": lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    "connect_error": lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)),
    "server_error": lambda r: httpx.Response(500, text="boom " + SECRET),
    "rate_limited": lambda r: httpx.Response(429, json={"error": "slow down"}),
    "unauthorised": lambda r: httpx.Response(401, json={"error": "bad key"}),
}


@pytest.mark.parametrize("name", FAILURES)
@pytest.mark.parametrize("cls,env", [(AzureOpenAIProvider, AZURE_ENV), (JaisProvider, JAIS_ENV)])
def test_http_failures_fall_back_to_the_template(monkeypatch, cls, env, name):
    configure(monkeypatch, env)
    provider = cls(transport=Recorder(FAILURES[name]).transport)
    with pytest.raises(ProviderUnavailable) as err:
        provider.draft(make_req())
    assert SECRET not in str(err.value)
    log = AbstentionLog()
    res = draft_with_fallback(make_req(), provider, log)  # no exception reaches the caller
    assert res.fallback_used and res.provider == "template"
    assert res.abstention_reason == "provider_unavailable"
    assert res.text == TemplateProvider().draft(make_req()).text
    assert SECRET not in repr(log.records())


MALFORMED = {
    "not_json": httpx.Response(200, text="<html>gateway</html>"),
    "no_choices": httpx.Response(200, json={"choices": []}),
    "no_message": httpx.Response(200, json={"choices": [{}]}),
    "null_content": httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    "cut_off": httpx.Response(200, json={"choices": [{"message": {"content": GOOD}, "finish_reason": "length"}]}),
}


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_responses_fall_back(monkeypatch, name):
    configure(monkeypatch, AZURE_ENV)
    provider = AzureOpenAIProvider(transport=Recorder(lambda r: MALFORMED[name]).transport)
    with pytest.raises(ProviderResponseError):
        provider.draft(make_req())
    res = draft_with_fallback(make_req(), provider, AbstentionLog())
    assert res.fallback_used and res.abstention_reason == "provider_error:ProviderResponseError"


def test_the_validator_still_runs_on_model_output(monkeypatch):
    configure(monkeypatch, AZURE_ENV)
    for text, rule in [
        ("You have cancer. Dr Sara Khan will arrange a colonoscopy.", "forbidden_phrase"),
        (GOOD + " Do not worry, it is probably nothing.", "forbidden_phrase"),
        ("Dr Sara Khan will arrange a colonoscopy. Call 0501234567.", "pii_digits"),
        ("", "empty"),
        ("هذه رسالة بالعربية عن د. سارة خان وتنظير القولون", "wrong_language"),
    ]:
        provider = AzureOpenAIProvider(transport=ok(text).transport)
        log = AbstentionLog()
        res = draft_with_fallback(make_req(), provider, log)
        assert res.fallback_used and res.abstention_reason.startswith("validation_failed:"), text
        assert rule in res.abstention_reason, (text, res.abstention_reason)
        assert res.text == TemplateProvider().draft(make_req()).text


def test_good_model_output_is_used_and_whitespace_trimmed(monkeypatch):
    configure(monkeypatch, AZURE_ENV)
    res = draft_with_fallback(make_req(), AzureOpenAIProvider(transport=ok(f"  {GOOD}\n").transport), AbstentionLog())
    assert not res.fallback_used and res.text == GOOD and res.provider == "azure_openai"


def test_record_then_replay_through_the_adapter(monkeypatch, tmp_path):
    """The whole offline-demo loop: record once with the (mocked) network, replay with it off."""
    from app.ai.cache import CachingProvider

    configure(monkeypatch, AZURE_ENV)
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    rec = ok()
    first = draft_with_fallback(make_req(), CachingProvider(AzureOpenAIProvider(transport=rec.transport)), AbstentionLog())
    assert len(rec.requests) == 1 and first.text == GOOD

    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    monkeypatch.setenv("LLM_NETWORK_ENABLED", "false")
    second = draft_with_fallback(make_req(), get_provider("azure_openai"), AbstentionLog())
    assert second.text == first.text and not second.fallback_used and len(rec.requests) == 1


def test_timeout_setting_is_bounded(monkeypatch):
    from app.ai import adapters

    assert adapters._timeout() == 10.0
    for value, expected in (("3", 3.0), ("0", 10.0), ("-1", 10.0), ("abc", 10.0), ("500", 10.0)):
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
        assert adapters._timeout() == expected
