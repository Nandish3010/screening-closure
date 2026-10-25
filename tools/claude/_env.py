"""Shared helper for the manual Claude tools: put ANTHROPIC_API_KEY from .env into the environment, never print it."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_key() -> bool:
    """True when ANTHROPIC_API_KEY is in the environment (taken from the gitignored .env if not already there)."""
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    try:
        lines = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return have_key
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == "ANTHROPIC_WORKSPACE_ID" and value.strip() and not os.environ.get(name.strip()):
            os.environ["ANTHROPIC_WORKSPACE_ID"] = value.strip().strip("'\"")
        if sep and name.strip() == "ANTHROPIC_API_KEY" and not have_key:
            # A key pasted into a terminal can pick up cursor-key or bracketed-paste escape sequences; they are never part of it.
            value = re.sub(r"\x1b\[[0-9;]*[A-Za-z~]", "", value).strip().strip("'\"")
            if value:
                os.environ["ANTHROPIC_API_KEY"] = value
                have_key = True
    return have_key
