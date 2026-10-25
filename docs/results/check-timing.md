# Check timing (F15, M6)

How long the gates take, before and after the speed-up work (ticket F15). All local numbers are from one 10-core Apple-silicon Mac,
`make check CHECK_KEEP_GOING=1`-style runs, one run each, wall seconds per stage. Reproduce with `make check` and `make check-full`.

## Before (serial `make check`, main at 102ee3b, 2026-10-24)

One stage at a time, in list order. Total **14 min 44 s** (884 s wall; 18 stages, `calibrate-check` skipped because the gitignored
downloads are absent). `test-db` failed in this run for a reason the speed-up removes: the shared `closure_test` database had been
migrated by two other branches in the meantime (`schema_migrations` held versions 015 and 016 this checkout has no file for), so
`test_migration_runner_is_idempotent` failed; its time (50 s) is of the whole stage.

| Stage | Seconds | Result |
|---|---:|---|
| test | 240.8 | 2496 passed |
| verify | 30.6 | 9057 abstract states |
| test-db | 50.1 | 1 failed (shared-database contamination, see above), 223 passed |
| web-test | 4.2 | 334 passed |
| web-build | 0.6 | |
| evaluate-check | 126.9 | |
| docs-check | 0.2 | |
| tracker-lint | 0.1 | |
| test-security | 15.8 | |
| test-tools | 16.9 | |
| test-redteam | 17.5 | |
| load-check | 3.0 | |
| risk-check | 15.2 | |
| review-packs-check | 0.3 | |
| test-review-packs | 1.6 | |
| effort-check | 5.5 | |
| web-e2e | 23.1 | 51 passed |
| sensitivity-check | 331.6 | |
| calibrate-check | skipped | |
| **Total, wall** | **884** | `time`: 14:44.11, 3081 s of user CPU |

`validate-check` (about 2.5 minutes, B20) and `evaluate-capacity-check` (not in `make check` before) arrived with week 2 and were
not part of this baseline.

## What changed

* **Parallel pytest.** `make test` and `make test-db` run `pytest -n auto --dist loadfile` (pytest-xdist, in the api dev extras). A file
  stays on one worker. The one test module that built test ids from the clock (`test_rbac.py::test_bad_tokens_are_401`) now has fixed
  ids, which xdist needs. Nothing else shared global state: the suite passed under xdist unchanged, and writes go to `tmp_path` or to
  scratch databases.
* **A database per worker.** `api/tests/xdist_db.py`: each worker copies the migrated template `closure_test_tpl` to its own
  `closure_test_gw<N>` (`CREATE DATABASE ... TEMPLATE`, under an advisory lock; the template is rebuilt when its migrations differ from the
  files). The `_test` guard (ADR-015) is kept: `testdb.WORKER_DB_RE` accepts only `_test_tpl` and `_test_gw<digits>` after a `_test` name.
  Serial runs (`XDIST=`, `make test-chaos`, `make test-oidc`, `make test-demo`) use `closure_test` as before; chaos and live tests stay
  serial. The simulator tests need no database and run under xdist as well.
* **Parallel stages.** `tools/check/run_check.py` runs stages in a worker pool (`min(cores, 6)`, `CHECK_JOBS` to change), longest first;
  the only ordering is `web-build` before `web-e2e` (both use `web/dist`). Each stage's output is printed as one block when it ends.
  `CHECK_SERIAL=1` restores the old one-at-a-time mode. `web-e2e` gets its own free preview port and serves the build `web-build` made.
  No `test-db-reset` step is needed first: the tests no longer use `closure_test` itself when run in parallel.
* **Tiers.** `make check` is the fast tier; `make check-full` adds the simulator-heavy checks. The path rule is in the header of
  `tools/check/run_check.py` and in the Makefile: if the diff against main (merge base with `origin/main`, plus uncommitted and untracked
  files) touches `simulator/`, `config/pathways/`, `tools/calibration/`, `docs/results/`, `api/app/risk/` or the engine modules the
  simulator imports, `make check` runs the heavy stages too. `CHECK_HEAVY=1` or `0` overrides.
* **CI.** `check.yml` is split into `unit-api`, `unit-sim`, `verify`, `tools-and-docs`, `redteam`, `db` and `web` (pip, npm and Playwright
  browser caches; HAPI starts while the Python environment builds), plus `simulator-heavy` (one matrix leg per check), which runs on a
  pull request or push only when the `changes` job (dorny/paths-filter, same path list) reports a simulator-related path, and always
  nightly and on manual runs. PR runs are cancelled when superseded.

## After

| Measurement | Before | After |
|---|---:|---:|
| Local fast tier, `make check` (16 stages, 6 at a time) | 14 min 44 s (serial, all 18 stages) | **2 min 25 s** (145.2 s) |
| Local full tier, `make check-full` (21 stages; `calibrate-check` skipped) | no equivalent (about 17 min serial with validate-check) | **10 min 56 s** (655.6 s) |
| CI, pull request without simulator changes | not measured (serial `unit` job alone about 12 minutes) | CI_PR |
| CI, pull request with simulator changes | | CI_PR_HEAVY |

The target was a local fast check under 6 minutes and a CI pull-request wall time under 10 minutes.

Per stage, local fast tier (wall seconds, stages overlap; total 672 s of stage time in 145 s):

| Stage | Before | After |
|---|---:|---:|
| test | 240.8 | 141.5 |
| verify | 30.6 | 145.2 (17,954 states now, with invariant I8; was 9,057) |
| test-db | 50.1 | 79.8 (231 passed; more tests than before, and sharing the machine with the other stages) |
| web-test | 4.2 | 9.2 |
| web-build | 0.6 | 1.1 |
| evaluate-smoke (1 replication) | evaluate-check 126.9 | 37.6 |
| docs-check, tracker-lint | 0.3 | 0.6 |
| test-security | 15.8 | 58.7 |
| test-tools | 16.9 | 57.9 |
| test-redteam | 17.5 | 69.6 |
| load-check | 3.0 | 7.9 |
| review-packs-check, test-review-packs | 1.9 | 5.4 |
| effort-check | 5.5 | 24.2 |
| web-e2e | 23.1 | 33.4 |

Single stages get slower when the machine is shared by up to six of them (and `test` by ten workers), which is why the sum of stage
times is 672 s against 884 s before, while the wall time is 145 s. The longest single stage, `verify` (three slices run one after the
other in one process), now sets the floor of the fast tier; running the slices in parallel would be the next step.

Full tier, per stage (wall seconds, all overlapped): test 319.6, verify 346.5, test-db 126.2, test-security 55.1, test-tools 59.1,
test-redteam 70.0, web-e2e 46.0, sensitivity-check 631.2, evaluate-check 610.9, validate-check 646.9, evaluate-capacity-check 655.6,
risk-check 67.5. Four stages that each use all cores for several minutes share the machine, so each takes about as long as the whole run.
