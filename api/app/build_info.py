"""Which commit is running. `_build_sha.txt` holds the `$Format:%H$` placeholder in git; `git archive` (how deploy/gcp/deploy.sh
delivers the code, see .gitattributes) replaces it with the commit's hash, so the image built on the VM carries the commit it was built
from with no extra step. `SC_COMMIT_SHA` overrides (a CI or compose build arg). Anywhere else the answer is "dev"."""

from __future__ import annotations

import os
import re
from pathlib import Path

_FILE = Path(__file__).with_name("_build_sha.txt")
_HEX = re.compile(r"^[0-9a-f]{7,40}$")


def commit() -> str:
    for raw in (os.environ.get("SC_COMMIT_SHA", ""), _read()):
        raw = raw.strip().lower()
        if _HEX.fullmatch(raw):
            return raw[:12]
    return "dev"


def _read() -> str:
    try:
        return _FILE.read_text(encoding="utf-8")
    except OSError:
        return ""
