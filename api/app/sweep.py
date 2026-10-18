"""Scheduler SLA sweep: find breaches, emit `mark_overdue` (A3), walk the escalation ladder (A4)
and bounce unanswered referral handoffs (A5).

The planner (`plan_case`) is a pure function of (case model, pathway, calendar, now). It never
reads a clock and never produces anything but `mark_overdue` / `escalate` / bounce (`handoff_rejected` with
reason `timeout`) events from `system:scheduler`; in particular it can never produce `close`
(T-A16). `run_sweep` feeds it
every open case from a store and appends what it plans.

Idempotency: each planned event has a deterministic id, a uuid5 over (case, the `event_id` of the
event that started the clock or made the request, kind [, rung]). Never over a timestamp (SEC-2):
two different clocks that start at the same instant (a state entered, left and entered again; a
handoff requested, bounced and requested again) are different obligations and must not share an
id, or the second would be swallowed as a repeat of the first. Running the sweep again, or two
sweeps at once, still inserts each event once: the store treats a repeated event_id on the same
case as a no-op. The state machine adds a second line of defence (a case that is already overdue
refuses another `mark_overdue`).

Parallel sweep (A17). `run_sweep_parallel` splits the open cases into `n` disjoint slices swept by `n`
spawned worker processes, each with its own connection pool; `SweepPool` keeps those processes alive for
the scheduler route (`app.routers.scheduler`, `SWEEP_WORKERS`, default min(4, cpus)). The slices are
disjoint and exhaustive and the event ids are deterministic, so the result equals `run_sweep` and a sweep
that is repeated, overlapped or killed part-way only ever leaves each planned event in the log once.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app import payloads
from app.calendar import WorkingCalendar
from app.events import CaseNotFound, Event, EventIdConflict, EventStore
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition

log = logging.getLogger("app.sweep")

SCHEDULER_ACTOR = "system:scheduler"
# The only event types the scheduler may ever emit (mirrors app.policy.EVENT_POLICY).
SCHEDULER_EVENT_TYPES = frozenset({"mark_overdue", "escalate", "handoff_rejected"})
# Fixed namespace for deterministic event ids (never change: it would break idempotency).
SWEEP_NAMESPACE = uuid.UUID("5c6d0a9e-77f1-4b8e-9d0c-0f3f6a1b2e44")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def sweep_event_id(case_id: UUID, started_by: UUID | str, kind: str) -> UUID:
    """uuid5(case, event that started the clock or request, kind): one id per breach (and per rung),
    one per unanswered handoff request. `started_by` is an event id, never a time."""
    return uuid.uuid5(SWEEP_NAMESPACE, f"{case_id}|{started_by}|{kind}")


def due_at_for(model: CaseModel, pathway: Pathway, calendar: WorkingCalendar) -> datetime | None:
    """When the current state's SLA expires, or None if the state has no SLA to watch.

    The clock starts at the event that entered the state (`model.clock_started_at`) and the
    SLA length is counted in working days.
    """
    sla = pathway.sla_for_state(model.state)
    if sla is None or model.clock_started_at is None or model.state not in pathway.open_states:
        return None
    _, days = sla
    return _utc(calendar.add_working_days(model.clock_started_at, days))


def handoff_due_at(
    model: CaseModel, pathway: Pathway, calendar: WorkingCalendar
) -> datetime | None:
    """When the pending handoff request expires unanswered, or None if nothing is pending.

    Counted in working days from the request (`handoff_response_working_days`, ADR-016).
    """
    pending = model.pending_handoff
    if not pending:
        return None
    requested = datetime.fromisoformat(pending["requested_at"])
    return _utc(calendar.add_working_days(requested, pathway.handoff_response_working_days))


def plan_bounce(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> Event | None:
    """`handoff_rejected {reason: timeout, bounced: true}` for a request that went unanswered.

    Ownership never moved (it changes only on acceptance), so the bounce returns the case to the
    requester by clearing the pending request; in the referral states it also moves the case to
    `referral_rejected` so it needs a re-route (which has its own SLA and ladder).
    """
    due = handoff_due_at(model, pathway, calendar)
    if due is None or now <= due:
        return None
    pending = model.pending_handoff
    return Event(
        case_id,
        "handoff_rejected",
        now,
        SCHEDULER_ACTOR,
        {
            "reason": pathway.handoff_bounce_reason,
            "bounced": True,
            "to_provider": pending["to_provider"],
            "returned_to": pending["from_owner"],
            "requested_at": pending["requested_at"],
            "due_at": due.isoformat(),
            "working_days": pathway.handoff_response_working_days,
        },
        sweep_event_id(case_id, pending["requested_event_id"], "bounce"),
    )


def plan_case(
    case_id: UUID, model: CaseModel, pathway: Pathway, calendar: WorkingCalendar, now: datetime
) -> list[Event]:
    """The events the scheduler should append for this case at sim time `now`, in order.

    A bounce comes alone: it can change the case's state, so the overdue/escalation events are
    planned from the new model (`run_sweep` re-plans straight after a bounce).
    """
    bounce = plan_bounce(case_id, model, pathway, calendar, now)
    if bounce is not None:
        return [bounce]
    due = due_at_for(model, pathway, calendar)
    if due is None or now <= due:
        return []
    planned: list[Event] = []
    sla_event, sla_days = pathway.sla_for_state(model.state)
    if not model.overdue:
        planned.append(
            Event(
                case_id,
                "mark_overdue",
                now,
                SCHEDULER_ACTOR,
                {
                    "state": model.state,
                    "due_at": due.isoformat(),
                    "sla_event": sla_event,
                    "sla_working_days": sla_days,
                },
                sweep_event_id(case_id, model.clock_started_event_id, "mark_overdue"),
            )
        )
    if not model.escalation_accepted:
        planned.extend(_plan_rungs(case_id, model, pathway, calendar, now, due))
    return planned


def _plan_rungs(
    case_id: UUID,
    model: CaseModel,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    due: datetime,
) -> list[Event]:
    """The escalation rungs now due for this breach, in ladder order, none skipped.

    A rung is due `after_breach_days` working days after the due date (0 = at the breach).
    Rungs already emitted (`escalation_level`) are not planned again. A human accepting a
    rung (`escalation_accepted`) stops the ladder for this breach; the caller checks that.
    """
    rungs = []
    for level, rung in enumerate(pathway.escalation_ladder, start=1):
        if level <= model.escalation_level:
            continue
        rung_due = _utc(calendar.add_working_days(due, rung.after_breach_days))
        if now < rung_due:
            break  # later rungs are due even later
        rungs.append(
            Event(
                case_id,
                "escalate",
                now,
                SCHEDULER_ACTOR,
                {
                    "rung": rung.rung,
                    "level": level,
                    "target": rung.target,
                    "action": rung.action,
                    "state": model.state,
                    "due_at": due.isoformat(),
                    "rung_due_at": rung_due.isoformat(),
                },
                sweep_event_id(case_id, model.clock_started_event_id, f"escalate:{rung.rung}"),
            )
        )
    return rungs


@dataclass
class SweepResult:
    sim_time: str
    cases_evaluated: int = 0
    mark_overdue: int = 0
    escalate: int = 0
    handoff_bounced: int = 0
    rejected: int = 0  # planned events the state machine refused (a race with another writer)
    errors: list[str] = field(default_factory=list)
    # (event_id, case_id) of every event a shard worker appended, filled only when the caller asked
    # for it (`record=True`): the parent process projects them to FHIR (A12) because the workers
    # write through their own plain stores, not the API's projecting wrapper. Not part of to_json.
    emitted_refs: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def to_json(self) -> dict:
        return {
            "sim_time": self.sim_time,
            "cases_evaluated": self.cases_evaluated,
            "mark_overdue": self.mark_overdue,
            "escalate": self.escalate,
            "handoff_bounced": self.handoff_bounced,
            "rejected": self.rejected,
            "errors": list(self.errors),
        }


def run_sweep(
    store: EventStore,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    only=None,
    shard: tuple[int, int] | None = None,
) -> SweepResult:
    """Evaluate every open case (or only those in `only`, or only slice `shard=(k, n)`) at sim
    time `now` and append the planned events. Safe to run repeatedly and concurrently."""
    result = SweepResult(sim_time=_utc(now).isoformat())
    # `shard` is only passed to stores that support it (Postgres); the in-memory store sweeps all.
    kwargs = {"shard": shard} if shard is not None else {}
    for case_id, model, error in store.iter_open_case_models(pathway, only=only, **kwargs):
        result.cases_evaluated += 1
        if model is None:
            result.errors.append(f"{case_id}: {error}")
            log.error("case %s does not replay: %s", case_id, error)
            continue
        # A bounce can move the case to another state, so re-plan once from the new model.
        for _ in range(2):
            bounced = False
            planned = plan_case(case_id, model, pathway, calendar, now)
            for event in planned:
                if event.type not in SCHEDULER_EVENT_TYPES or event.actor != SCHEDULER_ACTOR:
                    raise RuntimeError(f"scheduler planned a forbidden event: {event.type}")
                if event.type == "handoff_rejected" and (
                    event.payload.get("reason") != pathway.handoff_bounce_reason
                    or event.payload.get("bounced") is not True
                ):
                    raise RuntimeError("scheduler planned a handoff_rejected that is not a bounce")
            # Fast path: several events for one case (a breach and its first rung) go in one
            # transaction instead of one each. All-or-nothing; if the batch is refused for any
            # reason we fall back to the one-by-one loop below, which keeps its exact behaviour.
            if (
                len(planned) > 1
                and hasattr(store, "append_many")
                and not any(e.type == "handoff_rejected" for e in planned)
            ):
                try:
                    store.append_many(planned, pathway)
                except (InvalidTransition, CaseNotFound, EventIdConflict):
                    pass
                else:
                    for event in planned:
                        if event.type == "mark_overdue":
                            result.mark_overdue += 1
                        else:
                            result.escalate += 1
                    planned = []
            for event in planned:
                try:
                    # Whatever the sweep plans must also pass the payload schema (A14), so a bug
                    # here cannot put free text in the log. A planned event that does not is
                    # reported and skipped, not allowed to stop the sweep for every other case.
                    payloads.validate(event.type, event.payload)
                except payloads.PayloadSchemaError as exc:
                    result.errors.append(f"{case_id}: {exc}")
                    log.error("case %s: planned %s refused by the payload schema: %s", case_id, event.type, exc)
                    break
                try:
                    store.append(event, pathway)
                except InvalidTransition as exc:
                    # Another writer moved the case between our read and our write; the next
                    # sweep re-plans from the new state.
                    result.rejected += 1
                    log.info("case %s: %s refused: %s", case_id, event.type, exc)
                    break
                except (CaseNotFound, EventIdConflict) as exc:
                    result.errors.append(f"{case_id}: {type(exc).__name__}: {exc}")
                    break
                if event.type == "mark_overdue":
                    result.mark_overdue += 1
                elif event.type == "handoff_rejected":
                    result.handoff_bounced += 1
                    bounced = True
                else:
                    result.escalate += 1
            if not bounced:
                break
            model = store.read_model(case_id, pathway)
            if model is None:
                break
    return result


class _RecordingStore:
    """Delegates to a store and notes the (event_id, case_id) of every event it appended."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.refs: list[tuple[str, str]] = []
        if hasattr(inner, "append_many"):  # the sweep probes for it with hasattr
            self.append_many = self._append_many

    def append(self, event, pathway, *, within=None, guard=None):
        state = self._inner.append(event, pathway, within=within, guard=guard)
        self.refs.append((str(event.event_id), str(event.case_id)))
        return state

    def _append_many(self, events, pathway, *, within=None, guard=None):
        state = self._inner.append_many(events, pathway, within=within, guard=guard)
        self.refs.extend((str(e.event_id), str(e.case_id)) for e in events)
        return state

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _sweep_shard(args: tuple) -> dict:
    """Worker entry point (top level so it can be pickled): sweep one slice with its own pool."""
    database_url, pathway, calendar, now, index, count, record = args
    from psycopg_pool import ConnectionPool

    from app.store_pg import PostgresEventStore

    pool = ConnectionPool(database_url, min_size=1, max_size=2, open=True)
    try:
        pool.wait(timeout=30)
        store = PostgresEventStore(pool)
        if record:
            store = _RecordingStore(store)
        out = run_sweep(store, pathway, calendar, now, shard=(index, count)).to_json()
        if record:
            out["emitted_refs"] = store.refs
        return out
    finally:
        pool.close()


def _noop(_: int) -> int:
    return _


def make_sweep_executor(workers: int):
    """A reusable pool of `workers` spawned processes for `run_sweep_parallel`. A long-running
    scheduler creates it once and keeps it, so each sweep pays no process start-up (about a
    second); `warm=True` semantics: the workers import the sweep modules before the first use."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    list(executor.map(_noop, range(workers * 4)))
    return executor


def run_sweep_parallel(
    database_url: str,
    pathway: Pathway,
    calendar: WorkingCalendar,
    now: datetime,
    workers: int = 4,
    executor=None,
    record: bool = False,
) -> SweepResult:
    """The same sweep, split into `workers` disjoint slices of the cases swept by separate
    processes (each with its own connection pool). Replaying every open case is CPU-bound
    Python, so slices scale with cores. The result is identical to `run_sweep`: the slices
    cover every case exactly once, event ids are deterministic, and every append still goes
    through the transactional store, so a slice that overlaps another or is run twice can
    only produce no-ops.

    Processes are started with `spawn` (never `fork`) so this is safe to call from a threaded
    server process. Pass an `executor` from `make_sweep_executor` to reuse warm workers;
    otherwise a pool is created and shut down for this one sweep. `record=True` also collects
    `SweepResult.emitted_refs` (what the workers appended).
    """
    if workers < 1:
        raise ValueError("workers must be >= 1")
    jobs = [(database_url, pathway, calendar, now, k, workers, record) for k in range(workers)]
    total = SweepResult(sim_time=_utc(now).isoformat())
    own = executor is None
    if own:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    try:
        for part in executor.map(_sweep_shard, jobs):
            total.cases_evaluated += part["cases_evaluated"]
            total.mark_overdue += part["mark_overdue"]
            total.escalate += part["escalate"]
            total.handoff_bounced += part["handoff_bounced"]
            total.rejected += part["rejected"]
            total.errors += part["errors"]
            total.emitted_refs += [tuple(r) for r in part.get("emitted_refs", ())]
    finally:
        if own:
            executor.shutdown()
    return total


# ---- the scheduler route's long-lived pool (A17) ---------------------------------------------------

DEFAULT_MAX_WORKERS = 4


def default_sweep_workers() -> int:
    return max(1, min(DEFAULT_MAX_WORKERS, os.cpu_count() or 1))


def sweep_workers_from_env(environ=None) -> int:
    """`SWEEP_WORKERS` (an integer >= 1), default min(4, cpus). 1 means the single-process sweep.
    An unusable value (not a number, or below 1) falls back to the default and is logged."""
    raw = (os.environ if environ is None else environ).get("SWEEP_WORKERS", "").strip()
    if not raw:
        return default_sweep_workers()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        log.warning("SWEEP_WORKERS=%r is not an integer >= 1; using %d", raw, default_sweep_workers())
        return default_sweep_workers()
    return value


class SweepPool:
    """The scheduler route's sweep runner: `workers` spawned processes started once and kept (so a
    sweep pays no process start-up), created lazily on the first sweep. Sweeps are serialised by a
    lock, so overlapping calls never fight over the same connections; the sweep itself is
    idempotent, so serialising only costs time. A pool whose worker died is replaced and the sweep
    retried once. `workers == 1` never creates a pool: the caller runs `run_sweep` in-process."""

    def __init__(self, workers: int) -> None:
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self.workers = workers
        self._executor = None
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self._executor is not None

    def warm(self) -> None:
        """Start the worker processes now (they import the sweep modules before the first use), so
        the first sweep does not pay for it. The API does not call this at start-up; the load test does."""
        with self._lock:
            if self._executor is None:
                self._executor = make_sweep_executor(self.workers)

    def run(self, database_url: str, pathway: Pathway, calendar: WorkingCalendar, now: datetime,
            record: bool = False) -> SweepResult:
        from concurrent.futures.process import BrokenProcessPool

        with self._lock:
            for attempt in (1, 2):
                if self._executor is None:
                    self._executor = make_sweep_executor(self.workers)
                try:
                    return run_sweep_parallel(database_url, pathway, calendar, now, workers=self.workers,
                                              executor=self._executor, record=record)
                except BrokenProcessPool:
                    log.error("sweep worker pool broke (attempt %d); replacing it", attempt)
                    self._discard()
                    if attempt == 2:
                        raise
        raise AssertionError("unreachable")

    def _discard(self) -> None:
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def shutdown(self) -> None:
        with self._lock:
            self._discard()
