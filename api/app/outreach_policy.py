"""The pre-approved outreach policy (A21, ADR-032), from config/outreach_policy.yaml.

Which deterministic template the automation may send, on which channel, in response to which trigger, approved by whom
and at which version. `app.automation` reads the rules to decide what to send now; `auto_message_guard` below is the
gate: a `message_sent` from `system:automation` is written only for a template listed here, at the version listed here,
within `max_auto_messages_per_week` outbound messages. The gate runs at APPEND time, as a guard inside the store's
transaction (like the owner guard of `app.policy`), and nowhere else. The state machine does not read this file, so
replaying a log never depends on it: editing a template, or a policy that no longer loads, cannot make a historical case
fail to replay. Pure: a loaded file, no clock, no store.

The file is validated hard at load time: every template exists in the message template files for every language, on
that channel, and the approval names the template-set version it was given against, so editing a template without
re-approving it makes the policy fail to load (the automation then sends nothing; `app.automation.configure` logs it).

Signed or not (A26, ADR-037). The policy is signed when its top-level `approved_by` names the clinical lead (H1); the
shipped file says `approved_by: null`, because nobody has signed. An unsigned policy sends nothing: `auto_message_guard`
refuses every automatic `message_sent` and the automation only counts and logs what it held back (`outreach_held`,
reason `policy_unsigned`). `OUTREACH_DEMO_APPROVAL=1` (compose) makes `signed_by` answer `DEMO_APPROVER` for an unsigned
policy, so the demo shows the automatic path under a visible placeholder; a real signature in the file always wins, and the
API refuses to start outside APP_ENV=dev with the switch on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from functools import lru_cache
from pathlib import Path

import yaml

from app.state_machine import AUTO_MESSAGE_WINDOW, InvalidTransition

TRIGGERS = ("entered_state", "reminder", "escalation_rung")
# What /health, the web banner and the logs call an unsigned policy that runs on OUTREACH_DEMO_APPROVAL (A26).
DEMO_APPROVER = "DEMO PLACEHOLDER (clinical sign-off pending, H1)"
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
    approved_by: str | None  # the clinical lead who signed it (H1), or None: unsigned, nothing is sent (A26)
    max_auto_messages_per_week: int
    default_language: str
    templates: dict[str, AutoTemplate]
    rules: tuple[Rule, ...]

    def template_for(self, template_id: object) -> AutoTemplate | None:
        return self.templates.get(template_id) if isinstance(template_id, str) else None

    def template_id(self, purpose: str, channel: str) -> str:
        return f"{purpose}.{channel}"

    @property
    def signed(self) -> bool:
        """The file itself names an approver (the demo switch is not looked at here)."""
        return self.approved_by is not None


def parse_policy(raw: object, *, check_templates: bool = True) -> OutreachPolicy:
    if not isinstance(raw, dict):
        raise OutreachPolicyError("outreach policy must be a mapping")
    cap = raw.get("max_auto_messages_per_week", DEFAULT_MAX_PER_WEEK)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0 or cap > 14:
        raise OutreachPolicyError("max_auto_messages_per_week must be an integer from 0 to 14")
    language = raw.get("default_language", "en")
    if language not in ("en", "ar"):
        raise OutreachPolicyError("default_language must be en or ar")
    approver = raw.get("approved_by")
    if approver is not None and (not isinstance(approver, str) or not approver.strip()):
        raise OutreachPolicyError("approved_by must be null (unsigned: nothing is sent) or the name of the clinical lead who signed")
    if isinstance(approver, str) and approver.strip() == DEMO_APPROVER:
        raise OutreachPolicyError("approved_by may not be the demo placeholder text: leave it null and use OUTREACH_DEMO_APPROVAL")
    set_version = raw.get("template_set_version")
    if isinstance(set_version, bool) or not isinstance(set_version, int):
        raise OutreachPolicyError("template_set_version (the version of api/app/ai/templates the approval is against) is required")
    templates: dict[str, AutoTemplate] = {}
    for t in raw.get("templates") or []:
        if not isinstance(t, dict):
            raise OutreachPolicyError("each template must be a mapping")
        missing = {"id", "purpose", "channel", "version"} - set(t)
        if missing:
            raise OutreachPolicyError(f"template {t.get('id')!r} is missing {sorted(missing)}")
        if "approved_by" in t:
            raise OutreachPolicyError(f"template {t.get('id')!r}: approved_by is the policy's, at the top of the file (A26)")
        version = t["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise OutreachPolicyError(f"template {t['id']!r}: version must be an integer >= 1")
        if t["id"] != f"{t['purpose']}.{t['channel']}":
            raise OutreachPolicyError(f"template id {t['id']!r} must be '<purpose>.<channel>'")
        if t["id"] in templates:
            raise OutreachPolicyError(f"template {t['id']!r} listed twice")
        templates[t["id"]] = AutoTemplate(t["id"], t["purpose"], t["channel"], version)
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
    return OutreachPolicy(int(raw.get("version", 1)), set_version, str(raw.get("status", "")),
                          approver.strip() if isinstance(approver, str) else None, cap, language, templates, tuple(rules))


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


def signed_by(policy: OutreachPolicy) -> str | None:
    """Who the policy is signed by NOW: the name in the file, else `DEMO_APPROVER` while OUTREACH_DEMO_APPROVAL is on, else
    None (unsigned: the automation sends nothing). A real signature always wins over the demo switch."""
    from app.config import settings

    if policy.approved_by is not None:
        return policy.approved_by
    return DEMO_APPROVER if settings.outreach_demo_approval else None


def public_status(path: str | None = None) -> dict:
    """What `GET /health` says about automatic messaging (public, no patient data): whether the policy is signed, whether
    it only runs on the demo placeholder (the web shows an amber banner with `approved_by`), and whether anything can be
    sent at all. A policy that does not load is not usable: nothing is sent."""
    try:
        policy = get_policy(path)
    except OutreachPolicyError:
        return {"usable": False, "signed": False, "demo_approval": False, "approved_by": None, "automatic_sending": False}
    who = signed_by(policy)
    return {"usable": True, "signed": policy.signed, "demo_approval": who == DEMO_APPROVER, "approved_by": who,
            "automatic_sending": who is not None}


def auto_message_guard(event, path: str | None = None):
    """The append-time gate for an automatic `message_sent` (A21, ADR-032), as a store `guard(model)`: it runs inside the
    store's transaction, under the case lock, on the model replayed from the log as it stands, before the state machine
    sees the event, and raises InvalidTransition (nothing written) unless

    * the policy loads (otherwise nothing is sent: fail closed),
    * the policy is signed, or runs on the demo placeholder (A26: otherwise nothing is sent),
    * the template id is listed, at the version in the event, with the event's purpose and channel, and
    * the patient has had fewer than `max_auto_messages_per_week` OUTBOUND messages in the seven days before the event,
      counting both automatic messages and approved drafts (`model.outbound_message_times`).

    Consent for the channel and the outreach holds (opt-out, wrong number) are the state machine's, because the log alone
    decides them. A repeated event id is a retry, for which the store skips the guard. Not called during replay."""
    def guard(model) -> None:
        try:
            policy = get_policy(path)
        except OutreachPolicyError as exc:
            raise InvalidTransition(f"message_sent: the outreach policy is not usable: {exc}") from exc
        if signed_by(policy) is None:
            raise InvalidTransition(
                "message_sent: the outreach policy is not signed (approved_by is null): automatic messages are off "
                "until the clinical lead signs it")
        payload = event.payload
        template = policy.template_for(payload.get("template_id"))
        if template is None:
            raise InvalidTransition(
                f"message_sent: template {payload.get('template_id')!r} is not in the approved outreach policy")
        if payload.get("template_version") != template.version:
            raise InvalidTransition(
                f"message_sent: template {template.id!r} is approved at version {template.version}, "
                f"not {payload.get('template_version')!r}")
        if payload.get("purpose") != template.purpose or payload.get("channel") != template.channel:
            raise InvalidTransition(f"message_sent: purpose and channel must be those of template {template.id!r}")
        at = event.at if event.at.tzinfo else event.at.replace(tzinfo=UTC)
        recent = sum(1 for t in model.outbound_message_times
                     if at - AUTO_MESSAGE_WINDOW < (t if t.tzinfo else t.replace(tzinfo=UTC)) <= at)
        if recent >= policy.max_auto_messages_per_week:
            raise InvalidTransition(
                f"message_sent: the patient has had {recent} outbound messages (automatic or approved) in the last "
                f"7 days (the limit is {policy.max_auto_messages_per_week})")

    return guard
