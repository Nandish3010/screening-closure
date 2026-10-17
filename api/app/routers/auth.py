from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app import auth
from app.auth import AuthError, Identity, require_identity
from app.config import settings

router = APIRouter(tags=["auth"])


class DevLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    name: str
    patient_ref: str | None = None  # patients only: the Patient/<id> whose case they may see


@router.post("/auth/dev-login")
def dev_login(body: DevLogin) -> dict:
    """Development login: no password. Returns a signed bearer token for `role:name`."""
    if not settings.dev_login_enabled:
        raise HTTPException(404, "dev login is disabled")
    try:
        token = auth.issue_token(body.role, body.name, body.patient_ref)
    except AuthError as exc:
        status = 403 if body.role in auth.AUTOMATED_ROLE_NAMES else 422
        raise HTTPException(status, str(exc)) from exc
    return {"token": token, "token_type": "bearer", **_me(auth.verify_token(token))}


def _me(identity: Identity) -> dict:
    out = {
        "user_id": identity.user_id,
        "role": identity.role,
        "display_name": identity.name,
    }
    if identity.patient_ref:
        out["patient_ref"] = identity.patient_ref
    return out


@router.get("/me")
def me(identity: Identity = Depends(require_identity)) -> dict:
    return _me(identity)
