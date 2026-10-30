"""Optional TLS with client certificates (mutual TLS) for the MLLP listener. Standard library `ssl` only, off by default.

    HL7_TLS_CERT, HL7_TLS_KEY     server certificate chain and private key (PEM); both are required once either is set
    HL7_TLS_CLIENT_CA             CA bundle (PEM) that must have signed the client certificate; when set, a client
                                  certificate is REQUIRED (ssl.CERT_REQUIRED)
    HL7_TLS_ALLOWED_CLIENT_CNS    comma list of client subject CNs / DNS SANs accepted; unset = any certificate the CA
                                  signed; set-but-empty = nobody (fail closed)
    HL7_REQUIRE_TLS               1/true: refuse to start in clear text

Misconfiguration raises TlsConfigError (the listener refuses to start); it never falls back to clear text. Minimum TLS 1.2.
"""

from __future__ import annotations

import os
import ssl


class TlsConfigError(RuntimeError):
    """The TLS settings are incomplete or unusable: the listener must not start."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def build_context() -> ssl.SSLContext | None:
    """The server SSL context from the environment, None for plain MLLP; TlsConfigError when misconfigured."""
    cert, key, ca = _env("HL7_TLS_CERT"), _env("HL7_TLS_KEY"), _env("HL7_TLS_CLIENT_CA")
    if not cert and not key:
        if ca or "HL7_TLS_ALLOWED_CLIENT_CNS" in os.environ:
            raise TlsConfigError("HL7_TLS_CLIENT_CA / HL7_TLS_ALLOWED_CLIENT_CNS are set without HL7_TLS_CERT and HL7_TLS_KEY")
        if _env("HL7_REQUIRE_TLS").lower() in ("1", "true", "yes", "on"):
            raise TlsConfigError("HL7_REQUIRE_TLS is set but HL7_TLS_CERT / HL7_TLS_KEY are not")
        return None
    if not cert or not key:
        raise TlsConfigError("HL7_TLS_CERT and HL7_TLS_KEY must both be set")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(cert, key)
        if ca:
            ctx.load_verify_locations(cafile=ca)
            ctx.verify_mode = ssl.CERT_REQUIRED
    except (OSError, ssl.SSLError) as exc:
        raise TlsConfigError(f"cannot load the TLS files: {type(exc).__name__}") from None
    if "HL7_TLS_ALLOWED_CLIENT_CNS" in os.environ and not ca:
        raise TlsConfigError("HL7_TLS_ALLOWED_CLIENT_CNS needs HL7_TLS_CLIENT_CA (no client certificate to check otherwise)")
    return ctx


def allowed_client_cns() -> frozenset[str] | None:
    """None when no allow-list is configured; otherwise the (possibly empty) set of accepted identities."""
    raw = os.environ.get("HL7_TLS_ALLOWED_CLIENT_CNS")
    if raw is None:
        return None
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def client_identities(peercert: dict | None) -> list[str]:
    """Subject CN(s) and DNS SANs of a verified peer certificate (ssl.getpeercert() form)."""
    if not peercert:
        return []
    ids = [v for rdn in peercert.get("subject", ()) for k, v in rdn if k == "commonName"]
    ids += [v for k, v in peercert.get("subjectAltName", ()) if k == "DNS"]
    return ids


def client_cn(peercert: dict | None) -> str:
    ids = client_identities(peercert)
    return ids[0] if ids else "unknown"


def client_allowed(peercert: dict | None, allowed: frozenset[str] | None) -> bool:
    if allowed is None:
        return True
    return any(i in allowed for i in client_identities(peercert))
