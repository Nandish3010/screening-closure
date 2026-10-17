"""Closure evidence must resolve (A15, ADR-021).

`close` carries `evidence_ref`, which must be `DiagnosticReport/<id>` or `Procedure/<id>` (the
shape is checked by `app.payloads` always) and, when `FHIR_VERIFY_EVIDENCE=true` (compose),
must exist in HAPI. The closing clinician must also sign as themselves: `signed_by` is their own
actor string (`role:user_id`) or `Practitioner/<user_id>`, the Practitioner the FHIR projection
creates for them (A12).

Unit tests use `StaticVerifier`; the default verifier accepts anything, so a run without HAPI
is never blocked by a lookup.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Protocol

from app.auth import Identity


class EvidenceUnavailable(Exception):
    """The verifier could not answer (HAPI down or erroring). The closure is refused (503)."""


class EvidenceVerifier(Protocol):
    def exists(self, ref: str) -> bool:
        """True if the resource `Type/id` exists. Raises EvidenceUnavailable if it cannot tell."""
        ...


class AcceptAllVerifier:
    """No lookup: the reference shape was already checked. Used when verification is off."""

    def exists(self, ref: str) -> bool:
        return True


class StaticVerifier:
    """Resolves exactly the references it was given (unit tests)."""

    def __init__(self, existing=()) -> None:
        self.existing = set(existing)
        self.calls: list[str] = []

    def exists(self, ref: str) -> bool:
        self.calls.append(ref)
        return ref in self.existing


class FhirVerifier:
    """GET <base>/<Type>/<id> against HAPI: 200 resolves, 404 or 410 does not, anything else
    (or no answer) is `EvidenceUnavailable`."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def exists(self, ref: str) -> bool:
        request = urllib.request.Request(
            f"{self.base_url}/{ref}", headers={"Accept": "application/fhir+json"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return False
            raise EvidenceUnavailable(f"FHIR server answered HTTP {exc.code}") from exc
        except Exception as exc:  # connection refused, timeout, DNS ...
            raise EvidenceUnavailable(f"FHIR server unreachable: {exc}") from exc


# Bound by app.main at start-up (FhirVerifier when FHIR_VERIFY_EVIDENCE is true); tests replace it.
verifier: EvidenceVerifier = AcceptAllVerifier()


def signed_by_matches(identity: Identity, signed_by: str) -> bool:
    """`signed_by` is the caller's own actor string or the Practitioner mapped to their user id."""
    return signed_by in (identity.actor, f"Practitioner/{identity.user_id}")


def configure(app_settings) -> EvidenceVerifier:
    """Bind the module verifier from settings: HAPI lookups when FHIR_VERIFY_EVIDENCE is true."""
    global verifier
    if app_settings.fhir_verify_evidence:
        verifier = FhirVerifier(app_settings.fhir_base_url, app_settings.fhir_timeout_seconds)
    else:
        verifier = AcceptAllVerifier()
    return verifier
