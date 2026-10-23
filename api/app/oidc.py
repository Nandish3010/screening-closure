"""OIDC access-token verification (A29, ADR-040): the production side of `app.auth`.

In `AUTH_MODE=oidc` a person's bearer token is a JWT signed by the identity provider (Keycloak locally, UAE PASS in
production, docs/governance/identity.md). This module checks everything the API relies on: the signature against the
issuer's JWKS, `iss`, `aud`, `exp` and `nbf` (with a small leeway), and that the token carries exactly one of our six
user roles. It returns plain claims; `app.auth` turns them into an `Identity`.

Keys come from the JWKS document, cached with a time limit. A token with a `kid` the cache does not know triggers one
refetch (a rotated key), at most once per `OIDC_JWKS_MIN_REFETCH_SECONDS`, so a stream of garbage tokens cannot make
the API hammer the provider. If the provider is unreachable the last good keys stay in use for up to
`OIDC_JWKS_STALE_SECONDS`. Only asymmetric algorithms are accepted (never `none`, never HMAC with a public key).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable

import jwt
from jwt import PyJWK

from app.config import settings

ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384")


class OidcError(Exception):
    pass


def default_fetch(url: str, timeout: float) -> dict:
    if not url.startswith(("http://", "https://")):
        raise OidcError("JWKS URL must be http(s)")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
        return json.loads(resp.read(1_000_000))


class JwksCache:
    def __init__(self, fetch: Callable[[str, float], dict] = default_fetch, clock: Callable[[], float] = time.monotonic):
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None  # last successful fetch
        self._attempted_at: float | None = None  # last attempt, successful or not

    def reset(self) -> None:
        with self._lock:
            self._keys, self._fetched_at, self._attempted_at = {}, None, None

    def _refresh(self, url: str) -> None:
        now = self._clock()
        self._attempted_at = now
        try:
            doc = self._fetch(url, settings.oidc_jwks_timeout_seconds)
            keys = {}
            for jwk in doc.get("keys", []):
                if jwk.get("kid") and jwk.get("use", "sig") == "sig" and jwk.get("kty") in ("RSA", "EC"):
                    try:
                        keys[jwk["kid"]] = PyJWK.from_dict(jwk)
                    except Exception:  # noqa: BLE001 - one unusable key must not hide the others
                        continue
        except Exception as exc:  # noqa: BLE001
            stale_ok = self._fetched_at is not None and now - self._fetched_at <= settings.oidc_jwks_stale_seconds
            if not stale_ok:
                raise OidcError("cannot fetch the identity provider's signing keys") from exc
            return
        self._keys, self._fetched_at = keys, now

    def key_for(self, kid: str, url: str) -> PyJWK:
        with self._lock:
            now = self._clock()
            expired = self._fetched_at is None or now - self._fetched_at > settings.oidc_jwks_cache_seconds
            if expired:
                self._refresh(url)
            if kid not in self._keys:
                recent = self._attempted_at is not None and now - self._attempted_at < settings.oidc_jwks_min_refetch_seconds
                if not expired and not recent:
                    self._refresh(url)  # a rotated key: look once more
            key = self._keys.get(kid)
        if key is None:
            raise OidcError("unknown signing key")
        return key


jwks = JwksCache()


def jwks_url() -> str:
    return settings.oidc_jwks_url or settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/certs"


def role_map() -> dict[str, str]:
    """IdP role name -> our role. Empty OIDC_ROLE_MAP means the names are the same (the shipped realm does that)."""
    out: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in settings.oidc_role_map.split(","))):
        left, _, right = pair.partition("=")
        out[left.strip()] = right.strip()
    return out


def _dig(claims: dict, path: str):
    cur = claims
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def map_role(claims: dict, our_roles: tuple[str, ...]) -> str:
    """The one of our six roles the token carries. None or several is a refusal: no guessing, no precedence."""
    raw = _dig(claims, settings.oidc_role_claim)
    if not isinstance(raw, list):
        raise OidcError("token carries no roles")
    mapping = role_map()
    mapped = {mapping.get(r, r) for r in raw if isinstance(r, str)}
    ours = sorted(mapped & set(our_roles))
    if len(ours) != 1:
        raise OidcError("token must carry exactly one application role")
    return ours[0]


def verify(token: str) -> dict:
    """The verified claims of an access token, or OidcError. Signature, iss, aud, exp and nbf are all checked."""
    if not settings.oidc_issuer:
        raise OidcError("OIDC_ISSUER is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OidcError("malformed token") from exc
    alg, kid = header.get("alg"), header.get("kid")
    if alg not in ALLOWED_ALGORITHMS or not isinstance(kid, str):
        raise OidcError("unacceptable token header")
    key = jwks.key_for(kid, jwks_url())
    try:
        return jwt.decode(
            token,
            key.key,
            algorithms=[alg],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            leeway=settings.oidc_leeway_seconds,
            options={"require": ["exp", "iss", "aud", "sub"], "verify_signature": True, "verify_nbf": True},
        )
    except jwt.PyJWTError as exc:
        raise OidcError(type(exc).__name__) from exc
