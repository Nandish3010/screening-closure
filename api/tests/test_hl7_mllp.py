"""A28 (ADR-039): the optional MLLP listener frames and unframes, forwards every message to POST /hl7 (the one handler), and
answers with an ACK of its own when the API cannot be reached. Standard library only."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import confirmatory
from app.events import InMemoryEventStore
from app.hl7 import contacts, handler, mllp
from app.hl7 import parser as p
from app.routers import cases
from test_hl7_handler import fit


@pytest.fixture(autouse=True)
def world(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()


async def exchange(frames: list[bytes], forward) -> list[bytes]:
    """Start the listener on an ephemeral port with `forward` as the API, send raw frames on ONE connection, read the replies."""
    server = await asyncio.start_server(mllp.make_handler("http://api.invalid", "tok"), "127.0.0.1", 0, limit=p.MAX_MESSAGE_BYTES + 16)
    port = server.sockets[0].getsockname()[1]
    replies = []
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        for frame in frames:
            writer.write(frame)
            await writer.drain()
            data = await asyncio.wait_for(reader.readuntil(p.MLLP_END), 5)
            replies.append(data)
        writer.close()
    finally:
        server.close()
        await server.wait_closed()
    return replies


def run(frames, monkeypatch, forward=None):
    monkeypatch.setattr(mllp, "forward", forward or (lambda text, api, token: handler.handle(text)))
    return asyncio.run(exchange(frames, forward))


def test_a_framed_message_gets_a_framed_ack_from_the_same_handler(monkeypatch):
    (reply,) = run([p.mllp_frame(fit("M1"))], monkeypatch)
    assert reply.startswith(p.MLLP_START) and reply.endswith(p.MLLP_END)
    ack = p.parse(p.mllp_unframe(reply))
    assert ack.first("MSA").get(1) == "AA" and ack.first("MSA").get(2) == "M1"
    assert len(cases.store.load_cases(cases.pathway)) == 1


def test_several_messages_on_one_connection_and_redelivery_are_idempotent(monkeypatch):
    replies = run([p.mllp_frame(fit("M1")), p.mllp_frame(fit("M1")), p.mllp_frame(fit("M2", flag="N", value="negative"))], monkeypatch)
    codes = [p.parse(p.mllp_unframe(r)).first("MSA").get(1) for r in replies]
    assert codes == ["AA", "AA", "AA"] and len(cases.store.load_cases(cases.pathway)) == 1


def test_garbage_before_the_start_byte_is_ignored_and_a_malformed_message_is_an_ar(monkeypatch):
    (a, b) = run([b"noise" + p.mllp_frame(fit("M3")), p.mllp_frame("not hl7")], monkeypatch)
    assert p.parse(p.mllp_unframe(a)).first("MSA").get(1) == "AA"
    assert p.parse(p.mllp_unframe(b)).first("MSA").get(1) == "AR"


def test_invalid_utf8_and_an_empty_frame_are_an_ar(monkeypatch):
    (a, b) = run([p.MLLP_START + b"MSH|\xff\xfe" + p.MLLP_END, p.MLLP_START + p.MLLP_END], monkeypatch)
    assert all(p.parse(p.mllp_unframe(r)).first("MSA").get(1) == "AR" for r in (a, b))


class _Api(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        _Api.seen.append((self.path, self.headers["Authorization"], body))
        out = (handler.handle(body) if self.status == 200 else "denied").encode()
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api():
    _Api.seen, _Api.status = [], 200
    server = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", _Api
    server.shutdown()


def test_forward_posts_to_slash_hl7_with_the_service_token(fake_api):
    url, api = fake_api
    ack = mllp.forward(fit("F1"), url, "secret-token")
    assert p.parse(ack).first("MSA").get(1) == "AA"
    assert api.seen[0][0] == "/hl7" and api.seen[0][1] == "Bearer secret-token" and api.seen[0][2].startswith("MSH")


def test_when_the_api_refuses_or_is_down_the_sender_gets_an_ae_not_silence(fake_api):
    url, api = fake_api
    api.status = 403
    ack = p.parse(mllp.forward(fit("F2"), url, "bad"))
    assert ack.first("MSA").get(1) == "AE" and ack.first("MSA").get(2) == "F2" and ack.first("ERR").get(3, 1) == "207"
    dead = p.parse(mllp.forward(fit("F3"), "http://127.0.0.1:1", "t", timeout=1))
    assert dead.first("MSA").get(1) == "AE" and dead.first("MSA").get(2) == "F3"
    unreadable = p.parse(mllp.forward("garbage", "http://127.0.0.1:1", "t", timeout=1))
    assert unreadable.first("MSA").get(1) == "AR"


def test_the_listener_needs_a_token(monkeypatch):
    monkeypatch.delenv("HL7_SERVICE_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        mllp.main()
