"""Identity: signed dev tokens for people, static service tokens for services (ADR-012).

Who is calling is always decided here, never by the request body. The event `actor` recorded
in the log is derived from the identity (`role:name`), so a client cannot claim to be a
clinician, and automated actors (`system:`, `scheduler`, `watchdog`, `model:`) cannot
authenticate as a user at all: the six user roles are the only roles a token can carry.

Two ways to prove who you are (A29, ADR-040), chosen by `AUTH_MODE`:

* `dev`: tokens are `base64url(json).base64url(HMAC-SHA256(secret, first part))` from `POST /auth/dev-login`. They
  expire (`exp`, 8 hours by default) on the WALL clock: expiry is an identity concern, not case logic, which stays on
  the simulation clock (ADR-040 supersedes ADR-012's "never expire"). A refresh is a new login.
* `oidc`: JWTs from an OIDC provider, verified in `app.oidc` (signature via JWKS, iss, aud, exp, nbf). The role comes
  from the token's roles, the `role:user_id` actor from `sub` exactly as for dev logins (A14), the display name goes to
  the users table. The dev login and dev tokens are refused.

Service tokens are static and identical in both modes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import (
    AUTOMATION_TOKEN_DEFAULT,
    AGENCY_TOKEN_DEFAULT,
    DEV_AUTH_SECRET_DEFAULT,
    HL7_TOKEN_DEFAULT,
    METRICS_TOKEN_DEFAULT,
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
# The agency (A22, ADR-033). One service role for every agent; which agent it is lives in the name
# (`agency:supervisor`, `agency:outreach`, ...), so the actor is `system:agency:<department>`. Only the
# supervisor has a token, and it may start a run and nothing else; the departments act in process. No agency
# identity may emit any case event (its row of the state machine's actor table is empty).
AGENCY_ROLE = "agency"
AGENCY_SUPERVISOR_NAME = "agency:supervisor"
# The HL7 v2 adapter (A28, ADR-039): one service token, the actor `system:hl7`. It may call POST /hl7 and nothing else; the
# events it may write are in its row of the state machine's actor table.
HL7_ROLE = "hl7"
# Names that can never be a user role, even by accident.
AUTOMATED_ROLE_NAMES = ("system", "scheduler", "watchdog", "model", "automation", "agency", "hl7")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}\Z")  # no ":" so role:name parses; \Z, not $ (which also matches before a trailing newline)
PATIENT_REF_RE = re.compile(r"^Patient/[A-Za-z0-9-]{1,64}\Z")  # the same shape a case's patient_ref must have (SEC-1)
PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@/-]{0,99}\Z")
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
    # The identity provider's stable `sub` (oidc mode). The opaque id derives from it, never from the display name.
    subject: str | None = None

    @property
    def effective_provider(self) -> str:
        return self.provider or self.name

    @property
    def user_id(self) -> str:
        """The opaque id for a user (A14); a service is its own id (`system:<service>`)."""
        return f"system:{self.name}" if self.kind == "service" else opaque_user_id(self.role, self.subject or self.name)

    @property
    def actor(self) -> str:
        """The string recorded as `actor` on events this identity causes: `role:user_id` for
        people (the id is opaque, A14), `system:<service>` for services."""
        return self.user_id if self.kind == "service" else f"{self.role}:{self.user_id}"


def wall_now() -> float:
    """Seconds since the epoch on the real clock. Used for token lifetimes only (ADR-040); case logic never calls it."""
    return time.time()  # wall-clock-ok: token lifetime is identity, not case logic (ADR-040)


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
    lifetime_seconds: int | None = None,
) -> str:
    validate_user(role, name, patient_ref, provider)
    key = secret or settings.dev_auth_secret
    if not _is_dev() and key == DEV_AUTH_SECRET_DEFAULT:
        raise AuthError("the development secret is not usable outside APP_ENV=dev")
    issued = int(wall_now())
    life = settings.dev_token_lifetime_seconds if lifetime_seconds is None else lifetime_seconds
    claims: dict = {"v": 1, "role": role, "name": name, "iat": issued, "exp": issued + life}
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
        exp = claims["exp"]  # a dev token without an expiry is not valid (ADR-040)
        if isinstance(exp, bool) or not isinstance(exp, int):
            raise ValueError("bad exp")
        role, name, patient_ref = claims["role"], claims["name"], claims.get("patient_ref")
        provider = claims.get("provider")
        validate_user(role, name, patient_ref, provider)  # a signed token still cannot be a model
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("malformed token") from exc
    if wall_now() >= exp:
        raise AuthError("token expired")
    return Identity("user", role, name, patient_ref, provider)


def token_expiry(token: str) -> int:
    """The `exp` of a dev token this process just issued (for the login response)."""
    return int(json.loads(_unb64(token.split(".")[0]))["exp"])


_DISPLAY_OK = re.compile(r"[^A-Za-z0-9 ._@-]")


def _display_name(claims: dict, user_id_hint: str) -> str:
    """A safe display name from the token (users table only, never the event log); falls back to an opaque label."""
    for key in ("name", "preferred_username", "email"):
        raw = claims.get(key)
        if isinstance(raw, str):
            cleaned = " ".join(_DISPLAY_OK.sub("", raw).split())[:64].strip(" ._@-")
            if cleaned and NAME_RE.match(cleaned):
                return cleaned
    return f"user {user_id_hint}"


def oidc_identity(token: str) -> Identity:
    """Verify an OIDC access token and build the Identity (A29). Raises AuthError on anything wrong."""
    from app import oidc  # late: keeps PyJWT out of the import path of dev-only tools

    try:
        claims = oidc.verify(token)
        role = oidc.map_role(claims, USER_ROLES)
    except oidc.OidcError as exc:
        raise AuthError(str(exc)) from exc
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 256:
        raise AuthError("bad sub")
    patient_ref = claims.get("patient_ref") if role == "patient" else None
    if role == "patient" and (not isinstance(patient_ref, str) or not PATIENT_REF_RE.match(patient_ref)):
        raise AuthError("patient token without a valid patient_ref claim")
    provider = claims.get("provider") if role in PROVIDER_ROLES else None
    if provider is not None and not (isinstance(provider, str) and PROVIDER_RE.match(provider)):
        provider = None
    name = _display_name(claims, opaque_user_id(role, sub)[:6])
    return Identity("user", role, name, patient_ref, provider, subject=sub)


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
    if settings.agency_service_token and hmac.compare_digest(
        token.encode(), settings.agency_service_token.encode()
    ):
        return Identity("service", AGENCY_ROLE, AGENCY_SUPERVISOR_NAME)
    # The development default authenticates only in APP_ENV=dev: outside it the adapter is off until a real token is set
    # (so a deployment that does not use HL7 needs no new configuration, and the published default never opens it).
    if settings.hl7_service_token and (_is_dev() or settings.hl7_service_token != HL7_TOKEN_DEFAULT) and hmac.compare_digest(
        token.encode(), settings.hl7_service_token.encode()
    ):
        return Identity("service", HL7_ROLE, "hl7")
    return None


def authenticate(token: str) -> Identity:
    svc = service_identity(token)
    if svc is not None:
        return svc
    return oidc_identity(token) if settings.auth_mode == "oidc" else verify_token(token)


def auth_mode_problems() -> list[str]:
    """Problems with AUTH_MODE itself, checked in every environment (a typo must not silently fall back)."""
    if settings.auth_mode not in ("dev", "oidc"):
        return [f"AUTH_MODE {settings.auth_mode!r} is not 'dev' or 'oidc'"]
    if settings.auth_mode == "oidc" and not settings.oidc_issuer:
        return ["AUTH_MODE=oidc needs OIDC_ISSUER"]
    return []


MIN_SECRET_BYTES = 32  # DEV_AUTH_SECRET and every service token, outside dev


def secure_config_problems() -> list[str]:
    """What is wrong with the identity configuration for a non-dev environment (empty = fine)."""
    problems: list[str] = list(auth_mode_problems())
    if settings.auth_mode != "oidc":
        problems.append("AUTH_MODE is not 'oidc' (outside APP_ENV=dev people must sign in through an OIDC provider)")
    elif not settings.oidc_issuer.startswith("https://"):
        problems.append("OIDC_ISSUER must be an https:// URL")
    if settings.dev_login_enabled:
        problems.append("DEV_LOGIN_ENABLED is true (the passwordless login must be off)")
    secrets = (
        ("DEV_AUTH_SECRET", settings.dev_auth_secret, DEV_AUTH_SECRET_DEFAULT),
        ("SCHEDULER_SERVICE_TOKEN", settings.scheduler_service_token, SCHEDULER_TOKEN_DEFAULT),
        ("WATCHDOG_SERVICE_TOKEN", settings.watchdog_service_token, WATCHDOG_TOKEN_DEFAULT),
        ("AUTOMATION_SERVICE_TOKEN", settings.automation_service_token, AUTOMATION_TOKEN_DEFAULT),
    )
    optional = (("AGENCY_SERVICE_TOKEN", settings.agency_service_token, AGENCY_TOKEN_DEFAULT),)
    # the agency token (A22) is optional: empty switches it off, which is always safe; set, it is checked like the rest
    for name, value, default in (*secrets, *(o for o in optional if o[1])):
        if value == default:
            problems.append(f"{name} is the built-in development default")
        elif not value:
            problems.append(f"{name} is empty")
        elif len(value.encode()) < MIN_SECRET_BYTES:
            problems.append(f"{name} is shorter than {MIN_SECRET_BYTES} bytes")
    # the HL7 adapter's token (A28) is optional too, and its development default is simply OFF outside dev (see
    # `service_identity`): only a token someone set is checked, for length
    hl7 = settings.hl7_service_token
    if hl7 and hl7 != HL7_TOKEN_DEFAULT and len(hl7.encode()) < MIN_SECRET_BYTES:
        problems.append(f"HL7_SERVICE_TOKEN is shorter than {MIN_SECRET_BYTES} bytes")
    scrape = settings.metrics_scrape_token
    if scrape and scrape != METRICS_TOKEN_DEFAULT and len(scrape.encode()) < MIN_SECRET_BYTES:
        problems.append(f"METRICS_SCRAPE_TOKEN is shorter than {MIN_SECRET_BYTES} bytes")
    if settings.clock_mode != "wall":
        problems.append("CLOCK_MODE is not 'wall' (a deployment outside dev must run on real time, not the simulation clock)")
    if settings.outreach_demo_approval:
        problems.append("OUTREACH_DEMO_APPROVAL is on (a demo placeholder must not stand in for the clinical lead's "
                        "signature on the outreach policy)")
    tokens = (
        ("SCHEDULER_SERVICE_TOKEN", settings.scheduler_service_token),
        ("WATCHDOG_SERVICE_TOKEN", settings.watchdog_service_token),
        ("AUTOMATION_SERVICE_TOKEN", settings.automation_service_token),
        ("AGENCY_SERVICE_TOKEN", settings.agency_service_token),
        ("HL7_SERVICE_TOKEN", settings.hl7_service_token if settings.hl7_service_token != HL7_TOKEN_DEFAULT else ""),
    )
    for i, (name_a, a) in enumerate(tokens):
        for name_b, b in tokens[i + 1:]:
            if a and a == b:
                problems.append(f"{name_a} and {name_b} are the same value")
    return problems


def assert_secure_config() -> None:
    """Fail closed: refuse to start outside APP_ENV=dev with development settings (SEC-1, ADR-029)."""
    if _is_dev():
        problems = auth_mode_problems()
        if problems:
            raise RuntimeError(f"APP_ENV='dev': " + "; ".join(problems))
        return
    problems = secure_config_problems()
    if problems:
        raise RuntimeError(f"APP_ENV={settings.app_env!r}: " + "; ".join(problems))


_bearer = HTTPBearer(auto_error=False)


_seen_oidc: dict[str, tuple[str, str | None]] = {}  # user_id -> (display name, provider) last written by this process


def _refresh_directory(identity: Identity) -> None:
    from app import users

    key = (identity.name, identity.provider)
    if _seen_oidc.get(identity.user_id) != key:
        users.record_login(identity)
        _seen_oidc[identity.user_id] = key


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

        if identity.subject is not None:
            _refresh_directory(identity)  # oidc: the provider's display name wins, so a rename is picked up
        else:
            users.directory.ensure(identity)  # a valid token always has a display-name row
    return identity


def require_identity(identity: Identity | None = Depends(optional_identity)) -> Identity:
    if identity is None:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    return identity
