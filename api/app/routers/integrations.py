"""`GET /integrations/feed`: the last few integration events of this demo stack, for the Integrations page.

Staff only (coordinator, supervisor; see `policy.ROUTE_POLICY`). Bounded (`app.integration_feed.MAX_LIMIT`), metadata only,
read-only. It is a simulated feed with synthetic data: the HL7 messages come from the tools in `tools/hl7/`, the FHIR writes
go to the local HAPI server and the notifications go to the mock, none to a real exchange, provider or patient.
"""
from fastapi import APIRouter, Query

from app import integration_feed

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/feed")
def feed(limit: int = Query(integration_feed.DEFAULT_LIMIT, ge=1, le=integration_feed.MAX_LIMIT)) -> dict:
    return {
        "simulated": True,
        "synthetic": True,
        "scope": "this API process; newest first; metadata only (no message content, names or identifiers)",
        "max_entries": integration_feed.MAX_ENTRIES,
        "entries": [e.to_json() for e in integration_feed.latest(limit)],
    }
