"""The `make check` aggregator: tiers, the path rule, the parallel pool, summaries, and that a failing stage fails the run."""

from __future__ import annotations

import threading
import time

import run_check as rc

FAST = {"test", "verify", "test-db", "web-test", "web-build", "web-e2e", "docs-check", "tracker-lint", "test-security", "test-tools",
        "test-redteam", "load-check", "review-packs-check", "test-review-packs", "effort-check", "evaluate-smoke"}
HEAVY = {"sensitivity-check", "evaluate-check", "risk-check", "calibrate-check", "validate-check", "evaluate-capacity-check"}


def names(stages):
    return {s.name for s in stages}


def test_tiers_match_the_brief():
    assert names(rc.select_stages(False, set(), set())) == FAST
    full = names(rc.select_stages(True, set(), set()))
    assert full == (FAST - {"evaluate-smoke"}) | HEAVY  # the full evaluate-check replaces the smoke
    assert [s.name for s in rc.STAGES if s.needs_path] == ["calibrate-check"]


def test_dependencies_are_known_and_acyclic():
    for stage, deps in rc.DEPENDS.items():
        assert stage in rc.STAGE_BY_NAME and all(d in rc.STAGE_BY_NAME for d in deps)
    assert rc.DEPENDS["web-e2e"] == ("web-build",)
    assert "test-db" not in rc.DEPENDS  # per-worker databases from a template: no reset needed first


def test_path_rule():
    h = rc.heavy_decision
    assert h("fast", None, ["docs/governance/x.md", "web/src/a.tsx"])[0] is False
    for p in ["simulator/sim/engine.py", "simulator/params.yaml", "config/pathways/colorectal.yaml", "docs/results/results.json",
              "api/app/state_machine.py", "tools/calibration/fitted.json"]:
        assert h("fast", None, ["README.md", p])[0] is True, p
    assert h("fast", "1", [])[0] is True and h("fast", "0", ["simulator/x.py"])[0] is False
    assert h("full", "0", [])[0] is True
    assert h("fast", None, None)[0] is False  # no git diff available: off, loudly
    assert "simulator/sim/engine.py" in h("fast", None, ["simulator/sim/engine.py"])[1]


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


def _fake_run(calls, fail=()):
    def fake_run(cmd, env, stream=True):
        calls.append(cmd[-1])
        return (1, "boom\n") if cmd[-1] in fail else (0, "ok\n")
    return fake_run


def test_failing_stage_stops_new_stages_serial(monkeypatch, capsys):
    monkeypatch.setattr(rc, "pg_reachable", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(rc, "run", _fake_run(calls, {"test-db"}))
    assert rc.main(["--serial", "--only", "test,test-db,docs-check"]) == 1
    assert calls == ["test", "test-db"]
    assert "NOT RUN" in capsys.readouterr().out
    calls.clear()
    assert rc.main(["--serial", "--keep-going", "--only", "test,test-db,docs-check"]) == 1
    assert calls == ["test", "test-db", "docs-check"]


def test_parallel_failure_and_dependencies(monkeypatch, capsys):
    monkeypatch.setattr(rc, "pg_reachable", lambda: True)
    monkeypatch.setattr(rc, "ensure_web_install", lambda stages, env: None)
    calls: list[str] = []
    monkeypatch.setattr(rc, "run", _fake_run(calls, {"web-build"}))
    # web-e2e depends on web-build, which fails: e2e is not run even with --keep-going; docs-check still is
    assert rc.main(["--keep-going", "--jobs", "3", "--only", "web-build,web-e2e,docs-check"]) == 1
    assert sorted(calls) == ["docs-check", "web-build"]
    out = capsys.readouterr().out
    assert "dependency web-build did not pass" in out and "check: FAIL" in out


def test_parallel_pool_overlaps_stages_and_respects_order():
    active, peak, order = 0, 0, []
    lock = threading.Lock()

    def fake(stage, env, *, stream, prebuilt_web):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            order.append(("start", stage.name))
        time.sleep(0.15)
        with lock:
            active -= 1
            order.append(("end", stage.name))
        return rc.Result(stage.name, "PASS", 0.15, "ok", "out\n")

    stages = [rc.STAGE_BY_NAME[n] for n in ("web-build", "web-e2e", "docs-check", "tracker-lint", "verify")]
    res = rc.run_parallel(stages, {}, False, 4, executor_fn=fake)
    assert [r.status for r in res] == ["PASS"] * 5 and peak >= 3
    assert order.index(("end", "web-build")) < order.index(("start", "web-e2e"))


def test_serial_flag_runs_one_at_a_time(monkeypatch):
    monkeypatch.setattr(rc, "pg_reachable", lambda: True)
    active, peak = 0, 0

    def fake_run(cmd, env, stream=True):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.02)
        active -= 1
        return 0, "ok\n"

    monkeypatch.setattr(rc, "run", fake_run)
    assert rc.main(["--serial", "--only", "docs-check,tracker-lint,verify"]) == 0
    assert peak == 1


def test_unreachable_postgres_skips_loudly_but_is_not_a_pass(monkeypatch, capsys):
    monkeypatch.setattr(rc, "run", lambda cmd, env, stream=True: (0, "ok\n"))
    monkeypatch.setattr(rc, "pg_reachable", lambda: False)
    assert rc.main(["--only", "test-db,load-check"]) == 0
    out = capsys.readouterr().out
    assert out.count("SKIPPED") >= 4 and "NOT a pass" in out and "PASS WITH SKIPS" in out
