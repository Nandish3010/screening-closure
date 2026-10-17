#!/usr/bin/env python3
"""Documentation hygiene checks (F1, T-E2): links, tables, TODO markers.

Checks, over the markdown files in docs/ and project/ (plus README.md for links when asked):

1. Broken relative links: `[text](path)`, `![alt](path)` and `[ref]: path` whose target file or
   directory does not exist. External URLs, mailto: and pure #anchors are ignored; a `#fragment`
   on a local path is stripped before the existence check. Code fences and inline code are skipped.
2. Markdown tables whose rows do not all have the header's column count.
3. `TODO` in docs/ (text and generated JSON/markdown alike), except on lines that carry an H1 or
   H2 review marker (the clinician and Arabic reviews that are human-owner tasks).

4. Decision matrix completeness (T-E6): the event types in `docs/governance/decision-matrix.md`
   equal the keys of `EVENT_POLICY` in `api/app/policy.py` plus `case_created`; a row whose first cell
   says "(planned" is not counted. Checked only when both files exist.
5. Hazard log completeness (T-E7): every hazard row in `docs/governance/hazard-log.md` has a likelihood
   and an impact of L, M or H, a non-empty control, and a test cell that cites at least one T-ID, every
   cited T-ID existing in `project/TEST-CASES.md`.

Every finding is an error; there is no pre-freeze leniency, so the check is red while any TODO is
left (generated files such as docs/results/results.md are fixed at their source and regenerated).

Usage: python tools/check/docs_check.py [--root DIR] [--readme]
Exit code 0 = pass, 1 = findings.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TODO_RE = re.compile(r"\bTODO\b")
REVIEW_MARKER_RE = re.compile(r"\bH[12]\b")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
INLINE_LINK_RE = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
SEPARATOR_CELL_RE = re.compile(r"^\s*:?-{1,}:?\s*$")
TEXT_SUFFIXES = {".md", ".markdown", ".json", ".txt", ".yaml", ".yml", ".csv"}


@dataclass(frozen=True)
class Finding:
    kind: str  # link | table | todo | matrix | hazard
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.kind}] {self.message}"


def strip_inline_code(line: str) -> str:
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


def iter_prose_lines(text: str):
    """Yield (lineno, line, in_fence) for each line; fenced code is flagged, not removed."""
    fence = None
    for i, line in enumerate(text.splitlines(), start=1):
        m = FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * 3
            yield i, line, True
            continue
        if fence is not None:
            yield i, line, True
            if m and m.group(1).startswith(fence):
                fence = None
            continue
        yield i, line, False


def split_row(line: str) -> list[str]:
    """Split a GFM table row into cells; `\\|` is a literal pipe, edge pipes are optional."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(s[i])
        i += 1
    cells.append("".join(cur))
    return cells


def check_tables(rel: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    block: list[tuple[int, str]] = []

    def flush():
        if len(block) >= 2:
            header_no, header = block[0]
            sep_cells = split_row(block[1][1])
            if all(SEPARATOR_CELL_RE.match(c) for c in sep_cells):
                expected = len(split_row(header))
                if len(sep_cells) != expected:
                    findings.append(Finding("table", rel, block[1][0],
                        f"separator row has {len(sep_cells)} columns, header has {expected}"))
                for no, row in block[2:]:
                    n = len(split_row(row))
                    if n != expected:
                        findings.append(Finding("table", rel, no,
                            f"row has {n} columns, header (line {header_no}) has {expected}"))
        block.clear()

    for no, line, in_fence in iter_prose_lines(text):
        if not in_fence and line.lstrip().startswith("|"):
            block.append((no, line))
        else:
            flush()
    flush()
    return findings


def link_targets(text: str):
    for no, line, in_fence in iter_prose_lines(text):
        if in_fence:
            continue
        clean = strip_inline_code(line)
        for m in INLINE_LINK_RE.finditer(clean):
            yield no, m.group(1)
        m = REF_DEF_RE.match(clean)
        if m:
            yield no, m.group(1)


def is_external(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) or target.startswith("//")


def check_links(root: Path, path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    rel = path.relative_to(root).as_posix()
    for no, raw in link_targets(text):
        target = raw.strip("<>").strip()
        if not target or target.startswith("#") or is_external(target):
            continue
        target = target.split("#", 1)[0].split("?", 1)[0]
        if not target:
            continue
        from urllib.parse import unquote

        target = unquote(target)
        base = root if target.startswith("/") else path.parent
        resolved = (base / target.lstrip("/")).resolve()
        if not resolved.exists():
            findings.append(Finding("link", rel, no, f"broken relative link: {raw}"))
    return findings


def check_todos(root: Path, path: Path, text: str) -> list[Finding]:
    rel = path.relative_to(root).as_posix()
    out = []
    for no, line in enumerate(text.splitlines(), start=1):
        if TODO_RE.search(line) and not REVIEW_MARKER_RE.search(line):
            out.append(Finding("todo", rel, no, line.strip()[:120]))
    return out


MATRIX = "docs/governance/decision-matrix.md"
HAZARDS = "docs/governance/hazard-log.md"
POLICY = "api/app/policy.py"
TESTCASES = "project/TEST-CASES.md"
TID_RE = re.compile(r"\bT-[A-Z]\d+\b")


def policy_event_types(text: str) -> set[str]:
    """Keys of the EVENT_POLICY dict literal (a parse of the source, so no import is needed)."""
    m = re.search(r"^EVENT_POLICY[^=]*=\s*\{(.*?)^\}", text, re.S | re.M)
    return set(re.findall(r'^    "([a-z_]+)":', m.group(1), re.M)) if m else set()


def check_matrix(root: Path) -> list[Finding]:
    matrix, policy = root / MATRIX, root / POLICY
    if not (matrix.is_file() and policy.is_file()):
        return []
    rows = set()
    in_events = False
    for line in matrix.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_events = line.strip() == "## Events"
        elif in_events:
            m = re.match(r"^\| `([a-z_]+)`( \(planned)?", line)
            if m and not m.group(2):
                rows.add(m.group(1))
    wanted = policy_event_types(policy.read_text(encoding="utf-8")) | {"case_created"}
    out = [Finding("matrix", MATRIX, 1, f"event type {e!r} is in EVENT_POLICY but has no row") for e in sorted(wanted - rows)]
    out += [Finding("matrix", MATRIX, 1, f"row for {e!r} is not an event type in EVENT_POLICY") for e in sorted(rows - wanted)]
    return out


def check_hazards(root: Path) -> list[Finding]:
    hazards, cases = root / HAZARDS, root / TESTCASES
    if not (hazards.is_file() and cases.is_file()):
        return []
    known = set(re.findall(r"^\| (T-[A-Z]\d+) \|", cases.read_text(encoding="utf-8"), re.M))
    out = []
    for no, line in enumerate(hazards.read_text(encoding="utf-8").splitlines(), start=1):
        if not re.match(r"^\| HZ-\d+ \|", line):
            continue
        cells = [c.strip() for c in split_row(line)]
        hid = cells[0]
        if len(cells) < 9:
            out.append(Finding("hazard", HAZARDS, no, f"{hid}: expected 9 columns, found {len(cells)}"))
            continue
        _, _, _, likelihood, impact, control, test = cells[:7]
        if likelihood not in {"L", "M", "H"} or impact not in {"L", "M", "H"}:
            out.append(Finding("hazard", HAZARDS, no, f"{hid}: likelihood and impact must be L, M or H"))
        if not control:
            out.append(Finding("hazard", HAZARDS, no, f"{hid}: no control"))
        cited = TID_RE.findall(test)
        if not cited:
            out.append(Finding("hazard", HAZARDS, no, f"{hid}: the test cell cites no T-ID"))
        out += [Finding("hazard", HAZARDS, no, f"{hid}: {t} is not in TEST-CASES.md") for t in dict.fromkeys(cited) if t not in known]
    return out


def markdown_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix in {".md", ".markdown"})


def run(root: Path, include_readme: bool = False) -> list[Finding]:
    """Return every finding (all are errors)."""
    errors: list[Finding] = []
    link_dirs = [root / "docs", root / "project"]
    link_files: list[Path] = []
    for d in link_dirs:
        if d.is_dir():
            link_files += markdown_files(d)
    if include_readme and (root / "README.md").is_file():
        link_files.append(root / "README.md")
    for path in link_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        errors += check_links(root, path, text)
        errors += check_tables(rel, text)
    docs = root / "docs"
    if docs.is_dir():
        for path in sorted(p for p in docs.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES):
            errors += check_todos(root, path, path.read_text(encoding="utf-8", errors="replace"))
    errors += check_matrix(root)
    errors += check_hazards(root)
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="repository root (default: two levels above this file)")
    ap.add_argument("--readme", action="store_true", help="also check README.md links")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    errors = run(root, args.readme)
    shown = Counter()
    for f in errors:
        shown[f.path] += 1
        if shown[f.path] <= 5 or f.kind != "todo":
            print(f"ERROR {f}")
        elif shown[f.path] == 6:
            print(f"ERROR {f.path}: ... more TODO markers (see per-file counts below)")
    todos = Counter(f.path for f in errors if f.kind == "todo")
    for p, n in sorted(todos.items()):
        print(f"ERROR   {p}: {n} TODO marker(s)")
    kinds = Counter(f.kind for f in errors)
    print(f"docs-check: {'FAIL' if errors else 'PASS'} ({len(errors)} error(s): "
          f"{kinds['link']} link, {kinds['table']} table, {kinds['todo']} todo, "
          f"{kinds['matrix']} matrix, {kinds['hazard']} hazard)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
