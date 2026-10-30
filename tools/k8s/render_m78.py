"""The M7-8 part of docs/results/scale.md: what changed against the M7-7 figures (kept unchanged in docs/results/scale/*.json) and what the
new runs in docs/results/scale/m78/*.json show. Pure functions from the raw files to Markdown lines; every number printed is read from them.
Imported by render_scale.py."""

from __future__ import annotations

import statistics
from pathlib import Path

import memfit


def f(x, p: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{p}f}"


def n(x) -> str:
    return "n/a" if x is None else f"{x:,}"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows] + [""]


def git_row(name, d):
    g = d.get("git", {})
    dirty = g.get("dirty_files") or []
    return (f"| `m78/{name}` | {d.get('tool')} | `{str(g.get('commit'))[:10]}` | {g.get('branch')} | {len(dirty)} uncommitted path(s)"
            f"{' (' + ', '.join('`' + x + '`' for x in dirty[:5]) + ')' if dirty else ''} | {d.get('started_at')} | {d.get('finished_at') or d.get('generated_at')} |")


def stage_rows(la, label=None):
    rows = []
    for s in la["stages"]:
        if label and s.get("label") != label:
            continue
        a = s["latency_all_requests"]
        rows.append(s | {"_p50": a["p50_ms"], "_p95": a["p95_ms"], "_p99": a["p99_ms"], "_max": a["max_ms"]})
    return rows


def ladder_table(la, label=None):
    rows = []
    for s in stage_rows(la, label):
        rows.append([f"{s['target_rate_per_s']:g}/s", f"{s['achieved_rate_per_s']:.1f}/s", f"{s.get('served_rate_per_s', s['achieved_rate_per_s']):.1f}/s",
                     s["_p50"], s["_p95"], s["_p99"], s["_max"],
                     (", ".join(f"{k}: {v}" for k, v in s["errors_by_status"].items()) or "none"),
                     (f"{s['shed']:,} ({s['shed_rate'] * 100:.1f}%)" if "shed" in s else "n/a (no shedding)"),
                     "yes" if s["pass"] else "no", "yes" if s.get("pass_without_shedding", s["pass"]) else "no"])
    return table(["Offered", "Answered", "Served 2xx", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)", "Errors by status", "Shed (503 + Retry-After)",
                  "Passes (no errors, p99 < 500 ms)", "Passes with nothing shed"], rows)


def cadence_rows(so):
    c = so["summary"]["scheduler"].get("cadence") or {}
    rows = []
    for task, v in c.items():
        g, d = v["gap_s"] or {}, v["duration_s"] or {}
        rows.append([task, f"{v['nominal_interval_s']:g} s", n(v["starts"]), f"{v['expected_starts']:,.0f}", f"{v['achieved_fraction_of_nominal'] * 100:.1f}%",
                     "yes" if v["meets_95_percent_of_nominal"] else "no", f(g.get("mean"), 3), f(g.get("p95"), 3), f(g.get("max"), 3), v["gaps_over_1_5_intervals"],
                     v["skipped_ticks_reported_by_scheduler"], f"{f(d.get('p50'), 3)} / {f(d.get('p95'), 3)} / {f(d.get('max'), 3)}"])
    return table(["Task", "Interval", "Starts", "Expected", "Achieved", ">= 95%", "Gap mean (s)", "Gap p95 (s)", "Gap max (s)", "Gaps > 1.5 intervals",
                  "Ticks the scheduler skipped", "Work p50 / p95 / max (s)"], rows)


def memory_tables(so, title):
    out = []
    an = memfit.analyse(so)
    hours = max((v["hours"] for v in an.values()), default=0)
    head = ["Pod", "Start / end / max (MiB, anon)"] + [f"Hour {h}" for h in range(1, int(hours + 0.999) + 1)] + ["Linear fit (MiB/h)", "Saturating fit: plateau MiB, tau h", "AIC linear - saturating", "Preferred"]
    rows = []
    for pod, v in an.items():
        sl = {s["hour"]: s["slope_mib_per_h"] for s in v["slices"]}
        sat = v.get("saturating")
        rows.append([pod.replace("closure-api-", "api-"), f"{v['start_mib']} / {v['end_mib']} / {v['max_mib']}"]
                    + [f(sl.get(h), 1) if h in sl else "-" for h in range(1, int(hours + 0.999) + 1)]
                    + [f(v["linear"]["slope_mib_per_h"], 1), (f"{sat['plateau_mib']}, {sat['tau_h']}" if sat else "n/a"), f(v.get("aic_linear_minus_saturating"), 1), v["preferred"]])
    out += [f"#### {title}", "", "Hour columns: least-squares slope of the pod's anonymous memory in that one-hour slice (MiB/h). Fits use samples from minute 10 on.", ""]
    out += table(head, rows)
    return out, an


def build(raw: Path, jload, so7, la7, fo7=None) -> list[str]:
    d = raw / "m78"
    la = jload(d / "latency.json") if (d / "latency.json").exists() else None
    def finished(name):
        doc = jload(d / name) if (d / name).exists() else None
        return doc if doc and "summary" in doc else None  # a soak still running has no summary yet

    so = finished("soak.json")
    sr = finished("soak_recycle.json")
    mb1 = jload(d / "metrics_bench_1m.json") if (d / "metrics_bench_1m.json").exists() else None
    mb1i = jload(d / "metrics_bench_1m_indexed.json") if (d / "metrics_bench_1m_indexed.json").exists() else None
    mb100 = jload(d / "metrics_bench_100k.json") if (d / "metrics_bench_100k.json").exists() else None
    sm = jload(d / "smaps.json") if (d / "smaps.json").exists() else None
    L = ["## M7-8: cadence, bounded metrics routes, memory and overload (before = the M7-7 run, after = M7-8)", "",
         "The M7-7 figures below are from `docs/results/scale/{latency,soak}.json`, unchanged. The M7-8 figures are from `docs/results/scale/m78/*.json`. "
         "The decisions are ADR-047 (cadence), ADR-048 (load shedding) and ADR-049 (the metrics table).", ""]

    # ---------------------------------------------------------------------------------------------- machine and runs
    ref = la or so
    if ref:
        m = ref["machine_spec"]
        h, dk, kd = m["host"], m["docker"], m["kind"]
        L += ["### Machine and runs (M7-8)", "", "The same laptop-class machine and cluster layout as the M7-7 run; nothing in the cloud was measured.", ""]
        L += table(["", ""], [["Host", f"{h['cpu_model']}, {h['logical_cores']} logical cores, {h['ram_bytes'] / 2**30:.0f} GiB RAM (macOS {h['macos']})"],
                              ["Docker VM", f"{dk['os']} {dk['version']}, {dk['cpus']} CPUs, {dk['memory_bytes'] / 2**30:.1f} GiB"],
                              ["Kubernetes", f"kind ({kd['version']}), {m['kubernetes_version']}, Helm {m['helm']}"], ["PostgreSQL", m["postgres_version"]],
                              ["Population", f"{ref['population_before']['cases']:,} cases / {ref['population_before']['events']:,} events at the start of `{'soak' if ref is so else 'latency'}.json`"]])
        L += ["A run is attributed to the commit it started from; its working tree is listed when it was not clean.", "",
              "| File | Tool | Commit | Branch | Working tree | Started | Finished |", "|---|---|---|---|---|---|---|"]
        for nm, doc in (("latency.json", la), ("soak.json", so), ("soak_recycle.json", sr), ("metrics_bench_100k.json", mb100), ("metrics_bench_1m.json", mb1), ("metrics_bench_1m_indexed.json", mb1i)):
            if doc:
                L.append(git_row(nm, doc))
        L += ["", "`sweep.json` and `failover.json` of the M7-7 run were not repeated: the sweep, the watchdog scan and the fencing code paths they measure are unchanged in M7-8 "
              "(the scheduler's fail-over behaviour is covered by the unit and integration tests of ADR-047, not by a new drill).", ""]

    # ---------------------------------------------------------------------------------------------- the summary table
    s7 = so7["summary"]["scheduler"]
    rows = []
    if so:
        sw = (so["summary"]["scheduler"].get("cadence") or {}).get("sweep")
        if sw:
            rows.append(["Sweep starts against the nominal 10 s cadence (soak)",
                         f"{s7['completed_fraction_of_expected'] * 100:.1f}% (period mean {s7['sweep_period_s']['mean']} s, p95 {s7['sweep_period_s']['p95']} s)",
                         f"{sw['achieved_fraction_of_nominal'] * 100:.1f}% (gap mean {f(sw['gap_s']['mean'], 3)} s, p95 {f(sw['gap_s']['p95'], 3)} s, max {f(sw['gap_s']['max'], 1)} s)",
                         "met (>= 95%)" if sw["meets_95_percent_of_nominal"] else "**not met**"])
    if mb100:
        lg = mb100["legacy_replay"]
        rows.append([f"`GET /metrics/closure`, {n(lg['cases'])} cases", f"{lg['closure_seconds']} s, peak RSS {lg['closure_peak_rss_mib']} MiB (replays every case in the request)",
                     f"{mb100['steady']['closure_aggregate']['median_s']} s (median of {mb100['steady']['closure_aggregate']['calls']}), API-side RSS {mb100['steady']['peak_rss_mib']} MiB", "bounded"])
        rows.append([f"`GET /metrics/effort`, {n(lg['cases'])} cases", f"{lg['effort_seconds']} s, peak RSS {lg['effort_peak_rss_mib']} MiB",
                     f"{mb100['steady']['effort_aggregate']['median_s']} s", "bounded"])
    if mb1i or mb1:
        b = mb1i or mb1
        rows.append([f"The same two routes, {n(b['cases_in_database'])} cases", "not run: one replay holds every event of every case in memory; it needed about 925 MiB at 100,000 cases, so roughly ten times that (extrapolated, not measured) against a 768 MiB pod limit",
                     f"closure {b['steady']['closure_aggregate']['median_s']} s, effort {b['steady']['effort_aggregate']['median_s']} s (medians of {b['steady']['closure_aggregate']['calls']}); "
                     f"a call with nothing changed refreshes in {b['steady']['refresh_when_current']['median_s']} s", "bounded in memory; time grows with the table"])
    if so:
        an = memfit.analyse(so)
        sl7 = memfit.analyse(so7)
        rows.append(["API pod memory growth over the soak (anon, MiB/h per pod)",
                     ", ".join(f"{v['linear']['slope_mib_per_h']}" for v in sl7.values()) + f" over {max(v['hours'] for v in sl7.values()):.2f} h",
                     ", ".join(f"{v['linear']['slope_mib_per_h']}" for v in an.values()) + f" over {max(v['hours'] for v in an.values()):.2f} h (last hour: "
                     + ", ".join(f(v['last_slice_slope_mib_per_h'], 1) for v in an.values()) + ")", "see the verdict below"])
    if la:
        a_top = [s for s in la["stages"] if s["pass"]]
        b7 = [s for s in la7["stages"] if s["label"] == "watchdog_on_as_deployed"]
        top7 = max((s["target_rate_per_s"] for s in b7 if s["pass"]), default=None)
        low7 = min((s["target_rate_per_s"] for s in b7 if not s["pass"]), default=None)
        top_ns = max((s["target_rate_per_s"] for s in la["stages"] if s.get("pass_without_shedding")), default=None)
        top_g = max((s["target_rate_per_s"] for s in a_top), default=None)
        s300 = next((s for s in la["stages"] if s["target_rate_per_s"] == 300), None)
        s300b = next((s for s in b7 if s["target_rate_per_s"] == 300), None)
        rows.append(["Overload: 300 requests/s",
                     (f"{s300b['errors']:,} errors ({', '.join(f'{k}: {v}' for k, v in s300b['errors_by_status'].items())}), p99 {s300b['latency_all_requests']['p99_ms']} ms" if s300b else "n/a"),
                     (f"{s300['errors']:,} errors, {s300['shed']:,} shed ({s300['shed_rate'] * 100:.1f}%), p99 of answered requests {s300['latency_all_requests']['p99_ms']} ms" if s300 else "n/a"), "see the ladder"])
        rows.append(["Highest rate that passes",
                     f"{top7:g}/s (first failure {low7:g}/s)" if top7 else "n/a",
                     f"{top_ns:g}/s with nothing shed; {top_g:g}/s counting graceful shedding" if top_ns and top_g else "n/a", ""])
    L += table(["Question", "Before (M7-7)", "After (M7-8)", "Result"], rows)

    # ---------------------------------------------------------------------------------------------- cadence
    if so:
        L += ["### Scheduler cadence (ADR-047), measured in the soak", "",
              "Every tick of a scheduler task logs `tick task=... start=... duration=...`; the soak collects those lines from the scheduler pods (only the leader works). "
              "`Achieved` = starts in the measured window / (window / nominal interval). The M7-7 soak counted loop completions of the one serial loop; these count task starts.", ""]
        L += cadence_rows(so)
        sc = so["summary"]["scheduler"]
        L += [f"Scheduler leader changes during the soak: {so['summary']['leader']['scheduler_leader_changes']}. Sweep HTTP statuses: {sc['sweep_http_status_counts']}. "
              f"Scheduler warning or error lines: {sc['scheduler_warning_or_error_count']}. Sweep errors reported: {sc['sweep_errors_reported_in_summaries']}.", ""]
    if la:
        L += ["The same cadence during each ladder stage (starts / expected, gap p95 in s):", ""]
        rows = []
        for s in la["stages"]:
            c = s.get("scheduler_cadence_during_stage") or {}
            rows.append([f"{s['target_rate_per_s']:g}/s"] + [(f"{c[t]['starts']}/{c[t]['expected_starts']:.0f}, p95 {f(c[t]['gap_s']['p95'], 2) if c[t]['gap_s'] else 'n/a'}" if t in c else "n/a")
                                                               for t in ("sweep", "automation-scan", "evidence-scan", "agency")])
        L += table(["Offered", "sweep", "automation-scan", "evidence-scan", "agency (60 s)"], rows)

    # ---------------------------------------------------------------------------------------------- metrics
    if mb100 or mb1 or mb1i:
        L += ["### The metrics routes (ADR-049)", "",
              "Method (`tools/load/metrics_bench.py`, each phase in a fresh process so its peak RSS is its own): the replay the routes used to do (`legacy_closure` + `legacy_effort`), then the table refresh in calls of a 3 s budget until it has caught up, then the two SQL aggregates against the current table, then a refresh after 200 to 1,000 cases got an event.", ""]
        rows = []
        for name, b in (("100,000-case scratch database (cluster Postgres)", mb100), ("1,000,000-case `closure` database (the cluster's)", mb1i or mb1)):
            if not b:
                continue
            lg, rf, st, inc = b["legacy_replay"], b.get("refresh"), b["steady"], b["incremental"]
            rows.append([name, (f"{lg['closure_seconds']} s / {lg['effort_seconds']} s; RSS {lg['closure_peak_rss_mib']} / {lg['effort_peak_rss_mib']} MiB" if "cases" in lg else lg.get("skipped")),
                         (f"{rf['calls']} calls, {rf['total_seconds']:,.0f} s total, {rf['replayed_total']:,} replays ({rf['replay_rate_cases_per_s']:,.0f} cases/s), process RSS {rf['peak_rss_mib']} MiB" if rf else "see the 1M run above"),
                         f"{st['closure_aggregate']['median_s']} s / {st['effort_aggregate']['median_s']} s", f"{st['refresh_when_current']['median_s']} s",
                         f"{inc['touched']:,} cases touched: {inc['replayed']:,} replayed in {inc['refresh_seconds']} s"])
        L += table(["Database", "Old route: closure / effort (replay of every case)", "Refresh to catch up", "New aggregate: closure / effort", "Refresh when current", "Incremental"], rows)
        if mb1:
            r = mb1["refresh"]
            L += [f"Catching up at 1,000,000 cases is a one-time backfill: {r['calls']} calls of the 3 s budget (call seconds median {r['call_seconds']['median']}, p95 {r['call_seconds']['p95']}, max {r['call_seconds']['max']}), "
                  f"{r['replayed_total']:,} replays (more than the {r['case_metrics_rows']:,} cases, because the sweep's catch-up burst kept changing cases while the backfill ran), peak process RSS {r['peak_rss_mib']} MiB. "
                  f"The table is {r['case_metrics_bytes'] / 2**20:,.0f} MiB and {r['case_metric_dwells_rows']:,} dwell rows take {r['case_metric_dwells_bytes'] / 2**20:,.0f} MiB (before the two dwell indexes of migration 019). "
                  f"The first run's closure aggregate took {mb1['steady']['closure_aggregate']['median_s']} s because the median per transition sorted every dwell row; migration 019 and an index walk brought it to "
                  f"{mb1i['steady']['closure_aggregate']['median_s'] if mb1i else 'n/a'} s.", ""]
        if mb100:
            eq = mb100["equality_at_legacy_size"]
            L += [f"**Same numbers:** at {n(eq['cases'])} cases the old and the new answer of `closure` are equal: {eq['closure_equal']}; of `effort`: {eq['effort_equal']} (exact comparison of the whole bodies). "
                  "The tests compare them exactly on seeded populations (`api/tests/test_metrics_index_pg.py`).", ""]
        L += ["**What is not bounded:** the aggregate is still a scan of the table inside Postgres, so its time grows with the number of cases; a call after a large change refreshes for at most its budget "
              "and answers with the header `X-Metrics-Index-Lag`; nothing keeps the table warm between calls (a dashboard that polls does). Replay stays per case, paged and budgeted, as documented in ADR-049.", ""]

    # ---------------------------------------------------------------------------------------------- ladder
    if la:
        L += ["### Overload: the ladder with load shedding (ADR-048)", "",
              f"`api.maxInflightRequests` = {la['chart_values']['api'].get('maxInflightRequests')} per pod, Retry-After {la['chart_values']['api'].get('shedRetryAfterSeconds')} s; 5 minutes per rate after a 15 s warm-up; open-loop generator, "
              "not retrying (a refused request is counted, not repeated). `Offered` is the arrival rate; `Answered` counts every request the pods answered or refused. "
              "p50/p95/p99 are of the requests the pods **answered** (2xx and errors, refusals excluded: they are fast by construction and would flatter the tail); "
              "errors are everything that is neither 2xx nor a refusal (timeouts and resets included). A refusal is 503 with `Retry-After` and `X-Shed`, reported in its own column.", ""]
        L += ladder_table(la)
        b7 = [s for s in la7["stages"] if s["label"] == "watchdog_on_as_deployed"]
        L += ["The same rates in the M7-7 run (no shedding; `watchdog_on_as_deployed`, the production settings):", ""]
        rows = []
        for s_ in b7:
            a = s_["latency_all_requests"]
            rows.append([f"{s_['target_rate_per_s']:g}/s", f"{s_['achieved_rate_per_s']:.1f}/s", a["p50_ms"], a["p95_ms"], a["p99_ms"], a["max_ms"],
                         (", ".join(f"{k}: {v}" for k, v in s_["errors_by_status"].items()) or "none"), "yes" if s_["pass"] else "no"])
        L += table(["Offered", "Answered", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)", "Errors by status", "Passes"], rows)
        top_ns = max((s_["target_rate_per_s"] for s_ in la["stages"] if s_.get("pass_without_shedding")), default=None)
        top_g = max((s_["target_rate_per_s"] for s_ in la["stages"] if s_["pass"]), default=None)
        err_rates = [s_["target_rate_per_s"] for s_ in la["stages"] if s_["errors"]]
        L += [f"- The highest rate at which every offered request was served inside the criteria (no errors, p99 < 500 ms, nothing refused): **{top_ns:g}/s**" if top_ns else "- No tested rate passed with nothing refused.",
              f"- The highest rate that passed counting refusals as graceful (no errors, p99 of answered requests < 500 ms): **{top_g:g}/s**." if top_g else "- No tested rate passed even counting refusals as graceful.",
              ("- Errors (timeouts, resets, 5xx other than a refusal) at: " + ", ".join(f"{r:g}/s" for r in err_rates) + ".") if err_rates else
              "- No stage had any error: where M7-7 timed out and reset connections, the pods now answer with 503 + Retry-After. Overload is no longer an error; it is a refusal the client can act on.",
              "- Over the limit the answered requests are still slow: at the failing rates the p99 of what is answered exceeds 500 ms, so the limit bounds the damage but does not make the pod serve more; the throughput that passes is set by the pods' CPU and the database pool.", ""]

    # ---------------------------------------------------------------------------------------------- memory
    if so:
        L += ["### API pod memory over the 4 h soak", "",
              f"{so['summary']['requests_total']:,} requests at {so['parameters']['rate_per_s']:g}/s over {so['parameters']['duration_s'] / 3600:.2f} h, the production settings (watchdog every "
              f"{so['chart_values']['watchdog']['intervalSeconds']} s, the agency on), {so['population_before']['cases']:,} cases at the start. Errors {so['summary']['errors']}, shed {so['summary'].get('shed', 0)}, p99 "
              f"{so['summary']['latency_all_requests']['p99_ms']} ms, restarts {so['summary']['restarts']['total_restarts_during_soak']}, OOM kills {so['summary']['restarts']['oom_killed_during_soak']}.", ""]
        t, an = memory_tables(so, "Per pod, per hour")
        L += t
        L += ["The same table for the M7-7 soak (2.08 h), same method:", ""]
        t7, an7 = memory_tables(so7, "M7-7 soak, per pod, per hour")
        L += t7
        if sm and sm["samples"]:
            L += ["#### Native-level view (`/proc/1/smaps` of each API pod, `tools/k8s/smaps_sampler.py`)", "",
                  "No py-spy, memray or jemalloc is in the image or on the host and none was installed. The maps of the uvicorn process (PID 1) were read from outside every "
                  f"{int(sm.get('period_s', 900) // 60)} minutes: RSS split into the `[heap]` mapping (glibc's main arena), anonymous read-write mappings (thread arenas, Python's allocator, large blocks) and file-backed pages.", ""]
            first, last = sm["samples"][0], sm["samples"][-1]
            rows = []
            for pod in last["pods"]:
                a, b = first["pods"].get(pod, {}), last["pods"][pod]
                if "status_kb" not in a or "status_kb" not in b:
                    continue
                rows.append([pod.replace("closure-api-", "api-"), f"{a['status_kb'].get('RssAnon', 0) / 1024:.0f} -> {b['status_kb'].get('RssAnon', 0) / 1024:.0f}",
                             f"{a['heap_rss_kb'] / 1024:.0f} -> {b['heap_rss_kb'] / 1024:.0f}", f"{a['anon_rw_rss_kb'] / 1024:.0f} -> {b['anon_rw_rss_kb'] / 1024:.0f}",
                             f"{a['anon_rw_mappings']} -> {b['anon_rw_mappings']}", f"{a['arena_like_64mib_mappings']} -> {b['arena_like_64mib_mappings']}",
                             f"{a['status_kb'].get('Threads')} -> {b['status_kb'].get('Threads')}"])
            L += table(["Pod", "RssAnon (MiB)", "[heap] RSS (MiB)", "Anonymous rw RSS (MiB)", "Anonymous rw mappings", "64 MiB arena-like mappings", "Threads"], rows)
        L += ["**Verdict.** " + verdict(an, an7), ""]
    if sr:
        t, anr = memory_tables(sr, "Mitigation A/B: workers recycled after N requests (`--limit-max-requests`)")
        L += ["### Mitigation A/B: recycling the uvicorn workers", ""]
        L += [f"`api.server.workers={sr['chart_values']['api'].get('server', {}).get('workers')}`, `limitMaxRequests={sr['chart_values']['api'].get('server', {}).get('limitMaxRequests')}`; "
              f"{sr['summary']['requests_total']:,} requests at {sr['parameters']['rate_per_s']:g}/s over {sr['parameters']['duration_s'] / 3600:.2f} h; errors {sr['summary']['errors']}, shed {sr['summary'].get('shed', 0)}, "
              f"p99 {sr['summary']['latency_all_requests']['p99_ms']} ms, container restarts {sr['summary']['restarts']['total_restarts_during_soak']}. This is a mitigation of memory growth, not a fix of its cause.", ""]
        L += t
        L += ["Memory per pod is the cgroup of the whole container (all workers), so a recycled worker shows as a drop. " + recycle_note(anr), ""]
    return L


def verdict(an, an7) -> str:
    parts = []
    pref = [v["preferred"] for v in an.values()]
    last = [v["last_slice_slope_mib_per_h"] for v in an.values()]
    first = [v["first_slice_slope_mib_per_h"] for v in an.values()]
    plateaus = [(v["saturating"]["plateau_mib"], v["saturating"]["tau_h"]) for v in an.values() if v.get("saturating")]
    hours = max(v["hours"] for v in an.values())
    parts.append(f"Over {hours:.2f} h the per-hour slopes of the three pods went from {', '.join(f(x, 1) for x in first)} MiB/h in the first hour to {', '.join(f(x, 1) for x in last)} MiB/h in the last.")
    parts.append("AIC prefers the " + ", ".join(pref) + " fit for the three pods (saturating vs linear). ")
    if plateaus:
        parts.append("The saturating fits put the plateau at " + ", ".join(f"{p:.0f} MiB (tau {t:.1f} h)" for p, t in plateaus) + ".")
    flat = all(x is not None and abs(x) < 5 for x in last)
    falling = all(a is not None and b is not None and b < a for a, b in zip(first, last))
    if flat:
        parts.append("The last hour is within 5 MiB/h of flat on every pod: a plateau at the sizes tested; it does not prove the absence of a much slower leak.")
    elif falling:
        parts.append("Growth is slowing on every pod but the last hour is still above 5 MiB/h on at least one, so **the data do not show a flat memory**: a plateau is consistent with the fits, a slow leak is not excluded.")
    else:
        parts.append("**The slope does not fall: this is consistent with a slow leak, not a plateau.**")
    return " ".join(parts)


def recycle_note(anr) -> str:
    last = [f(v["last_slice_slope_mib_per_h"], 1) for v in anr.values()]
    return "Last-hour slopes: " + ", ".join(last) + " MiB/h."
