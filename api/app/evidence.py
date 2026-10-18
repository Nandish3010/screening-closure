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

Evidence must also be complete (A24, ADR-036). A resource that exists and is the case's is still not closure evidence
unless it says the colonoscopy was done and finished: `assess` returns the verdict. Closure evidence is

* a `DiagnosticReport` with `status=final` whose `conclusionCode` or `code` says the colonoscopy was `complete` (or that
  a CT colonography was done instead, `ct_colonography`, the ESGE alternative after an incomplete or unfeasible
  colonoscopy), or
* a `Procedure` with `status=completed` that is linked (`Procedure.report`) to such a report.

A `Procedure` alone, a `Procedure` that is `not-done`, `stopped`, `in-progress` or `entered-in-error`, a report that is
not `final`, and a report that says `incomplete` or says nothing about completeness are all refused. The allow-list is
the vocabulary `app.confirmatory` writes (our own completeness code system, LOINC 18746-8 and plain text): it uses no
SNOMED code and no external terminology server. The same verdict gates `close` (422) and the automatic
`complete_confirmatory` of the evidence scan (which raises `review_requested {incomplete_procedure}` instead).

Unit tests use `StaticVerifier`; the default verifier accepts anything, so a run without HAPI
is never blocked by a lookup.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.auth import Identity

CASE_IDENTIFIER_SYSTEM = "urn:screening-closure:case"
MAX_BODY_BYTES = 1_000_000
# The colonoscopy codes the recorder (app.confirmatory) writes and a HAPI search can match (A21).
COLONOSCOPY_SNOMED = "http://snomed.info/sct|73761001"
COLONOSCOPY_LOINC = "http://loinc.org|18746-8"
# Completeness of the colonoscopy a report states (A24). `app.confirmatory` writes these in `conclusionCode`.
COMPLETENESS_SYSTEM = "urn:screening-closure:completeness"
COMPLETENESS_VALUES = ("complete", "incomplete", "ct_colonography")
CLOSABLE_COMPLETENESS = ("complete", "ct_colonography")  # what may close a case
REPORT_STATUS_OK = "final"
PROCEDURE_STATUS_OK = "completed"
# Plain-text forms (lower-cased, whitespace squeezed) a `CodeableConcept.text` or `coding.display` may use. Deliberately
# short: it is the vocabulary our recorder writes, not a terminology.
_TEXT_COMPLETENESS = {
    "colonoscopy complete": "complete", "complete colonoscopy": "complete",
    "colonoscopy incomplete": "incomplete", "incomplete colonoscopy": "incomplete",
    "ct colonography": "ct_colonography", "ct colonography complete": "ct_colonography",
    "ct colonography report": "ct_colonography",
}
MAX_LINKED_REPORTS = 5
_PATIENT_REF = re.compile(r"^Patient/[A-Za-z0-9-]{1,64}$")
_FOUND_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")  # a reference the payload schemas accept (no dots)


def well_formed_ref(ref: str) -> bool:
    """`Type/id` where the id is not made only of dots (`.`, `..`, `...`): such an id is
    path-normalised away by HTTP clients and servers and reaches the search endpoint instead."""
    _, _, ident = ref.partition("/")
    return bool(ident) and ident.strip(".") != ""


class EvidenceUnavailable(Exception):
    """The verifier could not answer (HAPI down or erroring). The closure is refused (503)."""


@dataclass(frozen=True)
class Assessment:
    """The verdict on one piece of closure evidence (A24). `resolves`: the resource exists and is this case's.
    `completeness`: what the evidence says (`complete`, `incomplete`, `ct_colonography`), None if it says nothing or is
    not final. `reason`: one fixed sentence when it is not acceptable, shown in the 422 and never stored in the log."""

    resolves: bool
    completeness: str | None = None
    reason: str | None = None

    @property
    def acceptable(self) -> bool:
        return self.resolves and self.completeness in CLOSABLE_COMPLETENESS and self.reason is None

    @property
    def incomplete(self) -> bool:
        """It is the case's own colonoscopy record and it is not closure evidence (so a person should look)."""
        return self.resolves and not self.acceptable


class EvidenceVerifier(Protocol):
    def exists(self, ref: str, *, case_id, patient_ref: str | None = None) -> bool:
        """True if the resource `Type/id` exists AND belongs to this case (`case_id`, or
        `patient_ref` as its subject). Raises EvidenceUnavailable if it cannot tell."""
        ...

    def assess(self, ref: str, *, case_id, patient_ref: str | None = None) -> Assessment:
        """`exists`, and whether the resource is complete closure evidence (A24): a final report that states a complete
        colonoscopy (or a CT colonography), or a completed Procedure linked to one. Raises EvidenceUnavailable."""
        ...


class AcceptAllVerifier:
    """No lookup: the reference shape was already checked. Used when verification is off."""

    def exists(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> bool:
        return True

    def assess(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> Assessment:
        return Assessment(True, "complete")  # no lookup, so nothing to say about completeness: as before A24


class StaticVerifier:
    """Resolves exactly the references it was given (unit tests). `bindings` maps a reference to
    the case id it belongs to; a bound reference resolves only for that case. `completeness` maps a reference to what it
    says (default `complete`); anything outside CLOSABLE_COMPLETENESS (or None) is refused by `assess`."""

    def __init__(self, existing=(), bindings=None, completeness=None) -> None:
        self.existing = set(existing)
        self.bindings = {k: str(v) for k, v in (bindings or {}).items()}
        self.completeness = dict(completeness or {})
        self.calls: list[str] = []

    def exists(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> bool:
        self.calls.append(ref)
        if ref not in self.existing:
            return False
        bound = self.bindings.get(ref)
        return bound is None or bound == str(case_id)

    def assess(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> Assessment:
        if not self.exists(ref, case_id=case_id, patient_ref=patient_ref):
            return Assessment(False, None, "does not resolve to a resource of this case")
        value = self.completeness.get(ref, "complete")
        return Assessment(True, value, None if value in CLOSABLE_COMPLETENESS else _reason_for(value))


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


def _reason_for(completeness: str | None) -> str:
    if completeness == "incomplete":
        return "the colonoscopy is recorded as incomplete: repeat it or do a CT colonography before closing"
    return "the report does not state that the colonoscopy was complete"


def _concept_values(concept) -> list[str]:
    """The completeness values one CodeableConcept states: our code system's code, or a listed plain-text form."""
    if not isinstance(concept, dict):
        return []
    found: list[str] = []
    for coding in concept.get("coding") or []:
        if not isinstance(coding, dict):
            continue
        if coding.get("system") == COMPLETENESS_SYSTEM and coding.get("code") in COMPLETENESS_VALUES:
            found.append(coding["code"])
        display = coding.get("display")
        if isinstance(display, str) and (v := _TEXT_COMPLETENESS.get(" ".join(display.lower().split()))):
            found.append(v)
    text = concept.get("text")
    if isinstance(text, str) and (v := _TEXT_COMPLETENESS.get(" ".join(text.lower().split()))):
        found.append(v)
    return found


def report_completeness(report: dict) -> str | None:
    """What a DiagnosticReport says about the colonoscopy, from `conclusionCode` and `code`: `incomplete` if either
    says so (the cautious reading wins), else `ct_colonography` or `complete` if one says that, else None."""
    values = _concept_values(report.get("code")) + [
        v for c in (report.get("conclusionCode") or []) for v in _concept_values(c)]
    if "incomplete" in values:
        return "incomplete"
    for wanted in ("ct_colonography", "complete"):
        if wanted in values:
            return wanted
    return None


def assess_report(report: dict) -> Assessment:
    """A resolved DiagnosticReport: it must be `final` and state a complete colonoscopy (or a CT colonography)."""
    if report.get("status") != REPORT_STATUS_OK:
        return Assessment(True, None, f"the report is not final (status {str(report.get('status'))[:20]!r})")
    value = report_completeness(report)
    return Assessment(True, value, None if value in CLOSABLE_COMPLETENESS else _reason_for(value))


def linked_report_refs(procedure: dict) -> list[str]:
    """The DiagnosticReport references a Procedure links to (`Procedure.report`), safe ones only, at most MAX_LINKED_REPORTS."""
    out: list[str] = []
    for item in procedure.get("report") or []:
        ref = item.get("reference") if isinstance(item, dict) else None
        if isinstance(ref, str) and ref.startswith("DiagnosticReport/") and _FOUND_ID.match(ref.partition("/")[2]) \
                and ref not in out:
            out.append(ref)
    return out[:MAX_LINKED_REPORTS]


class FhirVerifier:
    """GET <base>/<Type>/<id> against HAPI: 404 or 410 does not resolve, anything but 200 (or no
    answer) is `EvidenceUnavailable`. A 200 resolves only if the body is that very resource
    (`resourceType` and `id` equal the reference; a Bundle or any other body is "not found") and
    it belongs to the case (`belongs_to_case`)."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, ref: str) -> dict | None:
        """The resource `ref` names, or None (not found, or a body that is not that very resource)."""
        if not well_formed_ref(ref):
            return None
        resource_type, _, resource_id = ref.partition("/")
        request = urllib.request.Request(
            f"{self.base_url}/{ref}", headers={"Accept": "application/fhir+json"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                raw = resp.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            raise EvidenceUnavailable(f"FHIR server answered HTTP {exc.code}") from exc
        except Exception as exc:  # connection refused, timeout, DNS ...
            raise EvidenceUnavailable(f"FHIR server unreachable: {exc}") from exc
        if len(raw) > MAX_BODY_BYTES:
            return None
        try:
            body = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(body, dict) or body.get("resourceType") != resource_type or body.get("id") != resource_id:
            return None  # a Bundle (search result), an OperationOutcome, another resource: not found
        return body

    def exists(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> bool:
        body = self._get(ref)
        return body is not None and belongs_to_case(body, case_id, patient_ref)

    def assess(self, ref: str, *, case_id=None, patient_ref: str | None = None) -> Assessment:
        body = self._get(ref)
        if body is None or not belongs_to_case(body, case_id, patient_ref):
            return Assessment(False, None, "does not resolve to a resource of this case")
        if body["resourceType"] == "DiagnosticReport":
            return assess_report(body)
        status = body.get("status")
        if status != PROCEDURE_STATUS_OK:
            value = "incomplete" if status in ("stopped", "not-done") else None
            return Assessment(True, value, f"the procedure is not completed (status {str(status)[:20]!r})")
        links = linked_report_refs(body)
        if not links:
            return Assessment(True, None, "a procedure alone is not closure evidence: it must link to a final colonoscopy report")
        verdict = Assessment(True, None, "no linked report is a final report of a complete colonoscopy")
        for link in links:
            report = self._get(link)
            if report is None or not belongs_to_case(report, case_id, patient_ref):
                continue  # a link to nothing, or to another case's report, proves nothing
            got = assess_report(report)
            if got.acceptable:
                return got
            if got.completeness == "incomplete" or verdict.completeness is None:
                verdict = got
        return verdict


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
