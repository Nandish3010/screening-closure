"""`GET /metrics/closure` (A10): the closure metrics over every case of the pathway.

A thin route: the numbers come from the pure functions in `app.metrics`, which the simulator's
evaluation can call too.

A case whose stored log no longer replays cannot be measured. It used to be dropped without a word, so
the dashboard showed a closure rate computed over fewer cases than exist (SEC-2). It is now counted and
named in `replay_failures`, and logged at error level: the numbers are those of the cases that replay, and
the response says how many do not."""

import logging

from fastapi import APIRouter, Query

from app import effort, metrics
from app.calendar import default_calendar
from app.clock import clock
from app.routers import cases

router = APIRouter(prefix="/metrics", tags=["metrics"])
log = logging.getLogger("app.metrics")

MAX_LISTED_FAILURES = 50  # the count is exact; the list that names them is capped


@router.get("/closure")
def closure() -> dict:
    now = clock.now()
    everything = cases.store.load_cases(cases.pathway)
    loaded = [lc for lc in everything if lc.model is not None]
    failed = [lc for lc in everything if lc.model is None]
    if failed:
        log.error("closure metrics: %d of %d case(s) do not replay and are not measured; first: %s: %s",
                  len(failed), len(everything), failed[0].case_id, failed[0].error)
    records = [metrics.build_record(lc.events, cases.pathway) for lc in loaded]
    body = metrics.closure_metrics(records, now, default_calendar(), terminal_state=cases.pathway.terminal_state)
    counts = cases.store.abstention_counts()
    return {
        "sim_time": now.isoformat(),
        **body,
        # Cases stored but not measured because their log does not replay (0 when all is well). `cases_total`
        # above counts only the cases that replay; this says how many were left out and which.
        "replay_failures": {
            "count": len(failed),
            "cases": [{"case_id": str(lc.case_id), "error": lc.error} for lc in failed[:MAX_LISTED_FAILURES]],
        },
        # AI draft abstentions, from the drafts table when the AI cut's migration is applied.
        "abstention": metrics.abstention(*counts) if counts is not None else None,
    }


@router.get("/effort")
def effort_metrics(limit: int = Query(default=500, ge=1, le=5000)) -> dict:
    """Human effort versus automation (A21, ADR-032): per case, the actions people took and the actions the automation
    took, the automation share, and the minutes (all ASSUMED, see app.effort) a person spent against what the same case
    would have cost with no automation. `totals.reduction_pct` is compared with the 40% target. `per_case` is capped at
    `limit` rows (the totals cover every case that replays)."""
    now = clock.now()
    everything = cases.store.load_cases(cases.pathway)
    loaded = [lc for lc in everything if lc.model is not None]
    failed = len(everything) - len(loaded)
    if failed:
        log.error("effort metrics: %d of %d case(s) do not replay and are not measured", failed, len(everything))
    report = effort.effort_report({str(lc.case_id): lc.events for lc in loaded})
    per_case = [
        {"case_id": cid, **{k: row[k] for k in (
            "human_actions", "automated_actions", "automation_share", "human_minutes", "baseline_minutes")}}
        for cid, row in list(report["rows"].items())[:limit]
    ]
    return {
        "sim_time": now.isoformat(),
        "cases": len(loaded),
        "totals": report["totals"],
        "per_case": per_case,
        "per_case_truncated": len(loaded) > limit,
        "replay_failures": {"count": failed},
        "assumptions": effort.assumptions(),
    }
