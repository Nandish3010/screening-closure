#!/usr/bin/env python3
"""Send HL7 v2 messages to the adapter and print the ACK. Standard library only.

    python tools/hl7/send.py tools/hl7/samples/oru_fit_positive.hl7                # POST /hl7 on $API_URL
    python tools/hl7/send.py --mllp localhost:2575 tools/hl7/samples/siu_s12_book.hl7   # the optional MLLP listener
    python tools/hl7/send.py --mllp localhost:2575 --tls-ca ca.pem --tls-cert client.pem --tls-key client.key FILE   # mutual TLS
    python tools/hl7/send.py --fresh tools/hl7/samples/oru_fit_positive.hl7        # a new MSH-10: a new case, not a duplicate

The token is HL7_SERVICE_TOKEN (default: the development token, which works only while the API runs with APP_ENV=dev).
Several files may be given; they are sent in order. Exit code 0 when every ACK is AA, 1 otherwise, 2 when nothing could be
sent. An ACK is printed as its MSA and ERR segments only; the message itself is never echoed.
"""

from __future__ import annotations

import argparse
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_API = "http://localhost:8000"
DEFAULT_TOKEN = "dev-only-hl7-service-token"  # noqa: S105 - the published development default (app.config)
MLLP_START, MLLP_END = b"\x0b", b"\x1c\x0d"


def load(path: Path, fresh: bool = False) -> str:
    """The file as an HL7 message: segments separated by CR. `fresh` replaces MSH-10 with a new unique id."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = text.split("\n")
    if fresh and lines and lines[0].startswith("MSH"):
        fields = lines[0].split(lines[0][3])
        if len(fields) > 9:
            fields[9] = "T" + uuid.uuid4().hex[:15]
            lines[0] = lines[0][3].join(fields)
    return "\r".join(lines) + "\r"


def send_http(message: str, api: str, token: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(api.rstrip("/") + "/hl7", data=message.encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/hl7-v2"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if body.startswith("MSH"):
            return body
        raise RuntimeError(f"HTTP {exc.code} from the API") from None


def send_mllp(message: str, host: str, port: int, timeout: float = 20.0, ssl_context: ssl.SSLContext | None = None) -> str:
    with socket.create_connection((host, port), timeout=timeout) as raw:
        sock = ssl_context.wrap_socket(raw, server_hostname=host) if ssl_context else raw
        sock.sendall(MLLP_START + message.encode("utf-8") + MLLP_END)
        data = b""
        while not data.endswith(MLLP_END):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return data.removeprefix(MLLP_START).removesuffix(MLLP_END).decode("utf-8")


def summary(ack: str) -> tuple[str, str]:
    """(ACK code, one printable line) from an ACK text."""
    code = "?"
    shown = []
    for line in ack.replace("\n", "\r").split("\r"):
        if line.startswith("MSA|"):
            parts = line.split("|")
            code = parts[1] if len(parts) > 1 else "?"
            shown.append(line)
        elif line.startswith("ERR|"):
            shown.append(line)
    return code, "  ".join(shown)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--mllp", metavar="HOST:PORT", help="send over MLLP instead of HTTP")
    ap.add_argument("--tls-ca", metavar="PEM", help="with --mllp: use TLS and verify the server against this CA bundle")
    ap.add_argument("--tls-cert", metavar="PEM", help="with --mllp --tls-ca: client certificate (mutual TLS)")
    ap.add_argument("--tls-key", metavar="PEM", help="private key of --tls-cert")
    ap.add_argument("--fresh", action="store_true", help="give each message a new MSH-10")
    args = ap.parse_args(argv)
    api = os.environ.get("API_URL", DEFAULT_API)
    token = os.environ.get("HL7_SERVICE_TOKEN", DEFAULT_TOKEN)
    ctx = None
    if args.tls_ca:
        try:
            ctx = ssl.create_default_context(cafile=args.tls_ca)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            if args.tls_cert:
                ctx.load_cert_chain(args.tls_cert, args.tls_key)
        except (OSError, ssl.SSLError) as exc:
            print(f"could not load the TLS files: {exc}", file=sys.stderr)
            return 2
    worst = 0
    for path in args.files:
        message = load(path, args.fresh)
        try:
            if args.mllp:
                host, _, port = args.mllp.rpartition(":")
                ack = send_mllp(message, host or "localhost", int(port), ssl_context=ctx)
            else:
                ack = send_http(message, api, token)
        except (OSError, ssl.SSLError, RuntimeError, ValueError) as exc:
            print(f"{path.name}: could not send: {exc}", file=sys.stderr)
            return 2
        code, line = summary(ack)
        print(f"{path.name}: {code}  {line}")
        worst = worst or (0 if code == "AA" else 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
