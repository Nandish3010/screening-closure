"""Model provider adapters over HTTP (D7): Azure OpenAI and Jais.

Both are plain chat-completion calls through `httpx`. Safety defaults:

* The network is OFF. Unless `LLM_NETWORK_ENABLED` is `true`, `draft` raises
  `ProviderUnavailable` before any request is built, and `draft_with_fallback` uses the template.
* Tests and the default configuration never call a real endpoint. Tests inject an
  `httpx.MockTransport`.
* A request carries only `SYSTEM_PROMPT` and `build_prompt(req)`: the care owner's name and
  role, the next step, an appointment date and the pathway name. No patient data.
* The model output is only text. It is validated by `app.ai.validate` before anyone sees it,
  and a human approves it before anything is sent.
* Endpoints must be https (http only for localhost, for local test servers). The API key is
  sent in a header and never logged or included in an error message.
* Every failure (disabled, not configured, timeout, connection error, non-2xx status, bad
  response body) becomes `ProviderUnavailable` or `ProviderResponseError`; neither reaches the
  API caller, who gets the template draft.

Configuration (environment variables; copy into `.env`, never commit values). These keys are
documented in docs/governance/ai-boundary.md and are deliberately not in `.env.example`:

    LLM_PROVIDER=azure_openai | jais | template   (template is the default)
    LLM_NETWORK_ENABLED=false                     (true to allow outbound calls)
    LLM_CACHE_MODE=replay | record | off          (see app.ai.cache; replay is the default)
    LLM_TIMEOUT_SECONDS=10

    AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
    AZURE_OPENAI_API_KEY=...
    AZURE_OPENAI_DEPLOYMENT=<deployment name>
    AZURE_OPENAI_API_VERSION=2024-06-01           (optional)

    JAIS_ENDPOINT=https://<host>/v1/chat/completions   (an OpenAI-compatible chat completions URL)
    JAIS_API_KEY=...
    JAIS_MODEL=<model id>                              (for example a Jais chat model)
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from app.ai.provider import (
    CHANNEL_MAX_CHARS,
    SYSTEM_PROMPT,
    DraftRequest,
    DraftResult,
    ProviderUnavailable,
    build_prompt,
    prompt_hash,
)

log = logging.getLogger("app.ai.adapters")

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_AZURE_API_VERSION = "2024-06-01"
_TRUE = {"1", "true", "yes", "on"}


class ProviderResponseError(Exception):
    """The provider answered, but not with a usable chat completion."""


def network_enabled() -> bool:
    return (os.environ.get("LLM_NETWORK_ENABLED") or "false").strip().lower() in _TRUE


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _timeout() -> float:
    try:
        value = float(_env("LLM_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if 0 < value <= 120 else DEFAULT_TIMEOUT_SECONDS


def _check_url(url: str, what: str) -> None:
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ProviderUnavailable(f"{what} must be an https URL")
    if not parts.hostname:
        raise ProviderUnavailable(f"{what} is not a valid URL")


class _ChatProvider:
    """Shared flow: gate, build the request, post it, extract and return the text."""

    name = ""
    fills_placeholders = True  # build_prompt asks for {owner_name} etc.; draft_with_fallback fills them in (A35)

    def __init__(self, transport=None) -> None:
        # `transport` is an httpx transport; tests pass httpx.MockTransport. None = real network.
        self._transport = transport

    # -- provider specifics -----------------------------------------------------------------
    @property
    def model_id(self) -> str:
        raise NotImplementedError

    def _request(self, messages: list[dict], req: DraftRequest) -> tuple[str, dict[str, str], dict]:
        """Return (url, headers, json body). Raise ProviderUnavailable if not configured."""
        raise NotImplementedError

    # -- shared flow -----------------------------------------------------------------------
    def draft(self, req: DraftRequest) -> DraftResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(req)},
        ]
        text = self._post(messages, req)
        return DraftResult(
            text=text,
            language=req.language,
            provider=self.name,
            model_id=self.model_id,
            fallback_used=False,
            abstention_reason=None,
            prompt_hash=prompt_hash(req),
        )

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        """One chat completion for a caller that is not drafting a patient message (the agency's planner, A22):
        same gate, timeout, https rule, key handling and error mapping as `draft`. The caller validates the text."""
        return self._post([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens)

    def _post(self, messages: list[dict], budget) -> str:
        if not network_enabled():
            raise ProviderUnavailable("LLM_NETWORK_ENABLED is not true: network calls are off")
        import httpx  # lazy: the default configuration never needs it

        url, headers, body = self._request(messages, budget)
        _check_url(url, f"{self.name} endpoint")
        try:
            with httpx.Client(transport=self._transport, timeout=_timeout()) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:  # timeout, connection refused, DNS, TLS, protocol
            raise ProviderUnavailable(f"{self.name}: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise ProviderUnavailable(f"{self.name}: HTTP {response.status_code}")
        self._record_usage(response)
        return self._extract(response)

    def _record_usage(self, response) -> None:
        """Token counts from an OpenAI-compatible `usage` object, for the llm-tokens metric (F14). Counts only; never text."""
        try:
            from app import observability

            observability.record_llm_usage(self.name, response.json().get("usage"))
        except Exception:  # noqa: BLE001 - a metric must never fail a draft
            pass

    def _extract(self, response) -> str:
        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderResponseError(f"{self.name}: unexpected response body") from None
        if choice.get("finish_reason") == "length":
            raise ProviderResponseError(f"{self.name}: output cut off at the token limit")
        if not isinstance(text, str):
            raise ProviderResponseError(f"{self.name}: message content is not text")
        return text.strip()

    @staticmethod
    def _max_tokens(req) -> int:
        if isinstance(req, int):  # `complete`: the caller names its token budget
            return min(1500, max(16, req))
        # Arabic can take about one token per character, so allow the channel's character limit
        # plus headroom; longer outputs fail validation anyway.
        return min(1500, CHANNEL_MAX_CHARS[req.channel] + 100)


class AzureOpenAIProvider(_ChatProvider):
    name = "azure_openai"

    @property
    def model_id(self) -> str:
        return _env("AZURE_OPENAI_DEPLOYMENT") or "unconfigured"

    def _request(self, messages, req):
        endpoint, key, deployment = (
            _env("AZURE_OPENAI_ENDPOINT"), _env("AZURE_OPENAI_API_KEY"), _env("AZURE_OPENAI_DEPLOYMENT"),
        )
        if not (endpoint and key and deployment):
            raise ProviderUnavailable(
                "azure_openai is not configured (AZURE_OPENAI_ENDPOINT, _API_KEY, _DEPLOYMENT)"
            )
        version = _env("AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_API_VERSION
        url = (
            f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={version}"
        )
        body = {"messages": messages, "temperature": 0, "max_tokens": self._max_tokens(req)}
        return url, {"api-key": key, "Content-Type": "application/json"}, body


class JaisProvider(_ChatProvider):
    """Jais behind an OpenAI-compatible chat completions endpoint (assumption: confirm with the host)."""

    name = "jais"

    @property
    def model_id(self) -> str:
        return _env("JAIS_MODEL") or "unconfigured"

    def _request(self, messages, req):
        endpoint, key, model = _env("JAIS_ENDPOINT"), _env("JAIS_API_KEY"), _env("JAIS_MODEL")
        if not (endpoint and key and model):
            raise ProviderUnavailable("jais is not configured (JAIS_ENDPOINT, JAIS_API_KEY, JAIS_MODEL)")
        body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": self._max_tokens(req)}
        return endpoint, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, body
