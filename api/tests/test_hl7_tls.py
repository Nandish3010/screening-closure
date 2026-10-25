"""M7-6: optional TLS / mutual TLS on the MLLP listener. Throw-away certificates are generated with openssl at test time;
the API forward is stubbed. No database."""

from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

from app.hl7 import mllp, tls
from app.hl7 import parser as p

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "hl7" / "gen_test_certs.sh"
MSG = "MSH|^~\\&|LABSYS|SYNTH-LAB|SC|SC|20260101000000||ORU^R01|M1|P|2.5\r"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")


@pytest.fixture(scope="module")
def certs(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("certs")
    subprocess.run(["bash", str(SCRIPT), str(d)], check=True, capture_output=True)
    return d


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(mllp, "forward", lambda text, api, token: "MSH|^~\\&|SC|SC|||20260101||ACK|A1|P|2.5\rMSA|AA|M1\r")
    for k in ("HL7_TLS_CERT", "HL7_TLS_KEY", "HL7_TLS_CLIENT_CA", "HL7_TLS_ALLOWED_CLIENT_CNS", "HL7_REQUIRE_TLS"):
        monkeypatch.delenv(k, raising=False)


def set_env(monkeypatch, certs, ca=True, cns=None):
    monkeypatch.setenv("HL7_TLS_CERT", str(certs / "server.pem"))
    monkeypatch.setenv("HL7_TLS_KEY", str(certs / "server.key"))
    if ca:
        monkeypatch.setenv("HL7_TLS_CLIENT_CA", str(certs / "ca.pem"))
    if cns is not None:
        monkeypatch.setenv("HL7_TLS_ALLOWED_CLIENT_CNS", cns)


def client_ctx(certs, name=None, version=None):
    ctx = ssl.create_default_context(cafile=str(certs / "ca.pem"))
    if name:
        ctx.load_cert_chain(str(certs / f"{name}.pem"), str(certs / f"{name}.key"))
    if version:
        ctx.minimum_version = ctx.maximum_version = version
    return ctx


async def attempt(client_ssl, plain=False) -> bytes:
    """Serve with the env-configured TLS, connect, send one frame, return the reply ('' when the server hung up)."""
    ctx = tls.build_context()
    server = await asyncio.start_server(mllp.make_handler("http://api.invalid", "t", tls.allowed_client_cns() if ctx else None),
                                        "127.0.0.1", 0, ssl=ctx)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=None if plain else client_ssl,
                                                       server_hostname=None if plain else "localhost")
        writer.write(p.mllp_frame(MSG))
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), 5)
        writer.close()
        return data
    finally:
        server.close()
        await server.wait_closed()


def run(client_ssl, plain=False):
    return asyncio.run(attempt(client_ssl, plain))


def rejected(client_ssl, plain=False) -> bool:
    try:
        return run(client_ssl, plain) == b""
    except (ssl.SSLError, ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return True


def test_good_client_cert_gets_the_ack(certs, monkeypatch, caplog):
    set_env(monkeypatch, certs)
    caplog.set_level("INFO", logger="app.hl7.mllp")
    reply = run(client_ctx(certs, "client"))
    assert reply.startswith(p.MLLP_START) and b"MSA|AA|M1" in reply
    assert "good-client" in caplog.text and "MSH" not in caplog.text


def test_no_client_cert_is_rejected(certs, monkeypatch):
    set_env(monkeypatch, certs)
    assert rejected(client_ctx(certs))


def test_rogue_ca_client_cert_is_rejected(certs, monkeypatch):
    set_env(monkeypatch, certs)
    assert rejected(client_ctx(certs, "rogue-client"))


def test_cn_not_in_allow_list_is_closed_and_logged(certs, monkeypatch, caplog):
    set_env(monkeypatch, certs, cns="good-client")
    caplog.set_level("INFO", logger="app.hl7.mllp")
    assert rejected(client_ctx(certs, "other-client"))
    assert "other-client" in caplog.text and "not in HL7_TLS_ALLOWED_CLIENT_CNS" in caplog.text
    assert b"MSA|AA" in run(client_ctx(certs, "client"))


def test_empty_allow_list_means_nobody(certs, monkeypatch):
    set_env(monkeypatch, certs, cns="")
    assert rejected(client_ctx(certs, "client"))


def test_plain_client_to_tls_listener_fails(certs, monkeypatch):
    set_env(monkeypatch, certs)
    assert rejected(None, plain=True)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_tls_below_1_2_is_rejected(certs, monkeypatch):
    set_env(monkeypatch, certs)
    ctx = client_ctx(certs, "client")
    ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    ctx.maximum_version = ssl.TLSVersion.TLSv1_1
    try:
        assert rejected(ctx)
    except ssl.SSLError:  # a local OpenSSL that cannot speak TLS 1.1 at all fails earlier, which is also a rejection
        pass


def test_cert_without_key_fails_closed(certs, monkeypatch):
    monkeypatch.setenv("HL7_TLS_CERT", str(certs / "server.pem"))
    with pytest.raises(tls.TlsConfigError):
        tls.build_context()
    with pytest.raises(tls.TlsConfigError):
        asyncio.run(mllp.serve("127.0.0.1", 0, "http://api.invalid", "t"))


def test_unreadable_files_and_orphan_settings_fail_closed(certs, monkeypatch):
    monkeypatch.setenv("HL7_TLS_CERT", str(certs / "nope.pem"))
    monkeypatch.setenv("HL7_TLS_KEY", str(certs / "server.key"))
    with pytest.raises(tls.TlsConfigError):
        tls.build_context()
    monkeypatch.delenv("HL7_TLS_CERT")
    monkeypatch.delenv("HL7_TLS_KEY")
    monkeypatch.setenv("HL7_TLS_CLIENT_CA", str(certs / "ca.pem"))
    with pytest.raises(tls.TlsConfigError):
        tls.build_context()


def test_require_tls_without_certs_refuses(monkeypatch):
    monkeypatch.setenv("HL7_REQUIRE_TLS", "1")
    with pytest.raises(tls.TlsConfigError):
        tls.build_context()


def test_plain_mode_is_unchanged_when_tls_env_is_unset():
    assert tls.build_context() is None
    assert tls.allowed_client_cns() is None
