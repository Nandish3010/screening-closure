"""`GET /metrics/closure` (A10): the closure metrics over every case of the pathway.

A thin route: the numbers come from the pure functions in `app.metrics`, which the simulator's
evaluation can call too.

A case whose stored log no longer replays cannot be measured. It used to be dropped without a word, so
the dashboard showed a closure rate computed over fewer cases than exist (SEC-2). It is now counted and
named in `replay_failures`, and logged at error level: the numbers are those of the cases that replay, and
the response says how many do not."""

import logging

from fastapi import APIRouter

from app import metrics
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
