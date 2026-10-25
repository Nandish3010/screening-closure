"""Drafting provider interface (D1).

A provider turns a `DraftRequest` into a `DraftResult` (text for a human owner to approve).
`template` is the default and needs no network. `azure_openai` and `jais` are registered names
that resolve to the HTTP adapters in `app.ai.adapters` (D7), wrapped by the output cache
(`app.ai.cache`, D6). Network is off by default, so they raise `ProviderUnavailable` unless a
cached output exists or `LLM_NETWORK_ENABLED=true`.

Data minimisation: a `DraftRequest` carries only what a message needs. It never carries
clinical findings at all (E14: no message states or hints at what the screening found), and no
patient name, contact detail or identifier.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, get_args, runtime_checkable

Purpose = Literal[
    "result_notification", "reminder", "appointment_confirmation", "escalation_notice", "callback_ack",
    "refusal_acknowledgement",
]
Channel = Literal["sms", "whatsapp", "letter"]
Language = Literal["ar", "en"]

PURPOSES: tuple[str, ...] = get_args(Purpose)
CHANNELS: tuple[str, ...] = get_args(Channel)
LANGUAGES: tuple[str, ...] = get_args(Language)

# Hard length ceilings in characters, per channel (D2/D3).
CHANNEL_MAX_CHARS: dict[str, int] = {"sms": 320, "whatsapp": 600, "letter": 1200}

# Longest value accepted for a free-text request field. Keeps worst-case messages inside the
# channel limits (templates are tested at this length).
MAX_FIELD_CHARS = 40

TEMPLATE_PROVIDER = "template"
PROVIDER_NAMES: tuple[str, ...] = ("template", "azure_openai", "jais", "anthropic")


class ProviderUnavailable(Exception):
    """The provider cannot produce a draft now (not configured, offline, timed out, errored)."""


class BudgetExhausted(ProviderUnavailable):
    """The daily token budget or the per-call token cap would be exceeded (A35). Counted as abstention `budget_exhausted`."""


class CacheMiss(ProviderUnavailable):
    """Replay mode found no recorded output for this prompt (D6). Counted as abstention `cache_miss`."""


class UnknownProvider(ValueError):
    """`LLM_PROVIDER` names a provider that is not registered. Startup should fail on this."""


@dataclass(frozen=True)
class DraftRequest:
    purpose: Purpose
    channel: Channel
    language: Language
    owner_name: str  # the care owner the patient will hear from, not the patient
    owner_role: str
    next_step: str  # e.g. "a colonoscopy" (already in the message language)
    pathway_name: str
    appointment_date: str | None = None  # human-readable, already in the message language

    def __post_init__(self) -> None:
        for field_name, allowed in (
            ("purpose", PURPOSES),
            ("channel", CHANNELS),
            ("language", LANGUAGES),
        ):
            if getattr(self, field_name) not in allowed:
                raise ValueError(f"{field_name} must be one of {allowed}, got {getattr(self, field_name)!r}")
        for field_name in ("owner_name", "owner_role", "next_step", "pathway_name"):
            _check_text_field(field_name, getattr(self, field_name))
        if self.appointment_date is not None:
            _check_text_field("appointment_date", self.appointment_date)
        if self.purpose == "appointment_confirmation" and not self.appointment_date:
            raise ValueError("appointment_confirmation requires appointment_date")


def _check_text_field(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(f"{name} is longer than {MAX_FIELD_CHARS} characters")
    if any(ch in value for ch in "{}[]<>") or "\n" in value:
        raise ValueError(f"{name} must not contain braces, brackets, angle brackets or newlines")


@dataclass(frozen=True)
class DraftResult:
    text: str
    language: str
    provider: str
    model_id: str
    fallback_used: bool
    abstention_reason: str | None
    prompt_hash: str
    usage: dict | None = None  # token counts of a live model call (A35); None for a template or a cache replay


@runtime_checkable
class Provider(Protocol):
    name: str
    model_id: str

    def draft(self, req: DraftRequest) -> DraftResult:
        """Return a draft. Raise `ProviderUnavailable` (or any exception) if it cannot."""
        ...


_LANGUAGE_NAMES = {"ar": "Arabic", "en": "English"}

# The system message a model-backed provider sends (D7). Together with `build_prompt` it is the
# whole instruction, so `prompt_hash` covers both and the `prompts` table stores both.
SYSTEM_PROMPT = (
    "You write one short message from a screening programme to a patient. A human care owner "
    "reads and approves it before anything is sent. "
    "Never diagnose, never name or hint at any disease, never give medical advice and never "
    "reassure. Never state, hint at or interpret the screening result or what it found. "
    "Say only that the screening result is ready, that the care team needs to talk with the "
    "patient about the next step, who the care owner is and what the next step is. "
    "Use plain language at about a grade-6 reading level. "
    "Write only in the language the user message asks for. "
    "Use exactly the care owner name, next step and appointment date given in the user message, "
    "word for word, and invent no other names, dates, numbers or links. "
    "Reply with the message text only: no title, no quotation marks, no notes."
)


def build_prompt(req: DraftRequest) -> str:
    """The user message a model-backed provider sends, and (with `SYSTEM_PROMPT`) the input to `prompt_hash`.

    Deterministic for a given request. Template providers do not send it anywhere, but they
    hash it so every draft has the same kind of Provenance reference.
    """
    lines = [
        "Draft one patient message for a human care owner to approve. Rules:",
        f"- Write in {_LANGUAGE_NAMES[req.language]}, plain language, about grade-6 reading level.",
        f"- Channel: {req.channel}. Maximum {CHANNEL_MAX_CHARS[req.channel]} characters.",
        f"- Purpose: {req.purpose}.",
        "- Do not diagnose, name any disease, give medical advice or reassure.",
        "- Never state, hint at or interpret the screening result or what it found.",
        "- Say that the screening result is ready and that the care team needs to talk with the patient about the next step.",
        "- Name the care owner and the next step exactly as given below, and say that the care team will contact the patient.",
        "- No links, no phone numbers, no email addresses, no identifiers.",
        f"- Care owner name to include: {req.owner_name}",
        f"- Care owner role: {req.owner_role}",
        f"- Next step to include: {req.next_step}",
        f"- Pathway: {req.pathway_name}",
    ]
    if req.appointment_date:
        lines.append(f"- Appointment date to include: {req.appointment_date}")
    return "\n".join(lines)


def full_prompt(req: DraftRequest) -> str:
    """The exact text that `prompt_hash` covers: system message, a separator line, user message."""
    return f"{SYSTEM_PROMPT}\n---\n{build_prompt(req)}"


def prompt_hash(req: DraftRequest) -> str:
    """SHA-256 of `full_prompt`. An auditor reproduces it from the stored system and user texts."""
    return hashlib.sha256(full_prompt(req).encode("utf-8")).hexdigest()


class TemplateProvider:
    """Deterministic provider: fills the reviewed template. No network, no model."""

    name = TEMPLATE_PROVIDER

    @property
    def model_id(self) -> str:
        from app.ai import templates  # lazy: keeps this module importable on its own

        return f"template-v{templates.load_templates().version}"

    def draft(self, req: DraftRequest) -> DraftResult:
        from app.ai import templates

        text = templates.render(
            req.purpose,
            req.channel,
            req.language,
            owner_name=req.owner_name,
            next_step=req.next_step,
            appointment_date=req.appointment_date,
        )
        return DraftResult(
            text=text,
            language=req.language,
            provider=self.name,
            model_id=self.model_id,
            fallback_used=False,
            abstention_reason=None,
            prompt_hash=prompt_hash(req),
        )


def _azure_openai() -> Provider:
    from app.ai import adapters, cache  # lazy: adapters imports this module

    return cache.CachingProvider(adapters.AzureOpenAIProvider())


def _jais() -> Provider:
    from app.ai import adapters, cache

    return cache.CachingProvider(adapters.JaisProvider())


def _anthropic() -> Provider:
    from app.ai import cache, claude  # lazy: the SDK is imported only when a call is really made

    return cache.CachingProvider(claude.ClaudeProvider())


_REGISTRY: dict[str, Callable[[], Provider]] = {
    "template": TemplateProvider,
    "azure_openai": _azure_openai,
    "jais": _jais,
    "anthropic": _anthropic,
}


def register_provider(name: str, factory: Callable[[], Provider]) -> None:
    """Replace or add a provider factory (tests use this to plug in fakes)."""
    _REGISTRY[name] = factory


def configured_provider_name() -> str:
    """The `LLM_PROVIDER` value: environment first, then app settings (.env), default `template`."""
    value = os.environ.get("LLM_PROVIDER")
    if not value or not value.strip():
        from app.config import settings

        value = settings.llm_provider
    return (value or TEMPLATE_PROVIDER).strip().lower()


def get_provider(name: str | None = None) -> Provider:
    """Resolve a provider by name, or from `LLM_PROVIDER` when no name is given.

    Raises `UnknownProvider` for an unregistered name, so a misconfiguration fails fast.
    """
    resolved = (name.strip().lower() if name else None) or configured_provider_name()
    try:
        factory = _REGISTRY[resolved]
    except KeyError:
        raise UnknownProvider(
            f"unknown LLM_PROVIDER {resolved!r}; expected one of {sorted(_REGISTRY)}"
        ) from None
    return factory()
