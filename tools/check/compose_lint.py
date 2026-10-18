#!/usr/bin/env python3
"""Compose hardening lint (SEC-1, ADR-029): every published port is bound to the loopback interface.

A `ports:` entry like `"8000:8000"` publishes the port on every interface of the host, so anyone on the same
network can reach the API, HAPI or Postgres. This lint reads every compose file given (default: docker-compose.yml and
docker-compose.offline.yml) and fails on any published port that does not name host IP 127.0.0.1 (or ::1/localhost,
which compose accepts as `[::1]`). It understands the short string syntax (`"HOST:CONTAINER"`, `"IP:HOST:CONTAINER"`,
with `${VAR:-default}` interpolation and `/tcp`), the long syntax (`published` + `host_ip`), and the `!reset` and
`!override` merge tags of an overlay. A port with no host side (`"8000"`, or only `target:`) is not published to a fixed
host port but still on every interface, so it counts as unbound too. `expose:` (container-to-container) is not
published and is ignored.

Usage: python tools/check/compose_lint.py [FILE ...]      exit 0 = clean, 1 = findings
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILES = ("docker-compose.yml", "docker-compose.offline.yml")
LOOPBACK = {"127.0.0.1", "::1", "[::1]", "localhost"}


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts compose's merge tags (`!reset`, `!override`) and returns the plain value."""


def _any_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_Loader.add_multi_constructor("!", _any_tag)


def _strip_interpolation(text: str) -> str:
    """`${PORT:-8000}` -> `8000`, `${HOST_IP}` -> `` (unknown: treated as absent)."""
    text = re.sub(r"\$\{[^}:]*:?-([^}]*)\}", r"\1", text)
    return re.sub(r"\$\{[^}]*\}", "", text)


def _short_syntax_host_ip(entry: str) -> tuple[str | None, bool]:
    """(host ip or None, has_host_side) of `[IP:]HOST[-range]:CONTAINER[/proto]` or a bare `CONTAINER`."""
    text = _strip_interpolation(entry.strip()).split("/", 1)[0]
    if text.startswith("["):  # [::1]:8000:8000
        end = text.index("]")
        ip, rest = text[: end + 1], text[end + 1 :].lstrip(":")
        return ip, rest.count(":") >= 1
    parts = text.split(":")
    if len(parts) == 1:
        return None, False
    if len(parts) == 2:
        return None, True
    return parts[0], True


def lint_data(data: dict, label: str) -> list[str]:
    problems: list[str] = []
    for name, service in (data.get("services") or {}).items():
        for entry in (service or {}).get("ports") or []:
            where = f"{label}: service {name!r}, port {entry!r}"
            if isinstance(entry, dict):
                host_ip = _strip_interpolation(str(entry.get("host_ip") or ""))
                if host_ip not in LOOPBACK:
                    problems.append(f"{where}: published without host_ip 127.0.0.1")
                continue
            ip, has_host = _short_syntax_host_ip(str(entry))
            if ip not in LOOPBACK:
                problems.append(
                    f"{where}: published on every interface; write \"127.0.0.1:HOST:CONTAINER\"" if has_host
                    else f"{where}: a bare container port is published on every interface; bind it to 127.0.0.1"
                )
    return problems


def lint_file(path: Path) -> list[str]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}
    return lint_data(data, path.name)


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or [ROOT / f for f in DEFAULT_FILES if (ROOT / f).exists()]
    problems = [p for f in files for p in lint_file(f)]
    for p in problems:
        print(f"compose-lint: FAIL {p}")
    if problems:
        return 1
    print(f"compose-lint: PASS {len(files)} file(s), every published port is bound to 127.0.0.1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
