#!/usr/bin/env python3
"""Docs-numbers check (B10 / E20b): every quoted result number in the README and the submission docs exists in the results.

Scans README.md and docs/submission/*.md for quoted result numbers:

  * percentages with a decimal       66.7%
  * percentage-point figures         +15.5pp, 16 points, 5.4 percentage points
  * bare decimals (ratios, days...)  1.31, 14.9, 4.20x

A number passes when it appears, numerically and at its own precision, somewhere in the sources of truth:
docs/results/** (*.md, *.json) first, then the documents results legitimately come from (docs/evidence.md,
docs/evidence-wages.md, docs/commercial/business-case.md, docs/pilot-plan.md, docs/governance/*.md), config/**/*.yaml
and simulator/params*.yaml. "At its own precision" means round(source, decimals of the quoted number) equals the quote,
so 1.307 supports 1.31; for % and pp a source fraction is scaled by 100, so 0.667 supports 66.7%.

Ignored: fenced and inline code, URLs and link targets, dates, times (0:15), version strings (1.2.3), ids (B12, T-E2,
ADR-041), file:line references, [[PLACEHOLDER]] tokens, integer percentages (95%, thresholds and rounded headlines
are too ambiguous to match), section numbers in headings. Remaining exceptions live in
tools/check/docs_numbers_allow.yaml: each entry names the file, the number as written and a reason. An allow entry
that no longer matches anything is itself an error, so the list cannot rot.

Usage: python tools/check/docs_numbers.py [--root DIR] [--allow FILE] [--list-allowed]
Exit code 0 = pass, 1 = findings.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SCAN_GLOBS = ["README.md", "docs/submission/*.md"]
PRIMARY_GLOBS = ["docs/results/**/*.md", "docs/results/**/*.json"]
SECONDARY_GLOBS = [
    "docs/evidence.md", "docs/evidence-wages.md", "docs/commercial/business-case.md", "docs/pilot-plan.md",
    "docs/governance/*.md", "config/**/*.yaml", "config/**/*.yml", "simulator/params*.yaml",
]
JSON_MAX_DECIMALS = 4
EXCLUDE_NAMES = {"validation_before_b21.json"}  # the superseded run, kept for the record; its numbers are stale by design
DEFAULT_ALLOW = "tools/check/docs_numbers_allow.yaml"

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
URL_RE = re.compile(r"https?://\S+|\]\([^)]*\)|<https?:[^>]*>")
PLACEHOLDER_RE = re.compile(r"\[\[[A-Z0-9_]+\]\]")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
NUM = r"\d+(?:\.\d+)?"
# Candidate forms, tried in this order on each prose line. Lookarounds keep ids (B12), versions (1.2.3),
# paths/refs (md:52, a/1.5) and thousands-grouped numbers out.
PCT_RE = re.compile(rf"(?<![\w.:/,-])[-+−]?(\d+\.\d+)\s?%")
PP_RE = re.compile(rf"(?<![\w.:/,-])[-+−]?({NUM})\s?(?:pp\b|percentage points?\b|points?\b)")
DEC_RE = re.compile(r"(?<![\w.:/,$])(?<!\w-)(?<!AED )(\d+\.\d+)(?![\w:/]|\.\d|,\d)(?!\s?%)")
HEADING_NUM_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)\d+(?:\.\d+)*\.?(?=\s)")
ANY_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Candidate:
    path: str
    line: int
    text: str  # as written, sign and unit stripped, e.g. "66.7"
    kind: str  # pct | pp | dec
    raw: str  # the full matched text, for allow-list matching

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.raw.strip()}"


def decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def prose_lines(text: str):
    fence = None
    for i, line in enumerate(text.splitlines(), start=1):
        m = FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * 3
            continue
        if fence is not None:
            if m and m.group(1).startswith(fence):
                fence = None
            continue
        yield i, line


def clean(line: str) -> str:
    line = HEADING_NUM_RE.sub(lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1))), line)  # "### 4.12 Title"
    for rx in (INLINE_CODE_RE, URL_RE, PLACEHOLDER_RE, DATE_RE):
        line = rx.sub(lambda m: " " * len(m.group(0)), line)
    return line


def candidates(path: str, text: str) -> list[Candidate]:
    out: list[Candidate] = []
    for no, line in prose_lines(text):
        s = clean(line)
        taken: list[tuple[int, int]] = []

        def free(m: re.Match) -> bool:
            return not any(m.start() < b and a < m.end() for a, b in taken)

        for kind, rx in (("pct", PCT_RE), ("pp", PP_RE), ("dec", DEC_RE)):
            for m in rx.finditer(s):
                if not free(m):
                    continue
                taken.append((m.start(), m.end()))
                out.append(Candidate(path, no, m.group(1), kind, m.group(0)))
    return out


UNIT_NUM_RE = re.compile(rf"(?<![\w.])({NUM})\s?(?:%|pp\b|percentage points?\b|points?\b)")


def load_values(root: Path, globs: list[str]) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """Numbers in the source files, as (plain, unit_tagged, json_fractions_x100).

    plain: every number token in prose/yaml and every hand-rounded JSON number.
    unit_tagged: numbers written with %, pp or points in prose, plus JSON numbers (a JSON gain may already be in points).
    json_fractions_x100: JSON numbers up to 1.5, times 100 (0.667 -> 66.7).
    """
    plain: list[Decimal] = []
    tagged: list[Decimal] = []
    scaled: list[Decimal] = []
    jplain: list[Decimal] = []
    seen: set[Path] = set()
    for g in globs:
        for p in sorted(root.glob(g)):
            if p in seen or not p.is_file() or p.name in EXCLUDE_NAMES:
                continue
            seen.add(p)
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in ANY_NUM_RE.finditer(text):
                if p.suffix == ".json" and decimals(m.group(1)) > JSON_MAX_DECIMALS:
                    continue  # full-precision floats (0.66714286) would round onto almost any quote; the .md mirrors them
                v = Decimal(m.group(1))
                plain.append(v)
                if p.suffix == ".json":
                    jplain.append(v)
                    if v <= Decimal("1.5"):
                        scaled.append(v * 100)
            if p.suffix != ".json":
                tagged += [Decimal(m.group(1)) for m in UNIT_NUM_RE.finditer(text)]
    return plain, tagged + jplain, scaled


class Corpus:
    def __init__(self, loaded: tuple[list[Decimal], list[Decimal], list[Decimal]]) -> None:
        plain, tagged, scaled = loaded
        self.plain = set(plain)
        # A quoted % or pp may come from a prose figure tagged with a unit, a JSON fraction, or a JSON number already in points.
        self.percent = set(tagged) | set(scaled)

    def has(self, text: str, kind: str) -> bool:
        pool = self.percent if kind in ("pct", "pp") else self.plain
        target = Decimal(text)
        if target in pool:
            return True
        q = Decimal(1).scaleb(-decimals(text))
        return any(v.quantize(q, rounding=ROUND_HALF_UP) == target for v in pool)


def load_allow(path: Path) -> list[dict]:
    if not path.exists():
        return []
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("allow", []) if isinstance(data, dict) else []
    for e in entries:
        missing = [k for k in ("file", "number", "reason") if not str(e.get(k, "")).strip()]
        if missing:
            raise SystemExit(f"{path}: allow entry {e!r} lacks {', '.join(missing)}")
    return entries


def norm(raw: str) -> str:
    return re.sub(r"[\s+\-−]", "", raw.lower()).replace("percentagepoints", "pp").replace("points", "pp").replace("point", "pp")


def run(root: Path, allow_path: Path | None = None):
    """Returns (mismatches, allowed, stale_allow_entries)."""
    root = Path(root)
    cands: list[Candidate] = []
    for g in SCAN_GLOBS:
        for p in sorted(root.glob(g)):
            if p.is_file():
                cands += candidates(p.relative_to(root).as_posix(), p.read_text(encoding="utf-8"))
    p1, t1, s1 = load_values(root, PRIMARY_GLOBS)
    p2, t2, s2 = load_values(root, SECONDARY_GLOBS)
    corpus = Corpus((p1 + p2, t1 + t2, s1 + s2))
    allow = load_allow(allow_path if allow_path is not None else root / DEFAULT_ALLOW)
    used = [0] * len(allow)
    bad: list[Candidate] = []
    allowed: list[tuple[Candidate, dict]] = []
    for c in cands:
        if corpus.has(c.text, c.kind):
            continue
        hit = next((i for i, e in enumerate(allow)
                    if e["file"] == c.path and norm(str(e["number"])) == norm(c.raw)), None)
        if hit is not None:
            used[hit] += 1
            allowed.append((c, allow[hit]))
        else:
            bad.append(c)
    stale = [e for e, n in zip(allow, used) if n == 0]
    return bad, allowed, stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--allow", default=None)
    ap.add_argument("--list-allowed", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)
    bad, allowed, stale = run(root, Path(a.allow) if a.allow else None)
    for c in bad:
        print(f"{c}: [number] not found in docs/results or the sources it may come from")
    for e in stale:
        print(f"{DEFAULT_ALLOW}: [stale-allow] {e['file']} {e['number']}: matches nothing, remove it")
    if a.list_allowed:
        for c, e in allowed:
            print(f"allowed {c}: {e['reason']}")
    print(f"docs-numbers: {len(bad)} unmatched, {len(allowed)} allow-listed, {len(stale)} stale allow entries")
    return 1 if bad or stale else 0


if __name__ == "__main__":
    sys.exit(main())
