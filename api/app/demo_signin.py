"""One-click sign-in for the hosted judge demo (ADR-060). Registered only under `auth.demo_controls_active()`.

A judge picks a role; the API exchanges that role's fixed demo user's password with Keycloak (direct access grant on the dedicated
demo client, over the private network) and returns Keycloak's token response. The browser then holds a normal OIDC session: the same
access token the authorization-code flow would give, verified by the same JWT path. The password is read from the API's own
environment, is never returned, never logged and never put in an error. Only the four fixed roles can be asked for.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import httpx

from app.config import settings

log = logging.getLogger("app.demo")

# The web role -> the fixed demo user in the realm (config/keycloak/screening-closure-realm.json). Nobody else can be signed in.
DEMO_USERS: dict[str, str] = {
    "coordinator": "maryam.alsuwaidi",
    "clinician": "huda.rahman",
    "supervisor": "noura.alhammadi",
    "patient": "demo.patient",
}
ROLES = tuple(DEMO_USERS)
# What the web may receive from the token endpoint. Anything else (including any field a misconfigured provider echoed) is dropped.
TOKEN_FIELDS = ("access_token", "refresh_token", "id_token", "token_type", "expires_in", "refresh_expires_in", "scope")


class SignInError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


_lock = threading.Lock()
_per_ip: dict[str, deque[float]] = {}
_all: deque[float] = deque()


def forget() -> None:
    """Drop the rate-limit memory (tests)."""
    with _lock:
        _per_ip.clear()
        _all.clear()


def _mono() -> float:
    return time.monotonic()  # wall-clock-ok: rate-limit window only


def _password(role: str) -> str:
    return getattr(settings, f"demo_signin_pw_{role}", "")


def token_url() -> str:
    if settings.demo_signin_token_url:
        return settings.demo_signin_token_url
    from app.oidc import jwks_url

    return jwks_url().removesuffix("/certs") + "/token"


def _admit(ip: str) -> None:
    window = settings.demo_signin_window_seconds
    now = _mono()
    with _lock:
        while _all and now - _all[0] > window:
            _all.popleft()
        if len(_all) >= settings.demo_signin_global_limit:
            raise SignInError(429, "Many judges are signing in right now. Please try again in a minute.")
        q = _per_ip.setdefault(ip, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= settings.demo_signin_per_ip_limit:
            raise SignInError(429, "Too many sign-ins from this address. Please wait a few minutes, or use the password sign-in.")
        q.append(now)
        _all.append(now)
        if len(_per_ip) > 5000:  # bounded memory: drop addresses with nothing in the window
            for k in [k for k, v in _per_ip.items() if not v or now - v[-1] > window]:
                del _per_ip[k]


def _exchange(url: str, form: dict[str, str]) -> httpx.Response:
    return httpx.post(url, data=form, headers={"Accept": "application/json"}, timeout=8.0)


def sign_in(role: str, ip: str) -> dict:
    """The token response for `role`, or SignInError. Never returns or logs the password."""
    if role not in DEMO_USERS:
        raise SignInError(422, "Unknown role.")
    password = _password(role)
    if not password:
        raise SignInError(503, "One-click sign-in is not set up on this server. Use the password sign-in.")
    _admit(ip)
    form = {"grant_type": "password", "client_id": settings.demo_signin_client_id,
            "username": DEMO_USERS[role], "password": password, "scope": "openid profile"}
    try:
        res = _exchange(token_url(), form)
    except httpx.HTTPError as exc:
        log.error("demo sign-in role=%s: identity provider unreachable (%s)", role, type(exc).__name__)
        raise SignInError(503, "The identity provider did not answer. Try again, or use the password sign-in.") from exc
    if res.status_code != 200:
        log.error("demo sign-in role=%s: identity provider refused (status %s)", role, res.status_code)
        raise SignInError(503, "The identity provider refused the sign-in. Use the password sign-in.")
    try:
        body = res.json()
    except ValueError as exc:
        raise SignInError(503, "The identity provider answered unexpectedly. Use the password sign-in.") from exc
    if not isinstance(body, dict) or not body.get("access_token"):
        raise SignInError(503, "The identity provider answered unexpectedly. Use the password sign-in.")
    log.info("demo sign-in role=%s", role)  # role and time (the log line's own); no name, address or token
    # client_id (not a secret) tells the browser which client to name when it refreshes these tokens
    return {**{k: body[k] for k in TOKEN_FIELDS if k in body}, "client_id": settings.demo_signin_client_id}
