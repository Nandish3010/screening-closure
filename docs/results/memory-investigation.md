# API memory growth (BUG-77): memray soak, 7 November 2026

Status: **investigated, not fixed.** BUG-77 stays open and is still mitigated by the memory-gated API restart (`docs/governance/operations.md`). This page records one profiling run and what it does and does not show. Nothing here claims the growth is explained or removed.

## What was run

| | |
|---|---|
| Date | 7 November 2026, 02:49 to 04:09 IST (soak started 2026-11-06 21:19 UTC) |
| Code tested | `bfdb548` (main at the time, merge of PR #58) |
| Harness | a throw-away Docker Compose project `scmemray`, commit `b4c46fa` on the local worktree `sc-memray` (`tools/load/memray/`). It was **not** merged and was removed from this branch after the run; this page is the only record of the method. Re-creating it needs that commit. |
| Stack | 3 API replicas behind a proxy, 2 schedulers, the watchdog, its own Postgres (port 15477). Local Docker on a Mac, not the GCP VM. |
| Profiler | `api-1` ran under `memray run --native` (the profiling image patched `api/Dockerfile`; that patch was discarded and is not in the repo). `api-2` and `api-3` ran unprofiled as controls. |
| Auth | `api-1` and `api-2` with OIDC tokens, `api-3` with dev login. |
| Load | open loop, 50 requests/s, 4,800 s (80 min), 240,000 requests, 80,000 per replica. Synthetic mix of reads, case creations and requests answered 403. Statuses: 201 x 60,312, 200 x 144,387, 403 x 35,301. |
| Data | seeded 50,000 cases / 527,070 events; the run created more and ended at 89,313 cases / 695,347 events. (The harness default is 100,000; this run started from 50,000.) |
| Measurement | `RssAnon` of each replica every 30 s from `/proc`; slopes are least-squares over the samples after a 900 s warm-up (two estimators: all samples after warm-up, and the last hour only). |

Raw outputs are in the scratchpad run directory of that session, not in git: `soak.json`, `soak.stdout`, and the memray reports (`api.bin.report.txt`, `api.bin.stats.txt`, `api.bin.summary.txt`, flamegraph and table HTML). The 5.6 GB capture lived in a Docker volume that was removed with the stack.

## Results

Anonymous memory growth, MiB per hour (all samples after warm-up / last hour):

| Replica | Slope | Anon MiB at end | Notes |
|---|---|---|---|
| api-1 | 12.8 / 8.4 | about 149 | memray `--native` attached |
| api-2 | 13.8 / 13.7 | about 110 | control |
| api-3 | 24.2 / 23.2 | about 120 | control, dev-auth: served 31,679 creations and no 403s, so a heavier write mix than the others |

memray capture of `api-1` (83 minutes, 772 million allocations):

- Peak tracked heap 117 MiB. RSS 42.8 MiB at the start of the capture, 189.1 MiB at the end. **Tracked Python heap at the end: 76.4 MiB.** So about 113 MiB of the final RSS is not live Python allocations that memray could see.
- Net growth of tracked live bytes from 25% of the run to the end: **+4.1 MiB over 1.04 h (about +4.0 MiB/h)**, against an RSS growth of roughly 8 to 13 MiB/h on the same replica.

Top sites among the 74.3 MiB still alive when the capture was closed (SIGTERM):

1. `psycopg` `fetchall`, from `_read_chunk` in `app/store_pg.py:406`, from `iter_open_case_models` and `run_automation_scan` (`app/automation.py:382`): 9.0 MiB, plus 7.0 MiB of JSON decoding under it, 3.0 MiB in `_event_from_row` (`store_pg.py:67`) and 2.0 MiB in `_read_chunk` (`store_pg.py:409`). These are the rows of an automation scan that was running at shutdown, so they are in-flight data, not retained state.
2. `app/automation.py:138` (`first`, called from `_outreach`): 2.0 MiB, same scan.
3. Python bytecode and module loading (`source_to_code`, `_compile_bytecode`): about 3.3 MiB + 2.2 MiB in the largest entries and many 1 MiB entries; one-off import cost.

Top sites whose live bytes grew from 25% of the run to the end (all small):

1. `psycopg` `fetchall` under `list_cases` (`store_pg.py:759`) from the OpenTelemetry observable gauge `_open_by_state` (`app/observability.py:144`): +2.0 MiB (+1.9 MiB/h).
2. `fetchall` / `_read_chunk` again under the automation scan: +1.0 MiB each (+1.0 MiB/h each).
3. `state_machine.replace` (`state_machine.py:112`) via `replay_model` from `agency/facts.py:225`: +1.0 MiB (+1.0 MiB/h).
4. psycopg prepared-statement bookkeeping (`_maybe_prepare_gen`, `_execute_send`): +0.3 MiB and +0.1 MiB (about 0.3 and 0.1 MiB/h).

By allocation volume (not retention), `state_machine.py:112` dominates (281 GB allocated over the run, 185 million allocations), then JSON decoding (about 85 GB) and psycopg query preparation (18.8 GB). That is churn, not growth.

## Conclusion

1. Under this load the three replicas' anonymous memory grew at 8 to 24 MiB/h, below the roughly 45 MiB/h reported on the VM.
2. memray sees only about +4 MiB/h of growing live Python allocations on the profiled replica, spread over small psycopg result buffers, the automation scan and the case-metrics gauge, with no single site dominating. That is well short of the RSS growth, and the survivors at shutdown are mostly an in-flight scan.
3. The gap between RSS and the tracked heap fits allocator retention or fragmentation (large transient result sets from the automation scan and metrics callbacks freed but not returned to the OS) more than a Python object leak. **This is a hypothesis; the run did not test it** (no `MALLOC_ARENA_MAX`, `malloc_trim` or jemalloc comparison was made).

## What this does not show

- It is local Docker on a Mac, not the GCP VM (e2-medium, different kernel, memory limit, CPU and libc behaviour). The VM figure was not reproduced.
- 80 minutes, not days. A slow leak (for example in a rarely used path) could be invisible, and a 900 s warm-up was discarded.
- The profiler has overhead on `api-1` (it ran with `--native`); its slope is not the unprofiled behaviour, and `api-1` is the only replica with allocation-level evidence. `api-2` and `api-3` carry no such evidence.
- The load is synthetic: request mix, a flat 50 requests/s, 50,000 to 89,313 cases. Real use, scheduler timing, hosted-demo auto-reset and the browser-driven paths were not exercised. `api-3` used dev login and a heavier write mix, and its steeper slope (about 24 MiB/h) is not explained.
- The slopes (8 to 24 MiB/h) are below the roughly 45 MiB/h seen on the VM, so either the VM growth depends on something not reproduced here, or it is smaller under this load. Both are open.
- Nothing was changed in the product code and nothing is claimed fixed. The memory-gated restart stays.

## Harness defect found

`tools/load/memray/run.sh` `mkenv` exits non-zero under `set -e` when the `API<n>_AUTH_MODE`, `_APP_ENV` and `_DEV_LOGIN` variables are unset, because the last `[ -n "${!v:-}" ] && echo` is false. The env file is written before that point, so re-running with the same `RUN_DIR` works. The fix is `|| true` or an `if`. The harness is not in this repository, so it was not fixed here.

## Cheapest next steps (not done)

- Repeat on the VM image with `MALLOC_ARENA_MAX=2` (or jemalloc) on one replica and compare against a control, to test the allocator hypothesis.
- Page the automation scan's `_read_chunk` and the `list_cases` call in the `_open_by_state` gauge, which are the two largest result-set holders.
- A multi-hour run on the VM itself, sampling `RssAnon` with the real traffic.
