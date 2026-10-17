"""Identity: signed dev tokens for people, static service tokens for services (ADR-012).

Who is calling is always decided here, never by the request body. The event `actor` recorded
in the log is derived from the identity (`role:name`), so a client cannot claim to be a
clinician, and automated actors (`system:`, `scheduler`, `watchdog`, `model:`) cannot
authenticate as a user at all: the six user roles are the only roles a token can carry.

Dev tokens are `base64url(json).base64url(HMAC-SHA256(secret, first part))`. They do not
expire (the API never reads the wall clock); production replaces this module with an OIDC
verifier behind the same `Identity`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import DEV_AUTH_SECRET_DEFAULT, SCHEDULER_TOKEN_DEFAULT, settings

USER_ROLES = (
    "lab",
    "primary_care_owner",
    "specialist_owner",
    "coordinator",
    "patient",
    "supervisor",
)
SCHEDULER_ROLE = "scheduler"  # service role; only reachable with the service token
# Names that can never be a user role, even by accident.
AUTOMATED_ROLE_NAMES = ("system", "scheduler", "watchdog", "model")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}$")  # no ":" so role:name parses
PATIENT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/._-]{0,99}$")
PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@/-]{0,99}$")
PROVIDER_ROLES = ("primary_care_owner", "specialist_owner")  # roles that act for a provider


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    kind: str  # "user" or "service"
    role: str
    name: str
    patient_ref: str | None = None
    # The provider (organisation) a clinician acts for. Referral handoffs are addressed to a
    # provider; a clinician may answer one addressed to theirs (A5). Defaults to the name.
    provider: str | None = None

    @property
    def effective_provider(self) -> str:
        return self.provider or self.name

    @property
    def actor(self) -> str:
        """The string recorded as `actor` on events this identity causes."""
        return f"system:{self.name}" if self.kind == "service" else f"{self.role}:{self.name}"

    @property
    def user_id(self) -> str:
        return self.actor


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def validate_user(
    role: str, name: str, patient_ref: str | None, provider: str | None = None
) -> None:
    if role in AUTOMATED_ROLE_NAMES:
        raise AuthError(f"automated actors cannot authenticate: {role!r}")
    if role not in USER_ROLES:
        raise AuthError(f"unknown role {role!r}")
    if not NAME_RE.match(name or ""):
        raise AuthError("name must be 1-64 characters: letters, digits, space . _ @ -")
    if patient_ref is not None:
        if role != "patient":
            raise AuthError("patient_ref is only for the patient role")
        if not PATIENT_REF_RE.match(patient_ref):
            raise AuthError("bad patient_ref")
    if provider is not None:
        if role not in PROVIDER_ROLES:
            raise AuthError("provider is only for primary_care_owner and specialist_owner")
        if not PROVIDER_RE.match(provider):
            raise AuthError("bad provider")


def issue_token(
    role: str,
    name: str,
    patient_ref: str | None = None,
    secret: str | None = None,
    provider: str | None = None,
) -> str:
    validate_user(role, name, patient_ref, provider)
    claims: dict = {"v": 1, "role": role, "name": name}
    if patient_ref is not None:
        claims["patient_ref"] = patient_ref
    if provider is not None:
        claims["provider"] = provider
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_sign(body, secret or settings.dev_auth_secret)}"


def verify_token(token: str, secret: str | None = None) -> Identity:
    try:
        body, signature = token.split(".")
    except ValueError:
        raise AuthError("malformed token") from None
    if not hmac.compare_digest(signature, _sign(body, secret or settings.dev_auth_secret)):
        raise AuthError("bad signature")
    try:
        claims = json.loads(_unb64(body))
        role, name, patient_ref = claims["role"], claims["name"], claims.get("patient_ref")
        provider = claims.get("provider")
        validate_user(role, name, patient_ref, provider)  # a signed token still cannot be a model
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("malformed token") from exc
    return Identity("user", role, name, patient_ref, provider)


def service_identity(token: str) -> Identity | None:
    """The identity for a configured service token, else None. Constant-time comparison."""
    if hmac.compare_digest(token.encode(), settings.scheduler_service_token.encode()):
        return Identity("service", SCHEDULER_ROLE, "scheduler")
    return None


def authenticate(token: str) -> Identity:
    return service_identity(token) or verify_token(token)


def assert_secure_config() -> None:
    """Refuse to start outside dev with the built-in development secrets."""
    if settings.app_env == "dev":
        return
    problems = []
    if settings.dev_auth_secret == DEV_AUTH_SECRET_DEFAULT and settings.dev_login_enabled:
        problems.append("DEV_AUTH_SECRET is the built-in development default")
    if settings.scheduler_service_token == SCHEDULER_TOKEN_DEFAULT:
        problems.append("SCHEDULER_SERVICE_TOKEN is the built-in development default")
    if problems:
        raise RuntimeError(f"APP_ENV={settings.app_env!r}: " + "; ".join(problems))


_bearer = HTTPBearer(auto_error=False)


def optional_identity(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Identity | None:
    if creds is None:
        return None
    try:
        return authenticate(creds.credentials)
    except AuthError:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"}) from None


def require_identity(identity: Identity | None = Depends(optional_identity)) -> Identity:
    if identity is None:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    return identity
