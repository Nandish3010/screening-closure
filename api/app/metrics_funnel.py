"""The outcome funnel (ADR-052): the screening loop in five steps, from a table, not from a replay of every case.

The programme's loop is: record the result, prioritise the higher-risk people, refer them to the appropriate service, remind and
navigate until they attend, and track the outcome. `GET /metrics/funnel` counts how many cases reached each step, how many of the
previous step that is, and the median time between steps on the simulated clock.

* Result recorded: the case exists (`case_created`, the day the result arrived). Every case of every loaded pathway.
* Prioritised: a result priority tier was recorded at intake (ADR-051). A pack with no priority rules (the bowel pack, today) records
  none, so those cases are in the first step and not in the second. Ranking does not gate anything in the engine (it feeds no state,
  SLA or closure), so the steps after it are counted against the result recorded, not against the prioritised.
* Referred: the first `handoff_requested`, or the first `book_confirmatory`, whichever the log shows first.
* Attended: the first `complete_confirmatory` (the confirmatory procedure or visit took place and was recorded).
* Outcome documented: the first SAFE close that is not an informed refusal: a human, an `evidence_ref` and a `signed_by`, the same
  rule as `closures_measured` in `GET /metrics/closure`. A case closed on an informed refusal (`outcome: declined`) is counted
  apart (`declined_closures`): the person decided, nothing was attended, and it is not a completed investigation.
* At risk of being lost: no confirmatory procedure completed, no refusal on record, and either the contact marked lost or no
  pathway progress for `metrics.LTFU_IDLE_DAYS` days at the simulated "now". The SAME rule as `lost_to_follow_up` in
  `GET /metrics/closure`, so the two never disagree on the default pathway.

Where the numbers come from. `case_funnel` (migration 020) holds one row per case of every loaded pathway; `refresh` brings it up to
date with the discipline of `metrics_index.refresh` (ADR-049): it replays only the cases whose `cases.updated_at` is newer than the
row, 200 per batch, one transaction per batch, inside a time budget, from a durable cursor per pathway, under an advisory lock of its
own. The funnel is then SQL aggregates over the table (one pass for the counts, a sort and an offset for each median, one bounded
list for the cases at risk). `legacy_funnel` keeps the replay as the reference the tests compare with and as the answer for stores
that have no table (the in-memory store of the unit tests). The medians are of the stored day counts, computed once with
`metrics._days`, so the two paths give the same numbers bit for bit.

What the funnel does not say. It counts cases that reached a step, not people helped; the time between steps is of the cases that
reached both ends (a case still waiting is not in the sample, so the medians understate the slow ones, as the bottleneck chart's
do); and nothing here is an outcome of treatment. All data are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from statistics import median

from app import metrics, metrics_index
from app.state_machine import InvalidTransition, replay_model

log = logging.getLogger("app.metrics_funnel")

FUNNEL_VERSION = "1"  # bump when fold_case changes what it stores
BATCH = 200
LOOKBACK_SECONDS = 120
LOCK_KEY = 727_019  # not metrics_index.LOCK_KEY: the two refreshes do not wait for each other
UNRANKED = "unranked"  # the value of the priority filter that means "no tier was recorded"
AT_RISK_LIMIT = 10  # the list of cases at risk is capped; the count is exact
_EPOCH = metrics_index._EPOCH
NIL = metrics_index.NIL

# The five steps, in her words (the head of screening's description of the loop). `from` is the step the conversion and the
# time are measured from. Prioritised has no time: the tier is derived in the same event that records the result.
STEPS = (
    {"key": "recorded", "label": "Result recorded", "from": None,
     "definition": "A screening result is recorded and linked to the person: a case exists."},
    {"key": "prioritised", "label": "Higher-risk people prioritised", "from": "recorded",
     "definition": "A result priority tier was recorded at intake. Only pathways with priority rules record one."},
    {"key": "referred", "label": "Referred to the appropriate service", "from": "recorded",
     "definition": "A referral to the service was requested, or the appointment was booked, whichever came first."},
    {"key": "attended", "label": "Attended", "from": "referred",
     "definition": "The confirmatory procedure or visit took place and was recorded, after reminders and navigation."},
    {"key": "documented", "label": "Outcome documented", "from": "attended",
     "definition": "Closed with evidence: a person signed the close and the report is on record. Informed refusals are counted apart."},
)
_TIME_COLUMN = {"referred": "d_referred", "attended": "d_attended", "documented": "d_documented"}


def signature(pathway) -> str:
    """What a stored row depends on besides its own log: the fold and the pathway."""
    basis = json.dumps({"fold": FUNNEL_VERSION, "pathway": hashlib.sha1(repr(pathway).encode()).hexdigest()}, sort_keys=True)
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def fold_case(events, pathway) -> dict:
    """One case's row of `case_funnel`, or `{"ok": False, "error": ...}` when the log does not replay."""
    try:
        replay_model(events, pathway)
    except InvalidTransition as exc:
        return {"ok": False, "error": str(exc)}
    return row_of(metrics.build_record(events, pathway))


def row_of(rec) -> dict:
    """The funnel facts of a `metrics.CaseRecord`: the instants of the steps and the days between them."""
    safe = rec.first_safe_close_at
    documented = safe if safe is not None and not rec.first_safe_close_declined else None
    declined = safe if safe is not None and rec.first_safe_close_declined else None
    return {
        "ok": True, "error": None, "state": rec.state, "t0": rec.t0, "priority": rec.priority,
        "referred_at": rec.referred_at, "attended_at": rec.attended_at, "documented_at": documented, "declined_at": declined,
        "d_referred": metrics._days(rec.t0, rec.referred_at) if rec.referred_at is not None else None,
        "d_attended": (metrics._days(rec.referred_at, rec.attended_at)
                       if rec.referred_at is not None and rec.attended_at is not None else None),
        "d_documented": (metrics._days(rec.attended_at, documented)
                         if rec.attended_at is not None and documented is not None else None),
        "completed": rec.completed_confirmatory, "refusal_on_record": rec.refusal_on_record, "lost_flag": rec.lost_contact_flag,
        "surfaced": rec.surfaced, "last_progress_at": rec.last_progress_at,
    }


_COLS = ("state", "t0", "priority", "referred_at", "attended_at", "documented_at", "declined_at", "d_referred", "d_attended",
         "d_documented", "completed", "refusal_on_record", "lost_flag", "surfaced", "last_progress_at")
_UPSERT = (
    "INSERT INTO case_funnel (case_id, src_updated_at, sig, pathway, ok, error, " + ", ".join(_COLS) + ") VALUES ("
    + ", ".join(["%s"] * (6 + len(_COLS))) + ") ON CONFLICT (case_id) DO UPDATE SET src_updated_at = EXCLUDED.src_updated_at,"
    " sig = EXCLUDED.sig, pathway = EXCLUDED.pathway, ok = EXCLUDED.ok, error = EXCLUDED.error, "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS)
)


def _state_key(pathway) -> str:
    return f"funnel:{pathway.name}"


def _refresh_pathway(pool, pathway, deadline: float, batch: int, monotonic) -> dict:
    key, sig = _state_key(pathway), signature(pathway)
    out = {"replayed": 0, "batches": 0, "caught_up": False, "busy": False, "lag": None}
    pos = None
    while True:
        with pool.connection() as conn, conn.transaction():
            if not conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
                out["busy"] = True
                return out
            state = metrics_index._read_state(conn, key)
            cursor = state.get("cursor") if state.get("sig") == sig else None  # another signature: start again from the top
            if pos is None:
                pos = (datetime.fromisoformat(cursor[0]) - timedelta(seconds=LOOKBACK_SECONDS), NIL) if cursor else (_EPOCH, NIL)
            rows = conn.execute(
                "SELECT id, updated_at FROM cases WHERE pathway = %s AND (updated_at, id) > (%s, %s)"
                " ORDER BY updated_at, id LIMIT %s", (pathway.name, pos[0], pos[1], batch)).fetchall()
            if not rows:
                out["caught_up"] = True
                if cursor is None and state.get("sig") != sig:
                    metrics_index._write_state(conn, {"sig": sig, "cursor": None}, key)
                return out
            ids = [r[0] for r in rows]
            have = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT case_id, src_updated_at, sig FROM case_funnel WHERE case_id = ANY(%s)", (ids,)).fetchall()}
            todo = [(cid, upd) for cid, upd in rows if have.get(cid) != (upd, sig)]
            if todo:
                by_case: dict = {}
                for r in conn.execute(
                        "SELECT case_id, seq, event_id, type, at, actor, payload FROM events WHERE case_id = ANY(%s)"
                        " ORDER BY case_id, seq", ([c for c, _ in todo],)).fetchall():
                    by_case.setdefault(r[0], []).append(r[1:])
                for cid, upd in todo:
                    raw = by_case.get(cid)
                    if not raw:
                        continue  # a case with no events is not a case of the reports
                    events = metrics_index._as_events(raw, cid)
                    row = fold_case(events, pathway)
                    conn.execute(_UPSERT, (cid, upd, sig, pathway.name, row["ok"], row["error"], *[row.get(c) for c in _COLS]))
                out["replayed"] += len(todo)
            last = rows[-1]
            pos = (last[1], last[0])
            if not cursor or (last[1].isoformat(), str(last[0])) > (cursor[0], cursor[1]):
                metrics_index._write_state(conn, {"sig": sig, "cursor": [last[1].isoformat(), str(last[0])]}, key)
            out["batches"] += 1
        if monotonic() >= deadline:
            with pool.connection() as conn:
                out["lag"] = conn.execute(
                    "SELECT count(*) FROM cases WHERE pathway = %s AND (updated_at, id) > (%s, %s)",
                    (pathway.name, pos[0], pos[1])).fetchone()[0]
            return out


def refresh(store, pathways, *, budget_seconds: float, batch: int = BATCH, monotonic=time.monotonic) -> dict:
    """Bring `case_funnel` up to date for every pathway, at most `budget_seconds` in all. Returns `{"replayed", "batches",
    "caught_up", "busy", "lag"}` summed over the pathways; `lag` is the number of cases still ahead of the cursors when the budget
    ran out (None when caught up). Safe to call from every API pod (one batch at a time holds an advisory lock)."""
    deadline = monotonic() + max(0.0, budget_seconds)
    total = {"replayed": 0, "batches": 0, "caught_up": True, "busy": False, "lag": None}
    for pathway in pathways:
        r = _refresh_pathway(store._pool, pathway, deadline, batch, monotonic)
        total["replayed"] += r["replayed"]
        total["batches"] += r["batches"]
        total["caught_up"] = total["caught_up"] and r["caught_up"]
        total["busy"] = total["busy"] or r["busy"]
        if r["lag"] is not None:
            total["lag"] = (total["lag"] or 0) + r["lag"]
        if r["busy"] or r["lag"] is not None:
            total["caught_up"] = False
    return total


# --------------------------------------------------------------------------------------------------- the report

def _where(pathway: str | None, priority: str | None) -> tuple[str, dict]:
    """The filter as SQL over fixed text (the values travel as parameters)."""
    clauses, args = ["ok"], {}
    if pathway is not None:
        clauses.append("pathway = %(pathway)s")
        args["pathway"] = pathway
    if priority == UNRANKED:
        clauses.append("priority IS NULL")
    elif priority is not None:
        clauses.append("priority = %(priority)s")
        args["priority"] = priority
    return " AND ".join(clauses), args


_AT_RISK = ("NOT completed AND NOT refusal_on_record AND (lost_flag OR extract(epoch FROM (%(now)s - coalesce(last_progress_at, t0)))"
            " / 86400 >= %(ltfu)s)")


def _median_of(conn, col: str, where: str, args: dict) -> tuple[int, float | None]:
    """`(n, median)` of the non-null `col` over the matching rows, as `statistics.median` computes it: the middle value, or the mean of
    the two middle ones as `(a + b) / 2`. A count, then a sort and an offset to the middle: the database never hands over the column."""
    n = conn.execute(f"SELECT count({col}) FROM case_funnel WHERE {where}", args).fetchone()[0]
    if not n:
        return 0, None
    lo = (n - 1) // 2
    xs = [r[0] for r in conn.execute(
        f"SELECT {col} FROM case_funnel WHERE {where} AND {col} IS NOT NULL ORDER BY {col} OFFSET %(off)s LIMIT %(lim)s",
        {**args, "off": lo, "lim": 1 if n % 2 else 2}).fetchall()]
    return n, xs[0] if n % 2 else (xs[0] + xs[1]) / 2


def _at_risk_item(case_id, pathway, priority, state, last_progress, t0, lost_flag, now) -> dict:
    since = last_progress or t0
    return {
        "case_id": str(case_id), "pathway": pathway, "priority": priority, "state": state,
        "idle_days": round(metrics._days(since, now), 1),
        "reason": "contact_lost" if lost_flag else "no_progress",
    }


def funnel_report(store, now: datetime, *, pathway: str | None = None, priority: str | None = None,
                  timeout_ms: int = 60000, at_risk_limit: int = AT_RISK_LIMIT) -> dict:
    """The counts, medians, declined count, cases at risk and failures, from the table, for the filter given. Shaped by `shape`."""
    where, args = _where(pathway, priority)
    args_now = {**args, "now": now, "ltfu": metrics.LTFU_IDLE_DAYS}
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        c = conn.execute(
            f"SELECT count(*), count(priority), count(referred_at), count(attended_at), count(documented_at), count(declined_at),"
            f" count(*) FILTER (WHERE {_AT_RISK}) FROM case_funnel WHERE {where}", args_now).fetchone()
        medians = {step: _median_of(conn, col, where, args) for step, col in _TIME_COLUMN.items()}
        rows = conn.execute(
            f"SELECT case_id, pathway, priority, state, last_progress_at, t0, lost_flag FROM case_funnel WHERE {where} AND {_AT_RISK}"
            f" ORDER BY coalesce(last_progress_at, t0), case_id LIMIT %(lim)s", {**args_now, "lim": at_risk_limit}).fetchall()
        # failures are those of the pathway filter alone: a log that does not replay has no priority to filter by
        pw_clause, pw_args = ("AND pathway = %(pathway)s", {"pathway": pathway}) if pathway is not None else ("", {})
        failed = conn.execute(f"SELECT count(*) FROM case_funnel WHERE NOT ok {pw_clause}", pw_args).fetchone()[0]
    counts = dict(zip(("recorded", "prioritised", "referred", "attended", "documented"), c[:5]))
    return shape(counts, medians, declined=c[5], at_risk=c[6],
                 at_risk_cases=[_at_risk_item(*r, now) for r in rows], failed=failed, at_risk_limit=at_risk_limit)


def shape(counts: dict, medians: dict, *, declined: int, at_risk: int, at_risk_cases: list, failed: int, at_risk_limit: int) -> dict:
    """The body of `GET /metrics/funnel` minus the filters, options and `sim_time`. `medians` maps a step to `(n, median days)`."""
    steps = []
    for spec in STEPS:
        key, count = spec["key"], counts[spec["key"]]
        prev = counts[spec["from"]] if spec["from"] else None
        n, med = medians.get(key, (0, None))
        steps.append({
            "key": key, "label": spec["label"], "definition": spec["definition"], "count": count,
            "share_of_recorded": round(count / counts["recorded"], 4) if counts["recorded"] else None,
            "from": spec["from"],
            "conversion": round(count / prev, 4) if prev else None,
            "median_days": med, "median_n": n,
        })
    return {
        "steps": steps,
        "declined_closures": declined,
        "at_risk": {
            "count": at_risk, "idle_days_threshold": metrics.LTFU_IDLE_DAYS,
            "cases": at_risk_cases, "truncated": at_risk > len(at_risk_cases), "limit": at_risk_limit,
        },
        "replay_failures": {"count": failed},
    }


# ---------------------------------------------------------------------------------------- the replay, kept as the reference

def legacy_funnel(store, pathways, now: datetime, *, pathway: str | None = None, priority: str | None = None,
                  at_risk_limit: int = AT_RISK_LIMIT) -> dict:
    """The same report by replaying every case of every pathway (unbounded). The reference the tests compare the SQL with, and
    the answer for a store that has no table (the in-memory store of the unit tests, small by construction)."""
    rows, failed = [], 0
    for pw in pathways:
        if pathway is not None and pw.name != pathway:
            continue
        everything = store.load_cases(pw)
        failed += sum(1 for lc in everything if lc.model is None)
        for lc in everything:
            if lc.model is not None:
                rows.append((pw.name, lc.case_id, metrics.build_record(lc.events, pw)))
    keep = [(p, cid, r) for p, cid, r in rows
            if priority is None or (r.priority is None if priority == UNRANKED else r.priority == priority)]
    facts = [(p, cid, r, row_of(r)) for p, cid, r in keep]
    counts = {
        "recorded": len(facts),
        "prioritised": sum(1 for *_, f in facts if f["priority"] is not None),
        "referred": sum(1 for *_, f in facts if f["referred_at"] is not None),
        "attended": sum(1 for *_, f in facts if f["attended_at"] is not None),
        "documented": sum(1 for *_, f in facts if f["documented_at"] is not None),
    }
    medians = {}
    for step, col in _TIME_COLUMN.items():
        xs = [f[col] for *_, f in facts if f[col] is not None]
        medians[step] = (len(xs), median(xs) if xs else None)
    now_utc = metrics._utc(now)
    at_risk = []
    for p, cid, r, f in facts:
        if r.completed_confirmatory or r.refusal_on_record:
            continue
        since = r.last_progress_at or r.t0
        if r.lost_contact_flag or metrics._days(since, now_utc) >= metrics.LTFU_IDLE_DAYS:
            at_risk.append((since, cid, p, r))
    at_risk.sort(key=lambda t: (t[0], t[1]))
    items = [_at_risk_item(cid, p, r.priority, r.state, r.last_progress_at, r.t0, r.lost_contact_flag, now_utc)
             for _, cid, p, r in at_risk[:at_risk_limit]]
    return shape(counts, medians, declined=sum(1 for *_, f in facts if f["declined_at"] is not None), at_risk=len(at_risk),
                 at_risk_cases=items, failed=failed, at_risk_limit=at_risk_limit)


def options(pathways) -> dict:
    """What the filters may be: the loaded pathways' names and every tier any of them defines (highest first), then `unranked`."""
    tiers: list[str] = []
    for pw in pathways:
        for t in (pw.priority.tiers if pw.priority else ()):
            if t not in tiers:
                tiers.append(t)
    return {"pathways": [pw.name for pw in pathways], "priorities": [*tiers, UNRANKED]}
