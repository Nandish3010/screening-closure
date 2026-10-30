"""`GET /metrics/closure` (A10): the closure metrics over every case of the pathway.

A thin route: the numbers come from the pure functions in `app.metrics`, which the simulator's
evaluation can call too.

A case whose stored log no longer replays cannot be measured. It used to be dropped without a word, so
the dashboard showed a closure rate computed over fewer cases than exist (SEC-2). It is now counted and
named in `replay_failures`, and logged at error level: the numbers are those of the cases that replay, and
the response says how many do not."""

import logging

from fastapi import APIRouter, Query, Response

from app import effort, metrics, metrics_index
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings
from app.routers import cases

router = APIRouter(prefix="/metrics", tags=["metrics"])
log = logging.getLogger("app.metrics")

MAX_LISTED_FAILURES = 50  # the count is exact; the list that names them is capped
LAG_HEADER = "X-Metrics-Index-Lag"


def _postgres(store):
    """The Postgres store behind `store` (through the FHIR-projecting wrapper if there is one), else None."""
    inner = getattr(store, "_inner", store)
    try:
        from app.store_pg import PostgresEventStore
    except ImportError:  # psycopg not installed: only the in-memory store can exist
        return None
    return inner if isinstance(inner, PostgresEventStore) else None


def _refresh(pg, response: Response | None) -> None:
    """M7-8: bring the metrics table up to date within a time budget. When the budget ran out the numbers are those of the
    table as it stands; the response says how many cases are still ahead of it (header only: the body is unchanged)."""
    result = metrics_index.refresh(pg, cases.pathway, default_calendar(), budget_seconds=settings.metrics_refresh_budget_seconds)
    if result["lag"]:
        if response is not None:
            response.headers[LAG_HEADER] = str(result["lag"])
        log.warning("metrics index is %d case(s) behind after a %.1f s refresh budget", result["lag"],
                    settings.metrics_refresh_budget_seconds)
    elif result["busy"] and response is not None:
        response.headers[LAG_HEADER] = "unknown"


@router.get("/closure")
def closure(response: Response = None) -> dict:  # response: None when the agency's read_metrics tool calls this directly
    now = clock.now()
    pg = _postgres(cases.store)
    if pg is None:  # the in-memory store: replay (small by construction)
        body, failed = metrics_index.legacy_closure(cases.store, cases.pathway, now, default_calendar())
        failures = [(lc.case_id, lc.error) for lc in failed]
        count = len(failures)
    else:
        _refresh(pg, response)
        body = metrics_index.closure_metrics(pg, now, default_calendar(), cases.pathway.terminal_state,
                                             timeout_ms=settings.metrics_query_timeout_ms)
        count, failures = metrics_index.replay_failures(pg, MAX_LISTED_FAILURES, timeout_ms=settings.metrics_query_timeout_ms)
    if count:
        log.error("closure metrics: %d case(s) do not replay and are not measured; first: %s: %s", count, failures[0][0],
                  failures[0][1])
    counts = cases.store.abstention_counts()
    return {
        "sim_time": now.isoformat(),
        **body,
        # Cases stored but not measured because their log does not replay (0 when all is well). `cases_total`
        # above counts only the cases that replay; this says how many were left out and which.
        "replay_failures": {
            "count": count,
            "cases": [{"case_id": str(cid), "error": error} for cid, error in failures[:MAX_LISTED_FAILURES]],
        },
        # AI draft abstentions, from the drafts table when the AI cut's migration is applied.
        "abstention": metrics.abstention(*counts) if counts is not None else None,
    }


@router.get("/effort")
def effort_metrics(response: Response = None, limit: int = Query(default=500, ge=1, le=5000)) -> dict:
    """Human effort versus automation (A21, ADR-032): per case, the actions people took and the actions the automation
    took, the automation share, and the minutes (all ASSUMED, see app.effort) a person spent against what the same case
    would have cost with no automation. `totals.reduction_pct` is compared with the 40% target. `per_case` is capped at
    `limit` rows (the totals cover every case that replays)."""
    now = clock.now()
    pg = _postgres(cases.store)
    if pg is None:
        report = metrics_index.legacy_effort(cases.store, cases.pathway, limit)
    else:
        _refresh(pg, response)
        report = metrics_index.effort_report(pg, limit, timeout_ms=settings.metrics_query_timeout_ms)
    if report["failed"]:
        log.error("effort metrics: %d case(s) do not replay and are not measured", report["failed"])
    return {
        "sim_time": now.isoformat(),
        "cases": report["cases"],
        "totals": report["totals"],
        "per_case": report["per_case"],
        "per_case_truncated": report["cases"] > limit,
        "replay_failures": {"count": report["failed"]},
        "assumptions": effort.assumptions(),
    }
