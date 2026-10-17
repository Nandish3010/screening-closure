"""T-F16: cross-reference lint between TRACKER.md, TEST-CASES.md and LOG.md."""

from __future__ import annotations

import tracker_lint as tl

from conftest import REPO_ROOT

HEADER = """# Tracker

## Milestone status

| Milestone | Scope | Target | Tickets | Done | Status |
|---|---|---|---|---|---|
| M0 | Scaffold | now | 1 | 1 | done |
| M1 | Core | later | 2 | 0 | todo |
| Total | | | 3 | 1 | |

## Tickets

| ID | Cut | Ticket | Depends on | Status | Test cases | Commit | Notes |
|---|---|---|---|---|---|---|---|
| M0-1 | M0 | Scaffold | - | done | T-A1 | {h0} | done |
| A1 | A | Thing | M0-1 | todo | T-A2 | - | M1. note |
| F1 | F | Check | M0-1 | todo | T-F1 | - | M1. note |

## Human-owner tasks

| ID | Task | Depends on | Status | Test cases | When |
|---|---|---|---|---|---|
| H1 | Review | A1 | todo | T-E1 | soon |
"""
TESTS = """# Test cases

| ID | What | Type | How | Pass | Status | Ticket |
|---|---|---|---|---|---|---|
| T-A1 | x | unit | make test | ok | existing | M0-1 |
| T-A2 | x | unit | make test | ok | planned | A1 |
| T-F1 | x | unit | make check | ok | planned | F1 |
| T-E1 | x | manual | human | ok | planned | H1 |
"""
LOG = """# Log

2026-10-18 00:03 IST | M0-1 | a | did it | {h0}
2026-10-18 00:12 IST | A1 | a | did more | -
"""


def build(tmp_path, tracker=HEADER, tests=TESTS, log=LOG, h0="abc1234"):
    (tmp_path / "project").mkdir(exist_ok=True)
    (tmp_path / "project/TRACKER.md").write_text(tracker.format(h0=h0))
    (tmp_path / "project/TEST-CASES.md").write_text(tests)
    (tmp_path / "project/LOG.md").write_text(log.format(h0=h0))
    return tmp_path


def lint(tmp_path, **kw):
    return tl.lint(build(tmp_path, **kw), use_git=False).errors


def test_good_fixture_passes(tmp_path):
    assert lint(tmp_path) == []


def test_tracker_references_unknown_test_id(tmp_path):
    errs = lint(tmp_path, tracker=HEADER.replace("| T-A2 |", "| T-A2, T-Z9 |"))
    assert errs == ["TRACKER.md references T-Z9, which TEST-CASES.md does not define"]


def test_test_case_never_referenced_by_tracker(tmp_path):
    errs = lint(tmp_path, tests=TESTS + "| T-A3 | x | unit | make test | ok | planned | A1 |\n")
    assert errs == ["TEST-CASES.md defines T-A3, which TRACKER.md never references"]


def test_test_case_names_unknown_ticket(tmp_path):
    errs = lint(tmp_path, tests=TESTS.replace("| A1 |", "| A1, A99 |"))
    assert errs == ["TEST-CASES.md: T-A2 names ticket A99, which TRACKER.md does not define"]


def test_duplicate_test_id(tmp_path):
    errs = lint(tmp_path, tests=TESTS + "| T-A1 | again | unit | m | ok | planned | A1 |\n")
    assert any("T-A1 defined more than once" in e for e in errs)


def test_done_row_needs_a_commit(tmp_path):
    errs = lint(tmp_path, tracker=HEADER.replace("{h0}", "-"))
    assert errs == ["TRACKER.md: M0-1 is done but has no commit hash"]


def test_unknown_status_and_duplicate_ticket(tmp_path):
    t = HEADER.replace("| todo | T-A2", "| wip | T-A2") + ""
    errs = lint(tmp_path, tracker=t)
    assert any("unknown status 'wip'" in e for e in errs)
    t2 = HEADER.replace("| F1 | F | Check", "| A1 | F | Check")
    assert any("A1 appears more than once" in e for e in lint(tmp_path, tracker=t2))


def test_commit_hash_must_exist_in_git(repo):
    sha = repo.commit("first")
    build(repo.root, h0=sha)
    assert tl.lint(repo.root).errors == []
    build(repo.root, h0="deadbee")
    errs = tl.lint(repo.root).errors
    assert any("cites commit deadbee" in e for e in errs) and any("LOG.md:3: commit deadbee" in e for e in errs)


def test_milestone_counts_must_match_rows(tmp_path):
    errs = lint(tmp_path, tracker=HEADER.replace("| M1 | Core | later | 2 | 0 | todo |", "| M1 | Core | later | 2 | 1 | doing |"))
    assert any("milestone M1 says 1/2 done, ticket rows say 0/2" in e for e in errs)
    assert any("Total" in e for e in errs)


def test_milestone_status_follows_counts(tmp_path):
    errs = lint(tmp_path, tracker=HEADER.replace("| M0 | Scaffold | now | 1 | 1 | done |", "| M0 | Scaffold | now | 1 | 1 | doing |"))
    assert errs == ["TRACKER.md: milestone M0 status is 'doing', counts imply 'done'"]


def test_ticket_without_milestone_in_notes(tmp_path):
    errs = lint(tmp_path, tracker=HEADER.replace("M1. note", "no milestone", 1))
    assert any("do not start with a milestone" in e for e in errs)


def test_log_must_be_chronological(tmp_path):
    errs = lint(tmp_path, log=LOG + "2026-10-18 00:05 IST | A1 | a | out of order | -\n")
    assert errs == ["LOG.md:5: timestamp goes backwards (line 4 is later)"]


def test_log_time_zones_are_normalised(tmp_path):
    ok = LOG + "2026-10-17 19:00 UTC | A1 | a | 00:30 IST next day, so later | -\n"
    assert lint(tmp_path, log=ok) == []
    bad = LOG + "2026-10-17 18:00 UTC | A1 | a | 23:30 IST on the 17th, earlier | -\n"
    assert any("goes backwards" in e for e in lint(tmp_path, log=bad))


def test_log_unknown_ticket_and_bad_line(tmp_path):
    errs = lint(tmp_path, log=LOG + "2026-10-18 01:00 IST | B7 | a | x | -\n2026-10-18 25:99 IST | A1 | a | x | -\n2026-10-18 | A1 | no time\n")
    assert any("ticket B7 is not in TRACKER.md" in e for e in errs)
    assert any("unparseable" in e for e in errs)
    assert any("not 'YYYY-MM-DD HH:MM TZ" in e for e in errs)


def test_real_tracker_is_consistent():
    rep = tl.lint(REPO_ROOT)
    assert rep.errors == [], "\n".join(rep.errors)
    assert rep.counts["tickets"] >= 72 and rep.counts["test_ids"] >= 112
