#!/usr/bin/env python3
"""A tiny TCP forwarder for the offline overlay (docker-compose.offline.yml): `tcp_forward.py 8000:api:8000 8080:fhir:8080`
listens on each local port and pipes every connection to host:port. Standard library only; it carries bytes and
looks at none of them. Runs in the `edge` container, nowhere else."""

import socket
import sys
import threading


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def serve(listen_port: int, host: str, port: int) -> None:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", listen_port))
    server.listen(128)
    while True:
        client, _ = server.accept()
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            client.close()
            continue
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def main(argv: list[str]) -> None:
    for spec in argv:
        listen, host, port = spec.split(":")
        threading.Thread(target=serve, args=(int(listen), host, int(port)), daemon=True).start()
        print(f"forwarding :{listen} -> {host}:{port}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main(sys.argv[1:])
