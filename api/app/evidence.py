"""Closure evidence must resolve (A15, ADR-021).

`close` carries `evidence_ref`, which must be `DiagnosticReport/<id>` or `Procedure/<id>` (the
shape is checked by `app.payloads` always) and, when `FHIR_VERIFY_EVIDENCE=true` (compose),
must exist in HAPI. The closing clinician must also sign as themselves: `signed_by` is their own
actor string (`role:user_id`) or `Practitioner/<user_id>`, the Practitioner the FHIR projection
creates for them (A12).

Evidence is tied to the case it closes (SEC-2, ADR-021 amended). A resource that merely exists is
not evidence for this case: the verifier fetches it, and the body must be the resource that was
asked for (`resourceType/id` equals the reference; a Bundle, an OperationOutcome or any other body
is "not found") and must belong to the case, either by `subject.reference` equal to the case's
`patient_ref` or by the identifier `urn:screening-closure:case|<case_id>` that
`app.confirmatory` writes on everything it records. If the resource names a case at all, it must
be this one: a colonoscopy recorded for another case of the same patient does not close this one.
An id made only of dots (`Procedure/.`, `Procedure/..`) is refused before any request: the URL
collapses to the type's search endpoint, which answers with a Bundle.

Unit tests use `StaticVerifier`; the default verifier accepts anything, so a run without HAPI
is never blocked by a lookup.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Protocol

from app.auth import Identity

CASE_IDENTIFIER_SYSTEM = "urn:screening-closure:case"
MAX_BODY_BYTES = 1_000_000
# The colonoscopy codes the recorder (app.confirmatory) writes and a HAPI search can match (A21).
COLONOSCOPY_SNOMED = "http://snomed.info/sct|73761001"
COLONOSCOPY_LOINC = "http://loinc.org|18746-8"
_PATIENT_REF = re.compile(r"^Patient/[A-Za-z0-9-]{1,64}$")
_FOUND_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")  # a reference the payload schemas accept (no dots)


def well_formed_ref(ref: str) -> bool:
    """`Type/id` where the id is not made only of dots (`.`, `..`, `...`): such an id is
    path-normalised away by HTTP clients and servers and reaches the search endpoint instead."""
    _, _, ident = ref.partition("/")
    return bool(ident) and ident.strip(".") != ""


class EvidenceUnavailable(Exception):
    """The verifier could not answer (HAPI down or erroring). The closure is refused (503)."""


class EvidenceVerifier(Protocol):
    def exists(self, ref: str, *, case_id, patient_ref: str | None = None) -> bool:
        """True if the resource `Type/id` exists AND belongs to this case (`case_id`, or
        `patient_ref` as its subject). Raises EvidenceUnavailable if it cannot tell."""
        ...


class AcceptAllVerifier:
    """No lookup: the reference shape was already checked. Used when verification is off."""

    def exists(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> bool:
        return True


class StaticVerifier:
    """Resolves exactly the references it was given (unit tests). `bindings` maps a reference to
    the case id it belongs to; a bound reference resolves only for that case."""

    def __init__(self, existing=(), bindings=None) -> None:
        self.existing = set(existing)
        self.bindings = {k: str(v) for k, v in (bindings or {}).items()}
        self.calls: list[str] = []

    def exists(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> bool:
        self.calls.append(ref)
        if ref not in self.existing:
            return False
        bound = self.bindings.get(ref)
        return bound is None or bound == str(case_id)


def belongs_to_case(resource: dict, case_id, patient_ref: str | None) -> bool:
    """The resource names this case: by its case identifier, or (when it names no case at all)
    by a `subject.reference` equal to the case's patient reference."""
    named = [
        str(i.get("value")) for i in (resource.get("identifier") or [])
        if isinstance(i, dict) and i.get("system") == CASE_IDENTIFIER_SYSTEM
    ]
    if named:
        return str(case_id) in named
    subject = resource.get("subject")
    reference = subject.get("reference") if isinstance(subject, dict) else None
    return bool(patient_ref) and isinstance(reference, str) and reference == patient_ref


class FhirVerifier:
    """GET <base>/<Type>/<id> against HAPI: 404 or 410 does not resolve, anything but 200 (or no
    answer) is `EvidenceUnavailable`. A 200 resolves only if the body is that very resource
    (`resourceType` and `id` equal the reference; a Bundle or any other body is "not found") and
    it belongs to the case (`belongs_to_case`)."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def exists(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> bool:
        if not well_formed_ref(ref):
            return False
        resource_type, _, resource_id = ref.partition("/")
        request = urllib.request.Request(
            f"{self.base_url}/{ref}", headers={"Accept": "application/fhir+json"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return False
                raw = resp.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return False
            raise EvidenceUnavailable(f"FHIR server answered HTTP {exc.code}") from exc
        except Exception as exc:  # connection refused, timeout, DNS ...
            raise EvidenceUnavailable(f"FHIR server unreachable: {exc}") from exc
        if len(raw) > MAX_BODY_BYTES:
            return False
        try:
            body = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(body, dict) or body.get("resourceType") != resource_type or body.get("id") != resource_id:
            return False  # a Bundle (search result), an OperationOutcome, another resource: not found
        return belongs_to_case(body, case_id, patient_ref)


# Bound by app.main at start-up (FhirVerifier when FHIR_VERIFY_EVIDENCE is true); tests replace it.
verifier: EvidenceVerifier = AcceptAllVerifier()


# ---- finding evidence (A21, ADR-032) -------------------------------------------------------------------------------
#
# The evidence scan asks HAPI whether a colonoscopy exists for a case, so a person does not have to hunt for it. It only
# FINDS references; whether one is evidence for THIS case is the verifier's call (the same `exists` that `close` uses), and
# whether the case may close stays a person's.


class EvidenceFinder(Protocol):
    def find(self, *, case_id, patient_ref: str | None, after: datetime | None) -> list[str]:
        """Candidate closure-evidence references (`Procedure/<id>` first, then `DiagnosticReport/<id>`) for the case:
        a resource carrying the case identifier, or a colonoscopy of the patient dated at or after `after` (the
        booking). Raises EvidenceUnavailable if HAPI cannot answer."""
        ...


class NullFinder:
    """No HAPI to ask: finds nothing, so the evidence scan does nothing."""

    def find(self, *, case_id, patient_ref=None, after=None) -> list[str]:
        return []


class StaticFinder:
    """Returns what it was given for a case id (unit tests)."""

    def __init__(self, found=None, fail: str | None = None) -> None:
        self.found = {str(k): list(v) for k, v in (found or {}).items()}
        self.fail = fail
        self.calls: list[tuple[str, str | None, datetime | None]] = []

    def find(self, *, case_id, patient_ref=None, after=None) -> list[str]:
        self.calls.append((str(case_id), patient_ref, after))
        if self.fail:
            raise EvidenceUnavailable(self.fail)
        return list(self.found.get(str(case_id), []))


def _when(resource: dict) -> datetime | None:
    raw = resource.get("performedDateTime") or (resource.get("performedPeriod") or {}).get("start") \
        or resource.get("effectiveDateTime") or (resource.get("effectivePeriod") or {}).get("start")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class FhirFinder:
    """GET <base>/Procedure and <base>/DiagnosticReport searches: first by the case identifier, then by the patient and
    the colonoscopy code. A hit by identifier counts whatever its date (it names this case); a hit by code must be dated
    at or after `after`. The client-side date test is the authority (HAPI's `date` search is only a pre-filter)."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _search(self, resource_type: str, params: dict) -> list[dict]:
        url = f"{self.base_url}/{resource_type}?{urllib.parse.urlencode({**params, '_count': 20})}"
        # no-cache: HAPI reuses an identical search's result for 60 s by default, which would hide a colonoscopy recorded
        # seconds ago behind an earlier "nothing yet" (found by the M5 demo run; compose also turns the reuse off).
        request = urllib.request.Request(url, headers={"Accept": "application/fhir+json", "Cache-Control": "no-cache"},
                                         method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise EvidenceUnavailable(f"FHIR server answered HTTP {exc.code}") from exc
        except Exception as exc:  # connection refused, timeout, DNS ...
            raise EvidenceUnavailable(f"FHIR server unreachable: {exc}") from exc
        if len(raw) > MAX_BODY_BYTES:
            return []
        try:
            body = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(body, dict) or body.get("resourceType") != "Bundle":
            return []
        out = []
        for entry in body.get("entry") or []:
            res = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(res, dict) and res.get("resourceType") == resource_type and _FOUND_ID.match(str(res.get("id"))):
                out.append(res)
        return out

    def find(self, *, case_id, patient_ref=None, after=None) -> list[str]:
        found: list[str] = []
        floor = (after if after is None or after.tzinfo else after.replace(tzinfo=UTC))
        for resource_type, code in (("Procedure", COLONOSCOPY_SNOMED), ("DiagnosticReport", COLONOSCOPY_LOINC)):
            hits = self._search(resource_type, {"identifier": f"{CASE_IDENTIFIER_SYSTEM}|{case_id}"})
            if patient_ref and _PATIENT_REF.match(patient_ref):
                hits += self._search(resource_type, {"subject": patient_ref, "code": code})
            for res in hits:
                ref = f"{resource_type}/{res['id']}"
                if ref in found:
                    continue
                named_case = any(isinstance(i, dict) and i.get("system") == CASE_IDENTIFIER_SYSTEM
                                 and str(i.get("value")) == str(case_id) for i in (res.get("identifier") or []))
                when = _when(res)
                if named_case or (floor is None or (when is not None and when >= floor)):
                    found.append(ref)
        return found


# Bound by app.main at start-up (FhirFinder when HAPI is in use); tests replace it.
finder: EvidenceFinder = NullFinder()


def signed_by_matches(identity: Identity, signed_by: str) -> bool:
    """`signed_by` is the caller's own actor string or the Practitioner mapped to their user id."""
    return signed_by in (identity.actor, f"Practitioner/{identity.user_id}")


def configure(app_settings) -> EvidenceVerifier:
    """Bind the module verifier from settings: HAPI lookups when FHIR_VERIFY_EVIDENCE is true. The evidence finder
    (A21) is bound with it: it asks HAPI when verification or projection is on, else it finds nothing."""
    global verifier, finder
    if app_settings.fhir_verify_evidence:
        verifier = FhirVerifier(app_settings.fhir_base_url, app_settings.fhir_timeout_seconds)
    else:
        verifier = AcceptAllVerifier()
    if app_settings.fhir_verify_evidence or app_settings.fhir_projection:
        finder = FhirFinder(app_settings.fhir_base_url, app_settings.fhir_timeout_seconds)
    else:
        finder = NullFinder()
    return verifier
