"""Red-team case table and recorder (D14). See docs/governance/red-team.md.

A `Case` is one adversarial attempt: an id, the category, the input (anything; shown as a truncated `repr`) and the
expected outcome in words. A test takes the case as its parameter and the `rt` fixture, calls `rt.actual(...)` with a
short description of what really happened, then asserts. The hook in `conftest.py` collects the records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

CATEGORIES = {"V": "validator", "P": "provider", "R": "replies", "A": "agency", "H": "hl7-automation", "Z": "authz", "G": "generated"}
INPUT_MAX = 90


def show(value: Any, limit: int = INPUT_MAX) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True)
class Case:
    id: str  # RT-V-03.2: category, abuse case, number
    input: Any
    expected: str
    note: str = ""
    display: Any = None  # what the results table shows instead of `input` (e.g. only the attack part)
    xfail: str | None = None  # "RT-V-03.2: exposed, fix in next commit" or "known limitation RT-V-14.1 / HZ-09"

    @property
    def shown(self) -> str:
        return show(self.input if self.display is None else self.display)

    @property
    def category(self) -> str:
        return CATEGORIES[self.id.split("-")[1]]

    @property
    def abuse(self) -> str:
        return self.id.rsplit(".", 1)[0]


def params(cases: list[Case]):
    """The cases as pytest params: id = case id, and a strict xfail mark where the case is a known weakness."""
    seen: set[str] = set()
    out = []
    for c in cases:
        assert c.id not in seen, f"duplicate red-team case id {c.id}"
        seen.add(c.id)
        marks = [pytest.mark.xfail(strict=True, reason=c.xfail)] if c.xfail else []
        out.append(pytest.param(c, id=c.id, marks=marks))
    return out


@dataclass
class Recorder:
    """Handed to a test as the `rt` fixture."""

    props: list = field(default_factory=list)

    def actual(self, text: str) -> None:
        self.props.append(("rt_actual", str(text)))
