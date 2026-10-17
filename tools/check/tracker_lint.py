#!/usr/bin/env python3
"""Tracker, test-case and log consistency lint (F1, T-F16).

Reads project/TRACKER.md, project/TEST-CASES.md and project/LOG.md and fails on:

1. Test-ID cross-reference (T-F16): a T-ID mentioned anywhere in TRACKER.md that TEST-CASES.md
   does not define; a T-ID defined in TEST-CASES.md that TRACKER.md never mentions; a ticket ID in
   the Ticket column of TEST-CASES.md that TRACKER.md does not define.
2. Ticket table hygiene: duplicate IDs, an unknown status, a `done` row without a commit hash, any
   commit hash that is not in `git rev-list --all`.
3. Milestone table: the Tickets/Done counts must equal what the ticket rows say (a ticket belongs
   to the milestone its Notes start with, `M1. ...`; M0 tickets by their Cut), the status must
   follow from the counts, and the Total row must equal the sum.
4. LOG.md: every dated line parses, timestamps never go backwards, ticket IDs exist in TRACKER.md,
   and a hash in the last column exists in git.

Usage: python tools/check/tracker_lint.py [--root DIR] [--no-git]
Exit code 0 = pass, 1 = findings.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUSES = {"todo", "doing", "review", "done", "blocked"}
TID_RE = re.compile(r"\bT-[A-Z]\d+\b")
TICKET_ID_RE = re.compile(r"^(?:[A-Z]\d+|M0-\d+)$")
HASH_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) ([A-Za-z]+) \|")
TZ_OFFSETS = {"IST": (5, 30), "UTC": (0, 0), "GMT": (0, 0), "GST": (4, 0), "CET": (1, 0), "BST": (1, 0)}


def split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def table_rows(text: str, heading: str) -> list[list[str]]:
    """Data rows of the first table under the `## heading` section (separator rows dropped)."""
    rows: list[list[str]] = []
    in_section = False
    seen_table = False
    for line in text.splitlines():
        if line.startswith("## "):
            if in_section and seen_table:
                break
            in_section = line[3:].strip().lower() == heading.lower()
            continue
        if in_section and line.lstrip().startswith("|"):
            seen_table = True
            cells = split_cells(line)
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c) and any(cells):
                continue
            rows.append(cells)
        elif in_section and seen_table and line.strip() == "":
            break
    return rows[1:] if rows else []  # drop the header row


@dataclass
class Ticket:
    id: str
    cut: str
    status: str
    tests: list[str]
    commit: str
    notes: str
    milestone: str | None


def parse_tickets(tracker: str) -> list[Ticket]:
    tickets = []
    for cells in table_rows(tracker, "Tickets"):
        if len(cells) < 8:
            continue
        tid, cut, _title, _dep, status, tests, commit, notes = (
            cells[0], cells[1], cells[2], cells[3], cells[4], cells[5], cells[6], cells[7:])
        notes_s = "|".join(notes).strip()
        m = re.match(r"(M\d)\b", notes_s)
        milestone = "M0" if cut == "M0" else (m.group(1) if m else None)
        tickets.append(Ticket(tid, cut, status, TID_RE.findall(tests), commit, notes_s, milestone))
    return tickets


def parse_human_ids(tracker: str) -> list[str]:
    return [c[0] for c in table_rows(tracker, "Human-owner tasks") if c and re.fullmatch(r"H\d+", c[0])]


def parse_test_cases(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Return ({T-ID: [ticket ids from the Ticket column]}, [duplicate T-IDs])."""
    defs: dict[str, list[str]] = {}
    dups: list[str] = []
    for line in text.splitlines():
        if not re.match(r"^\|\s*T-[A-Z]\d+\s*\|", line):
            continue
        cells = split_cells(line)
        tid = cells[0]
        if tid in defs:
            dups.append(tid)
        defs[tid] = [t.strip() for t in re.split(r"[,\s]+", cells[-1]) if t.strip() and t.strip() != "-"]
    return defs, dups


def git_hashes(root: Path) -> set[str] | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-list", "--all"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return set(out.split())


def hash_exists(h: str, known: set[str]) -> bool:
    return any(full.startswith(h) for full in known)


def lint_xref(tracker: str, tests_text: str, ticket_ids: set[str]) -> list[str]:
    errs = []
    defs, dups = parse_test_cases(tests_text)
    errs += [f"TEST-CASES.md: test ID {t} defined more than once" for t in dups]
    refs = set(TID_RE.findall(tracker))
    for t in sorted(refs - set(defs)):
        errs.append(f"TRACKER.md references {t}, which TEST-CASES.md does not define")
    for t in sorted(set(defs) - refs):
        errs.append(f"TEST-CASES.md defines {t}, which TRACKER.md never references")
    for t, tickets in sorted(defs.items()):
        for tk in tickets:
            if TICKET_ID_RE.match(tk) or re.fullmatch(r"H\d+", tk):
                if tk not in ticket_ids:
                    errs.append(f"TEST-CASES.md: {t} names ticket {tk}, which TRACKER.md does not define")
            else:
                errs.append(f"TEST-CASES.md: {t} has an unrecognised ticket reference {tk!r}")
    return errs


def lint_tickets(tickets: list[Ticket], known: set[str] | None) -> list[str]:
    errs = []
    seen: set[str] = set()
    for t in tickets:
        if t.id in seen:
            errs.append(f"TRACKER.md: ticket {t.id} appears more than once")
        seen.add(t.id)
        if t.status not in STATUSES:
            errs.append(f"TRACKER.md: {t.id} has unknown status {t.status!r}")
        hashes = HASH_RE.findall(t.commit)
        if t.status == "done" and not hashes:
            errs.append(f"TRACKER.md: {t.id} is done but has no commit hash")
        if known is not None:
            for h in hashes:
                if not hash_exists(h, known):
                    errs.append(f"TRACKER.md: {t.id} cites commit {h}, which is not in git history")
    return errs


def lint_milestones(tracker: str, tickets: list[Ticket]) -> list[str]:
    errs = []
    rows = table_rows(tracker, "Milestone status")
    by_ms: dict[str, list[Ticket]] = {}
    unassigned = []
    for t in tickets:
        if t.milestone is None:
            unassigned.append(t.id)
        else:
            by_ms.setdefault(t.milestone, []).append(t)
    if unassigned:
        errs.append("TRACKER.md: Notes of " + ", ".join(unassigned) + " do not start with a milestone (M1., M2., ...)")
    sum_tickets = sum_done = 0
    total_row = None
    for cells in rows:
        if len(cells) < 6:
            continue
        name, _scope, _target, n_tickets, n_done, status = cells[:6]
        if name.lower() == "total":
            total_row = cells
            continue
        try:
            nt, nd = int(n_tickets), int(n_done)
        except ValueError:
            errs.append(f"TRACKER.md: milestone {name} has non-numeric counts")
            continue
        sum_tickets += nt
        sum_done += nd
        group = by_ms.get(name, [])
        actual_t, actual_d = len(group), sum(1 for t in group if t.status == "done")
        if (nt, nd) != (actual_t, actual_d):
            errs.append(f"TRACKER.md: milestone {name} says {nd}/{nt} done, ticket rows say {actual_d}/{actual_t}")
        expected = "done" if actual_t and actual_d == actual_t else ("todo" if actual_d == 0 and not any(
            t.status in {"doing", "review"} for t in group) else "doing")
        if status != expected:
            errs.append(f"TRACKER.md: milestone {name} status is {status!r}, counts imply {expected!r}")
    if total_row is None:
        errs.append("TRACKER.md: milestone table has no Total row")
    else:
        try:
            tt, td = int(total_row[3]), int(total_row[4])
        except ValueError:
            errs.append("TRACKER.md: Total row has non-numeric counts")
        else:
            real_done = sum(1 for t in tickets if t.status == "done")
            if (tt, td) != (len(tickets), real_done):
                errs.append(f"TRACKER.md: Total says {td}/{tt}, ticket rows say {real_done}/{len(tickets)}")
            if (tt, td) != (sum_tickets, sum_done):
                errs.append(f"TRACKER.md: Total {td}/{tt} is not the sum of the milestone rows {sum_done}/{sum_tickets}")
    return errs


def parse_log_time(date_s: str, time_s: str, tz: str) -> datetime | None:
    if tz not in TZ_OFFSETS:
        return None
    h, m = TZ_OFFSETS[tz]
    try:
        naive = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone(timedelta(hours=h, minutes=m)))


def lint_log(log: str, ticket_ids: set[str], known: set[str] | None) -> list[str]:
    errs = []
    prev: datetime | None = None
    prev_no = 0
    for no, line in enumerate(log.splitlines(), start=1):
        if not line[:1].isdigit():
            continue
        m = LOG_LINE_RE.match(line)
        if not m:
            errs.append(f"LOG.md:{no}: line starts with a digit but is not 'YYYY-MM-DD HH:MM TZ | ...'")
            continue
        ts = parse_log_time(*m.groups())
        if ts is None:
            errs.append(f"LOG.md:{no}: unparseable or unknown time zone in {m.group(0)!r}")
            continue
        if prev is not None and ts < prev:
            errs.append(f"LOG.md:{no}: timestamp goes backwards (line {prev_no} is later)")
        if prev is None or ts >= prev:
            prev, prev_no = ts, no
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 2:
            for tk in re.split(r"[,\s/]+", cols[1]):
                if TICKET_ID_RE.match(tk) and tk not in ticket_ids:
                    errs.append(f"LOG.md:{no}: ticket {tk} is not in TRACKER.md")
        if known is not None and len(cols) >= 3 and re.fullmatch(r"[0-9a-f]{7,40}", cols[-1]):
            if not hash_exists(cols[-1], known):
                errs.append(f"LOG.md:{no}: commit {cols[-1]} is not in git history")
    return errs


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def lint(root: Path, use_git: bool = True) -> Report:
    pdir = root / "project"
    tracker = (pdir / "TRACKER.md").read_text(encoding="utf-8")
    tests_text = (pdir / "TEST-CASES.md").read_text(encoding="utf-8")
    log = (pdir / "LOG.md").read_text(encoding="utf-8")
    tickets = parse_tickets(tracker)
    ids = {t.id for t in tickets} | set(parse_human_ids(tracker))
    known = git_hashes(root) if use_git else None
    rep = Report()
    if use_git and known is None:
        rep.errors.append("git history unavailable: cannot verify commit hashes (use --no-git to skip)")
    rep.errors += lint_xref(tracker, tests_text, ids)
    rep.errors += lint_tickets(tickets, known)
    rep.errors += lint_milestones(tracker, tickets)
    rep.errors += lint_log(log, ids, known)
    defs, _ = parse_test_cases(tests_text)
    rep.counts = {
        "tickets": len(tickets),
        "done": sum(1 for t in tickets if t.status == "done"),
        "test_ids": len(defs),
        "log_lines": sum(1 for ln in log.splitlines() if LOG_LINE_RE.match(ln)),
    }
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None)
    ap.add_argument("--no-git", action="store_true", help="skip commit-hash verification")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    rep = lint(root, use_git=not args.no_git)
    for e in rep.errors:
        print(f"ERROR {e}")
    c = rep.counts
    print(f"tracker-lint: {'FAIL' if rep.errors else 'PASS'} ({len(rep.errors)} error(s); "
          f"{c['tickets']} tickets, {c['done']} done, {c['test_ids']} test IDs, {c['log_lines']} log lines)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
