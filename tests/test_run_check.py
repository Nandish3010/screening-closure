"""The `make check` aggregator: stage list, summaries, and that a failing stage fails the run."""

from __future__ import annotations

import run_check as rc


def test_stage_order_matches_the_brief():
    names = [s.name for s in rc.STAGES]
    assert names[:8] == ["test", "test-db", "web-test", "web-build", "evaluate-check",
                         "docs-check", "tracker-lint", "test-security"]
    assert "load-check" in names and "test-tools" in names
    assert names[-7:] == ["risk-check", "review-packs-check", "test-review-packs", "effort-check", "web-e2e",
                          "sensitivity-check", "calibrate-check"]
    assert [s.name for s in rc.STAGES if s.full] == ["sensitivity-check", "calibrate-check"]
    assert names.index("load-check") < names.index("risk-check")


def test_summarise_playwright():
    assert rc.summarise("  2 skipped\n  33 passed (12.5s)\n") == "playwright 33 passed"


def test_summarise_pytest_vitest_and_markers():
    assert "729 passed" in rc.summarise("...\n============ 729 passed, 34 deselected, 2 warnings in 37.98s ============\n")
    assert "40 passed" in rc.summarise(".....\n40 passed in 4.01s\n")
    assert "vitest 80 passed (80)" in rc.summarise(" Tests  80 passed (80)\n")
    assert rc.summarise("noise\ndocs-check: PASS (0 error(s))\n").endswith("docs-check: PASS (0 error(s))")
    assert rc.summarise("Success: built in 2.1s\n").startswith("Success")


def test_unknown_stage_is_rejected(capsys):
    assert rc.main(["--only", "nonsense"]) == 2


def test_failing_stage_fails_and_stops(monkeypatch, capsys):
    monkeypatch.delenv("CHECK_KEEP_GOING", raising=False)
    monkeypatch.setattr(rc, "DEFAULT_KEEP_GOING", False)
    calls = []

    def fake_run(cmd, env):
        calls.append(cmd[-1])
        return (1, "boom\n") if cmd[-1] == "test-db" else (0, "ok\n")

    monkeypatch.setattr(rc, "run", fake_run)
    monkeypatch.setattr(rc, "pg_reachable", lambda: True)
    assert rc.main(["--only", "test,test-db,web-build,docs-check"]) == 1
    assert calls == ["test", "test-db"]  # stopped at the first failure
    out = capsys.readouterr().out
    assert "NOT RUN" in out and "check: FAIL" in out

    calls.clear()
    assert rc.main(["--keep-going", "--only", "test,test-db,web-build"]) == 1
    assert calls == ["test", "test-db", "web-build"]


def test_unreachable_postgres_skips_loudly_but_is_not_a_pass(monkeypatch, capsys):
    monkeypatch.setattr(rc, "run", lambda cmd, env: (0, "ok\n"))
    monkeypatch.setattr(rc, "pg_reachable", lambda: False)
    assert rc.main(["--only", "test-db,load-check"]) == 0
    out = capsys.readouterr().out
    assert out.count("SKIPPED") >= 4 and "NOT a pass" in out and "PASS WITH SKIPS" in out
