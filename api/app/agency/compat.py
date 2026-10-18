"""Paths the agency may use when another service provides them (A22 alongside A21 and D13).

The agency never writes a case event. Two things it may cause are written by other services, under their own
actors and rules, and only when those services exist in this build:

* `handoff_requested {to_provider, auto}` by the automation service (A21, actor `system:automation`). Without it a
  reroute recommendation becomes a task for a person.
* `reply_classified {intent, confidence, model_id, needs_human}` by the reply classifier (D13). Without it, a
  person reads the reply.

Each is a hook: a callable registered by the providing service (`register_automation_handoff`,
`register_reply_classifier`), or found by convention (`app.automation.request_handoff`,
`app.triage.classify_reply`). The agency passes ids and labels only, never patient text, and treats whatever the
hook returns as data: a refusal or an exception is a task for a person, never a retry loop.

Event names the agency recognises by name (so it is compatible with those branches before they merge):
`reply_received`, `reply_classified` (D13); `system:automation` events `acknowledge {auto}`, `assign_ownership`,
`message_sent {auto, template_id}`, `closure_proposed`, `handoff_requested {auto}`, `review_requested
{reason: reroute_exhausted}` (A21).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

REPLY_RECEIVED = "reply_received"
REPLY_CLASSIFIED = "reply_classified"
CLOSURE_PROPOSED = "closure_proposed"
AUTOMATION_ACTOR = "system:automation"
REROUTE_EXHAUSTED = "reroute_exhausted"

_hooks: dict[str, Callable[..., Any] | None] = {"handoff": None, "classify": None}


def register_automation_handoff(fn: Callable[..., Any] | None) -> None:
    """`fn(case_id, to_provider, now) -> dict`: the automation service requests the handoff as itself."""
    _hooks["handoff"] = fn


def register_reply_classifier(fn: Callable[..., Any] | None) -> None:
    """`fn(case_id, reply_event_id, now) -> {intent, confidence, model_id, needs_human}`."""
    _hooks["classify"] = fn


def _by_convention(module: str, attr: str) -> Callable[..., Any] | None:
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return None
    fn = getattr(mod, attr, None)
    return fn if callable(fn) else None


def automation_handoff() -> Callable[..., Any] | None:
    return _hooks["handoff"] or _by_convention("app.automation", "request_handoff")


def reply_classifier() -> Callable[..., Any] | None:
    return _hooks["classify"] or _by_convention("app.triage", "classify_reply")


def automation_templates_available() -> bool:
    """True when the automation service sends templated reminders itself (A21): outreach then defers the plain
    template cases to it and drafts only where a template is not suitable."""
    return _by_convention("app.automation", "send_template_message") is not None or _hooks.get("templates") is True


def set_automation_templates(available: bool | None) -> None:
    """Tests and the merge wiring: say whether automation templates exist."""
    _hooks["templates"] = available
