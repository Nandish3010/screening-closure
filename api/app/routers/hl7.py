"""POST /hl7: the HL7 v2 adapter's HTTP transport (A28, ADR-039).

The body is one HL7 v2 message as text; the answer is its ACK as text (`application/hl7-v2`). The status is 200 for any
message that reached the handler, whatever the ACK code says: the code is in MSA-1, which is what an HL7 sender reads. 401
and 403 come from the route policy (only the `system:hl7` service token passes); 413 is a body over the size limit. The MLLP
listener (app.hl7.mllp) posts here, so both transports run the same handler.
"""

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.clock import clock
from app.hl7 import handler
from app.hl7.parser import MAX_MESSAGE_BYTES, build_ack

router = APIRouter(tags=["hl7"])
MEDIA_TYPE = "application/hl7-v2; charset=utf-8"


@router.post("/hl7")
async def receive(request: Request) -> Response:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_MESSAGE_BYTES:
        return Response(build_ack(None, "AR", "102", handler.R_TOO_LARGE, clock.now()), status_code=413, media_type=MEDIA_TYPE)
    body = await request.body()
    if len(body) > MAX_MESSAGE_BYTES:
        return Response(build_ack(None, "AR", "102", handler.R_TOO_LARGE, clock.now()), status_code=413, media_type=MEDIA_TYPE)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return Response(build_ack(None, "AR", "102", handler.R_ENCODING, clock.now()), media_type=MEDIA_TYPE)
    ack = await run_in_threadpool(handler.handle, text)
    return Response(ack, media_type=MEDIA_TYPE)
