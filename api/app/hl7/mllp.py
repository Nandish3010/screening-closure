"""An optional MLLP listener on port 2575 (off by default: compose profile `hl7`, service `hl7-listener`).

    python -m app.hl7.mllp            # listens on HL7_MLLP_HOST:HL7_MLLP_PORT (0.0.0.0:2575 inside the container)

It frames and unframes (`<VT> message <FS><CR>`) and nothing else. Each message is posted to the API's `POST /hl7` with the
`system:hl7` service token (HL7_SERVICE_TOKEN), so the handler, the policy checks, the idempotency and the log rules are the
ones of the HTTP transport, and the listener holds no database credentials. If the API cannot be reached the sender gets an
AE (retry), never silence. Standard library only.

TLS is optional and off by default (app.hl7.tls: HL7_TLS_CERT / HL7_TLS_KEY, HL7_TLS_CLIENT_CA to require client
certificates, HL7_TLS_ALLOWED_CLIENT_CNS to allow-list them). With it unset the listener is clear text, which needs a VPN or
a TLS tunnel agreed with the exchange operator; there is no IP allow-list. Incomplete TLS settings stop the start-up, they
never fall back to clear text. The log carries the verified client CN, never message content. Compose publishes the port
on the loopback interface only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime

from app.hl7 import tls
from app.hl7.parser import MAX_MESSAGE_BYTES, MLLP_END, MLLP_START, Hl7ParseError, build_ack, mllp_frame, parse

log = logging.getLogger("app.hl7.mllp")


def _now() -> datetime:
    """MSH-7 of an ACK this process writes itself (the API could not be reached). This is a transport process, not the
    engine: it has no simulation clock and the stamp decides nothing."""
    return datetime.now(UTC)  # wall-clock-ok
IDLE_SECONDS = 60.0
MAX_CONNECTIONS = 32


def forward(text: str, api_url: str, token: str, timeout: float = 15.0) -> str:
    """Post one message to the API and return the ACK text; an AE/AR ACK of our own if the API cannot be reached."""
    request = urllib.request.Request(
        api_url.rstrip("/") + "/hl7", data=text.encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/hl7-v2; charset=utf-8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - the URL is configuration
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if body.startswith("MSH"):  # the API answered with an ACK (413 for an oversize message)
            return body
        log.error("API refused the message: HTTP %s", exc.code)
        reason = "interface not authorised" if exc.code in (401, 403) else "internal error, retry"
        return _local_nack(text, reason)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.error("API unreachable: %s", type(exc).__name__)
        return _local_nack(text, "internal error, retry")


def _local_nack(text: str, reason: str) -> str:
    try:
        return build_ack(parse(text), "AE", "207", reason, _now())
    except Hl7ParseError as exc:
        return build_ack(None, "AR", exc.code, exc.reason, _now())


async def _read_frame(reader: asyncio.StreamReader) -> bytes | None:
    """One MLLP frame's payload, or None at end of stream. Bytes before the start byte are discarded."""
    try:
        await asyncio.wait_for(reader.readuntil(MLLP_START), IDLE_SECONDS)
        data = await asyncio.wait_for(reader.readuntil(MLLP_END), IDLE_SECONDS)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        return None
    except asyncio.LimitOverrunError:
        return b""  # a frame over the limit: answered with an AR below
    return data[:-2]


def make_handler(api_url: str, token: str, allowed_cns: frozenset[str] | None = None):
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async def on_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with semaphore:
            try:
                ssl_object = writer.get_extra_info("ssl_object")
                if ssl_object is not None:
                    peercert = ssl_object.getpeercert()
                    if not tls.client_allowed(peercert, allowed_cns):
                        log.warning("TLS client %r is not in HL7_TLS_ALLOWED_CLIENT_CNS: connection closed", tls.client_cn(peercert))
                        return
                    log.info("TLS client %r connected", tls.client_cn(peercert))
                while True:
                    data = await _read_frame(reader)
                    if data is None:
                        break
                    if not data or len(data) > MAX_MESSAGE_BYTES:
                        ack = build_ack(None, "AR", "102", "message too large or empty", _now())
                    else:
                        try:
                            text = data.decode("utf-8")
                        except UnicodeDecodeError:
                            ack = build_ack(None, "AR", "102", "message is not valid UTF-8", _now())
                        else:
                            ack = await asyncio.get_running_loop().run_in_executor(None, forward, text, api_url, token)
                    writer.write(mllp_frame(ack))
                    await writer.drain()
            finally:
                writer.close()

    return on_connection


async def serve(host: str, port: int, api_url: str, token: str) -> None:
    ctx = tls.build_context()  # TlsConfigError: refuse to start, never clear text by accident
    server = await asyncio.start_server(make_handler(api_url, token, tls.allowed_client_cns() if ctx else None), host, port,
                                        limit=MAX_MESSAGE_BYTES + 16, ssl=ctx)
    log.info("MLLP listener on %s:%s (%s), forwarding to %s", host, port,
             "TLS, client certificate required" if ctx and ctx.verify_mode == ssl.CERT_REQUIRED else "TLS" if ctx else "clear text",
             api_url)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    token = os.environ.get("HL7_SERVICE_TOKEN", "")
    if not token:
        raise SystemExit("HL7_SERVICE_TOKEN is not set: the listener cannot authenticate to the API")
    try:
        asyncio.run(serve(os.environ.get("HL7_MLLP_HOST", "0.0.0.0"), int(os.environ.get("HL7_MLLP_PORT", "2575")),
                          os.environ.get("API_URL", "http://api:8000"), token))
    except tls.TlsConfigError as exc:
        raise SystemExit(f"TLS misconfigured, not starting: {exc}") from None


if __name__ == "__main__":
    main()
