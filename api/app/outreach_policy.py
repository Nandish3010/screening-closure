"""The pre-approved outreach policy (A21, ADR-032), from config/outreach_policy.yaml.

Which deterministic template the automation may send, on which channel, in response to which trigger, approved by whom
and at which version. The state machine reads `template_for` and `max_auto_messages_per_week` at apply time (a
`message_sent` from `system:automation` is accepted only for a template listed here); `app.automation` reads the rules to
decide what to send now. Pure: a loaded file, no clock, no store.

The file is validated hard at load time: every template exists in the message template files for every language, on
that channel, and the approval names the template-set version it was given against, so editing a template without
re-approving it makes the policy fail to load (the automation then sends nothing; `app.automation.configure` logs it).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

TRIGGERS = ("entered_state", "reminder", "escalation_rung")
DEFAULT_MAX_PER_WEEK = 2
AUTO_WINDOW_DAYS = 7  # "per week": the seven days before the message


class OutreachPolicyError(ValueError):
    """config/outreach_policy.yaml is malformed or out of step with the template files."""


@dataclass(frozen=True)
class AutoTemplate:
    id: str
    purpose: str
    channel: str
    version: int
    approved_by: str


@dataclass(frozen=True)
class Rule:
    id: str
    trigger: str
    state: str | None
    purpose: str
    channels: tuple[str, ...]
    after_working_days: int | None = None
    rung: str | None = None


@dataclass(frozen=True)
class OutreachPolicy:
    version: int
    template_set_version: int
    status: str
    max_auto_messages_per_week: int
    default_language: str
    templates: dict[str, AutoTemplate]
    rules: tuple[Rule, ...]

    def template_for(self, template_id: object) -> AutoTemplate | None:
        return self.templates.get(template_id) if isinstance(template_id, str) else None

    def template_id(self, purpose: str, channel: str) -> str:
        return f"{purpose}.{channel}"


def parse_policy(raw: object, *, check_templates: bool = True) -> OutreachPolicy:
    if not isinstance(raw, dict):
        raise OutreachPolicyError("outreach policy must be a mapping")
    cap = raw.get("max_auto_messages_per_week", DEFAULT_MAX_PER_WEEK)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0 or cap > 14:
        raise OutreachPolicyError("max_auto_messages_per_week must be an integer from 0 to 14")
    language = raw.get("default_language", "en")
    if language not in ("en", "ar"):
        raise OutreachPolicyError("default_language must be en or ar")
    set_version = raw.get("template_set_version")
    if isinstance(set_version, bool) or not isinstance(set_version, int):
        raise OutreachPolicyError("template_set_version (the version of api/app/ai/templates the approval is against) is required")
    templates: dict[str, AutoTemplate] = {}
    for t in raw.get("templates") or []:
        if not isinstance(t, dict):
            raise OutreachPolicyError("each template must be a mapping")
        missing = {"id", "purpose", "channel", "version", "approved_by"} - set(t)
        if missing:
            raise OutreachPolicyError(f"template {t.get('id')!r} is missing {sorted(missing)}")
        version = t["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise OutreachPolicyError(f"template {t['id']!r}: version must be an integer >= 1")
        if not isinstance(t["approved_by"], str) or not t["approved_by"].strip():
            raise OutreachPolicyError(f"template {t['id']!r}: approved_by is required (the clinical lead; a placeholder until H1)")
        if t["id"] != f"{t['purpose']}.{t['channel']}":
            raise OutreachPolicyError(f"template id {t['id']!r} must be '<purpose>.<channel>'")
        if t["id"] in templates:
            raise OutreachPolicyError(f"template {t['id']!r} listed twice")
        templates[t["id"]] = AutoTemplate(t["id"], t["purpose"], t["channel"], version, t["approved_by"].strip())
    if not templates:
        raise OutreachPolicyError("the policy lists no template")
    rules: list[Rule] = []
    for r in raw.get("rules") or []:
        if not isinstance(r, dict) or r.get("trigger") not in TRIGGERS:
            raise OutreachPolicyError(f"rule {r!r}: trigger must be one of {list(TRIGGERS)}")
        channels = tuple(r.get("channels") or ())
        if not channels:
            raise OutreachPolicyError(f"rule {r.get('id')!r}: channels must list at least one channel")
        purpose = r.get("template")
        for ch in channels:
            if f"{purpose}.{ch}" not in templates:
                raise OutreachPolicyError(f"rule {r.get('id')!r}: template {purpose}.{ch} is not in the approved list")
        days = r.get("after_working_days")
        if r["trigger"] == "reminder" and (isinstance(days, bool) or not isinstance(days, int) or days < 1):
            raise OutreachPolicyError(f"rule {r.get('id')!r}: a reminder needs after_working_days >= 1")
        if r["trigger"] == "escalation_rung" and not r.get("rung"):
            raise OutreachPolicyError(f"rule {r.get('id')!r}: an escalation_rung rule needs a rung")
        if r["trigger"] != "escalation_rung" and not r.get("state"):
            raise OutreachPolicyError(f"rule {r.get('id')!r}: needs a state")
        rules.append(Rule(str(r.get("id")), r["trigger"], r.get("state"), purpose, channels,
                          days if r["trigger"] == "reminder" else None, r.get("rung")))
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise OutreachPolicyError("rule ids must be unique")
    if check_templates:
        from app.ai.templates import load_templates

        ts = load_templates()
        if ts.version != set_version:
            raise OutreachPolicyError(
                f"the templates are at version {ts.version} but the outreach approval is for version {set_version}: "
                "re-approve (H1) and update template_set_version")
        for t in templates.values():
            for lang in ("en", "ar"):
                if not ts.has(lang, t.purpose, t.channel, False):
                    raise OutreachPolicyError(f"template {t.id}: no {lang} text for it in api/app/ai/templates")
    return OutreachPolicy(int(raw.get("version", 1)), set_version, str(raw.get("status", "")), cap, language,
                          templates, tuple(rules))


def load_policy(path: str | Path, *, check_templates: bool = True) -> OutreachPolicy:
    return parse_policy(yaml.safe_load(Path(path).read_text(encoding="utf-8")), check_templates=check_templates)


@lru_cache(maxsize=4)
def _cached(path: str) -> OutreachPolicy:
    return load_policy(path)


def get_policy(path: str | None = None) -> OutreachPolicy:
    """The policy named by settings (or `path`), loaded once per process and path. Raises OutreachPolicyError."""
    if path is None:
        from app.config import settings

        path = settings.outreach_policy_path
    return _cached(str(path))
