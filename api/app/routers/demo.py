"""The hosted demo's judge-experience routes (ADR-057). Registered ONLY when `auth.demo_controls_active()` (DEMO_SIMULATED_CLOCK=1 and
DEMO_DEPLOYMENT=1 with the simulation clock); in any other configuration these paths do not exist (404).

    GET  /demo/guide?session=<random id>   every role   one fresh case per guided step for this browser session
    GET  /demo/credentials                 public      the four judge logins; 404 unless the operator opted in at deploy time
    POST /demo/reset                       supervisor   ask the host to restore the seeded snapshot (rate limited; who and when kept)
    POST /demo/missed-followup             supervisor   move the sim clock past a case's follow-up and let the real engine catch it

Nothing here writes a case event. See app.demo_controls.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict

from app import demo_controls
from app.auth import Identity, require_identity

router = APIRouter(prefix="/demo", tags=["demo"])

SESSION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class MissedFollowup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: UUID


def _problem(exc: demo_controls.DemoError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/guide")
def guide(session: str = Query(..., description="a random id the browser keeps for this tab"), identity: Identity = Depends(require_identity)) -> dict:
    if not SESSION.fullmatch(session):
        raise HTTPException(422, "session must be 8 to 64 letters, digits, '-' or '_'")
    return {"steps": demo_controls.pick_cases(session), "status": demo_controls.status()}


@router.get("/credentials")
def credentials(response: Response) -> dict:
    """PUBLIC and OFF by default: the four judge logins, only when the operator opted in at deploy time (DEMO_SHOW_CREDENTIALS=1 with the
    logins rendered from Secret Manager). 404 otherwise, so a deployment that did not opt in is indistinguishable from one without it."""
    logins = demo_controls.judge_logins()
    if logins is None:
        raise HTTPException(404, "not offered")
    response.headers["Cache-Control"] = "no-store"
    return {"logins": logins}


@router.post("/reset", status_code=202)
def reset(identity: Identity = Depends(require_identity)) -> dict:
    try:
        return demo_controls.request_reset(identity)
    except demo_controls.DemoError as exc:
        raise _problem(exc) from exc


@router.post("/missed-followup")
def missed_followup(body: MissedFollowup, identity: Identity = Depends(require_identity)) -> dict:
    try:
        return demo_controls.missed_followup(body.case_id)
    except demo_controls.DemoError as exc:
        raise _problem(exc) from exc
