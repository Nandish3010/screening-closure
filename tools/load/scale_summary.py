#!/usr/bin/env python3
"""Render docs/results/scale/summary.md from sweep.json and latency.json (M7-6). Every number in the tables is read from those
files; the prose around them states only what the tables show. Run after tools/load/scale_sweep.py and tools/k8s/api_load.py.

    python tools/load/scale_summary.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs/results/scale"


def f(x, p=2):
    return "n/a" if x is None else f"{x:,.{p}f}"


def mb(b):
    return "n/a" if b is None else f"{b / 2**20:,.0f} MiB"


def load(name: str) -> dict:
    try:
        return json.loads((OUT_DIR / name).read_text())
    except (OSError, ValueError):
        return {}


def explain_head(lines: list[str]) -> str:
    nodes = [ln.strip().split("  (")[0].replace("->  ", "") for ln in lines if "(cost=" in ln]
    ex = next((ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("Execution Time")), "?")
    return f"{' > '.join(nodes[:4])} ({ex})"


def sweep_section(d: dict) -> list[str]:
    out = ["## Sweep at scale (`sweep.json`)", ""]
    if not d:
        return out + ["no sweep.json", ""]
    cells = [c for c in d.get("cells", []) if not c.get("profiled")]
    out += ["### Timed sweeps (daily scenario, in-cluster Postgres)", "",
            "| Open cases | Workers | Label | Sweep (s) | Events emitted | Cases read | Cases skipped | Re-sweep (s) | Full scan, no index (s) | Read+replay (s) | Planning (s) | Appends (s) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        ph = c.get("phases") or {}
        out.append(f"| {c['size']:,} | {c['workers']} | {c['label']} | {f(c['wall_seconds'])} | {c['emitted']:,} | {c['cases_read']:,} | {c['cases_skipped']:,} | "
                   f"{f(c['resweep_seconds'])} | {f(c.get('full_scan_seconds'), 1)} | {f(ph.get('read_replay_seconds'))} | {f(ph.get('planning_seconds'))} | {f(ph.get('append_seconds'))} |")
    out += ["", "Re-sweep = the same sweep straight afterwards (nothing due, emits nothing): the cost of an idle pass of the 10-second loop. "
            "Workers 1 = `run_sweep` in one process; N = `POST /scheduler/sweep` with `SWEEP_WORKERS=N`. Phases are only available for one process.", ""]
    steady = [c for c in cells if c.get("steady_state_sweeps")]
    if steady:
        out += ["### Steady state: sweeps at +10 s, +20 s, ... after the daily sweep (only what became due in each window)", "",
                "| Open cases | Label | Sweep | Wall (s) | Cases read | Events emitted |", "|---|---|---|---|---|---|"]
        for c in steady:
            for i, s in enumerate(c["steady_state_sweeps"], 1):
                out.append(f"| {c['size']:,} | {c['label']} | +{10 * i} s | {f(s['wall_seconds'])} | {s['cases_read']:,} | {s['emitted']:,} |")
        out.append("")
    out += ["### Seeding", "", "| Open cases | Processes | Insert (COPY) (s) | Cases/s | VACUUM ANALYZE (s) | Total (s) | Events | Expected sweep events |", "|---|---|---|---|---|---|---|---|"]
    for size, s in sorted(d.get("seeding", {}).items(), key=lambda kv: int(kv[0])):
        out.append(f"| {s['cases']:,} | {s['processes']} | {f(s['insert_seconds'], 1)} | {s['cases_per_second']:,.0f} | {f(s['vacuum_analyze_seconds'], 1)} | "
                   f"{f(s['total_seconds'], 1)} | {s['events']:,} | {s['expected_emitted']:,} |")
    out.append("")
    big = next((c for c in cells if c["size"] == 1_000_000 and c["workers"] == 1 and c.get("sizes_before")), None)
    if big:
        sb = big["sizes_before"]
        out += ["### Size at 1,000,000 cases (before the sweep)", "", f"Database `closure_load`: {sb['database_bytes'] / 2**30:.2f} GiB.", "",
                "| Relation | Kind | Size |", "|---|---|---|"]
        for r in sb["relations"]:
            out.append(f"| {r['name']} | {r['kind']} | {mb(r['relation_bytes'])} |")
        t = sb["tables"]
        out += ["", "| Table | Rows | Heap bytes/row | Heap+indexes+toast bytes/row |", "|---|---|---|---|"]
        for name, v in t.items():
            out.append(f"| {name} | {v['rows']:,} | {v['heap_bytes_per_row']:.0f} | {v['total_bytes_per_row']:.0f} |")
        out += ["", "### Index use during the 1,000,000-case sweep (`pg_stat_user_indexes`, delta over the first sweep)", "",
                "| Index | idx_scan | idx_tup_read | idx_tup_fetch |", "|---|---|---|---|"]
        a, b = big["index_stats_before"]["indexes"], big["index_stats_after_sweep"]["indexes"]
        for k in b:
            out.append(f"| {k} | {b[k]['idx_scan'] - a[k]['idx_scan']:,} | {b[k]['idx_tup_read'] - a[k]['idx_tup_read']:,} | {b[k]['idx_tup_fetch'] - a[k]['idx_tup_fetch']:,} |")
        ta, tb = big["index_stats_before"]["tables"], big["index_stats_after_sweep"]["tables"]
        out += ["", "| Table | seq_scan | seq_tup_read | idx_scan | n_tup_ins | n_tup_upd | n_tup_hot_upd |", "|---|---|---|---|---|---|---|"]
        for k in tb:
            out.append(f"| {k} | {tb[k]['seq_scan'] - ta[k]['seq_scan']} | {tb[k]['seq_tup_read'] - ta[k]['seq_tup_read']:,} | {tb[k]['idx_scan'] - ta[k]['idx_scan']:,} | "
                       f"{tb[k]['n_tup_ins'] - ta[k]['n_tup_ins']:,} | {tb[k]['n_tup_upd'] - ta[k]['n_tup_upd']:,} | {tb[k]['n_tup_hot_upd'] - ta[k]['n_tup_hot_upd']:,} |")
        e = big["explain_before_sweep"]
        out += ["", "### EXPLAIN (ANALYZE, BUFFERS) at 1,000,000 cases (full plans in sweep.json)", "",
                f"- Due-case selection, single process ({e['due_selection_rows']:,} rows): {explain_head(e['due_selection_single_process'])}",
                f"- Due-case selection, shard 0 of 4: {explain_head(e['due_selection_shard_0_of_4'])}",
                f"- Same predicate without the `next_due_basis` clause (for comparison): {explain_head(e['due_predicate_index_only_for_comparison'])}",
                f"- Per-chunk read, 500 cases: {explain_head(e['per_chunk_read_500_cases'])}",
                f"- Single-case read: {explain_head(e['single_case_read'])}", ""]
    prof = next((c for c in d.get("cells", []) if c.get("profiled")), None)
    if prof:
        out += ["### cProfile, 1,000,000 cases, one process (top by own time)", "", "```", prof["cprofile_tottime_top20"].strip()[:2500], "```", ""]
    pops = d.get("closure_population", [])
    if pops:
        p = pops[-1]
        out += ["### Population left in the cluster's `closure` database", "",
                f"{p['count_after']['cases']:,} cases / {p['count_after']['events']:,} events (before: {p['count_before']['cases']:,} / {p['count_before']['events']:,}); "
                f"seeded {p['cases']:,} cases in {f(p['total_seconds'], 0)} s with {p['processes']} processes; state weights {p.get('state_weights')}; "
                f"database {p['sizes']['database_bytes'] / 2**30:.2f} GiB.", ""]
    return out


def latency_section(d: dict) -> list[str]:
    out = ["## API latency at a sustained rate (`latency.json`)", ""]
    if not d:
        return out + ["no latency.json", ""]
    pb, pa = d.get("population_before", {}), d.get("population_after", {})
    out += [f"Population during the test: {pb.get('cases', 0):,} cases / {pb.get('events', 0):,} events before, {pa.get('cases', 0):,} / {pa.get('events', 0):,} after. "
            f"Pass criteria: {d.get('pass_criteria')}. The load generator runs on the same host as the cluster.", "",
            "| Target rate | Achieved | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | p99 excl. worklist (ms) | Errors | Error rate | Pass (all) | Pass (excl. worklist) |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in d.get("stages", []):
        la, lp = s["latency_all_requests"], s["latency_all_requests_excluding_worklist"]
        out.append(f"| {s['target_rate_per_s']:g}/s ({s.get('label')}) | {s['achieved_rate_per_s']:.1f}/s | {la['p50_ms']} | {la['p95_ms']} | {la['p99_ms']} | {la['max_ms']} | {lp['p99_ms']} | "
                   f"{s['errors']:,} {s['errors_by_status'] or ''} | {s['error_rate'] * 100:.3f}% | {'yes' if s['pass'] else 'no'} | {'yes' if s['pass_excluding_worklist'] else 'no'} |")
    out.append("")
    for label, sm in d.get("summary", {}).items():
        out.append(f"- `{label}`: highest passing rate (all requests) {sm['pass']['max_passing_rate_per_s']}/s, lowest failing {sm['pass']['lowest_failing_rate_per_s']}/s; "
                   f"excluding the worklist {sm['pass_excluding_worklist']['max_passing_rate_per_s']}/s / {sm['pass_excluding_worklist']['lowest_failing_rate_per_s']}/s.")
    out.append("")
    for s in d.get("stages", []):
        out += [f"### Stage {s['target_rate_per_s']:g}/s ({s.get('label')})", "",
                "| Endpoint | Requests | Errors | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |", "|---|---|---|---|---|---|---|"]
        for ep, v in s["per_endpoint"].items():
            lt = v["latency"]
            out.append(f"| {ep} | {v['requests']:,} | {v['errors']} | {lt.get('p50_ms')} | {lt.get('p95_ms')} | {lt.get('p99_ms')} | {lt.get('max_ms')} |")
        out += ["", "| Pod | Requests (share) | CPU mean / p95 / max (cores) | Memory mean / max | Memory limit | CPU-throttled periods |", "|---|---|---|---|---|---|"]
        dist = s["request_distribution_per_pod"]
        for pod, v in s["resources"]["pods"].items():
            share = dist.get(pod)
            out.append(f"| {pod} | {('%s (%.0f%%)' % (format(share['total'], ','), share['share'] * 100)) if share else '-'} | {v['cpu_cores_mean']:.2f} / {v['cpu_cores_p95']:.2f} / {v['cpu_cores_max']:.2f} | "
                       f"{mb(v['memory_bytes_mean'])} / {mb(v['memory_bytes_max'])} | {mb(v['memory_limit_bytes']) if v['memory_limit_bytes'] else '-'} | "
                       f"{v['cpu_throttled_periods']:,} of {v['cpu_periods']:,} |")
        out += ["", "| Kind node | CPU % mean / max (of one core) | Memory (last) |", "|---|---|---|"]
        for n, v in s["resources"]["nodes"].items():
            out.append(f"| {n} | {v['cpu_percent_mean']:.0f} / {v['cpu_percent_max']:.0f} | {v['mem_usage_last']} |")
        sc = s["scheduler_during_stage"]
        out += ["", f"Scheduler during the stage: sweep {sc['sweep_duration']}; automation scan {sc['automation_scan_duration']}; sweep-to-sweep period {sc['sweep_to_sweep_period']}.", ""]
    probe = d.get("unbounded_worklist_probe")
    if probe:
        out += ["### Unbounded worklist read at this population", "", "```", json.dumps(probe, indent=1, default=str)[:2500], "```", ""]
    return out


def soak_section(d: dict) -> list[str]:
    out = ["## Soak (`soak.json`)", ""]
    sm = d.get("summary") if d else None
    if not sm:
        return out + ["no finished soak.json", ""]
    la, sc, wd, rs = sm["latency_all_requests"], sm["scheduler"], sm["watchdog"], sm["restarts"]
    ww = sm.get("worst_window_by_p99") or {}
    wl = ww.get("latency_all", {})
    out += [f"{sm['measured_seconds'] / 3600:.2f} h at {sm['target_rate_per_s']:g} req/s ({d['started_at']} to {d['finished_at']}), "
            f"{sm['requests_total']:,} requests, {sm['errors']:,} errors ({sm['error_rate'] * 100:.4f}%) {sm['errors_by_status'] or ''}; "
            f"uninterrupted sampling: {sm['sampling']['uninterrupted']}.", "",
            "| Latency (ms) | p50 | p95 | p99 | max |", "|---|---|---|---|---|",
            f"| overall | {la['p50_ms']} | {la['p95_ms']} | {la['p99_ms']} | {la['max_ms']} |",
            f"| worst {sm['window_seconds']:g} s window by p99 ({ww.get('start')}) | {wl.get('p50_ms')} | {wl.get('p95_ms')} | {wl.get('p99_ms')} | {wl.get('max_ms')} |", "",
            f"Restarts during the soak: {rs['total_restarts_during_soak']} (OOMKilled {rs['oom_killed_during_soak']}). Scheduler: {sc['sweeps_completed']:,} sweeps "
            f"completed vs {sc['sweeps_expected_at_10s']:,.0f} expected at 10 s, max sweep {sc['max_sweep_duration_s']} s, leader changes "
            f"{sm['leader']['scheduler_leader_changes']}. Watchdog (every {wd['interval_s']:g} s): {wd['scans_observed']} scans, duration {wd['duration_s']}.", "",
            "| Pod | Memory start / end / max (MiB) | Anon start / end (MiB) | Slope (MiB/h) | Slope after 20 min (MiB/h) | Restarts |", "|---|---|---|---|---|---|"]
    for pod, v in sm["pods"].items():
        r = rs["per_pod"].get(pod, {}).get("restarts_during_soak", "-")
        out.append(f"| {pod} | {v['memory_current_start_mib']} / {v['memory_current_end_mib']} / {v['memory_current_max_mib']} | {v['memory_anon_start_mib']} / "
                   f"{v['memory_anon_end_mib']} | {v['memory_current_slope_mib_per_hour']} | {v['memory_current_slope_after_20min_mib_per_hour']} | {r} |")
    out.append("")
    return out


def main() -> int:
    sw, la, sk = load("sweep.json"), load("latency.json"), load("soak.json")
    lines = ["# Scalability evidence (M7-6)", "", "Generated by `tools/load/scale_summary.py` from `sweep.json` and `latency.json`. Do not edit by hand.", ""]
    for d in (sw, la, sk):
        if d:
            lines.append(f"- {d['tool']}: commit {d['git']['commit'][:10]}, generated {d.get('generated_at') or d.get('started_at')}")
    lines.append("")
    lines += sweep_section(sw) + latency_section(la) + soak_section(sk)
    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT_DIR / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
