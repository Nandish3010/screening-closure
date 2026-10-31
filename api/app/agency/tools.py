"""The agency's tools: thin wrappers over the functions the API routes call (A22, ADR-033).

The agency is preparation only. Every tool is a read (ids, refs, labels and counts from the same functions the read
routes use), except one: `create_draft`, a request through the drafting route's own code, named by the event that
path writes (`causes`: `message_drafted`, actor `model:<provider>`), whose draft waits for a person's approval. A tool that
causes something is checked against `app.policy.authorize_agency` for the calling agent every time it is called,
so an agent's allow-list here and its policy entry there must both admit it. There is no tool that writes a case
event as the agency, and none that closes, reviews, approves, accepts, reassigns or signs: the state machine would
refuse the agency actor anyway (`AGENCY_ACTOR_PREFIX`).

`ToolBox` is what an agent holds: its identity, its allow-list, and the record of every call (name, the ids it was
given, whether it worked, attempts). A tool that fails transiently is tried at most twice in all; any other error
is returned to the agent, which ends its task as `failed` or `needs_human`. No loops.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException

from app import policy
from app.agency import config as agency_config
from app.agency.facts import CaseFacts, case_facts, facts_for
from app.auth import Identity

log = logging.getLogger("app.agency.tools")

MAX_ATTEMPTS = 2  # a transient failure is retried once; never more


class ToolDenied(Exception):
    """The tool is not on the agent's allow-list, or the policy refuses what it would cause."""


class ToolFailed(Exception):
    """The tool ran and could not do its job (a refusal from the path it uses, a missing service)."""


class ToolTransient(ToolFailed):
    """Worth one more try (the evidence server did not answer)."""


@dataclass(frozen=True)
class Tool:
    name: str
    causes: str | None  # None: read only. Else the event the path it uses writes (under that path's actor)
    fn: Callable
    description: str


# ---------------------------------------------------------------- the reads


def _cases():
    from app.routers import cases  # late: the routers import the agency's router module

    return cases


class Worklist(list):
    """The facts a bounded read returned, plus where it stopped: `through` is the `(updated_at, case_id)` keyset position
    of the last case read and `truncated` says more cases follow (the cap was hit)."""

    through: tuple | None = None
    truncated: bool = False


def read_worklist(ctx) -> Worklist:
    """Case facts in worklist order (case id): `GET /cases`, BOUNDED (M7-7). At most `AGENCY_MAX_CASES_READ` cases, however big
    the table is (a SQL-side LIMIT, not a read of every case): `truncated` says the window did not cover all of them. The old
    unbounded read built the facts of every case and exhausted an API pod's memory at 873,000 open cases."""
    c = _cases()
    cap = agency_config.max_cases_read()
    page, after = c._worklist_page(None, None, cap, None)  # every loaded pathway, in case-id order
    out = Worklist()
    for lc in page:
        f = case_facts(lc, c.pathway_by_name(lc.pathway) or c.pathway)
        if f is not None:
            out.append(f)
    out.truncated = after is not None
    return out


def read_active_cases(ctx, since: datetime | None, after: tuple | None = None) -> Worklist:
    """The supervisor's bounded read: facts only for cases changed at or after `since` (the previous run's sim time; every case
    when there was no previous run), oldest change first, AT MOST `AGENCY_MAX_CASES_READ` of them per run (M7-7). The set is a
    SQL query on `cases.updated_at` with a LIMIT and a keyset position (`after`), so memory and time are bounded by the cap,
    not by the table. When the changed set is bigger than the cap the result says so (`truncated`) and where it stopped
    (`through`); the runner then resumes from that position on its next run instead of skipping the rest.

    Before M7-7 this also replayed EVERY open case to add the ones that are overdue or watchdog-flagged. A case becomes overdue or
    flagged by an event (`mark_overdue`, `watchdog_flagged`) which moves its `updated_at`, so a newly overdue or flagged case is
    in the changed set; an unchanged one was routed when it changed, and its tasks are deduplicated against the log
    (`runner._recent`)."""
    c = _cases()
    cap = agency_config.max_cases_read()
    keys, more = c.store.cases_changed_page(since, after, cap)
    wanted = [case_id for _, case_id in keys]
    truncated = more
    if after is None and not more and since is not None:
        # Small tables only (at most AGENCY_METRICS_MAX_CASES cases): also the open cases that are overdue or watchdog-flagged
        # but did not change since `since`, found by replaying every open case as before. That replay is O(open cases), so past
        # that size it is not done; the changed set above is the read. The cap applies to the two together.
        if sum(c.store.state_counts(c.pathway).values()) <= agency_config.metrics_max_cases():
            have = set(wanted)
            extra = sorted((cid for pw in c.all_pathways() for cid, model, _ in c.store.iter_open_case_models(pw)
                            if model is not None and (model.any_overdue or model.watchdog_flag is not None) and cid not in have),
                           key=lambda i: i.int)
            wanted += extra[: max(cap - len(wanted), 0)]
            truncated = len(extra) > max(cap - len(keys), 0)
    out = Worklist()
    for case_id in wanted:
        f = facts_for(case_id, c.store, c.pathway_or_default(case_id))
        if f is not None:
            out.append(f)
    out[:] = sorted(out, key=lambda f: (f.history[0].at if f.history else "", f.case_id))  # worklist order, as before
    out.truncated = truncated
    out.through = keys[-1] if (more and keys) else None
    return out


def _aware(dt: datetime) -> datetime:
    from datetime import UTC

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def read_case(ctx, case_id: UUID) -> CaseFacts:
    c = _cases()
    f = facts_for(case_id, c.store, c.pathway_or_default(case_id))
    if f is None:
        raise ToolFailed("case not found or does not replay")
    return f


def read_drafts(ctx, case_id: UUID) -> list[dict]:
    """The case's drafts as decisions, without their text: `GET /cases/{id}/drafts` minus the message."""
    from app.routers import drafts

    out = []
    for d in drafts.draft_store().list_for_case(case_id):
        decided = "approved" if d.approved_at else ("rejected" if d.rejected_at else "pending")
        out.append({"draft_id": str(d.draft_id), "purpose": d.purpose, "channel": d.channel, "language": d.language,
                    "provider": d.provider, "fallback_used": d.fallback_used, "created_by": d.created_by,
                    "decision": decided, "decided_by": d.approved_by or d.rejected_by})
    return out


def read_notifications(ctx, case_id: UUID) -> list[dict]:
    """The case's notification log without bodies: `GET /cases/{id}/notifications` minus the text."""
    from app import notify

    return [{"channel": n.channel, "language": n.language, "purpose": n.purpose, "status": n.status,
             "reason": n.reason, "at": n.created_at.isoformat()} for n in notify.log.for_case(case_id)]


def read_metrics(ctx) -> dict:
    """The closure metrics the dashboard reads (`GET /metrics/closure`), as counts."""
    from app.routers import metrics as metrics_router

    # The closure metrics replay every case (p50/p90 need them all). Past a size the agency does not ask: it would hold the
    # whole table in memory. The runner only echoes two counts of it, and gets them from the SQL aggregate instead.
    c = _cases()
    counts = c.store.state_counts(c.pathway)
    total = sum(counts.values())
    if total > agency_config.metrics_max_cases():
        closed = counts.get(c.pathway.terminal_state, 0)
        return {"cases_total": total, "cases_open": total - closed, "cases_closed": closed, "truncated": True}
    body = metrics_router.closure()
    keep = ("sim_time", "cases_total", "cases_open", "cases_closed", "unsafe_closures", "p50_days", "p90_days",
            "open_by_age_bucket", "cases_flagged", "lost_to_follow_up", "lost_never_surfaced", "replay_failures")
    return {k: body[k] for k in keep if k in body}


def read_alerts(ctx) -> list[dict]:
    """Open system alerts: `GET /alerts?active=true`."""
    from app.routers import watchdog

    return [{"kind": a["kind"], "since": a["since"]} for a in watchdog.alerts(active=True)]


def read_abstention(ctx) -> dict:
    """The drafting abstention split by language and channel: `GET /metrics/abstention`."""
    from app.routers import drafts

    return drafts.abstention_metric()


def read_ledger(ctx, status: str | None = None, agent_type: str | None = None) -> list[dict]:
    """The agency's own earlier work (status, type, case), for the quality checks."""
    return [{"task_id": str(t.id), "agent_type": t.agent_type, "status": t.status,
             "case_id": str(t.case_id) if t.case_id else None}
            for t in ctx.ledger.query(status=status, agent_type=agent_type)]


def find_evidence(ctx, case_id: UUID, refs: list[str]) -> dict:
    """Ask the closure-evidence verifier (the same check `close` runs, A15 and A24) which candidate references are
    complete closure evidence for this case. Read only: it never creates a resource. With verification switched off
    nothing is verified."""
    from app import evidence

    c = _cases()
    events = c.store.events_for(case_id)
    patient_ref = events[0].payload.get("patient_ref") if events else None
    if isinstance(evidence.verifier, evidence.AcceptAllVerifier):
        return {"verification": "off", "found": [], "unverified": list(refs)}
    found = []
    for ref in refs:
        if not evidence.well_formed_ref(ref):
            continue
        try:
            if evidence.verifier.assess(ref, case_id=case_id, patient_ref=patient_ref).acceptable:
                found.append(ref)
        except evidence.EvidenceUnavailable as exc:
            raise ToolTransient(f"evidence server: {exc}") from None
    return {"verification": "on", "found": found, "unverified": []}


def explain_factors(ctx, case_id: UUID) -> dict | None:
    """The worklist ordering aid's top factors for one case, as `GET /cases/{id}` shows staff. It feeds a sentence
    for a person and nothing else: no plan, dispatch or decision reads it (T-D10, tests/test_agency.py)."""
    return _cases().factors_for_agency(case_id)


def read_providers(ctx) -> list[str]:
    from app.agency import config as agency_config

    return list(agency_config.providers())


# ---------------------------------------------------------------- the requests through another actor's path


def create_draft(ctx, case_id: UUID, purpose: str, channel: str, language: str) -> dict:
    """Ask for a model draft through the drafting route's own code (`POST /cases/{id}/drafts`): the provider, the
    validator (`ai.validate`), the template fallback, the Provenance, the consent and closed-case refusals and the
    `message_drafted` event (actor `model:<provider>`) are all that route's. The draft then waits for the case's
    owner to approve or reject it; nothing is sent without that. The agency is recorded as the requester."""
    from app.routers import drafts

    try:
        body = drafts.CreateDraft(purpose=purpose, channel=channel, language=language)
        out = drafts.create_draft(case_id, body, ctx.identity)
    except HTTPException as exc:
        raise ToolFailed(f"drafting refused: HTTP {exc.status_code}: {exc.detail}") from None
    record = drafts.draft_store().get(UUID(out["draft_id"]))
    return {**{k: out[k] for k in ("draft_id", "provider", "fallback_used", "abstention_reason", "prompt_hash",
                                   "language")}, "model_id": record.model_id if record else out["provider"]}


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("read_worklist", None, read_worklist, "every case's facts (ids, states, clocks, counts)"),
    Tool("read_case", None, read_case, "one case's facts"),
    Tool("read_active_cases", None, read_active_cases, "facts of the cases changed since a time, plus overdue and flagged"),
    Tool("read_drafts", None, read_drafts, "a case's drafts and their decisions, without text"),
    Tool("read_notifications", None, read_notifications, "a case's notification log, without bodies"),
    Tool("read_metrics", None, read_metrics, "closure metrics (counts)"),
    Tool("read_alerts", None, read_alerts, "open system alerts"),
    Tool("read_abstention", None, read_abstention, "drafting abstention by language and channel"),
    Tool("read_ledger", None, read_ledger, "the agency's own task ledger"),
    Tool("read_providers", None, read_providers, "the receiving providers for referrals"),
    Tool("find_evidence", None, find_evidence, "which closure-evidence references resolve (read only)"),
    Tool("explain_factors", None, explain_factors, "the worklist ordering aid's top factors (for a sentence)"),
    Tool("create_draft", "message_drafted", create_draft, "a model draft through the drafting route, for approval"),
)}


@dataclass
class ToolContext:
    identity: Identity
    now: datetime
    ledger: object


class ToolBox:
    """An agent's tools: only the ones on its allow-list, each call recorded."""

    def __init__(self, identity: Identity, allowed: tuple[str, ...], now: datetime, ledger) -> None:
        unknown = [n for n in allowed if n not in TOOLS]
        if unknown:
            raise ValueError(f"unknown tools {unknown}")
        self.allowed = tuple(allowed)
        self.ctx = ToolContext(identity, now, ledger)
        self.calls: list[dict] = []

    def call(self, name: str, **kwargs):
        if "case_id" in kwargs and kwargs["case_id"] is not None and not isinstance(kwargs["case_id"], UUID):
            kwargs["case_id"] = UUID(str(kwargs["case_id"]))  # orders carry ids as strings
        record = {"tool": name, "args": {k: _ref(v) for k, v in kwargs.items()}, "ok": False, "attempts": 0}
        self.calls.append(record)
        if name not in self.allowed:
            record["error"] = "not on this agent's allow-list"
            raise ToolDenied(f"{self.ctx.identity.actor} may not use {name!r}")
        tool = TOOLS[name]
        if tool.causes is not None:
            try:
                policy.authorize_agency(self.ctx.identity, tool.causes)
            except HTTPException as exc:
                record["error"] = f"policy: {exc.detail}"
                raise ToolDenied(str(exc.detail)) from None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            record["attempts"] = attempt
            try:
                result = tool.fn(self.ctx, **kwargs)
            except ToolTransient as exc:
                record["error"] = str(exc)
                if attempt == MAX_ATTEMPTS:
                    raise
                continue
            except ToolFailed as exc:
                record["error"] = str(exc)
                raise
            record["ok"] = True
            record.pop("error", None)
            return result
        raise ToolFailed("unreachable")  # pragma: no cover


def _ref(value):
    """What a call record keeps of an argument: ids, short codes and lists of them."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_ref(v) for v in value][:20]
    if isinstance(value, str):
        return value[:100]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__
