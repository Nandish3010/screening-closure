"""Erasure of a patient's personal data (A32, ADR-025): the append-only audit trail stays, the patient goes.

Two people, two calls (`app/routers/erasure.py`):

    POST /erasures                         supervisor A   {patient_ref, reason}   -> a request, nothing erased yet
    POST /erasures/{erasure_id}/approve    supervisor B   (not A)    -> performs it

`request` checks that the patient's cases are closed (an open screening is never erased from under the people working it),
records the request in the register (`erasures`, opaque ids only) and writes the TRANSIENT `erasure_map` (patient reference ->
a random opaque token, per case). `approve` then, in this order, so that a failure leaves a state that can simply be retried:

  1. HAPI: delete, then EXPUNGE (HAPI soft-deletes; the row and its history stay readable to `_history` until expunged) the
     Patient, the resources the projection wrote for the cases (Task, ServiceRequest, Appointment, Communication, Consent,
     Provenance) and everything else in the Patient compartment (DiagnosticReport, Procedure, ...), and check each is gone.
     HAPI unreachable or refusing = HTTP 502 and nothing in Postgres has changed.
  2. The HL7 contact registry forgets the reference (in-process hint, `app/hl7/contacts.py`).
  3. Per case, in ONE transaction with the `erasure_completed` event: the reference in the log becomes the opaque token, message
     and reply text and prompt text become a tombstone (ids, hashes and classifications stay), free-text notes in events become
     a tombstone, projection queue rows are deleted. All of it inside `erasure_scrub_case` (migration 016), the only code
     path the append-only guards let an UPDATE through; the event itself carries only the erasure id and the categories.
  4. The register row is completed and the `erasure_map` rows are DELETED, in one transaction: after this nothing links a case to
     a patient. A retry after a crash between 3 and 4 finds the cases already done (the event id is derived from the erasure
     and the case) and only completes 4.

What is NOT erased, on purpose and why: `docs/governance/erasure.md`.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from app import payloads
from app.events import Event
from app.fhir_projection import FhirError, HttpFhirClient, case_resource_ids, is_safe_ref, safe_ref_url
from app.hl7 import contacts
from app.state_machine import ERASURE_ACTOR, ERASURE_EVENT

log = logging.getLogger("app.erasure")

# The reasons a supervisor may give: a fixed taxonomy, no free text (the register must hold opaque data only). The same list
# is a CHECK constraint in migration 016 (tests/test_erasure.py keeps the two equal).
REASONS = ("data_subject_request", "consent_withdrawn", "collected_in_error", "regulator_or_court_order",
           "retention_period_ended")

ERASURE_NAMESPACE = uuid.UUID("3b8e5c1e-77a4-4c0a-9f55-2f5d0f2a6b14")  # never change: event ids derive from it
ERASED_REF_PREFIX = "Patient/erased-"


class ErasureError(Exception):
    """A refusal with the HTTP status the route should answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def new_token() -> str:
    return f"{ERASED_REF_PREFIX}{uuid.uuid4().hex}"


def tombstone(erasure_id: UUID | str) -> str:
    return f"[erased {erasure_id}]"


def erasure_event_id(erasure_id: UUID, case_id: UUID) -> UUID:
    return uuid.uuid5(ERASURE_NAMESPACE, f"{erasure_id}|{case_id}")


def is_erased_ref(ref: str | None) -> bool:
    return bool(ref) and ref.startswith(ERASED_REF_PREFIX)


# ---- HAPI ----------------------------------------------------------------------------------------------------------------

# Referential integrity: HAPI refuses to delete a resource another still points at. Referrers go first, the Patient last.
_DELETE_ORDER = ("Provenance", "Communication", "Consent", "Appointment", "Task", "ServiceRequest", "Procedure",
                 "DiagnosticReport", "Observation", "Encounter", "Condition")
# Staff and devices are not the patient's data (staff are out of scope, docs/governance/erasure.md).
_NOT_THE_PATIENTS = frozenset({"Practitioner", "Device", "Organization", "Location"})
_EXPUNGE = {"resourceType": "Parameters", "parameter": [
    {"name": "limit", "valueInteger": 10000},
    {"name": "expungeDeletedResources", "valueBoolean": True},
    {"name": "expungePreviousVersions", "valueBoolean": True},
]}


class HapiEraser:
    """Delete-and-expunge against HAPI over HTTP. HAPI must run with `hapi.fhir.expunge_enabled` (docker-compose.yml)."""

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.client = HttpFhirClient(base_url, timeout=timeout)
        self.base_url = self.client.base_url

    def _json(self, method: str, url: str, body: dict | None = None):
        try:
            with self.client._call(method, url, body) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, None
        except Exception as exc:  # noqa: BLE001
            raise FhirError(f"HAPI unreachable: {exc}") from exc

    def exists(self, ref: str) -> bool:
        status, _ = self._json("GET", safe_ref_url(self.base_url, ref))
        if status == 200:
            return True
        if status in (404, 410):
            return False
        raise FhirError(f"HAPI answered HTTP {status}")

    def compartment(self, patient_ref: str) -> list[str]:
        """Every resource in the Patient's compartment (`$everything`, all pages), as `Type/id`. Empty when the Patient is gone."""
        url: str | None = safe_ref_url(self.base_url, patient_ref) + "/$everything?_count=200"
        refs: list[str] = []
        while url:
            status, bundle = self._json("GET", url)
            if status in (404, 410):
                return refs
            if status != 200 or not isinstance(bundle, dict):
                raise FhirError(f"HAPI answered HTTP {status} to $everything")
            for entry in bundle.get("entry", []):
                res = entry.get("resource") or {}
                if res.get("resourceType") and res.get("id"):
                    refs.append(f"{res['resourceType']}/{res['id']}")
            url = next((link["url"] for link in bundle.get("link", []) if link.get("relation") == "next"), None)
        return refs

    def _delete_and_expunge(self, ref: str) -> bool:
        """True when HAPI has nothing left of `ref`. 409 (still referenced) is False: the caller goes again after the referrers."""
        url = safe_ref_url(self.base_url, ref)
        status, _ = self._json("DELETE", url)
        if status == 409:
            return False
        if status not in (200, 202, 204, 404, 410):
            raise FhirError(f"HAPI answered HTTP {status} to DELETE {ref.split('/')[0]}")
        status, _ = self._json("POST", url + "/$expunge", _EXPUNGE)
        if status in (404, 410):
            return True  # nothing there to expunge: already gone
        if status in (400, 403, 405, 422, 501):
            raise FhirError(f"HAPI refused $expunge (HTTP {status}): is hapi.fhir.expunge_enabled set? docs/governance/erasure.md")
        if status != 200:
            raise FhirError(f"HAPI answered HTTP {status} to $expunge")
        return True

    def erase(self, refs: list[str]) -> list[str]:
        """Delete and expunge `refs` (deduplicated, referrers first, the Patient last), then check each is unreadable. Returns
        the refs handled. Raises FhirError when something is still there, or HAPI will not expunge."""
        unique = sorted({r for r in refs if is_safe_ref(r) and r.split("/")[0] not in _NOT_THE_PATIENTS})

        def rank(ref: str) -> tuple[int, str]:
            kind = ref.split("/")[0]
            if kind == "Patient":
                return (len(_DELETE_ORDER) + 1, ref)
            return (_DELETE_ORDER.index(kind) if kind in _DELETE_ORDER else len(_DELETE_ORDER), ref)

        pending = sorted(unique, key=rank)
        for _ in range(4):  # a few passes: whatever answered 409 goes again after its referrers
            pending = [r for r in pending if not self._delete_and_expunge(r)]
            if not pending:
                break
        if pending:
            raise FhirError(f"{len(pending)} resource(s) could not be deleted from HAPI (still referenced)")
        for ref in unique:
            if self.exists(ref):
                raise FhirError(f"{ref.split('/')[0]} is still readable in HAPI after the expunge")
        return unique


# ---- the procedure -------------------------------------------------------------------------------------------------------

# What the pre-scan of a case counts, so the event can name the categories before it is written.
_INVENTORY = (
    ("draft_text", "SELECT count(*) FROM drafts WHERE case_id = %s AND text !~ '^\\[erased '"),
    ("prompt_text", "SELECT count(*) FROM prompts WHERE prompt_text !~ '^\\[erased ' AND prompt_hash IN"
                    " (SELECT prompt_hash FROM drafts WHERE case_id = %s)"),
    ("notification_body", "SELECT count(*) FROM notifications WHERE case_id = %s AND body !~ '^\\[erased '"),
    ("reply_text", "SELECT count(*) FROM patient_replies WHERE case_id = %s AND text !~ '^\\[erased '"),
    ("event_notes", "SELECT count(*) FROM events WHERE case_id = %s AND jsonb_typeof(payload -> 'note') = 'string'"
                    " AND payload ->> 'note' !~ '^\\[erased '"),
    ("agency_ledger", "SELECT count(*) FROM agency_tasks WHERE case_id = %s AND"
                      " (input::text ~ 'Patient/[A-Za-z0-9-]' OR coalesce(output::text, '') ~ 'Patient/[A-Za-z0-9-]')"),
)


@dataclass(frozen=True)
class MapRow:
    case_id: UUID | None
    patient_ref: str
    token: str | None


class ErasureService:
    def __init__(self, pool, store, pathway, hapi: HapiEraser | None, resolver=None) -> None:
        # `pathway` is the default; `resolver(case_id)` (optional) names the pathway a case runs on (several pathways, one engine).
        self.pool, self.store, self.pathway, self.hapi = pool, store, pathway, hapi
        self.resolver = resolver

    def _pathway_of(self, case_id):
        return self.resolver(case_id) if self.resolver is not None else self.pathway

    # -- request ----------------------------------------------------------------------------------------------------------

    def request(self, patient_ref: str, reason: str, requested_by: str, now: datetime) -> dict:
        if reason not in REASONS:
            raise ErasureError(422, f"reason must be one of {list(REASONS)}")
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT e.case_id, c.current_state FROM events e JOIN cases c ON c.id = e.case_id"
                " WHERE e.seq = 1 AND e.payload ->> 'patient_ref' = %s ORDER BY e.case_id", (patient_ref,)).fetchall()
            open_cases = [r for r in rows if r[1] != self.pathway.terminal_state]
            if open_cases:
                raise ErasureError(409, f"{len(open_cases)} of this patient's {len(rows)} case(s) are not closed: "
                                        "an open screening is closed by a clinician first, then erased")
            if not rows and not (self.hapi and self.hapi.exists(patient_ref)):
                raise ErasureError(404, "no case and no FHIR Patient for this reference")
            erasure_id = uuid.uuid4()
            try:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO erasures (erasure_id, reason, requested_by, requested_at, case_ids)"
                        " VALUES (%s, %s, %s, %s, %s)", (erasure_id, reason, requested_by, now, [r[0] for r in rows]))
                    for case_id, _ in rows or [(None, None)]:
                        conn.execute("INSERT INTO erasure_map (erasure_id, case_id, patient_ref, token) VALUES (%s, %s, %s, %s)",
                                     (erasure_id, case_id, patient_ref, new_token() if case_id else None))
            except Exception as exc:
                if getattr(getattr(exc, "diag", None), "constraint_name", "") == "erasure_map_one_open_per_patient":
                    raise ErasureError(409, "an erasure of this patient is already requested") from exc
                raise
        return {"erasure_id": str(erasure_id), "status": "requested", "reason": reason, "cases": len(rows),
                "requested_by": requested_by, "requested_at": now.isoformat(), "approval_needed_from": "another supervisor"}

    # -- read -------------------------------------------------------------------------------------------------------------

    def get(self, erasure_id: UUID) -> dict:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT erasure_id, reason, status, requested_by, requested_at, approved_by, approved_at, completed_at,"
                " coalesce(array_length(case_ids, 1), 0), categories, counts FROM erasures WHERE erasure_id = %s",
                (erasure_id,)).fetchone()
        if row is None:
            raise ErasureError(404, "no such erasure")

        def iso(d):
            return d.isoformat() if d else None

        return {"erasure_id": str(row[0]), "reason": row[1], "status": row[2], "requested_by": row[3],
                "requested_at": iso(row[4]), "approved_by": row[5], "approved_at": iso(row[6]),
                "completed_at": iso(row[7]), "cases": row[8], "categories": list(row[9]), "counts": row[10]}

    # -- approve and perform ----------------------------------------------------------------------------------------------

    def approve(self, erasure_id: UUID, approver: str, now: datetime) -> dict:
        with self.pool.connection() as conn, conn.transaction():
            # The row lock is held for the whole procedure: one approval at a time, and a crash rolls the register back to
            # "requested" while the per-case work already committed is skipped on the retry.
            row = conn.execute("SELECT status, requested_by FROM erasures WHERE erasure_id = %s FOR UPDATE",
                               (erasure_id,)).fetchone()
            if row is None:
                raise ErasureError(404, "no such erasure")
            status, requested_by = row
            if status == "completed":
                raise ErasureError(409, "this erasure is already completed")
            if approver == requested_by:
                raise ErasureError(403, "the approval must come from a different supervisor than the request")
            mapping = [MapRow(*r) for r in conn.execute(
                "SELECT case_id, patient_ref, token FROM erasure_map WHERE erasure_id = %s ORDER BY id", (erasure_id,))]
            if not mapping:
                raise ErasureError(409, "the erasure map is gone: nothing to do")
            patient_ref = mapping[0].patient_ref
            case_ids = [m.case_id for m in mapping if m.case_id]

            fhir_refs = self._erase_fhir(patient_ref, case_ids)
            contacts.forget(patient_ref)

            totals: dict[str, int] = {"fhir_resources": len(fhir_refs)}
            categories: set[str] = {"contact_registry"} | ({"fhir_resources"} if fhir_refs else set())
            for m in mapping:
                if m.case_id:
                    cats, counts = self._erase_case(erasure_id, m, now)
                    categories |= cats
                    for key, n in counts.items():
                        totals[key] = totals.get(key, 0) + n
            conn.execute(
                "UPDATE erasures SET status = 'completed', approved_by = %s, approved_at = %s, completed_at = %s,"
                " categories = %s, counts = %s WHERE erasure_id = %s",
                (approver, now, now, sorted(categories), Jsonb(totals), erasure_id))
            conn.execute("DELETE FROM erasure_map WHERE erasure_id = %s", (erasure_id,))
        return self.get(erasure_id)

    def _erase_fhir(self, patient_ref: str, case_ids: list[UUID]) -> list[str]:
        if self.hapi is None:
            raise ErasureError(503, "FHIR_BASE_URL is not configured: the FHIR store cannot be erased, so nothing is")
        try:
            refs = [patient_ref, *self.hapi.compartment(patient_ref)]
            from app.calendar import default_calendar

            for case_id in case_ids:
                events = self.store.events_for(case_id)
                if events and not is_erased_ref(events[0].payload.get("patient_ref")):
                    ids = case_resource_ids(self._pathway_of(case_id), default_calendar(), case_id, events)
                    refs += [r for r in (ids["task"], ids["consent"]) if r]
                    for key in ("service_requests", "appointments", "communications", "provenance"):
                        refs += ids[key]
            return self.hapi.erase(refs)
        except FhirError as exc:
            raise ErasureError(502, f"the FHIR store could not be erased ({exc}); nothing in the database was changed") from exc

    def _erase_case(self, erasure_id: UUID, m: MapRow, now: datetime) -> tuple[set[str], dict]:
        events = self.store.events_for(m.case_id)
        if events and is_erased_ref(events[0].payload.get("patient_ref")):
            # done by an earlier attempt of this erasure: its record in the log says what was erased
            done = next((e for e in events if e.type == ERASURE_EVENT and e.payload.get("erasure_id") == str(erasure_id)), None)
            return set(done.payload["categories"]) if done else set(), {}
        categories = {"event_patient_reference"}
        with self.pool.connection() as conn:
            for name, query in _INVENTORY:
                if conn.execute(query, (m.case_id,)).fetchone()[0]:
                    categories.add(name)
            if conn.execute("SELECT 1 FROM fhir_projection_queue WHERE case_id = %s UNION ALL"
                            " SELECT 1 FROM fhir_projection_dead_letter WHERE case_id = %s",
                            (m.case_id, m.case_id)).fetchone():
                categories.add("projection_queue")
        event = Event(m.case_id, ERASURE_EVENT, now, ERASURE_ACTOR,
                      {"erasure_id": str(erasure_id), "categories": sorted(categories)},
                      erasure_event_id(erasure_id, m.case_id))
        payloads.validate(ERASURE_EVENT, event.payload)
        counts: dict = {}

        def scrub(cur) -> None:
            cur.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (m.case_id, m.patient_ref, m.token, tombstone(erasure_id)))
            counts.update(cur.fetchone()[0])
            cur.execute("DELETE FROM fhir_projection_queue WHERE case_id = %s", (m.case_id,))
            cur.execute("DELETE FROM fhir_projection_dead_letter WHERE case_id = %s", (m.case_id,))

        self.store.append(event, self._pathway_of(m.case_id), within=scrub)
        return categories, {k: v for k, v in counts.items() if isinstance(v, int)}
