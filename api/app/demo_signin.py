"""One-click sign-in for the hosted judge demo (ADR-060). Registered only under `auth.demo_controls_active()`.

A judge picks a role; the API exchanges that role's fixed demo user's password with Keycloak (direct access grant on the dedicated
demo client, over the private network) and returns Keycloak's token response. The browser then holds a normal OIDC session: the same
access token the authorization-code flow would give, verified by the same JWT path. The password is read from the API's own
environment, is never returned, never logged and never put in an error. Only the four fixed roles can be asked for.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

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


LOCKOUT_RETRY_SECONDS = 60  # the realm's longest brute-force wait (render_realm.BRUTE_FORCE maxFailureWaitSeconds)


# Stable error codes the web acts on (S1). `signin_warming_up`: the identity provider is not ready (restarting, not yet answering);
# the web retries on its own. The others are final for the click.
WARMING_UP = "signin_warming_up"
WARMING_RETRY_SECONDS = 5


class SignInError(Exception):
    def __init__(self, status: int, message: str, code: str = "signin_failed", retry_after: int | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.retry_after = status, message, code, retry_after


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


def _admit(ip: str) -> float:
    """Count this attempt and return its timestamp (the receipt for `_refund`), or raise 429 with the wait in seconds."""
    window = settings.demo_signin_window_seconds
    now = _mono()
    with _lock:
        while _all and now - _all[0] > window:
            _all.popleft()
        if len(_all) >= settings.demo_signin_global_limit:
            raise SignInError(429, "Many judges are signing in right now. Please try again in a minute.", "signin_busy",
                              max(1, math.ceil(window - (now - _all[0]))))
        q = _per_ip.setdefault(ip, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= settings.demo_signin_per_ip_limit:
            raise SignInError(429, "Too many sign-ins from this address. Please wait a few minutes, or use the password sign-in.",
                              "signin_rate_limited", max(1, math.ceil(window - (now - q[0]))))
        q.append(now)
        _all.append(now)
        if len(_per_ip) > 5000:  # bounded memory: drop addresses with nothing in the window
            for k in [k for k, v in _per_ip.items() if not v or now - v[-1] > window]:
                del _per_ip[k]
    return now


def _refund(ip: str, stamp: float) -> None:
    """Take back an attempt that failed for a reason the visitor cannot cause (the identity provider was not ready). A panel behind one
    office address must not be locked out by a VM restart; the edge's own request-rate limit still bounds how fast this can be retried."""
    with _lock:
        for dq in (_per_ip.get(ip), _all):
            if dq is not None:
                try:
                    dq.remove(stamp)
                except ValueError:  # already aged out of the window
                    pass


def _exchange(url: str, form: dict[str, str]) -> tuple[int, object]:
    """POST the form to the token endpoint (stdlib only: the runtime image has no HTTP client library). Returns (status, parsed body or None)."""
    if not url.startswith(("http://", "https://")):
        raise OSError("token URL must be http(s)")
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:  # noqa: S310 - scheme checked above
            status, raw = resp.status, resp.read(1_000_000)
    except urllib.error.HTTPError as exc:  # the provider answered with a refusal: a status, not an outage
        try:
            refusal = json.loads(exc.read(100_000))
        except (ValueError, OSError):
            refusal = None
        return exc.code, refusal
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def _locked(body: object) -> bool:
    """True when Keycloak's refusal says the user is disabled or temporarily locked (brute-force protection), whatever the status."""
    if not isinstance(body, dict):
        return False
    text = f"{body.get('error_description', '')} {body.get('error', '')}".lower()
    return "disabled" in text or "locked" in text


def sign_in(role: str, ip: str) -> dict:
    """The token response for `role`, or SignInError. Never returns or logs the password."""
    if role not in DEMO_USERS:
        raise SignInError(422, "Unknown role.", "signin_unknown_role")
    password = _password(role)
    if not password:
        raise SignInError(503, "One-click sign-in is not set up on this server. Use the password sign-in.", "signin_not_configured")
    stamp = _admit(ip)
    try:
        return _sign_in_admitted(role, password)
    except SignInError:
        _refund(ip, stamp)  # every failure after admission is the server's or the provider's, not the visitor's (the role is validated)
        raise


def _sign_in_admitted(role: str, password: str) -> dict:
    form = {"grant_type": "password", "client_id": settings.demo_signin_client_id,
            "username": DEMO_USERS[role], "password": password, "scope": "openid profile"}
    if settings.demo_signin_client_secret:  # the demo client is confidential (ADR-060 addendum): only this API holds the secret
        form["client_secret"] = settings.demo_signin_client_secret
    try:
        status, body = _exchange(token_url(), form)
    except OSError as exc:  # URLError, timeouts and refused connections are OSErrors
        log.error("demo sign-in role=%s: identity provider unreachable (%s)", role, type(exc).__name__)
        raise SignInError(503, "The demo is starting up: the identity provider is not answering yet. Please try again in a few seconds.",
                          WARMING_UP, WARMING_RETRY_SECONDS) from exc
    if status != 200:
        if _locked(body):  # the public can guess at the demo usernames; the realm locks a user briefly (render_realm.BRUTE_FORCE)
            log.warning("demo sign-in role=%s: the demo user is temporarily locked by the identity provider", role)
            raise SignInError(503, f"The {role} demo role is temporarily locked after repeated failed sign-ins by someone else. "
                                   f"Try again in {LOCKOUT_RETRY_SECONDS} s, or pick another role.", "signin_locked", LOCKOUT_RETRY_SECONDS)
        if status >= 500 or status in (404, 408, 425, 429):  # still starting (or realm not imported yet): worth waiting for
            log.error("demo sign-in role=%s: identity provider not ready (status %s)", role, status)
            raise SignInError(503, "The demo is starting up: the identity provider is not ready yet. Please try again in a few seconds.",
                              WARMING_UP, WARMING_RETRY_SECONDS)
        log.error("demo sign-in role=%s: identity provider refused (status %s)", role, status)
        raise SignInError(503, "The identity provider refused the sign-in. Use the password sign-in.", "signin_refused")
    if not isinstance(body, dict) or not body.get("access_token"):
        raise SignInError(503, "The demo is starting up: the identity provider answered unexpectedly. Please try again in a few seconds.",
                          WARMING_UP, WARMING_RETRY_SECONDS)
    log.info("demo sign-in role=%s", role)  # role and time (the log line's own); no name, address or token
    # client_id (not a secret) tells the browser which client to name when it refreshes these tokens
    return {**{k: body[k] for k in TOKEN_FIELDS if k in body}, "client_id": settings.demo_signin_client_id}
