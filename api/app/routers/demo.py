"""The hosted demo's judge-experience routes (ADR-058). Registered ONLY when `auth.demo_controls_active()` (DEMO_SIMULATED_CLOCK=1 and
DEMO_DEPLOYMENT=1 with the simulation clock); in any other configuration these paths do not exist (404).

    POST /demo/sign-in                     public       one-click sign-in as a fixed demo role (ADR-060); tokens, never a password
    GET  /demo/guide?session=<random id>   every role   one fresh case per guided step for this browser session
    POST /demo/reset                       supervisor   ask the host to restore the seeded snapshot (rate limited; who and when kept)
    POST /demo/missed-followup             supervisor   move the sim clock past a case's follow-up and let the real engine catch it

Nothing here writes a case event. See app.demo_controls.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app import demo_controls, demo_signin
from app.auth import Identity, require_identity

router = APIRouter(prefix="/demo", tags=["demo"])

SESSION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class MissedFollowup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: UUID


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str


def _visitor(request: Request) -> str:
    """The edge (nginx) overwrites X-Forwarded-For with the visitor's address; the API port itself is not public."""
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


def _problem(exc: demo_controls.DemoError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.post("/sign-in")
def sign_in(body: SignIn, request: Request) -> dict:
    """ADR-060: one click per role on the hosted demo. Public (it is how a judge gets a session), rate limited, 404 outside the gate."""
    try:
        return demo_signin.sign_in(body.role, _visitor(request))
    except demo_signin.SignInError as exc:
        # `detail` as every other refusal; `code` is stable for the web to act on (S1); Retry-After on 429 and on "warming up".
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse({"detail": exc.message, "code": exc.code, "retry_after": exc.retry_after}, status_code=exc.status, headers=headers)


@router.get("/guide")
def guide(session: str = Query(..., description="a random id the browser keeps for this tab"), identity: Identity = Depends(require_identity)) -> dict:
    if not SESSION.fullmatch(session):
        raise HTTPException(422, "session must be 8 to 64 letters, digits, '-' or '_'")
    return {"steps": demo_controls.pick_cases(session), "status": demo_controls.status()}


@router.post("/reset", status_code=202)
def reset(identity: Identity = Depends(require_identity)) -> dict:
    try:
        return demo_controls.request_reset(identity)
    except demo_controls.DemoError as exc:
        raise _problem(exc) from exc


@router.post("/missed-followup")
def missed_followup(body: MissedFollowup, identity: Identity = Depends(require_identity)) -> dict:
    try:
        return demo_controls.missed_followup(body.case_id, f"{identity.name} ({identity.role})")
    except demo_controls.DemoError as exc:
        raise _problem(exc) from exc
