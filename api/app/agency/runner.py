"""One agency run: supervisor -> department heads -> executioners, inside a budget (A22, ADR-033).

    run_agency(now, trigger)
      1. the run's root task (id = uuid5 of trigger and sim time: the same run twice inserts nothing)
      2. reconcile: tasks left running by a run that died are failed; `needs_human` tasks whose case has moved on
         (or whose draft a person decided) are resolved, naming who resolved them
      3. the supervisor reads the cases changed since the previous run plus the overdue and flagged ones (all cases on
         a first run), metrics, alerts and the ledger, and writes one dispatch per department with its work items
         (case ids and reason codes) and a rationale
      4. each head turns its dispatch into executioner tasks (rules, then the planner's priority order), skipping a
         case that already has an open task of that type, or one made since the case last changed
      5. the executioners run in order until the time budget (which also covers steps 3 and 4) or the task cap is
         reached; the rest are failed with
         `budget_exhausted` (not carried over: the next run plans afresh). A follow-up an executioner asks for (one
         level, same case, the same department's agent types only) joins the queue
      6. case-less items superseded by this run's are resolved; the root records the outcome

`after_sweep(now)` is the scheduler hook: every `AGENCY_EVERY_SWEEPS` sweeps (default 6, 0 = never) it starts a
run in the background, unless one is already going. Nothing in a run writes a case event as the agency; what it
causes (a draft) goes through the path that owns it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from app.agency import config as agency_config
from app.agency.agents import EXECUTIONERS, HEADS, SUPERVISOR_TOOLS, Outcome, WorkItem, route_case
from app.agency.facts import facts_for
from app.agency.ledger import (
    DEPARTMENTS,
    DONE,
    FAILED,
    NEEDS_HUMAN,
    QUEUED,
    RUNNING,
    STATUSES,
    SUPERVISOR,
    InMemoryTaskLedger,
    LedgerError,
    PostgresTaskLedger,
    Task,
    TaskLedger,
    dispatch_id_for,
    run_id_for,
    task_id_for,
)
from app.agency.planner import DepartmentPlan, TaskOrder, TemplatePlanner, get_planner
from app.agency.tools import ToolBox, ToolDenied, ToolFailed
from app.auth import AGENCY_ROLE, Identity
from app.state_machine import is_automated

log = logging.getLogger("app.agency")

SUPERVISOR_ACTOR = "system:agency:supervisor"
RULES_PROVIDER = "rules"
# Within this many days of the last one, no new task of these types is made for a case even if the case changed
# (the sweep's own events change it): a person is not sent a fresh draft or call sheet every run.
COOLDOWN_DAYS: dict[str, float] = {"draft_message": 3.0, "followup_call_prep": 1.0, "callback_brief": 1.0,
                                   "sla_risk_explainer": 1.0, "prepare_handoff_pack": 1.0}


def identity_for(department: str) -> Identity:
    """The in-process identity of an agent: `system:agency:<department>`. No token maps to a department."""
    return Identity("service", AGENCY_ROLE, f"agency:{department}")


# ---------------------------------------------------------------- the ledger the API uses

_mem: InMemoryTaskLedger = InMemoryTaskLedger()
_pg: PostgresTaskLedger | None = None


def ledger() -> TaskLedger:
    """The Postgres ledger when the event store is Postgres, else the in-memory one (like the draft store)."""
    global _pg
    from app.routers import cases

    pool = getattr(cases.store, "_pool", None)
    if pool is None:
        return _mem
    if _pg is None or _pg.pool is not pool:
        _pg = PostgresTaskLedger(pool)
    return _pg


def reset_memory_ledger() -> InMemoryTaskLedger:
    """A fresh in-memory ledger (tests)."""
    global _mem
    _mem = InMemoryTaskLedger()
    return _mem


# ---------------------------------------------------------------- provenance for rules and tools


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _rules_provenance(agent_type: str, payload) -> dict:
    return {"prompt_hash": _digest([agent_type, payload]), "provider": RULES_PROVIDER,
            "model_id": f"agency-{agent_type.replace('_', '-')}-v1", "fallback_used": False}


def _finish(L: TaskLedger, task: Task, status: str, now: datetime, output: dict, prov: dict) -> Task:
    return L.finish(task.id, status, now, output, prompt_hash=prov.get("prompt_hash"), provider=prov["provider"],
                    model_id=prov["model_id"], fallback_used=bool(prov["fallback_used"]))


# ---------------------------------------------------------------- reconcile


def _first_human_after(case_id: UUID, after: datetime) -> str | None:
    from app.routers import cases

    for e in cases.store.events_for(case_id):
        at = e.at
        if at >= after and not is_automated(e.actor):
            return e.actor
    return None


def _resolution(task: Task, store, pathway) -> tuple[str, str, dict] | None:
    """(status, resolved_by, resolution) when a waiting task no longer needs a person, else None."""
    if task.case_id is None:
        return None
    f = facts_for(task.case_id, store, pathway)
    if f is None:
        return FAILED, SUPERVISOR_ACTOR, {"reason": "case_unreadable"}
    out = task.output or {}
    if task.agent_type == "draft_message" and out.get("draft_id"):
        from app.routers import drafts

        d = drafts.draft_store().get(UUID(out["draft_id"]))
        if d is not None and (d.approved_at or d.rejected_at):
            decision = "approved" if d.approved_at else "rejected"
            return DONE, d.approved_by or d.rejected_by, {"reason": f"draft_{decision}"}
    if task.agent_type == "triage_reply" and task.input.get("reply_event_id") not in f.pending_replies:
        by = _first_human_after(task.case_id, task.created_at) or SUPERVISOR_ACTOR
        return DONE, by, {"reason": "reply_handled"}
    if task.agent_type == "callback_brief" and f.last_human_activity_at and f.callback_requested_at \
            and f.last_human_activity_at > f.callback_requested_at:
        return DONE, _first_human_after(task.case_id, task.created_at) or SUPERVISOR_ACTOR, {"reason": "called_back"}
    expected = task.input.get("state")
    if not f.open or (expected and f.state != expected):
        by = _first_human_after(task.case_id, task.created_at) or SUPERVISOR_ACTOR
        return DONE, by, {"reason": "case_moved_on", "state": f.state}
    return None


def reconcile(L: TaskLedger, now: datetime, run_id: UUID) -> dict:
    from app.routers import cases

    abandoned = resolved = 0
    for status in (RUNNING, QUEUED):
        for t in L.query(status=status):
            if t.run_id == run_id:
                continue
            prov = {"prompt_hash": t.prompt_hash, "provider": t.provider or RULES_PROVIDER,
                    "model_id": t.model_id or f"agency-{t.agent_type.replace('_', '-')}-v1",
                    "fallback_used": bool(t.fallback_used)}
            try:
                _finish(L, t, FAILED, now, {"summary": "Abandoned: the run that held it did not finish.",
                                            "error": "abandoned"}, prov)
                abandoned += 1
            except LedgerError:
                pass  # another process moved it on first
    for t in L.query(status=NEEDS_HUMAN):
        r = _resolution(t, cases.store, cases.pathway)
        if r is not None:
            try:
                L.resolve(t.id, r[0], now, r[1], r[2])
                resolved += 1
            except LedgerError:
                pass
    return {"abandoned": abandoned, "resolved": resolved}


# ---------------------------------------------------------------- planning


def _recent(L: TaskLedger, case_id: str, agent_type: str, key: str, case_seq: int | None, now: datetime) -> str | None:
    """Why a new task of this type for this case should not be made now, or None."""
    for t in L.query(case_id=UUID(case_id), agent_type=agent_type):
        if key and t.input.get("key", "") != key:
            continue
        if t.status == FAILED and (t.output or {}).get("error") in ("budget_exhausted", "abandoned"):
            continue  # never started: planned afresh
        if t.status in (QUEUED, RUNNING, NEEDS_HUMAN):
            return "open_task_exists"
        if case_seq is not None and t.input.get("case_seq") == case_seq:
            return "unchanged_since_last_task"
        if (now - t.created_at).total_seconds() < COOLDOWN_DAYS.get(agent_type, 0.0) * 86400 and t.status != FAILED:
            return "recently_done"
    return None


def _recent_caseless(L: TaskLedger, agent_type: str, key: str) -> str | None:
    for t in L.query(agent_type=agent_type):
        if t.case_id is None and t.input.get("key") == key and t.status != FAILED:
            return "already_done_today"
    return None


def _dispatch_rationale(department: str, work: list[WorkItem]) -> str:
    if not work:
        return f"{HEADS[department].spec.label}: nothing routed this run."
    reasons: dict[str, int] = {}
    for w in work:
        reasons[w.reason] = reasons.get(w.reason, 0) + 1
    parts = ", ".join(f"{n} {r.replace('_', ' ')}" for r, n in sorted(reasons.items()))
    return f"{HEADS[department].spec.label}: {len(work)} case(s) routed ({parts})."[:400]


# ---------------------------------------------------------------- one run

_lock = threading.Lock()


def run_agency(now: datetime | None = None, *, trigger: str = "manual", created_by: str = SUPERVISOR_ACTOR,
               budget_seconds: float | None = None, max_tasks: int | None = None, planner=None,
               monotonic=time.monotonic, wait: bool = True) -> dict:
    """Run the agency once at sim time `now`. Idempotent per (trigger, now). Returns the run's summary."""
    from app.clock import clock

    now = now or clock.now()
    L = ledger()
    run_id = run_id_for(trigger, now)
    existing = L.get(run_id)
    if existing is not None:
        return {**run_summary(existing, L), "created": False}
    if not _lock.acquire(blocking=wait, timeout=60 if wait else -1):
        return {"run_id": str(run_id), "status": "busy", "created": False}
    try:
        existing = L.get(run_id)
        if existing is not None:
            return {**run_summary(existing, L), "created": False}
        return {**_run(L, run_id, now, trigger, created_by, budget_seconds, max_tasks, planner, monotonic),
                "created": True}
    finally:
        _lock.release()


def _run(L, run_id, now, trigger, created_by, budget_seconds, max_tasks, planner, monotonic) -> dict:
    from app.routers import cases

    t0 = monotonic()
    budget = agency_config.budget_seconds() if budget_seconds is None else budget_seconds
    cap = agency_config.max_tasks() if max_tasks is None else max_tasks
    planner = planner or get_planner()
    root = Task(run_id, run_id, None, SUPERVISOR, SUPERVISOR, None, QUEUED,
                {"trigger": trigger, "sim_at": now.isoformat(), "budget_seconds": budget, "max_tasks": cap,
                 "planner": planner.name}, now, created_by)
    if not L.add(root):
        return run_summary(L.get(run_id), L)
    L.start(run_id, now)
    housekeeping = reconcile(L, now, run_id)

    def over() -> bool:  # the budget covers the supervisor's reading and every head's planning, not only executioners
        return monotonic() - t0 > budget

    # -- the supervisor: reads, routes, dispatches. It has read tools only. Its fact-building is bounded: the cases
    #    changed since the previous run, plus every open case that is overdue or flagged (all cases on a first run).
    since = _previous_run_at(L, run_id)
    sup = ToolBox(identity_for(SUPERVISOR), SUPERVISOR_TOOLS, now, L)
    exhausted = over()
    worklist = [] if exhausted else sup.call("read_active_cases", since=since)
    metrics = {} if exhausted else sup.call("read_metrics")
    alerts = [] if exhausted else sup.call("read_alerts")
    waiting = [] if exhausted else sup.call("read_ledger", status=NEEDS_HUMAN)
    exhausted = exhausted or over()
    work: dict[str, list[WorkItem]] = {d: [] for d in DEPARTMENTS}
    for f in worklist:
        for dept, reason in route_case(f, now):
            work[dept].append(WorkItem(f.case_id, reason, f))
    work["quality_ops"].insert(0, WorkItem(None, "daily"))

    queue: list[tuple[Task, TaskOrder, str]] = []
    created = 0
    rationale: dict[str, str] = {}
    for dept in DEPARTMENTS:
        items = work[dept]
        dispatch = Task(dispatch_id_for(run_id, dept), run_id, run_id, dept, "department_head", None, QUEUED,
                        {"work": [{"case_id": w.case_id, "reason": w.reason} for w in items],
                         "rationale": _dispatch_rationale(dept, [w for w in items if w.case_id]),
                         "alerts_open": len(alerts), "waiting_for_people": len(waiting)},
                        now, SUPERVISOR_ACTOR)
        L.add(dispatch)
        L.start(dispatch.id, now)
        if exhausted or over():
            exhausted = True
            rationale[dept] = dispatch.input["rationale"]
            _finish(L, dispatch, FAILED, now, {"summary": "Not planned: the run's time budget was used up.",
                                               "error": "budget_exhausted", "rationale": rationale[dept]},
                    _rules_provenance("department_head", dispatch.input))
            continue
        head = HEADS[dept]
        candidates = head.candidates([w for w in items if w.case_id is not None], now)
        kept, skipped = [], []
        for o in candidates.orders:
            if o.agent_type not in head.spec.executioners:
                skipped.append({"agent_type": o.agent_type, "reason": "not_this_department"})
                continue
            why = (_recent(L, o.case_id, o.agent_type, o.key, o.inputs.get("case_seq"), now) if o.case_id
                   else _recent_caseless(L, o.agent_type, o.key))
            if why:
                skipped.append({"case_id": o.case_id, "agent_type": o.agent_type, "reason": why})
            else:
                kept.append(o)
        plan, prov = planner.plan(dept, DepartmentPlan(orders=kept, deferred=candidates.deferred))
        planned = []
        for o in plan.orders:
            if created >= cap:
                skipped.append({"case_id": o.case_id, "agent_type": o.agent_type, "reason": "budget_exhausted"})
                continue
            task = _new_task(run_id, dispatch.id, dept, o, now)
            if L.add(task):
                created += 1
                queue.append((task, o, dept))
                planned.append({"task_id": str(task.id), "agent_type": o.agent_type, "case_id": o.case_id,
                                "reason": o.reason})
        rationale[dept] = plan.rationale or dispatch.input["rationale"]
        _finish(L, dispatch, DONE, now, {"orders": planned, "deferred": [d.model_dump() for d in plan.deferred],
                                         "skipped": skipped, "rationale": rationale[dept],
                                         "abstention_reason": prov.abstention_reason},
                {"prompt_hash": prov.prompt_hash, "provider": prov.provider, "model_id": prov.model_id,
                 "fallback_used": prov.fallback_used})

    # -- the executioners, in plan order, inside the same budget
    i = 0
    while i < len(queue):
        task, order, dept = queue[i]
        i += 1
        if over():
            exhausted = True
            _finish(L, task, FAILED, now, {"summary": "Not started: the run's time budget was used up.",
                                           "error": "budget_exhausted"}, _rules_provenance(task.agent_type, order.inputs))
            continue
        outcome_task, follow_ups = _execute(L, task, order, dept, now)
        if task.parent_id is not None and L.get(task.parent_id).agent_type == "department_head":
            for fu in follow_ups:
                if created >= cap:
                    exhausted = True
                    break
                # one level, the same case, an executioner of the agency (it runs in its own department, under
                # that department's identity and allow-list)
                if fu.agent_type not in EXECUTIONERS or fu.case_id != order.case_id:
                    continue
                if _recent(L, fu.case_id, fu.agent_type, fu.key, None, now):
                    continue
                owner_dept = EXECUTIONERS[fu.agent_type].spec.department
                child = _new_task(run_id, task.id, owner_dept, fu, now, key=f"after:{str(task.id)[:8]}")
                if L.add(child):
                    created += 1
                    queue.append((child, fu, owner_dept))
    superseded = _supersede_caseless(L, run_id, now)
    status = "budget_exhausted" if exhausted or _skipped_for_budget(L, run_id) else "done"
    summary = {"status": status, "housekeeping": {**housekeeping, "superseded": superseded},
               "rationale": rationale, "tasks_created": created, "cases_read": len(worklist),
               "read_since": since.isoformat() if since else None,
               "metrics": {k: metrics.get(k) for k in ("cases_open", "cases_closed")}}
    _finish(L, L.get(run_id), DONE, now, summary,
            {**_rules_provenance(SUPERVISOR, {d: [w.reason for w in work[d]] for d in DEPARTMENTS}),
             "model_id": "agency-supervisor-rules-v1"})
    return run_summary(L.get(run_id), L)


def _previous_run_at(L: TaskLedger, run_id: UUID) -> datetime | None:
    """The sim time of the latest earlier run that completed (not cut short by its budget), or None: then every case
    is read. A run that ran out of budget does not count, so the cases it never reached are read again."""
    for root in L.runs(50):
        if (root.run_id != run_id and root.status == DONE and root.input.get("sim_at")
                and (root.output or {}).get("status") == "done"):
            return datetime.fromisoformat(root.input["sim_at"])
    return None


def _skipped_for_budget(L: TaskLedger, run_id: UUID) -> bool:
    return any(any(s.get("reason") == "budget_exhausted" for s in (t.output or {}).get("skipped", []))
               for t in L.query(run_id=run_id, agent_type="department_head"))


def _new_task(run_id: UUID, parent_id: UUID, dept: str, o: TaskOrder, now: datetime, key: str | None = None) -> Task:
    case_id = UUID(o.case_id) if o.case_id else None
    k = key if key is not None else o.key
    return Task(task_id_for(run_id, case_id, o.agent_type, k), run_id, parent_id, dept, o.agent_type, case_id,
                QUEUED, {**o.inputs, "reason": o.reason, "key": o.key}, now, f"system:agency:{dept}")


def _execute(L: TaskLedger, task: Task, order: TaskOrder, dept: str, now: datetime) -> tuple[Task, list[TaskOrder]]:
    ex = EXECUTIONERS[task.agent_type]
    L.start(task.id, now)
    box = ToolBox(identity_for(dept), ex.spec.tools, now, L)
    prov = _rules_provenance(task.agent_type, task.input)
    follow: list[TaskOrder] = []
    try:
        outcome: Outcome = ex.run(box, order, now)
        if outcome.status not in (DONE, NEEDS_HUMAN, FAILED):
            raise ValueError(f"unknown outcome status {outcome.status!r}")
        if not isinstance(outcome.output, ex.spec.output):
            raise ValueError(f"{task.agent_type} must return {ex.spec.output.__name__}")
        checked = ex.spec.output.model_validate(outcome.output.model_dump())  # strict: re-validated
        output, status = checked.model_dump(), outcome.status
        follow = list(outcome.follow_ups)
        if outcome.provenance:
            prov = {**prov, **outcome.provenance}
    except ToolDenied as exc:
        output, status = {"summary": f"Refused: {exc}"[:300], "error": "tool_denied"}, FAILED
    except ToolFailed as exc:
        output, status = {"summary": str(exc)[:300], "error": "tool_failed"}, FAILED
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        output, status = {"summary": f"Invalid output: {type(exc).__name__}", "error": "invalid_output"}, FAILED
    except Exception as exc:  # noqa: BLE001 - an agent's bug fails its task, never the run
        log.exception("agency task %s (%s) failed", task.id, task.agent_type)
        output, status = {"summary": f"Error: {type(exc).__name__}", "error": "exception"}, FAILED
    output["tool_calls"] = box.calls
    return _finish(L, L.get(task.id), status, now, output, prov), follow if status != FAILED else []


def _supersede_caseless(L: TaskLedger, run_id: UUID, now: datetime) -> int:
    fresh = {t.agent_type: t for t in L.query(run_id=run_id) if t.case_id is None and t.parent_id != run_id
             and t.agent_type != "department_head"}
    n = 0
    for t in L.query(status=NEEDS_HUMAN):
        if t.case_id is None and t.run_id != run_id and t.agent_type in fresh:
            try:
                L.resolve(t.id, DONE, now, SUPERVISOR_ACTOR,
                          {"reason": "superseded", "by_task": str(fresh[t.agent_type].id)})
                n += 1
            except LedgerError:
                pass
    return n


# ---------------------------------------------------------------- read side


def _counts() -> dict:
    return {s: 0 for s in STATUSES}


def run_summary(root: Task, L: TaskLedger) -> dict:
    tasks = L.query(run_id=root.run_id)
    by_status = _counts()
    by_department = {d: {**_counts(), "total": 0} for d in DEPARTMENTS}
    rationale: dict[str, str] = {}
    for t in tasks:
        if t.agent_type == "department_head":
            rationale[t.department] = (t.output or {}).get("rationale") or t.input.get("rationale", "")
            continue
        if t.parent_id is None:
            continue
        by_status[t.status] += 1
        by_department[t.department][t.status] += 1
        by_department[t.department]["total"] += 1
    if root.status == RUNNING or root.status == QUEUED:
        status = "running"
    elif root.status == FAILED:
        status = "failed"
    else:
        status = (root.output or {}).get("status", "done")
    return {
        "run_id": str(root.run_id), "trigger": root.input.get("trigger"), "sim_at": root.input.get("sim_at"),
        "created_by": root.created_by, "status": status,
        "planner": {"prompt_hash": root.prompt_hash, "provider": root.provider, "model_id": root.model_id,
                    "fallback_used": root.fallback_used, "name": root.input.get("planner")},
        "started_at": root.started_at.isoformat() if root.started_at else None,
        "finished_at": root.finished_at.isoformat() if root.finished_at else None,
        "counts": {"total": sum(by_status.values()), "by_status": by_status, "by_department": by_department},
        "rationale": rationale,
    }


# ---------------------------------------------------------------- the scheduler hook

_sweeps = 0
_sweeps_lock = threading.Lock()


def after_sweep(now: datetime, *, background: bool = True) -> dict:
    """Count a sweep; every N-th starts a run (`AGENCY_EVERY_SWEEPS`, 0 = never). Never raises into the sweep."""
    global _sweeps
    every = agency_config.every_sweeps()
    with _sweeps_lock:
        _sweeps += 1
        n = _sweeps
    if every <= 0 or n % every:
        return {"sweeps": n, "every": every, "triggered": False}

    def go() -> None:
        try:
            run_agency(now, trigger="scheduler", wait=False)
        except Exception:  # noqa: BLE001 - the agency must never take the sweep down
            log.exception("agency run after sweep %d failed", n)

    if background:
        threading.Thread(target=go, name="agency-run", daemon=True).start()
    else:
        go()
    return {"sweeps": n, "every": every, "triggered": True}


def reset_sweep_counter() -> None:
    global _sweeps
    with _sweeps_lock:
        _sweeps = 0


# The template planner is the fallback for every model planner; exported for tests.
TEMPLATE_PLANNER = TemplatePlanner()
