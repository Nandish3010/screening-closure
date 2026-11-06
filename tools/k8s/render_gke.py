"""The "Cloud run (GKE)" part of docs/results/scale.md: what the GKE load test (deploy/gke/loadtest.sh, docs/governance/gke-loadtest.md) measured.
Pure functions from the raw files in docs/results/scale/gke/ to Markdown lines; every number printed is read from them. Imported by render_scale.py.

Until a real run has been imported (`make gke-import RUN=results/gke-loadtest/<run id>`, which copies run.json, seed.json, sweep.json, latency.json and
soak.json there) the section says "not run: owner budget limit" and lists what the run will measure. A local kind REHEARSAL (mode "kind") is never
rendered as a measurement: the importer refuses it.

    python tools/k8s/render_gke.py --import-run results/gke-loadtest/20261108-101500
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memfit  # noqa: E402
import render_m78 as m78  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GKE_DIR = ROOT / "docs/results/scale/gke"
FILES = ("run.json", "seed.json", "sweep.json", "latency.json", "soak.json")
LABEL = "gke"
SWEEP_TARGET_S = 10.0
P99_LIMIT_MS = 500.0

PENDING = [
    "# Cloud run (GKE): not run: owner budget limit (owner spend cap, 6 Nov 2026)",
    "",
    "**Status: not run: owner budget limit. On 6 November 2026 the owner capped spend at the single demo VM, so this test was not run. The harness is ready; results are pending a funded run.** Nothing in this section has been measured yet. Every figure in the sections below this one was measured on one laptop-class "
    "machine with a local kind cluster (see \"What it is not\" above); no cloud environment has been measured. The load test is prepared and rehearsed locally "
    "(`deploy/gke/`, `docs/governance/gke-loadtest.md`, a local kind rehearsal of the same pipeline) but the owner has not approved any cloud spend, so it is NOT scheduled and the script refuses to run without `SC_ALLOW_SPEND=1`. "
    "If spend is approved later it would run as `SC_ALLOW_SPEND=1 make gke-loadtest PROJECT=<id>`. This section is regenerated from `docs/results/scale/gke/*.json` once a run has been imported "
    "(`make gke-import RUN=<results folder>`), and the paragraph above is then replaced by the machine, the commit and the figures.",
    "",
    "What the run will measure, on the same chart, the same traffic mix and the same tools as the kind runs, so the two can be compared:",
    "",
    "| Question | Measured by | Reported here as |",
    "|---|---|---|",
    "| 3 API replicas, 2 schedulers and 1 watchdog on GKE (zonal Standard cluster, separate node pools for the API, Postgres and the load generator) | `values-gke-loadtest.yaml` | machine and cluster table |",
    "| The 1,000,000-case population seeded in parallel | `tools/load/scale_sweep.py seed-closure` in the load-generator pod | seeding time and database size |",
    "| The 1,000,000-case daily burst: one unbounded sweep (1 and 4 workers), the bounded drain loops, the incremental watchdog scans | `tools/load/scale_sweep.py sweep` | sweep and drain tables, against the 10 s loop |",
    "| p50 / p95 / p99 latency and error rate at 50, 100, 175, 250 requests/s | `tools/k8s/api_load.py` (open loop, from a pod in the cluster) | ladder table |",
    "| Load shedding past the limit (503 with Retry-After) at 300, 400, 500 requests/s | the same ladder | shed count, shed rate, p99 of answered requests |",
    "| Per-pod memory over at least one hour of the soak mix at 50 requests/s (BUG-77) | `tools/k8s/soak.py`, a cgroup sample every 30 s | slope per pod per hour, against the kind figures |",
    "| What it cost and what it left behind | `deploy/gke/loadtest.sh` | estimate, resources created, teardown verification |",
    "",
]


def jload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _opt(raw: Path, name: str):
    p = raw / "gke" / name
    return jload(p) if p.exists() else None


def _f(x, p=2):
    return m78.f(x, p)


def machine_section(run: dict, spec: dict, docs: dict) -> list[str]:
    nodes = spec.get("nodes") or []
    pools: dict[str, list] = {}
    for nd in nodes:
        pools.setdefault(nd.get("pool") or "?", []).append(nd)
    rows = []
    for pool in ("app", "data", "load"):
        ns = pools.get(pool) or []
        if ns:
            rows.append([pool, f"{len(ns)} x {ns[0].get('machine_type')}", f"{ns[0].get('allocatable_cpu')} CPU / {ns[0].get('allocatable_memory')} allocatable per node",
                         {"app": "API x3 (one per node), scheduler x2, watchdog", "data": "Postgres", "load": "load generator and seeders"}[pool]])
    L = ["## Machine and runs (GKE)", "",
         f"Region {run.get('region')}, zone {run.get('zone') or 'n/a'}. Kubernetes {spec.get('kubernetes_version')}, Helm {spec.get('helm')}, PostgreSQL {spec.get('postgres_version')} "
         f"(in-cluster, one pod, pd-ssd volume, no TLS, no HA). The load generator ran in a pod in the same zone, so no laptop network is in any latency. "
         f"Run `{run.get('run_id')}`, commit `{str(run.get('git', {}).get('commit'))[:10]}` (branch {run.get('git', {}).get('branch')}).", ""]
    L += m78.table(["Pool", "Nodes", "Allocatable", "Runs"], rows)
    rows = []
    for nm, doc in (("seed.json", docs.get("seed")), ("sweep.json", docs.get("sweep")), ("latency.json", docs.get("latency")), ("soak.json", docs.get("soak"))):
        if doc:
            g = doc.get("git", {})
            rows.append([f"`gke/{nm}`", doc.get("tool", "tools/load/scale_sweep.py"), f"`{str(g.get('commit'))[:10]}`", doc.get("started_at"), doc.get("finished_at") or doc.get("generated_at")])
    if rows:
        L += m78.table(["File", "Tool", "Commit", "Started", "Finished"], rows)
    return L


def seed_rows(seed: dict | None) -> list[list]:
    if not seed:
        return []
    pop = (seed.get("closure_population") or [{}])[-1]
    return [["Seeding the 1M population", f"{pop.get('cases', 0):,} cases / {pop.get('events', 0):,} events in {_f(pop.get('total_seconds'), 0)} s ({_f(pop.get('cases_per_second'), 0)} cases/s, "
            f"{pop.get('processes')} processes)", "kind: 184 s with 4 processes (docs/results/scale/m78/seed.json)"]]


def sweep_section(sw: dict | None) -> tuple[list[str], list[list]]:
    if not sw:
        return [], []
    L = ["## The 1,000,000-case daily burst and the watchdog (GKE)", "",
         "The same cells as the kind report (`tools/load/scale_sweep.py sweep`), run in the load-generator pod against the in-cluster Postgres, with the scheduler and the watchdog scaled to zero. "
         f"The unbounded sweep is judged against the {SWEEP_TARGET_S:g} s loop; the bounded loops are what the scheduler runs.", ""]
    rows, summary = [], []
    for c in sorted((x for x in sw.get("cells", []) if x.get("label") == LABEL and not x.get("profiled")), key=lambda c: (c["size"], c["workers"])):
        rows.append([f"{c['size']:,}", c["workers"], _f(c["wall_seconds"], 2), f"{c['emitted']:,}", f"{c['expected_emitted']:,}", _f(c["resweep_seconds"], 2),
                     "yes" if c["wall_seconds"] <= SWEEP_TARGET_S else "**no**", "none" if not c.get("problems") else "; ".join(map(str, c["problems"]))[:80]])
        summary.append([f"Unbounded sweep of the daily burst, {c['size']:,} cases, {c['workers']} worker(s), against {SWEEP_TARGET_S:g} s",
                        f"{_f(c['wall_seconds'], 1)} s ({c['emitted']:,} events)", "met" if c["wall_seconds"] <= SWEEP_TARGET_S else "**not met**"])
    L += ["### Timed sweeps (one unbounded sweep per cell)", ""]
    L += m78.table(["Open cases", "Workers", "Sweep (s)", "Events emitted", "Planner expected", "Idle re-sweep (s)", f"<= {SWEEP_TARGET_S:g} s", "Problems"], rows)
    drows = []
    for c in (x for x in sw.get("drain_cells", []) if x.get("label") == LABEL):
        lw = c["loop_wall_seconds"]
        drows.append([f"{c['size']:,}", c["loop_count"], _f(lw["max"], 2), c["loops_over_interval"], _f(c["drain_compute_seconds"], 1), _f(c["time_to_drain_seconds_with_interval_sleeps"], 0), f"{c['emitted']:,}",
                      "none" if not c.get("problems") else "; ".join(map(str, c["problems"]))[:80]])
        summary.append([f"Bounded drain loops of the same burst, {c['size']:,} cases: longest loop and loops over the interval",
                        f"{_f(lw['max'], 2)} s, {c['loops_over_interval']} over; {c['loop_count']} loops, {_f(c['time_to_drain_seconds_with_interval_sleeps'], 0)} s to drain at the interval",
                        "met" if c["loops_over_interval"] == 0 else "**not met**"])
    if drows:
        L += ["### Bounded drain loops (what the scheduler runs)", ""]
        L += m78.table(["Open cases", "Loops", "Longest loop (s)", "Loops over the interval", "Compute (s)", "Time to drain with sleeps (s)", "Events emitted", "Problems"], drows)
    wrows = []
    for c in (x for x in sw.get("watchdog_cells", []) if x.get("label") == LABEL):
        wrows.append([f"{c['size']:,}", c["catchup_scans"], f"{c['catchup_flagged']:,}", _f(c["catchup_scan_seconds"]["max"], 2), _f(c["steady_scan_seconds"]["median"], 3),
                      "none" if not c.get("problems") else "; ".join(map(str, c["problems"]))[:80]])
        summary.append([f"Watchdog scan at {c['size']:,} open cases against its interval", f"steady median {_f(c['steady_scan_seconds']['median'], 3)} s; longest catch-up scan {_f(c['catchup_scan_seconds']['max'], 2)} s",
                        "met" if c["steady_scan_seconds"]["median"] < 10 else "**not met**"])
    if wrows:
        L += ["### Watchdog scans", ""]
        L += m78.table(["Open cases", "Catch-up scans", "Flagged", "Longest scan (s)", "Steady median (s)", "Problems"], wrows)
    return L, summary


def ladder_section(la: dict | None) -> tuple[list[str], list[list]]:
    if not la:
        return [], []
    stages = [s for s in la["stages"] if s.get("label") == LABEL]
    L = ["## API latency and load shedding (GKE)", "",
         "Open-loop constant arrival rate from the load-generator pod to the API Service (ClusterIP, three pods), the mix of `tools/k8s/api_load.py`, the scheduler and the watchdog on at the chart's settings. "
         f"Pass = no errors beyond {0.1}%, p99 of answered requests under {P99_LIMIT_MS:g} ms; refusals with 503 + Retry-After are counted apart (ADR-048).", ""]
    L += m78.ladder_table({"stages": stages}, None)
    summ = (la.get("summary") or {}).get(LABEL) or {}
    rows = []
    top_ns = (summ.get("pass_without_shedding") or {}).get("max_passing_rate_per_s")
    top = (summ.get("pass") or {}).get("max_passing_rate_per_s")
    low = (summ.get("pass") or {}).get("lowest_failing_rate_per_s")
    rows.append(["Highest offered rate that passes", f"{_f(top, 0)}/s counting graceful shedding; {_f(top_ns, 0)}/s with nothing shed" + (f"; first failure {_f(low, 0)}/s" if low else ""),
                 "kind (M7-8): 250/s counting shedding, 175/s with nothing shed"])
    shed = [s for s in stages if s.get("shed")]
    for s in shed:
        rows.append([f"Overload at {s['target_rate_per_s']:g}/s", f"{s['shed']:,} requests shed ({s['shed_rate'] * 100:.1f}%), errors {s['errors']:,}, p99 of answered requests {s['latency_all_requests']['p99_ms']} ms", "503 + Retry-After + X-Shed"])
    return L, rows


def soak_section(so: dict | None) -> tuple[list[str], list[list]]:
    if not so or "summary" not in so:
        return [], []
    s = so["summary"]
    la = s["latency_all_requests"]
    L = ["## Soak: steady load with the scheduler and the watchdog on (GKE)", "",
         f"One uninterrupted open-loop stage at {s['target_rate_per_s']:g} requests/s for {s['measured_seconds'] / 3600:.2f} h after the warm-up, {s['requests_total']:,} requests, from the load-generator pod. "
         "Per-pod memory is the cgroup's anonymous memory read every 30 s (`kubectl exec` from the pod).", ""]
    L += m78.table(["Requests", "Errors", "Shed", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)", "Windows with p99 over 500 ms", "Restarts", "Passes"],
                   [[f"{s['requests_total']:,}", f"{s['errors']:,}", f"{s['shed']:,}", la["p50_ms"], la["p95_ms"], la["p99_ms"], la["max_ms"], f"{s['windows_over_p99_500ms']} of {s['windows']}",
                     s["restarts"]["total_restarts_during_soak"], "yes" if s["pass"] else "no"]])
    L += ["### Scheduler cadence in the soak", ""]
    L += m78.cadence_rows(so)
    mem, an = m78.memory_tables(so, "API pod memory over the soak (GKE)")
    L += [ln.replace("#### ", "### ", 1) if ln.startswith("#### ") else ln for ln in mem]
    slopes = [v["linear"]["slope_mib_per_h"] for v in an.values() if v["linear"]["slope_mib_per_h"] is not None]
    hours = max((v["hours"] for v in an.values()), default=0)
    rows = [["Soak at the target rate: errors, p99 under 500 ms", f"{s['errors']:,} errors, p99 {la['p99_ms']} ms over {s['measured_seconds'] / 3600:.2f} h", "met" if s["pass"] else "**not met**"]]
    if slopes:
        rows.append([f"API pod memory growth over {hours:.2f} h (anon, MiB/h per pod; BUG-77)", ", ".join(f"{x:g}" for x in slopes) + "; kind (M7-8, 4 h): 43.85, 45.63, 45.94",
                     "flat" if max(slopes) < 5 else "**growing**"])
    return L, rows


def build(raw: Path, jload_fn=jload) -> list[str]:
    run = _opt(raw, "run.json")
    if not run or run.get("mode") != "gke":
        return PENDING
    docs = {k: _opt(raw, f"{k}.json") for k in ("seed", "sweep", "latency", "soak")}
    spec = (docs["sweep"] or docs["latency"] or docs["soak"] or {}).get("machine_spec") or {}
    cost = run.get("cost_estimate") or {}
    L = ["# Cloud run (GKE)", "",
         f"Measured by `deploy/gke/loadtest.sh` on Google Kubernetes Engine, run `{run['run_id']}`, from `docs/results/scale/gke/*.json`. All data are synthetic. "
         "These are the only cloud measurements in this report; the sections below them are the laptop runs.", ""]
    L += machine_section(run, spec, docs)
    sweep_lines, sweep_rows = sweep_section(docs["sweep"])
    ladder_lines, ladder_rows = ladder_section(docs["latency"])
    soak_lines, soak_rows = soak_section(docs["soak"])
    summary = seed_rows(docs["seed"])
    L += ["## Results against the targets (GKE)", ""]
    L += m78.table(["Question", "Measured on GKE", "Result / the kind figure"], summary + sweep_rows + ladder_rows + soak_rows)
    L += sweep_lines + ladder_lines + soak_lines
    failed = [p for p in run.get("phases", []) if str(p.get("phase", "")).endswith("-exit") and p.get("rc") not in (0, None)]
    L += ["## Cost, resources and teardown (GKE)", ""]
    if cost:
        L += [f"Estimate before the run (`deploy/gke/cost.py`, list prices from memory, not a quote): expected USD {cost.get('expected_usd')}, at the wall-clock cap USD {cost.get('cap_usd')}, "
              f"worst case USD {cost.get('worst_case_usd')} at USD {cost.get('usd_per_hour')} per hour. The bill is the authority.", ""]
    tl = {e["event"]: e["epoch"] for e in run.get("timeline", []) if "event" in e}
    if "confirmed" in tl and "teardown_end" in tl:
        L += [f"Wall clock from confirmation to the end of the teardown: {(tl['teardown_end'] - tl['confirmed']) / 60:.0f} min.", ""]
    ver = run.get("teardown-verification") or []
    L += [f"Teardown verification: {' '.join(ver) if ver else 'not recorded'}.", ""]
    if failed:
        L += ["Scenarios that exited non-zero: " + ", ".join(f"`{p['phase']}` ({p['rc']})" for p in failed) + ".", ""]
    L += ["**Limits of this run.** One zone, one Postgres pod without HA or TLS, a throw-away OIDC key, no FHIR server, NetworkPolicy off, synthetic data; the load generator is a single-process asyncio client "
          "(its lag is in the ladder JSON). It is not a production sizing.", ""]
    return L


def import_run(src: Path, force: bool) -> int:
    run = jload(src / "run.json")
    if run.get("mode") != "gke":
        print(f"refusing: {src} is a '{run.get('mode')}' run (a local rehearsal is not a cloud measurement)", file=sys.stderr)
        return 1
    res = src / "results"
    for need in ("sweep.json", "latency.json", "soak.json", "seed.json"):
        if not (res / need).exists():
            print(f"missing {res / need}", file=sys.stderr)
            if not force:
                return 1
    if run.get("result_status", {}).get("failed_phases") and not force:
        print("the run reports failed phases (run.json result_status); use --force to import it anyway", file=sys.stderr)
        return 1
    if run.get("exit_code") not in (0,) and not force:
        print(f"the run exited {run.get('exit_code')}; use --force to import it anyway", file=sys.stderr)
        return 1
    soak = jload(res / "soak.json") if (res / "soak.json").exists() else {}
    if soak.get("status") != "finished" and not force:
        print("soak.json is not a finished soak (status != finished)", file=sys.stderr)
        return 1
    GKE_DIR.mkdir(parents=True, exist_ok=True)
    run_out = {k: v for k, v in run.items() if k not in ("resources-created",)}
    (GKE_DIR / "run.json").write_text(json.dumps(run_out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name in ("seed.json", "sweep.json", "latency.json", "soak.json"):
        if (res / name).exists():
            shutil.copyfile(res / name, GKE_DIR / name)
    print(f"imported {src} into {GKE_DIR.relative_to(ROOT)}; now run `make scale-report`")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--import-run", metavar="DIR", help="copy a finished run's JSON into docs/results/scale/gke/")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.import_run:
        return import_run(Path(a.import_run), a.force)
    print("\n".join(build(ROOT / "docs/results/scale")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
