"""`/integrations/mock/*`: the MOCK Malaffi result replay and the MOCK Sahatna booking callback (ADR-057, app.mock_integrations).

Coordinator and supervisor only (`policy.ROUTE_POLICY`), and only on a development or declared demo deployment: anywhere else
every route answers 404, as if it did not exist. Each response says `mock: true` and carries the label the page shows.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import mock_integrations as mocks

router = APIRouter(prefix="/integrations/mock", tags=["integrations-mock"])


def require_mocks() -> None:
    if not mocks.enabled():
        raise HTTPException(404, "Not Found")


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except mocks.MockError as exc:
        raise HTTPException(exc.status, exc.detail) from None


class MalaffiReplay(BaseModel):
    redeliver: bool = False  # send the last mock message again: the adapter answers 'duplicate' and no second case appears


class SahatnaCallback(BaseModel):
    case_id: UUID
    slot_start: datetime | None = None  # default: 09:30 on the first weekday 14 days after the simulation clock


@router.get("", dependencies=[Depends(require_mocks)])
def mock_status() -> dict:
    return mocks.status()


@router.post("/malaffi/replay", dependencies=[Depends(require_mocks)])
def malaffi_replay(body: MalaffiReplay | None = None) -> dict:
    return _call(mocks.malaffi_replay, bool(body and body.redeliver))


@router.post("/sahatna/booking-callback", dependencies=[Depends(require_mocks)])
def sahatna_booking_callback(body: SahatnaCallback) -> dict:
    return _call(mocks.sahatna_booking_callback, body.case_id, body.slot_start)
