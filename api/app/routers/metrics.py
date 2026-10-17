"""`GET /metrics/closure` (A10): the closure metrics over every case of the pathway.

A thin route: the numbers come from the pure functions in `app.metrics`, which the simulator's
evaluation can call too."""

from fastapi import APIRouter

from app import metrics
from app.calendar import default_calendar
from app.clock import clock
from app.routers import cases

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/closure")
def closure() -> dict:
    now = clock.now()
    loaded = [lc for lc in cases.store.load_cases(cases.pathway) if lc.model is not None]
    records = [metrics.build_record(lc.events, cases.pathway) for lc in loaded]
    body = metrics.closure_metrics(records, now, default_calendar(), terminal_state=cases.pathway.terminal_state)
    counts = cases.store.abstention_counts()
    return {
        "sim_time": now.isoformat(),
        **body,
        # AI draft abstentions, from the drafts table when the AI cut's migration is applied.
        "abstention": metrics.abstention(*counts) if counts is not None else None,
    }
