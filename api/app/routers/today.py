"""The coordinator's Today screen (C14): `GET /today`, what needs a person now, in seven queues.

    GET /today?limit=&cursor=&agency_cursor=&group=   coordinator, supervisor

Bounded by construction (docs/results/scale.md says why an unbounded read is not allowed):

* The six case queues come from ONE keyset scan of the OPEN cases only (a state filter, so the closed cases that make up most
  of a large table are never read), `SCAN_BUDGET` cases at most per request, `PAGE` at a time, and it stops as soon as any
  requested queue holds `limit` items. No replay of the whole table, no per-case request; each case read is one replay the
  worklist does anyway.
* `cursor` (the answer's `cursor`) continues that scan; it is opaque. A queue that is empty while `complete` is false has not
  been found empty yet: the web says "nothing found so far" and offers to look further, never "all clear".
* The agency queue is a bounded read of the ledger (`needs_human` tasks, newest first) paged by `agency_cursor`.

Read-only. It decides nothing and appends nothing; the one-click actions the web offers call the routes that exist
(`POST /cases/{id}/events`), under their own RBAC and audit trail. Membership of each queue is `app.today.memberships`, a pure
function of the replayed read model (the same one `GET /cases` shows).
"""

from __future__ import annotations

import base64
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app import today
from app.agency import runner
from app.agency.ledger import NEEDS_HUMAN
from app.auth import Identity, require_identity
from app.calendar import default_calendar
from app.clock import clock
from app.routers import cases
from app.triage import dialect

log = logging.getLogger("app.today")
router = APIRouter(tags=["today"])

DEFAULT_LIMIT = 10  # items per queue in a page
MAX_LIMIT = 50
SCAN_BUDGET = 1000  # open cases read for ONE request, across all queues
PAGE = 200  # cases loaded per store call


def _encode(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _decode(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        obj = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        return obj
    except Exception:  # noqa: BLE001 - any malformed value is a client error
        raise HTTPException(422, "cursor is not one returned by this endpoint")


def _plan() -> list[tuple]:
    """The scan order: each loaded pathway's open states, one SQL-filtered keyset walk per (pathway, state)."""
    return [(pw, state) for pw in cases.all_pathways() for state in pw.open_states]


def _start(cursor: str | None, plan: list[tuple]) -> tuple[int, UUID | None]:
    obj = _decode(cursor)
    if obj is None:
        return 0, None
    try:
        idx = next(i for i, (pw, s) in enumerate(plan) if pw.name == obj["p"] and s == obj["s"])
        return idx, (UUID(obj["a"]) if obj.get("a") else None)
    except (StopIteration, KeyError, ValueError, TypeError):
        raise HTTPException(422, "cursor is not one returned by this endpoint")


def _scan(wanted: set[str], limit: int, cursor: str | None) -> tuple[dict[str, list[dict]], int, str | None]:
    """`({group: items}, cases read, next cursor or None when the open cases are all read)`."""
    plan = _plan()
    i, after = _start(cursor, plan)
    calendar, now = default_calendar(), clock.now()
    found: dict[str, list[dict]] = {g: [] for g in wanted}
    scanned = 0
    while i < len(plan):
        size = min(PAGE, SCAN_BUDGET - scanned)
        if size <= 0:
            return found, scanned, _encode({"p": plan[i][0].name, "s": plan[i][1], "a": str(after) if after else None})
        pw, state = plan[i]
        page, nxt = cases.store.load_cases_page(pw, state, after, size, None)
        for lc in page:
            scanned += 1
            for group, item in today.memberships(lc, cases.pathway_by_name(lc.pathway) or pw, calendar, now).items():
                if group in wanted:
                    found[group].append(item)
            if any(len(items) >= limit for items in found.values()):  # a queue is full: stop here, resume after this case
                return found, scanned, _encode({"p": pw.name, "s": state, "a": str(lc.case_id)})
        if nxt is None:
            i, after = i + 1, None
        else:
            after = nxt
    return found, scanned, None


def _reply_detail(case_id: str, reply: dict) -> dict:
    """What the web shows of a reply that is with a person: why (the triage flag, a call-back), how sure the model was and
    whether the Gulf-dialect/Arabizi rule is why a person reads it. Never the text; it stays behind `GET /cases/{id}/replies`."""
    cls = reply.pop("classification", None)
    out = {**reply, "intent": None, "confidence": None, "forced_reason": None}
    if not cls:
        return out
    out["intent"], out["confidence"] = cls.get("intent"), cls.get("confidence")
    try:
        record = cases_reply_store().get(UUID(str(cls["text_ref"])))
        if record is not None and record.case_id == UUID(case_id):
            out["forced_reason"] = dialect.group_reason(
                {"intent": cls.get("intent"), "confidence": cls.get("confidence"), "needs_human": True}, record.text)
    except Exception:  # noqa: BLE001 - a display detail must never take the screen down
        log.warning("reply detail unavailable for case %s", case_id, exc_info=True)
    return out


def cases_reply_store():
    from app.routers import replies

    return replies.reply_store()


def _agency_page(limit: int, cursor: str | None) -> tuple[list[dict], str | None]:
    obj = _decode(cursor) or {}
    offset = obj.get("o", 0)
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(422, "cursor is not one returned by this endpoint")
    # the ledger keeps the newest `offset + limit + 1` tasks (oldest first): bounded by the page asked for, not by the ledger
    newest_first = list(reversed(runner.ledger().query(status=NEEDS_HUMAN, limit=offset + limit + 1)))
    page, more = newest_first[offset:offset + limit], len(newest_first) > offset + limit
    items = []
    for t in page:
        out = t.output or {}
        item = {
            "task_id": str(t.id), "case_id": str(t.case_id) if t.case_id else None, "department": t.department,
            "agent_type": t.agent_type, "assignee_role": out.get("assignee_role") or "coordinator",
            "title": out.get("title") or t.agent_type, "summary": out.get("summary", ""),
            "recommended_action": out.get("recommended_action") or "review", "since": t.created_at.isoformat(),
            "pathway": None, "state": None, "priority": None, "actions": ["open_case"] if t.case_id else [],
        }
        if t.case_id is not None:  # one case read per item shown (bounded by `limit`)
            try:
                pw = cases.pathway_or_default(t.case_id)
                events = cases.store.events_for(t.case_id)
                item["pathway"] = cases.store.pathway_name_of(t.case_id)
                item["state"] = cases.store.current_state(t.case_id)
                prio = cases.readmodel.priority_of(events, pw) if events else None
                item["priority"] = prio
            except Exception:  # noqa: BLE001
                log.warning("case detail unavailable for agency task %s", t.id, exc_info=True)
        items.append(item)
    return items, (_encode({"o": offset + limit}) if more else None)


@router.get("/today")
def get_today(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="items per queue (default 10, at most 50)"),
    cursor: str | None = Query(None, description="the `cursor` of the previous answer: continues the scan of open cases"),
    agency_cursor: str | None = Query(None, description="the `agency_cursor` of the previous answer"),
    group: list[str] | None = Query(None, description="only these queues (default: all seven)"),
    identity: Identity = Depends(require_identity),
) -> dict:
    """What needs a person now, in seven queues (`urgent`, `overdue`, `escalations`, `replies`, `flagged`, `signatures`, `agency`).

    `groups` is the list of queues in that order, each `{group, items, count, more}`. `count` is the number of items in THIS
    answer; `more` says the queue may hold further items (for a case queue: the scan of open cases is not complete). Send the
    answer's `cursor` back to read on (and `group=` to ask only for the queues you are paging). `complete` is true when every open
    case has been read: only then is an empty queue "all clear". At most `SCAN_BUDGET` (1000) open cases are read per request."""
    wanted = list(dict.fromkeys(group)) if group else list(today.GROUPS)
    unknown = [g for g in wanted if g not in today.GROUPS]
    if unknown:
        raise HTTPException(422, f"unknown group {unknown[0]!r}; known: {list(today.GROUPS)}")
    case_wanted = {g for g in wanted if g in today.CASE_GROUPS}
    found: dict[str, list[dict]] = {}
    scanned, next_cursor = 0, None
    if case_wanted:
        found, scanned, next_cursor = _scan(case_wanted, limit, cursor)
        for items in found.values():
            items.sort(key=today.order_key)
        for item in found.get("replies", []):
            item["reply"] = _reply_detail(item["case_id"], item["reply"])
    agency_items, agency_next = ([], None)
    if today.AGENCY_GROUP in wanted:
        agency_items, agency_next = _agency_page(limit, agency_cursor)
    groups = []
    for g in today.GROUPS:
        if g not in wanted:
            continue
        if g == today.AGENCY_GROUP:
            groups.append({"group": g, "items": agency_items, "count": len(agency_items), "more": agency_next is not None})
        else:
            groups.append({"group": g, "items": found[g], "count": len(found[g]), "more": next_cursor is not None})
    return {
        "sim_time": clock.now().isoformat(), "limit": limit, "scanned": scanned, "cursor": next_cursor,
        "complete": next_cursor is None, "agency_cursor": agency_next, "groups": groups,
    }
