"""The watchdog's idle-case detection (A11, ADR-023).

The dangerous failure is the absence of events, so this looks for cases where nothing has
happened for `WATCHDOG_IDLE_DAYS` of simulation time. Like the sweep, it runs in the API next to
the event store and reads time from the sim clock only; the watchdog *service* just asks for a
scan (`POST /watchdog/scan`) with its own service token, which may emit `watchdog_flagged` and
nothing else.

An idle episode is the stretch since a case's last HUMAN event (`last_human_activity_at`: an event
written by a person, SEC-2). The scheduler's `mark_overdue` and escalations, drafted messages, the
notifier and a watchdog flag are not anyone looking at the case; counting them made a case the sweep
kept escalating look alive for ever. The flag's id is a uuid5 of (case, that event's id), never its time: two
quiet stretches that start at the same instant are different episodes. Scanning again, or two scans
at once, flags an episode once. Any later event clears the flag (the read model drops
`watchdog_flag`), and a new quiet stretch is a new episode with a new id. A closed case is never
flagged.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import payloads
from app.events import CaseNotFound, Event, EventContentConflict, EventIdConflict, EventStore
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition

log = logging.getLogger("app.watchdog")

WATCHDOG_ACTOR = "system:watchdog"
# Fixed namespace for deterministic event ids (never change: it would break idempotency).
WATCHDOG_NAMESPACE = uuid.UUID("c3f1d6a8-52b9-4e07-8a1d-6b0e9f4c7d25")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def idle_flag_event_id(case_id: UUID, started_by: UUID | str) -> UUID:
    """One id per idle episode: `started_by` is the id of the event the quiet stretch runs from."""
    return uuid.uuid5(WATCHDOG_NAMESPACE, f"{case_id}|{started_by}|idle")


def plan_idle_flag(
    case_id: UUID, model: CaseModel, pathway: Pathway, now: datetime, idle_days: int
) -> Event | None:
    """`watchdog_flagged {reason: idle}` for an open case no person has acted on for at least `idle_days`,
    once per episode; None otherwise. Pure."""
    if model.state == pathway.terminal_state or model.last_human_activity_at is None:
        return None
    if model.watchdog_flag is not None:
        return None  # already flagged since a person last acted
    idle = _utc(now) - _utc(model.last_human_activity_at)
    if idle < timedelta(days=idle_days):
        return None
    return Event(
        case_id,
        "watchdog_flagged",
        now,
        WATCHDOG_ACTOR,
        {
            "reason": "idle",
            "idle_days": idle.days,
            "last_activity_at": _utc(model.last_human_activity_at).isoformat(),
        },
        idle_flag_event_id(case_id, model.last_human_activity_event_id),
    )


@dataclass
class ScanResult:
    sim_time: str
    idle_days: int
    cases_evaluated: int = 0
    flagged: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    # incremental scan only (M7-7)
    mode: str = "full"  # "full" (every open case replayed) or "incremental"
    incremental_evaluated: int = 0  # cases read through the idle index
    uncomputed_evaluated: int = 0  # cases whose index value was never computed
    reconcile_evaluated: int = 0  # cases replayed by this scan's slice of the full reconciliation pass
    index_healed: int = 0  # index values written or corrected
    index_drift: int = 0  # of those, stored values that DISAGREED with the log (should stay 0)
    reconcile_cycles_completed: int = 0  # full reconciliation passes finished so far (durable)
    index_complete: bool = True  # False: the time budget ran out before the due set was exhausted; the next scan resumes
    seconds: float = 0.0

    def to_json(self) -> dict:
        out = {"sim_time": self.sim_time, "idle_days": self.idle_days, "cases_evaluated": self.cases_evaluated,
               "flagged": self.flagged, "rejected": self.rejected, "errors": list(self.errors)}
        if self.mode == "incremental":
            out.update({"mode": self.mode, "incremental_evaluated": self.incremental_evaluated,
                        "uncomputed_evaluated": self.uncomputed_evaluated, "reconcile_evaluated": self.reconcile_evaluated,
                        "index_healed": self.index_healed, "index_drift": self.index_drift,
                        "reconcile_cycles_completed": self.reconcile_cycles_completed,
                        "index_complete": self.index_complete, "seconds": round(self.seconds, 3)})
        return out


def _flag(store: EventStore, pathway: Pathway, result: ScanResult, case_id: UUID, model: CaseModel, now: datetime,
          idle_days: int) -> None:
    """Plan and append the idle flag of one case, if it is due; count the outcome. Shared by both scans."""
    event = plan_idle_flag(case_id, model, pathway, now, idle_days)
    if event is None:
        return
    if event.type != "watchdog_flagged" or event.actor != WATCHDOG_ACTOR:
        raise RuntimeError(f"watchdog planned a forbidden event: {event.type}")
    try:
        payloads.validate(event.type, event.payload)
        store.append(event, pathway)
    except (InvalidTransition, EventContentConflict) as exc:
        result.rejected += 1  # another writer moved the case first; the next scan re-plans
        log.info("case %s: flag refused: %s", case_id, exc)
    except (payloads.PayloadSchemaError, CaseNotFound, EventIdConflict) as exc:
        result.errors.append(f"{case_id}: {type(exc).__name__}: {exc}")
    else:
        result.flagged += 1


def run_idle_scan(store: EventStore, pathway: Pathway, now: datetime, idle_days: int, only=None) -> ScanResult:
    """Flag every open case idle for `idle_days` (or only the cases in `only`). Safe to repeat. Replays every open case:
    this is the reference the incremental scan is tested against, and the scan for stores without the idle index."""
    result = ScanResult(sim_time=_utc(now).isoformat(), idle_days=idle_days)
    for case_id, model, error in store.iter_open_case_models(pathway, only=only):
        result.cases_evaluated += 1
        if model is None:
            result.errors.append(f"{case_id}: {error}")
            continue
        _flag(store, pathway, result, case_id, model, now, idle_days)
    return result


# ---- the incremental scan --------------------------------------------------------------------------------------------

DEFAULT_BATCH = 500  # cases replayed per read
DEFAULT_BUDGET_SECONDS = 5.0  # wall time one scan may spend (the index pass and the reconciliation slice together)
DEFAULT_RECONCILE_SLICE = 1000  # open cases re-derived from their logs per scan; 0 switches the reconciliation pass off


def supports_incremental(store) -> bool:
    return all(hasattr(store, n) for n in ("idle_due_ids", "uncomputed_ids", "open_case_ids_page", "models_for",
                                           "write_idle_anchors", "stored_idle_since", "watchdog_state",
                                           "save_watchdog_state"))


def _evaluate(store, pathway: Pathway, result: ScanResult, ids: list, now: datetime, idle_days: int, counter: str) -> None:
    """Replay `ids`, flag the idle ones, and write back every index value that is missing or disagrees with the log."""
    from app.store_pg import idle_anchor

    stored = store.stored_idle_since(ids)
    heal: list[tuple] = []
    for case_id, model, error, last_seq in store.models_for(ids, pathway):
        result.cases_evaluated += 1
        setattr(result, counter, getattr(result, counter) + 1)
        if model is None:
            result.errors.append(f"{case_id}: {error}")
            continue
        anchor = idle_anchor(model, pathway)
        have = stored.get(case_id)
        if have is None or have != anchor:
            heal.append((case_id, last_seq, anchor))
            if have is not None:
                result.index_drift += 1
        _flag(store, pathway, result, case_id, model, now, idle_days)
    if heal:
        result.index_healed += store.write_idle_anchors(heal)


def run_incremental_scan(
    store, pathway: Pathway, now: datetime, idle_days: int, *, budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    batch: int = DEFAULT_BATCH, reconcile_slice: int = DEFAULT_RECONCILE_SLICE, timer=None,
) -> ScanResult:
    """One bounded watchdog pass. Flags the same cases `run_idle_scan` would (tests/test_watchdog_incremental_pg.py).

    1. The idle index: open cases with `idle_since_at <= now - idle_days`, by (idle_since_at, id) in keyset batches from the
       durable cursor, until the due set is exhausted (the cursor then resets) or `budget_seconds` is spent (the cursor stays
       and the next scan resumes). A flagged case leaves the index range, so a case is read again only if its flag was refused
       or failed. The cutoff is absolute, so a missed scan or a jump of the sim clock needs no catching up: whatever is past its
       deadline at this `now` is read, wherever the previous scan stopped.
    2. The never-computed rows (`idle_since_at IS NULL`), evaluated and written back.
    3. A slice of the full reconciliation pass: the next `reconcile_slice` open cases by id from the durable cursor, replayed
       and checked whatever the index says. When the cursor reaches the end it wraps and one cycle is counted.
    """
    import time

    timer = timer or time.perf_counter  # liveness/measurement only; never a decision about a case
    started = timer()
    result = ScanResult(sim_time=_utc(now).isoformat(), idle_days=idle_days, mode="incremental")
    state = store.watchdog_state()
    cutoff = _utc(now) - timedelta(days=idle_days)

    def out_of_time() -> bool:
        return timer() - started >= budget_seconds

    # 1. the idle index
    after = None
    if state.get("idle_after"):
        raw = state["idle_after"]
        after = (datetime.fromisoformat(raw[0]), UUID(raw[1]))
    state["idle_after"] = None
    while True:
        keys = store.idle_due_ids(pathway, cutoff, after, batch)
        if not keys:
            break
        _evaluate(store, pathway, result, [k[1] for k in keys], now, idle_days, "incremental_evaluated")
        after = keys[-1]
        if len(keys) < batch:
            break
        if out_of_time():
            state["idle_after"] = [after[0].isoformat(), str(after[1])]
            result.index_complete = False
            break
    # 2. never-computed rows (a bulk load, a row from before migration 017)
    if not out_of_time():
        ids = store.uncomputed_ids(pathway, None, batch)
        if ids:
            _evaluate(store, pathway, result, ids, now, idle_days, "uncomputed_evaluated")
    else:
        result.index_complete = False
    # 3. a slice of the full reconciliation pass
    if reconcile_slice > 0:
        last = UUID(state["reconcile_after"]) if state.get("reconcile_after") else None
        remaining = reconcile_slice
        while remaining > 0 and not out_of_time():
            want = min(batch, remaining)
            ids = store.open_case_ids_page(pathway, last, want)
            if ids:
                _evaluate(store, pathway, result, ids, now, idle_days, "reconcile_evaluated")
                last = ids[-1]
                remaining -= len(ids)
            if len(ids) < want:  # reached the end of the open cases: wrap, one full cycle done
                last = None
                state["reconcile_cycles"] = int(state.get("reconcile_cycles", 0)) + 1
                break
        state["reconcile_after"] = str(last) if last else None
    state["last_scan_sim_time"] = result.sim_time
    result.reconcile_cycles_completed = int(state.get("reconcile_cycles", 0))
    result.seconds = timer() - started
    state["last_scan_seconds"] = round(result.seconds, 3)
    store.save_watchdog_state(state)
    return result
