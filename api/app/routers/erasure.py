"""Erasure of a patient's personal data (A32, ADR-025). Two supervisors, two calls; the procedure is `app.erasure`.

    POST /erasures                         supervisor   {patient_ref, reason}   request
    POST /erasures/{erasure_id}/approve    a DIFFERENT supervisor   performs it, then answers with the register row
    GET  /erasures/{erasure_id}            supervisor               the register row (opaque ids, categories, counts)

Answers carry the erasure id and counts only: never the patient reference, never a name, never text.

The patient reference is in the BODY, not the path (the ticket sketched `/patients/{patient_ref}/erasure`): T-F11 allows only
opaque UUIDs in URLs (`tools/check/phi_scan.py routes`), and a URL is written to the access log, which erasure cannot scrub.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.auth import Identity, require_identity
from app.clock import clock
from app.config import settings
from app.erasure import ErasureError, ErasureService, HapiEraser
from app.routers import cases

router = APIRouter(tags=["erasure"])

PATIENT_REF_RE = re.compile(r"^Patient/[A-Za-z0-9-]{1,64}$")  # the shape a case's patient_ref must have (payloads.PatientRef)


class RequestErasure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_ref: str  # `Patient/<id>`
    reason: str  # one of app.erasure.REASONS; checked by the service so the 422 lists them


def service() -> ErasureService:
    pool = getattr(cases.store, "_pool", None)
    if pool is None:
        raise HTTPException(503, "erasure needs the Postgres store (the in-memory store has no erasure register)")
    return ErasureService(pool, cases.store, cases.pathway, HapiEraser(settings.fhir_base_url), resolver=cases.pathway_of)


def _run(fn, *args):
    try:
        return fn(*args)
    except ErasureError as exc:
        raise HTTPException(exc.status, exc.message) from None


@router.post("/erasures", status_code=202)
def request_erasure(body: RequestErasure, identity: Identity = Depends(require_identity)) -> dict:
    if not PATIENT_REF_RE.match(body.patient_ref):
        raise HTTPException(422, "patient_ref must be 'Patient/<id>': letters, digits and '-' in the id")
    return _run(service().request, body.patient_ref, body.reason, identity.actor, clock.now())


@router.post("/erasures/{erasure_id}/approve")
def approve_erasure(erasure_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    return _run(service().approve, erasure_id, identity.actor, clock.now())


@router.get("/erasures/{erasure_id}")
def get_erasure(erasure_id: UUID, identity: Identity = Depends(require_identity)) -> dict:
    return _run(service().get, erasure_id)
