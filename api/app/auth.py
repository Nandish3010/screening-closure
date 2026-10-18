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
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import (
    AUTOMATION_TOKEN_DEFAULT,
    DEV_AUTH_SECRET_DEFAULT,
    SCHEDULER_TOKEN_DEFAULT,
    WATCHDOG_TOKEN_DEFAULT,
    settings,
)

USER_ROLES = (
    "lab",
    "primary_care_owner",
    "specialist_owner",
    "coordinator",
    "patient",
    "supervisor",
)
SCHEDULER_ROLE = "scheduler"  # service role; only reachable with the service token
WATCHDOG_ROLE = "watchdog"  # service role; may emit watchdog_flagged and nothing else (A11)
AUTOMATION_ROLE = "automation"  # service role; the A21 automation actor (system:automation), never closes a case
# Names that can never be a user role, even by accident.
AUTOMATED_ROLE_NAMES = ("system", "scheduler", "watchdog", "model", "automation")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}$")  # no ":" so role:name parses
PATIENT_REF_RE = re.compile(r"^Patient/[A-Za-z0-9-]{1,64}$")  # the same shape a case's patient_ref must have (SEC-1)
PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@/-]{0,99}$")
PROVIDER_ROLES = ("primary_care_owner", "specialist_owner")  # roles that act for a provider


# Fixed namespace for opaque user ids (never change: ids would change and so would every
# actor already written to the log).
USER_ID_NAMESPACE = uuid.UUID("a2c1f0d4-8b6e-4c53-9f1a-5d7e3b2a9c60")
USER_ID_LENGTH = 12  # hex digits of the uuid5: short, URL-safe, not derived from anything readable


def opaque_user_id(role: str, name: str) -> str:
    """The opaque id behind `role:user_id` actors (A14, ADR-019): uuid5 of role+name, short form.

    The same person logging in twice as the same role gets the same id; the name itself is
    written only to the `users` table (erasable), never to the event log.
    """
    return uuid.uuid5(USER_ID_NAMESPACE, f"{role}|{name}").hex[:USER_ID_LENGTH]


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    kind: str  # "user" or "service"
    role: str
    name: str  # the display name for a user (never written to the event log); the service name
    patient_ref: str | None = None
    # The provider (organisation) a clinician acts for. Referral handoffs are addressed to a
    # provider; a clinician may answer one addressed to theirs (A5). Defaults to the name.
    provider: str | None = None

    @property
    def effective_provider(self) -> str:
        return self.provider or self.name

    @property
    def user_id(self) -> str:
        """The opaque id for a user (A14); a service is its own id (`system:<service>`)."""
        return f"system:{self.name}" if self.kind == "service" else opaque_user_id(self.role, self.name)

    @property
    def actor(self) -> str:
        """The string recorded as `actor` on events this identity causes: `role:user_id` for
        people (the id is opaque, A14), `system:<service>` for services."""
        return self.user_id if self.kind == "service" else f"{self.role}:{self.user_id}"


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
    key = secret or settings.dev_auth_secret
    if not _is_dev() and key == DEV_AUTH_SECRET_DEFAULT:
        raise AuthError("the development secret is not usable outside APP_ENV=dev")
    claims: dict = {"v": 1, "role": role, "name": name}
    if patient_ref is not None:
        claims["patient_ref"] = patient_ref
    if provider is not None:
        claims["provider"] = provider
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_sign(body, key)}"


def _is_dev() -> bool:
    return settings.app_env == "dev"


def verify_token(token: str, secret: str | None = None) -> Identity:
    try:
        body, signature = token.split(".")
    except ValueError:
        raise AuthError("malformed token") from None
    key = secret or settings.dev_auth_secret
    if not _is_dev():
        # Outside dev the published development secret never authenticates anyone: neither when it
        # is (mis)configured as the secret, nor when a token was simply signed with it (SEC-1).
        # Checked before the real comparison and in constant time.
        if key == DEV_AUTH_SECRET_DEFAULT:
            raise AuthError("the development secret is not accepted outside APP_ENV=dev")
        if hmac.compare_digest(signature, _sign(body, DEV_AUTH_SECRET_DEFAULT)):
            raise AuthError("token signed with the development secret")
    if not hmac.compare_digest(signature, _sign(body, key)):
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
    # An empty configured token never matches (outside dev the start-up check refuses it anyway).
    if settings.scheduler_service_token and hmac.compare_digest(
        token.encode(), settings.scheduler_service_token.encode()
    ):
        return Identity("service", SCHEDULER_ROLE, "scheduler")
    if settings.watchdog_service_token and hmac.compare_digest(
        token.encode(), settings.watchdog_service_token.encode()
    ):
        return Identity("service", WATCHDOG_ROLE, "watchdog")
    if settings.automation_service_token and hmac.compare_digest(
        token.encode(), settings.automation_service_token.encode()
    ):
        return Identity("service", AUTOMATION_ROLE, "automation")
    return None


def authenticate(token: str) -> Identity:
    return service_identity(token) or verify_token(token)


MIN_SECRET_BYTES = 32  # DEV_AUTH_SECRET and every service token, outside dev


def secure_config_problems() -> list[str]:
    """What is wrong with the identity configuration for a non-dev environment (empty = fine)."""
    problems: list[str] = []
    if settings.dev_login_enabled:
        problems.append("DEV_LOGIN_ENABLED is true (the passwordless login must be off)")
    secrets = (
        ("DEV_AUTH_SECRET", settings.dev_auth_secret, DEV_AUTH_SECRET_DEFAULT),
        ("SCHEDULER_SERVICE_TOKEN", settings.scheduler_service_token, SCHEDULER_TOKEN_DEFAULT),
        ("WATCHDOG_SERVICE_TOKEN", settings.watchdog_service_token, WATCHDOG_TOKEN_DEFAULT),
        ("AUTOMATION_SERVICE_TOKEN", settings.automation_service_token, AUTOMATION_TOKEN_DEFAULT),
    )
    for name, value, default in secrets:
        if value == default:
            problems.append(f"{name} is the built-in development default")
        elif not value:
            problems.append(f"{name} is empty")
        elif len(value.encode()) < MIN_SECRET_BYTES:
            problems.append(f"{name} is shorter than {MIN_SECRET_BYTES} bytes")
    tokens = (
        ("SCHEDULER_SERVICE_TOKEN", settings.scheduler_service_token),
        ("WATCHDOG_SERVICE_TOKEN", settings.watchdog_service_token),
        ("AUTOMATION_SERVICE_TOKEN", settings.automation_service_token),
    )
    for i, (name_a, a) in enumerate(tokens):
        for name_b, b in tokens[i + 1:]:
            if a == b:
                problems.append(f"{name_a} and {name_b} are the same value")
    return problems


def assert_secure_config() -> None:
    """Fail closed: refuse to start outside APP_ENV=dev with development settings (SEC-1, ADR-029)."""
    if _is_dev():
        return
    problems = secure_config_problems()
    if problems:
        raise RuntimeError(f"APP_ENV={settings.app_env!r}: " + "; ".join(problems))


_bearer = HTTPBearer(auto_error=False)


def optional_identity(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Identity | None:
    if creds is None:
        return None
    try:
        identity = authenticate(creds.credentials)
    except AuthError:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"}) from None
    if identity.kind == "user":
        from app import users  # late: users imports this module

        users.directory.ensure(identity)  # a valid token always has a display-name row
    return identity


def require_identity(identity: Identity | None = Depends(optional_identity)) -> Identity:
    if identity is None:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    return identity
