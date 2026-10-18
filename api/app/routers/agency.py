"""The agency's routes (A22, ADR-033): start a run, read the ledger, the org chart, the inbox, a case's summary.

Agents are labour, not authority. Nothing here changes a case: the only write is `POST /agency/run`, which starts
the agents, and what they do on a case goes through the paths that already exist (a draft through the drafting
route's code, waiting for a person's approval). The inbox lists the tasks that need a person, with the material an
agent prepared; the person acts on the case page, through the ordinary routes and checks.

Roles (`app.policy.ROUTE_POLICY`):
* `POST /agency/run`             supervisor, or the agency supervisor's service token
* `GET  /agency/org`, `/agency/runs`, `/agency/tasks`   coordinator, supervisor
* `GET  /agency/inbox`           owners (items on cases they own), coordinator, supervisor
* `GET  /cases/{id}/agency`      owners (their own case), coordinator, supervisor
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app import policy
from app.agency import runner
from app.agency.agents import EXECUTIONERS, HEADS, SUPERVISOR_TOOLS
from app.agency.ledger import DEPARTMENTS, NEEDS_HUMAN, STATUSES, Task
from app.auth import Identity, require_identity
from app.routers import cases

router = APIRouter(tags=["agency"])

PRINCIPLE = ("Agents prepare work; people decide. No agent can close a case, accept an escalation, approve a "
             "message or sign anything. Every agent acts through the same API, policy and approval gate as a "
             "person, and its work is recorded in the task ledger.")
_HUMAN_KEYS = ("title", "summary", "assignee_role", "recommended_action", "skipped", "tool_calls")
StatusFilter = Literal["queued", "running", "done", "failed", "needs_human"]
DepartmentFilter = Literal["supervisor", "outreach", "referral_scheduling", "evidence_closure", "patient_support",
                           "quality_ops"]


@router.post("/agency/run")
def run(identity: Identity = Depends(require_identity)) -> dict:
    """One run at the current sim time; a second call at the same sim time returns the same run (`created: false`)."""
    return runner.run_agency(trigger="manual", created_by=identity.actor)


@router.get("/agency/runs")
def runs(limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    L = runner.ledger()
    return [runner.run_summary(r, L) for r in L.runs(limit)]


@router.get("/agency/tasks")
def tasks(status: StatusFilter | None = None, department: DepartmentFilter | None = None,
          case_id: UUID | None = None, run_id: UUID | None = None,
          limit: int = Query(default=500, ge=1, le=5000)) -> list[dict]:
    return [t.to_json() for t in runner.ledger().query(status=status, department=department, case_id=case_id,
                                                        run_id=run_id, limit=limit)]


def _counts(rows: list[Task]) -> dict:
    out = {s: 0 for s in STATUSES}
    for t in rows:
        out[t.status] += 1
    return out


@router.get("/agency/org")
def org() -> dict:
    L = runner.ledger()
    everything = L.query()
    by_type: dict[str, list[Task]] = {}
    for t in everything:
        by_type.setdefault(t.agent_type, []).append(t)
    departments = []
    for dept in DEPARTMENTS:
        head = HEADS[dept]
        execs = []
        for name in head.spec.executioners:
            spec = EXECUTIONERS[name].spec
            execs.append({"agent_type": name, "label": spec.label, "tools": list(spec.tools),
                          "output_schema": spec.output.__name__, "replaces": list(spec.replaces),
                          "counts": _counts(by_type.get(name, []))})
        actor = runner.identity_for(dept).actor
        departments.append({
            "department": dept, "label": head.spec.label, "actor": actor, "head_tools": list(head.spec.head_tools),
            "capabilities": sorted(policy.AGENCY_CAUSES.get(actor, ())), "executioners": execs,
            "counts": _counts([t for t in everything if t.department == dept and t.agent_type != "department_head"]),
        })
    latest = L.runs(1)
    return {"principle": PRINCIPLE,
            "supervisor": {"actor": runner.SUPERVISOR_ACTOR, "agent_type": "supervisor", "tools": list(SUPERVISOR_TOOLS)},
            "departments": departments,
            "last_run": runner.run_summary(latest[0], L) if latest else None}


def _inbox_item(t: Task) -> dict:
    out = t.output or {}
    material = {k: v for k, v in out.items() if k not in _HUMAN_KEYS and v is not None}
    links = {"draft_id": out["draft_id"]} if out.get("draft_id") else {}
    return {"task_id": str(t.id), "run_id": str(t.run_id), "department": t.department, "agent_type": t.agent_type,
            "case_id": str(t.case_id) if t.case_id else None, "created_at": t.created_at.isoformat(),
            "assignee_role": out.get("assignee_role") or "coordinator", "title": out.get("title") or t.agent_type,
            "summary": out.get("summary", ""), "recommended_action": out.get("recommended_action") or "review",
            "material": material, "links": links}


def _owns(identity: Identity, case_id: UUID | None) -> bool:
    if identity.role not in policy.OWNER_SCOPED:
        return True
    if case_id is None:
        return False
    model = cases.store.read_model(case_id, cases.pathway)
    return model is not None and model.owner == identity.actor


@router.get("/agency/inbox")
def inbox(identity: Identity = Depends(require_identity)) -> list[dict]:
    """Tasks that need a person, newest first, with what the agent prepared. Owners see items on cases they own."""
    rows = [t for t in runner.ledger().query(status=NEEDS_HUMAN) if _owns(identity, t.case_id)]
    return [_inbox_item(t) for t in reversed(rows)]


@router.get("/cases/{case_id}/agency")
def case_agency(case_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    model = cases.store.read_model(case_id, cases.pathway)
    if model is None:
        raise HTTPException(404, "case not found")
    policy.authorize_owner_scope(identity, "view_agency", model)
    rows = [t for t in runner.ledger().query(case_id=case_id) if t.agent_type != "department_head"]
    return {
        "case_id": str(case_id), "total": len(rows),
        "open": sum(t.status in ("queued", "running", NEEDS_HUMAN) for t in rows),
        "needs_human": sum(t.status == NEEDS_HUMAN for t in rows),
        "tasks": [{"task_id": str(t.id), "department": t.department, "agent_type": t.agent_type, "status": t.status,
                   "created_at": t.created_at.isoformat(), "title": (t.output or {}).get("title") or t.agent_type,
                   "summary": (t.output or {}).get("summary", "")} for t in reversed(rows)],
    }
