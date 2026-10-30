"""The closure and effort metrics from a table (M7-8, ADR-049), not from a replay of every case.

`GET /metrics/closure` and `GET /metrics/effort` folded the log of EVERY case on each call (`load_cases`). At 1,000,000 cases that
is minutes of CPU and hundreds of MiB in one request. Here the per-case facts those reports need are kept in `case_metrics` (and
`case_metric_dwells`, migration 018) and the reports are SQL aggregates over them.

* `refresh` brings the table up to date. It walks `cases` in `(updated_at, id)` order after a durable cursor, replays only the
  cases whose stored row is older than `cases.updated_at` (or has another signature), 200 per batch, one transaction per batch,
  and stops at its time budget. The replay is still per case, but it is paged with keyset batches and a budget, and it is the
  CHANGED cases only: a call after a quiet minute replays nothing. The first call over a big, never-indexed table replays
  at the budget per call until it has caught up (the response header says how far behind it still is).
* The fold is the SAME code as before (`metrics.build_record`, `effort.case_effort`, `calendar.working_days_between`) run at refresh
  time; only the aggregation moved into SQL. `legacy_closure` and `legacy_effort` keep the replay as the reference the tests
  compare with, and the answer for stores that have no such table (the in-memory store of the unit tests).
* What depends on `now` (the age buckets, lost to follow-up) is computed in SQL from the stored instants, so it is exact at
  every call. What depends on the calendar (working days) is stored with a signature of the calendar and recomputed when it changes.

Race: a transaction that commits late can carry an `updated_at` older than the cursor. Every refresh starts `LOOKBACK_SECONDS` before
the cursor; rows that are already current are skipped by one indexed lookup. A transaction open longer than that would be missed
until its case changes again; append transactions here last milliseconds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import effort, metrics
from app.state_machine import InvalidTransition, replay_model

log = logging.getLogger("app.metrics_index")

FOLD_VERSION = "1"  # bump when build_record / case_effort change what they store
BATCH = 200
LOOKBACK_SECONDS = 120
LOCK_KEY = 727_018
NIL = UUID(int=0)
STATE_KEY = "refresh"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def signature(pathway, calendar) -> str:
    """What a stored row depends on besides its own log: the fold, the pathway, the calendar and the assumed minutes."""
    basis = json.dumps({
        "fold": FOLD_VERSION,
        "pathway": hashlib.sha1(repr(pathway).encode()).hexdigest(),
        "weekend": sorted(calendar.weekend),
        "holidays": sorted(d.isoformat() for d in calendar.holidays),
        "tz": getattr(calendar.tz, "key", str(calendar.tz)),
        "minutes": sorted(effort.HUMAN_ACTION_MINUTES.items()),
        "surfacing": sorted(metrics.SURFACING_EVENTS),
    }, sort_keys=True, default=str)
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def _as_events(rows, case_id) -> list:
    from app.store_pg import _event_from_row

    return [_event_from_row(case_id, r) for r in rows]


def fold_case(events, pathway, calendar) -> dict:
    """One case's row (the columns of `case_metrics` and its dwells), or `{"ok": False, "error": ...}` when the log does not replay."""
    try:
        replay_model(events, pathway)
    except InvalidTransition as exc:
        return {"ok": False, "error": str(exc), "dwells": []}
    rec = metrics.build_record(events, pathway)
    eff = effort.case_effort(events)
    safe = rec.first_safe_close_at
    return {
        "ok": True, "error": None, "t0": rec.t0, "state": rec.state, "first_close_at": rec.first_close_at,
        "first_safe_close_at": safe, "safe_declined": rec.first_safe_close_declined, "unsafe": rec.unsafe_closure,
        "completed": rec.completed_confirmatory, "last_progress_at": rec.last_progress_at, "lost_flag": rec.lost_contact_flag,
        "surfaced": rec.surfaced, "flagged": rec.flagged, "declined_closure": rec.declined_closure,
        "refusal_pending": rec.refusal_pending, "refusal_on_record": rec.refusal_on_record,
        "ttc_days": metrics._days(rec.t0, safe) if safe is not None else None,
        "ttc_wdays": calendar.working_days_between(rec.t0, safe) if safe is not None else None,
        "breach_providers": list(rec.breach_providers),
        "e_human": eff["human_actions"], "e_auto": eff["automated_actions"], "e_patient": eff["patient_actions"],
        "e_model": eff["model_actions"], "e_service": eff["service_actions"], "e_unpriced": eff["unpriced_human_actions"],
        "e_hmin": eff["human_minutes"], "e_bmin": eff["baseline_minutes"], "e_smin": eff["minutes_saved"],
        "dwells": [(frm, to, metrics._days(a, b), calendar.working_days_between(a, b)) for frm, to, a, b in rec.dwells],
    }


_COLS = ("t0", "state", "first_close_at", "first_safe_close_at", "safe_declined", "unsafe", "completed", "last_progress_at",
         "lost_flag", "surfaced", "flagged", "declined_closure", "refusal_pending", "refusal_on_record", "ttc_days", "ttc_wdays",
         "breach_providers", "e_human", "e_auto", "e_patient", "e_model", "e_service", "e_unpriced", "e_hmin", "e_bmin", "e_smin")
_UPSERT = (
    "INSERT INTO case_metrics (case_id, src_updated_at, sig, created_at, ok, error, " + ", ".join(_COLS) + ") VALUES ("
    + ", ".join(["%s"] * (6 + len(_COLS))) + ") ON CONFLICT (case_id) DO UPDATE SET src_updated_at = EXCLUDED.src_updated_at,"
    " sig = EXCLUDED.sig, created_at = EXCLUDED.created_at, ok = EXCLUDED.ok, error = EXCLUDED.error, "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS)
)


def _read_state(conn) -> dict:
    row = conn.execute("SELECT value FROM metrics_index_state WHERE key = %s", (STATE_KEY,)).fetchone()
    return row[0] if row else {}


def _write_state(conn, value: dict) -> None:
    conn.execute(
        "INSERT INTO metrics_index_state (key, value, updated_at) VALUES (%s, %s::jsonb, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (STATE_KEY, json.dumps(value)))


def refresh(store, pathway, calendar, *, budget_seconds: float, batch: int = BATCH, monotonic=time.monotonic) -> dict:
    """Bring `case_metrics` up to date, at most `budget_seconds`. Returns `{"replayed", "batches", "caught_up", "busy", "lag"}`;
    `lag` is the number of cases past the cursor when the budget ran out (None when caught up). Safe to call from every API pod:
    one batch at a time holds an advisory lock, and a pod that does not get it leaves the work to the one that has it."""
    pool = store._pool
    sig = signature(pathway, calendar)
    deadline = monotonic() + max(0.0, budget_seconds)
    out = {"replayed": 0, "batches": 0, "caught_up": False, "busy": False, "lag": None}
    pos = None
    while True:
        with pool.connection() as conn, conn.transaction():
            if not conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
                out["busy"] = True
                return out
            state = _read_state(conn)
            cursor = state.get("cursor") if state.get("sig") == sig else None  # another signature: start again from the top
            if pos is None:
                # the first batch of this call starts a little before the stored cursor (late commits)
                if cursor:
                    t = datetime.fromisoformat(cursor[0]) - timedelta(seconds=LOOKBACK_SECONDS)
                    pos = (t, NIL)
                else:
                    pos = (_EPOCH, NIL)
            rows = conn.execute(
                "SELECT id, updated_at FROM cases WHERE pathway = %s AND (updated_at, id) > (%s, %s)"
                " ORDER BY updated_at, id LIMIT %s", (pathway.name, pos[0], pos[1], batch)).fetchall()
            if not rows:
                out["caught_up"] = True
                if cursor is None and state.get("sig") != sig:
                    _write_state(conn, {"sig": sig, "cursor": None})
                return out
            ids = [r[0] for r in rows]
            have = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT case_id, src_updated_at, sig FROM case_metrics WHERE case_id = ANY(%s)", (ids,)).fetchall()}
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
                        continue  # a case with no events is not a case of the reports (load_cases skips it too)
                    events = _as_events(raw, cid)
                    row = fold_case(events, pathway, calendar)
                    vals = [row.get(c) for c in _COLS]
                    conn.execute(_UPSERT, (cid, upd, sig, events[0].at, row["ok"], row["error"], *vals))
                    conn.execute("DELETE FROM case_metric_dwells WHERE case_id = %s", (cid,))
                    if row["dwells"]:
                        with conn.cursor() as cur:
                            cur.executemany(
                                "INSERT INTO case_metric_dwells (case_id, ord, frm, to_state, days, wdays) VALUES (%s,%s,%s,%s,%s,%s)",
                                [(cid, i, f, t, d, w) for i, (f, t, d, w) in enumerate(row["dwells"])])
                out["replayed"] += len(todo)
            last = rows[-1]
            pos = (last[1], last[0])
            new_cursor = [last[1].isoformat(), str(last[0])]
            if not cursor or (last[1].isoformat(), str(last[0])) > (cursor[0], cursor[1]):
                _write_state(conn, {"sig": sig, "cursor": new_cursor})
            out["batches"] += 1
        if monotonic() >= deadline:
            with pool.connection() as conn:
                out["lag"] = conn.execute(
                    "SELECT count(*) FROM cases WHERE pathway = %s AND (updated_at, id) > (%s, %s)",
                    (pathway.name, pos[0], pos[1])).fetchone()[0]
            return out


# ------------------------------------------------------------------------------------------------ the reports in SQL

def _timeout(conn, ms: int) -> None:
    conn.execute(f"SET LOCAL statement_timeout = {int(ms)}")


def _percentiles(conn, col: str, where: str, n: int) -> tuple:
    """`(p50, p90)` of `col` over the rows of `case_metrics` matching `where`, by the SAME arithmetic as `metrics.percentile`
    (linear interpolation at q x (n - 1), `xs[lo] + (pos - lo) * (xs[hi] - xs[lo])`) so the answer is bit for bit the old one: the
    database only sorts and hands over the (at most four) values at the ranks the formula needs."""
    if not n:
        return None, None
    plan = []
    for q in (0.5, 0.9):
        pos = q * (n - 1)
        lo = int(pos)
        plan.append((pos, lo, min(lo + 1, n - 1)))
    ranks = sorted({r + 1 for _, lo, hi in plan for r in (lo, hi)})
    at = dict(conn.execute(
        f"SELECT rn, x FROM (SELECT {col} AS x, row_number() OVER (ORDER BY {col}) AS rn FROM case_metrics"
        f" WHERE ok AND {where}) t WHERE rn = ANY(%s)", (ranks,)).fetchall())
    return tuple(at[lo + 1] + (pos - lo) * (at[hi + 1] - at[lo + 1]) for pos, lo, hi in plan)


def _medians(conn, col: str) -> dict:
    """`{(from, to): (n, median)}` of `col` over `case_metric_dwells`, as `statistics.median` computes it (the middle value, or
    the mean of the two middle ones as `(a + b) / 2`). One count per transition, then an index walk (migration 019) to the middle."""
    out = {}
    for frm, to, n in conn.execute("SELECT frm, to_state, count(*) FROM case_metric_dwells GROUP BY frm, to_state").fetchall():
        lo = (n - 1) // 2  # 0-based rank of the lower middle value (also the middle one when n is odd)
        xs = [r[0] for r in conn.execute(
            f"SELECT {col} FROM case_metric_dwells WHERE frm = %s AND to_state = %s ORDER BY {col} OFFSET %s LIMIT %s",
            (frm, to, lo, 1 if n % 2 else 2)).fetchall()]
        out[(frm, to)] = (n, xs[0] if n % 2 else (xs[0] + xs[1]) / 2)
    return out


def closure_metrics(store, now: datetime, calendar, terminal: str, *, timeout_ms: int = 60000) -> dict:
    """The body of `GET /metrics/closure` minus `sim_time`, `replay_failures` and `abstention`: the same keys and numbers as
    `metrics.closure_metrics` over every case that replays, from the table."""
    age = "extract(epoch FROM (%(now)s - t0)) / 86400"
    idle = "extract(epoch FROM (%(now)s - coalesce(last_progress_at, t0))) / 86400"
    safe = "first_safe_close_at IS NOT NULL AND NOT safe_declined"
    declined = "first_safe_close_at IS NOT NULL AND safe_declined"
    sql = f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE state = %(term)s) AS closed,
               count(*) FILTER (WHERE {safe}) AS ttc_n,
               count(*) FILTER (WHERE declined_closure) AS dc,
               count(*) FILTER (WHERE refusal_pending) AS dps,
               count(*) FILTER (WHERE {declined}) AS dn,
               count(*) FILTER (WHERE unsafe) AS unsafe,
               count(*) FILTER (WHERE state <> %(term)s AND trunc({age}) <= 30) AS b0,
               count(*) FILTER (WHERE state <> %(term)s AND trunc({age}) > 30 AND trunc({age}) <= 60) AS b1,
               count(*) FILTER (WHERE state <> %(term)s AND trunc({age}) > 60 AND trunc({age}) <= 90) AS b2,
               count(*) FILTER (WHERE state <> %(term)s AND trunc({age}) > 90) AS b3,
               count(*) FILTER (WHERE NOT completed AND NOT refusal_on_record AND (lost_flag OR {idle} >= %(ltfu)s)) AS lost,
               count(*) FILTER (WHERE NOT completed AND NOT refusal_on_record AND (lost_flag OR {idle} >= %(ltfu)s)
                                AND NOT surfaced) AS never,
               count(*) FILTER (WHERE flagged AND state <> %(term)s) AS flagged
          FROM case_metrics WHERE ok"""
    args = {"now": now, "term": terminal, "ltfu": metrics.LTFU_IDLE_DAYS}
    with store._pool.connection() as conn, conn.transaction():
        _timeout(conn, timeout_ms)
        cur = conn.execute(sql, args)
        r = dict(zip([d.name for d in cur.description], cur.fetchone()))
        p50, p90 = _percentiles(conn, "ttc_days", safe, r["ttc_n"])
        p50w, p90w = _percentiles(conn, "ttc_wdays", safe, r["ttc_n"])
        dp50, dp90 = _percentiles(conn, "ttc_days", declined, r["dn"])
        dp50w, dp90w = _percentiles(conn, "ttc_wdays", declined, r["dn"])
        breaches = conn.execute(
            "SELECT provider, n FROM (SELECT coalesce(nullif(p, ''), %s) AS provider, count(*) AS n FROM"
            " (SELECT unnest(breach_providers) AS p FROM case_metrics WHERE ok AND cardinality(breach_providers) > 0) b"
            " GROUP BY 1) g ORDER BY n DESC, provider COLLATE \"C\"", (metrics.UNASSIGNED,)).fetchall()
        days = _medians(conn, "days")
        wdays = _medians(conn, "wdays")
    bottleneck = []
    for key, (n, med) in days.items():
        medw = wdays[key][1]
        # statistics.median returns an int for an odd count of ints and a float for an even one; keep that shape
        bottleneck.append({"transition": f"{key[0]} -> {key[1]}", "median_days": med,
                           "median_working_days": int(medw) if n % 2 == 1 else float(medw), "n": n})
    bottleneck.sort(key=lambda row: (-row["median_days"], row["transition"]))
    return {
        "cases_total": r["total"],
        "cases_open": r["total"] - r["closed"],
        "cases_closed": r["closed"],
        "closures_measured": r["ttc_n"],
        "declined_closures": r["dc"],
        "declined_pending_signoff": r["dps"],
        "declined_p50_days": dp50, "declined_p90_days": dp90,
        "declined_p50_working_days": dp50w, "declined_p90_working_days": dp90w,
        "cases_closed_investigated": r["closed"] - r["dc"],
        "unsafe_closures": r["unsafe"],
        "p50_days": p50, "p90_days": p90, "p50_working_days": p50w, "p90_working_days": p90w,
        "open_by_age_bucket": {"0-30": r["b0"], "31-60": r["b1"], "61-90": r["b2"], "90+": r["b3"]},
        "breaches_by_provider": [{"provider": prov, "count": n} for prov, n in breaches],
        "bottleneck_by_transition": bottleneck,
        "lost_to_follow_up": r["lost"], "lost_never_surfaced": r["never"],
        "cases_flagged": r["flagged"],
    }


def replay_failures(store, limit: int = 50, *, timeout_ms: int = 60000) -> tuple[int, list[tuple]]:
    """`(count, [(case_id, error), ...])` of the cases whose log does not replay, the first `limit` in (created_at, id) order."""
    with store._pool.connection() as conn, conn.transaction():
        _timeout(conn, timeout_ms)
        n = conn.execute("SELECT count(*) FROM case_metrics WHERE NOT ok").fetchone()[0]
        rows = conn.execute("SELECT case_id, error FROM case_metrics WHERE NOT ok ORDER BY created_at, case_id::text LIMIT %s",
                            (limit,)).fetchall()
    return n, rows


def effort_report(store, limit: int, *, timeout_ms: int = 60000) -> dict:
    """The numbers of `effort.effort_report` over every case that replays, from the table: `{"totals", "per_case", "cases",
    "failed"}`. `per_case` is the first `limit` rows in the order the replay used."""
    with store._pool.connection() as conn, conn.transaction():
        _timeout(conn, timeout_ms)
        cur = conn.execute(
            "SELECT count(*), coalesce(sum(e_human),0), coalesce(sum(e_auto),0), coalesce(sum(e_patient),0), coalesce(sum(e_model),0),"
            " coalesce(sum(e_service),0), coalesce(sum(e_unpriced),0), coalesce(sum(e_hmin),0), coalesce(sum(e_bmin),0),"
            " coalesce(sum(e_smin),0) FROM case_metrics WHERE ok")
        n, human, auto, patient, model, service, unpriced, hmin, bmin, smin = cur.fetchone()
        failed = conn.execute("SELECT count(*) FROM case_metrics WHERE NOT ok").fetchone()[0]
        rows = conn.execute(
            "SELECT case_id, e_human, e_auto, e_hmin, e_bmin FROM case_metrics WHERE ok ORDER BY created_at, case_id::text LIMIT %s",
            (limit,)).fetchall()
    totals = effort.totals_from_sums({
        "human_actions": human, "automated_actions": auto, "patient_actions": patient, "model_actions": model,
        "service_actions": service, "unpriced_human_actions": unpriced, "human_minutes": hmin, "baseline_minutes": bmin,
        "minutes_saved": smin})
    per_case = []
    for cid, h, a, hm, bm in rows:
        acted = a + h
        per_case.append({"case_id": str(cid), "human_actions": h, "automated_actions": a,
                         "automation_share": round(a / acted, 4) if acted else None,
                         "human_minutes": round(hm, 2), "baseline_minutes": round(bm, 2)})
    return {"totals": totals, "per_case": per_case, "cases": n, "failed": failed}


# --------------------------------------------------------------------------------------- the replay, kept as the reference

def legacy_closure(store, pathway, now, calendar) -> tuple[dict, list]:
    """The pre-M7-8 computation: replay every case. `(metrics.closure_metrics(...), failed LoadedCases)`. Unbounded; kept for the
    in-memory store and as the reference the tests compare the SQL aggregates with."""
    everything = store.load_cases(pathway)
    loaded = [lc for lc in everything if lc.model is not None]
    failed = [lc for lc in everything if lc.model is None]
    records = [metrics.build_record(lc.events, pathway) for lc in loaded]
    return metrics.closure_metrics(records, now, calendar, terminal_state=pathway.terminal_state), failed


def legacy_effort(store, pathway, limit: int) -> dict:
    everything = store.load_cases(pathway)
    loaded = [lc for lc in everything if lc.model is not None]
    report = effort.effort_report({str(lc.case_id): lc.events for lc in loaded})
    per_case = [
        {"case_id": cid, **{k: row[k] for k in ("human_actions", "automated_actions", "automation_share", "human_minutes",
                                                "baseline_minutes")}}
        for cid, row in list(report["rows"].items())[:limit]
    ]
    return {"totals": report["totals"], "per_case": per_case, "cases": len(loaded), "failed": len(everything) - len(loaded)}
