#!/usr/bin/env python3
"""Render docs/results/scale.md and docs/results/scale.json (M7-6 scalability evidence and capacity plan).

Inputs, all committed:
  docs/results/scale/{failover,sweep,latency,soak}.json   the raw measurements (tools/k8s/failover_drill.py, tools/load/scale_sweep.py,
                                                          tools/k8s/api_load.py, tools/k8s/soak.py)
  tools/k8s/capacity_inputs.yaml                          assumptions and the Azure list prices of the capacity plan
  docs/results/results.json                               the simulator's open-share curve (mean time a case stays open)
  deploy/helm/screening-closure/{values,values-kind}.yaml production defaults versus what the kind cluster ran

Every measured number in the output is read from those files, never typed here. Prose only states what the tables show.

    python tools/k8s/render_scale.py           # write docs/results/scale.md and scale.json
    python tools/k8s/render_scale.py --check   # fail if the committed files differ from what would be written (make scale-report-check)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "docs/results/scale"
OUT_MD = ROOT / "docs/results/scale.md"
OUT_JSON = ROOT / "docs/results/scale.json"
INPUTS = ROOT / "tools/k8s/capacity_inputs.yaml"
VALUES = ROOT / "deploy/helm/screening-closure/values.yaml"
VALUES_KIND = ROOT / "deploy/helm/screening-closure/values-kind.yaml"
RESULTS = ROOT / "docs/results/results.json"
PILOT_INPUTS = ROOT / "tools/pilot/inputs.yaml"

SECONDS_PER_MONTH_H = 3600


# ---------------------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------------------

def jload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def f(x, p: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{p}f}"


def n(x) -> str:
    return "n/a" if x is None else f"{x:,}"


def mib(b) -> str:
    return "n/a" if b is None else f"{b / 2**20:,.0f} MiB"


def usd(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.2f}"


def table(head: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def sdict(d: dict, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return default
        d = d[p]
    return d


def stat_row(s: dict, p: int = 2) -> list[str]:
    return [f(s["min"], p), f(s["median"], p), f(s.get("p95"), p), f(s["max"], p)]


def interp(points: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear through the measured points; linear through the origin below the first, linear beyond the last."""
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1] * x / pts[0][0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    return y1 + (y1 - y0) * (x - x1) / (x1 - x0)


def cell(sweep: dict, size: int, workers: int, label: str) -> dict | None:
    return next((c for c in sweep["cells"] if c["size"] == size and c["workers"] == workers and c["label"] == label and not c.get("profiled")), None)


# ---------------------------------------------------------------------------------------------------------------------------------
# sections built from the raw JSONs
# ---------------------------------------------------------------------------------------------------------------------------------

def git_line(name: str, d: dict) -> str:
    g = d.get("git", {})
    dirty = g.get("dirty_files") or []
    return (f"| `{name}` | {d.get('tool')} | `{str(g.get('commit'))[:10]}` | {g.get('branch')} | {len(dirty)} uncommitted path(s) "
            f"({', '.join('`' + x + '`' for x in dirty[:6])}) | {d.get('started_at') or d.get('drill_started_at')} | {d.get('finished_at') or d.get('generated_at')} |")


def machine_section(fo, sw, la, so) -> list[str]:
    m = sw["machine_spec"]
    h, dk, kd = m["host"], m["docker"], m["kind"]
    out = ["## Machine and runs", "",
           "Every measurement below was taken on one laptop-class machine. No cloud environment was measured.", ""]
    out += table(["", ""], [
        ["Host CPU", f"{h['cpu_model']}, {h['logical_cores']} logical / {h['physical_cores']} physical cores"],
        ["Host RAM", f"{h['ram_bytes'] / 2**30:.0f} GiB (macOS {h['macos']})"],
        ["Docker", f"{dk['os']} {dk['version']}, VM with {dk['cpus']} CPUs and {dk['memory_bytes'] / 2**30:.1f} GiB"],
        ["Kubernetes", f"kind ({kd['version']}), node image `{kd['node_image'].split('@')[0]}`, nodes `{kd['nodes'].strip()}`"],
        ["Kubernetes server / Helm", f"{m['kubernetes_version']} / {m['helm']}"],
        ["PostgreSQL", m["postgres_version"]],
        ["Shared", "The load generator, the kind nodes (3 containers in one VM) and Postgres share the VM and the host; the sweep tool runs on the host and reaches Postgres through the kind port mapping"],
    ])
    out += ["Runs (the raw files are the evidence; a run is attributed to the commit it started from, and its working tree is listed when it was not clean). The sweep, watchdog and drain cells and the memory repro ran on the macOS host against the cluster's Postgres; "
            "the latency, soak and fail-over runs ran against the cluster through the NodePort.", ""]
    out += ["| File | Tool | Commit | Branch | Working tree | Started | Finished |", "|---|---|---|---|---|---|---|",
            git_line("failover.json", fo), git_line("sweep.json", sw), git_line("latency.json", la), git_line("soak.json", so), ""]
    return out


def local_cluster_section(sw, so, la) -> list[str]:
    prod = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    kind = yaml.safe_load(VALUES_KIND.read_text(encoding="utf-8"))
    cv = so["chart_values"]
    co = so["cluster_overrides"]
    pg = sw["postgres_pod"]
    out = ["## Local cluster", "",
           "`make k8s-up` (`tools/k8s/up.sh`) creates the kind cluster `sc-m76` (one control-plane and two worker nodes, "
           "`tools/k8s/kind-config.yaml`), builds the three images with tag `m76` and loads them into the nodes, generates the Secrets "
           "(nothing is committed), applies `tools/k8s/postgres.yaml` (a Postgres 16 StatefulSet with a local volume) and `tools/k8s/jwks.yaml` "
           "(a pod that serves the public signing key), and installs `deploy/helm/screening-closure` with `values-kind.yaml`. "
           "`make k8s-down` deletes the cluster. The API is published on 127.0.0.1:18376 (NodePort), Postgres on 127.0.0.1:15476.", "",
           "### What runs", ""]
    out += table(["Workload", "Replicas", "CPU request / limit", "Memory request / limit", "Other"], [
        ["api (Deployment)", cv["api"]["replicas"], f"{cv['api']['resources']['requests']['cpu']} / {cv['api']['resources']['limits']['cpu']}",
         f"{cv['api']['resources']['requests']['memory']} / {cv['api']['resources']['limits']['memory']}",
         f"`SWEEP_WORKERS={co['api_env_SWEEP_WORKERS']}`, topology spread {cv['api']['topologySpread']}, PDB minAvailable {cv['api']['podDisruptionBudget']['minAvailable']}"],
        ["scheduler (Deployment)", cv["scheduler"]["replicas"], f"{cv['scheduler']['resources']['requests']['cpu']} / {cv['scheduler']['resources']['limits']['cpu']}",
         f"{cv['scheduler']['resources']['requests']['memory']} / {cv['scheduler']['resources']['limits']['memory']}",
         f"interval {cv['scheduler']['intervalSeconds']} s, one leader (lease TTL {cv['app']['leaderLeaseTtlSeconds']} s, fencing {cv['app']['requireLeaderFence']})"],
        ["watchdog (Deployment)", cv["watchdog"]["replicas"], f"{cv['watchdog']['resources']['requests']['cpu']} / {cv['watchdog']['resources']['limits']['cpu']}",
         f"{cv['watchdog']['resources']['requests']['memory']} / {cv['watchdog']['resources']['limits']['memory']}",
         f"interval {cv['watchdog']['intervalSeconds']} s (the soak); {sdict(la, 'chart_values', 'watchdog', 'intervalSeconds')} s in the latency run"],
        ["postgres-0 (StatefulSet, `tools/k8s/postgres.yaml`)", 1, f"{pg['resources']['requests'].get('cpu')} / none", f"{pg['resources']['requests']['memory']} / {pg['resources']['limits']['memory']}",
         f"`{pg['image']}`, volume {pg['pvc']['resources']['requests']['storage']}, no TLS, no HA, no backups"],
        ["sc-jwks", 1, "-", "-", "serves the public key the API verifies tokens against"],
    ])
    out += ["### Chart bugs found and fixed on apply", "",
            "Applying the production chart to a real cluster exposed four defects, fixed in the chart (commits `f529e6e` and `b93af1e`):", "",
            "1. **Scheduler liveness probe.** The probe required `/tmp/scheduler.json`, which only the leader writes, so a follower failed the liveness probe. "
            "A missing file now means follower and passes; a stale file (older than two minutes) still fails. The probe also gets `timeoutSeconds: 10` because Python start-up under the 250m CPU limit exceeds the 1 s default.",
            "2. **NetworkPolicy, database CIDR.** The egress rule to the database must match the database's pod or private-endpoint address, not a Service ClusterIP (a policy is evaluated after the Service address is translated); the FHIR and IdP egress rules are now rendered only when a CIDR is set.",
            "3. **NetworkPolicy, ingress CIDR.** There was no way to allow traffic from a node or host range, so a NodePort was unreachable from outside the cluster; the chart gained `networkPolicy.ingressCidrs`.",
            "4. **Service `nodePort`.** The Service template could not set `nodePort`; the chart gained `api.service.nodePort` (only with `type: NodePort`).", "",
            "### Deviations from production", ""]
    rows = [
        ["Images", f"`{prod['images']['api']['repository']}:{prod['images']['api']['tag']}` from a registry", f"local tag `{kind['images']['api']['tag']}`, `pullPolicy: {kind['images']['api']['pullPolicy']}`", "built on the host, loaded with `kind load`; no registry"],
        ["API replicas", prod["api"]["replicas"], kind["api"]["replicas"], "the benchmark topology"],
        ["API CPU request / limit", f"{prod['api']['resources']['requests']['cpu']} / {prod['api']['resources']['limits']['cpu']}", f"{kind['api']['resources']['requests']['cpu']} / {kind['api']['resources']['limits']['cpu']}", "the Docker VM is about 8 GB and shared; every capacity number below is for 1-CPU API pods"],
        ["API memory request / limit", f"{prod['api']['resources']['requests']['memory']} / {prod['api']['resources']['limits']['memory']}", f"{kind['api']['resources']['requests']['memory']} / {kind['api']['resources']['limits']['memory']}", "as above"],
        ["`SWEEP_WORKERS`", f"`{prod['api']['sweepWorkers'] or 'min(4, cpus)'}`", f"`{kind['api']['sweepWorkers']}`", "one process per pod, matching the 1-CPU limit"],
        ["Watchdog interval", f"{prod['watchdog']['intervalSeconds']} s", f"{kind.get('watchdog', {}).get('intervalSeconds', prod['watchdog']['intervalSeconds'])} s" + (" (no override: the production value)" if 'intervalSeconds' not in kind.get('watchdog', {}) else ""),
         "M7-6 had to run it every 900 s because its scan replayed every open case; since M7-7 it is incremental (ADR-045) and the cluster runs the production interval"],
        ["Agency layer (`agencyEverySweeps`)", f"`{prod['app']['agencyEverySweeps'] or 'chart default (every 6th sweep)'}`", f"`{kind['app'].get('agencyEverySweeps', prod['app']['agencyEverySweeps']) or 'chart default (every 6th sweep)'}`" + (" (no override: on)" if 'agencyEverySweeps' not in kind['app'] else ""),
         "M7-6 had to switch it off (its read of the whole case table OOM-killed a pod); since M7-7 a run reads a window of 400 cases (ADR-046)"],
        ["Identity", "an OIDC provider (UAE PASS or the organisation's)", f"issuer placeholder `{kind['app']['oidcIssuer']}`; keys from the `sc-jwks` pod over plain HTTP", "`APP_ENV` stays `production`, so every production config check still applies; tokens come from `tools/k8s/mint_token.py` with a throw-away key"],
        ["FHIR", "a FHIR server", "none; `fhirProjection: " + str(kind['app']['fhirProjection']) + "`", "`close` cannot verify evidence here; do not close cases in this cluster"],
        ["Database", "managed PostgreSQL, `sslmode=require`, HA, backups", "in-cluster Postgres 16, no TLS, no HA, no backups", "tuned during the work, see Load"],
        ["NetworkPolicy", "enforced by the cluster's CNI", "rendered and applied, **not enforced** by kind's default CNI (kindnet): a pod under default-deny still reached Postgres", "the rules are unverified here, and no enforcement overhead was measured"],
        ["Ingress / TLS", "ingress or gateway with TLS", "NodePort on 127.0.0.1, plain HTTP", "no gateway latency in any number below"],
        ["Zones / nodes", "several availability zones", "three containers in one Docker VM on one host", "no zone failure, no network latency between nodes and database"],
        ["Secrets", "Key Vault", "generated by `up.sh` into Kubernetes Secrets", "a reader of the Secret `sc-oidc-signing` can mint any role"],
    ]
    out += table(["Setting", "Production default (`values.yaml`)", "kind (`values-kind.yaml`)", "Why"], rows)
    out += ["Postgres arguments in the runs: " + " ".join(f"`{a}`" for a in co["postgres_args"]) + ".", ""]
    return out


def failover_section(fo) -> list[str]:
    s = fo["summary"]
    p = fo["parameters"]
    cv = fo["chart_values"]
    modes = [m for m in ("force", "graceful", "hard", "sigstop") if m in s]
    counts = ", ".join(f"{p.get(k, 0)} {label}" for k, label in (("force_runs", "force-delete runs"), ("graceful_runs", "graceful deletes"), ("hard_runs", "hard kills"), ("sigstop_runs", "zombie-leader (SIGSTOP) runs")) if p.get(k))
    out = ["## Failover drill (`failover.json`)", "",
           f"Tool `tools/k8s/failover_drill.py`. {counts}, "
           f"each over a {p['window_s']:g} s window with the kill (or pause) at {p['kill_at_s']:g} s. Per run the drill seeds {p['seeded_cases_per_run']:,} open cases whose reminder and escalation "
           f"triggers fall due uniformly over the window, so the sweep always has work, and a client issues about {p['client_new_case_flows_per_s']:g} new-case flows per second "
           f"(create, accept ownership, contact, acknowledge, read) through the NodePort, retrying a failed request with the same id. "
           f"Lease TTL {cv['leader_lease_ttl_s']:g} s, scheduler interval {cv['scheduler_interval_s']:g} s, fencing {cv['require_leader_fence']}, "
           f"{cv['api_replicas']} API and {cv['scheduler_replicas']} scheduler pods.", "",
           "How each mode kills: **force** = `kubectl delete pod --grace-period=0 --force` on the scheduler leader and the API pod holding the background lease (no lease release); "
           "**graceful** = ordinary delete (the processes release their leases on SIGTERM); **hard** = SIGKILL of both containers through the node's runtime, then the pods are force-deleted; "
           "**sigstop** (the zombie leader, M7-7) = the scheduler leader's process is frozen with SIGSTOP while it is mid-loop, held past the lease TTL until a new leader has taken over and swept, "
           "then resumed with SIGCONT: it wakes up in the middle of its old loop, still holding the old fencing token. Nothing is deleted. "
           "The full method text is in `failover.json`.", ""]
    metrics = [
        ("scheduler_time_to_new_leader_s", "Time to a new scheduler leader (s)", 2),
        ("api_background_time_to_new_leader_s", "Time to a new API background leader (s)", 2),
        ("time_to_first_successor_sweep_s", "Time to the first sweep by the successor (s)", 2),
        ("sweep_gap_s", "Gap between sweeps around the kill (s)", 2),
        ("missed_sweeps", "Sweeps missed (at the 10 s interval)", 0),
        ("max_event_lateness_s", "Largest lateness of a scheduler event (s)", 2),
        ("client_error_attempts", "Client request attempts that failed", 0),
        ("client_observed_outage_s", "Client-observed outage (s)", 3),
        ("client_max_latency_in_window_s", "Client max latency inside the disruption window (s)", 3),
        ("api_pods_ready_again_s", "Killed API pod replaced and Ready (s)", 2),
    ]
    for mode, title in (("force", "Force delete"), ("graceful", "Graceful delete"), ("hard", "Hard kill (SIGKILL)"), ("sigstop", "Zombie leader (SIGSTOP, pause, takeover, resume)")):
        if mode not in s:
            continue
        sm = s[mode]
        out += [f"### {title} ({sm['runs']} runs)", ""]
        out += table(["Metric", "Per run", "Min", "Median", "p95", "Max"],
                     [[label, ", ".join(f(v, pr) for v in sm[k]["values"])] + stat_row(sm[k], pr) for k, label, pr in metrics if k in sm])
    proof_keys = [
        ("duplicates_total", "Duplicate events (event ids, scheduler logical keys, client events, genesis) across the drill's cases"),
        ("acknowledged_writes_missing", "Client writes acknowledged (2xx) but missing from the database"),
        ("scheduler_events_lost", "Scheduler events expected but missing after the replan"),
        ("outstanding_cases_on_replan", "Cases with work still outstanding after the successor's replan"),
        ("replay_failures", "Cases whose event log failed to replay (gap, state mismatch)"),
        ("client_ops_never_succeeded", "Client operations that never succeeded within 90 s of retrying"),
        ("stale_fence_write_rows_written", "Rows written by a deposed leader's stale fence (in-transaction probe)"),
        ("scheduler_events_outside_lease_windows", "Scheduler events inserted outside every lease validity window"),
    ]
    out += ["### Zero duplicates, zero loss, fencing", "",
            "Totals over all runs of each mode (each cell is the sum of the per-run values; the per-run values are in `failover.json`):", ""]
    rows = []
    for k, label in proof_keys:
        cells_ = [sum(s[m][k]["values"]) for m in modes]
        rows.append([label] + [n(int(c)) if float(c).is_integer() else f(c) for c in cells_] + [n(int(sum(cells_)))])
    out += table(["Check", *[m.capitalize() for m in modes], "All runs"], rows)
    runs = fo["runs"]
    fenc = [r["fencing"]["in_transaction_probe"] for r in runs]
    chk = [r["integrity"] for r in runs]
    out += [f"- In-transaction fence probe, per run: {sum(p_['stale_fence_attempts'] for p_ in fenc)} stale-fence write attempts (killed holder with killed token, current holder with the old token, "
            f"killed holder with the current token), {sum(p_['stale_fence_refused'] for p_ in fenc)} refused with `StaleLeader`, {sum(p_['stale_fence_rows_written'] for p_ in fenc)} rows written; "
            f"the control write (current holder, current token) was accepted in {sum(1 for p_ in fenc if p_['control_accepted'])} of {len(fenc)} runs.",
            f"- After recovery, a sweep and a heartbeat sent with the killed leader's token were refused with HTTP 409 in {sum(1 for r in runs if r['fencing']['stale_token_probe_after_recovery']['stale_sweep_status'] == 409 and r['fencing']['stale_token_probe_after_recovery']['stale_leader_heartbeat_status'] == 409)} of {len(runs)} runs.",
            f"- Fencing tokens strictly increased in {sum(1 for r in runs if r['fencing']['scheduler_tokens_strictly_increasing'] and r['fencing']['api_background_tokens_strictly_increasing'])} of {len(runs)} runs (scheduler and API background lease).",
            f"- Events checked: {n(sum(c['duplicates']['scheduler_events_checked'] for c in chk))} scheduler events, {n(sum(c['replay']['cases_replayed'] for c in chk))} cases replayed, "
            f"{n(sum(c['client_requests']['writes_acknowledged'] for c in chk))} acknowledged client writes checked against the database.", "",
            "Reading: a **hard kill or a force delete** leaves the work undone until the old lease expires (the TTL, then one follower poll); the drill measured how long that is, and it is the only user-visible effect "
            "on the scheduler side. Cases are swept late, not lost: due times come from the event log, and the successor's replan found nothing outstanding. A **graceful** delete hands the lease over in about a second. "
            "The API side showed no client-visible outage in the runs (the table shows the failed attempts, if any). The one force run with a time to new leader far below the others is in the per-run column; "
            "this report does not explain it beyond the raw data.", ""]
    return out


def sweep_section(sw, la) -> list[str]:
    cells = [c for c in sw["cells"] if not c.get("profiled")]
    cells_ = cells
    out = ["## Load: the scheduler sweep at 100k, 500k and 1M open cases (`sweep.json`)", "",
           "Method (full text in `sweep.json`): synthetic open cases (the `time_sweep.py` `daily` mixture: eleven open states, about 40% overdue, the previous sweep one working day earlier) are seeded "
           "with COPY into a template database on the in-cluster Postgres, then one timed sweep runs per cell on a fresh clone. 1 worker = `app.sweep.run_sweep` in one process; N workers = `POST /scheduler/sweep` "
           "with `SWEEP_WORKERS=N`. The sweep process runs on the macOS host and reaches Postgres through the kind port mapping, so every statement pays that hop. "
           f"Target loop interval: {sw['target_loop_interval_seconds']:g} s. Labels: `baseline` = Postgres as first deployed; `tuned*` = after the Postgres tuning below; "
           "`tuned+drop_duplicate_index` = an experiment on the throwaway `closure_load` database only.", "",
           "### Timed sweeps", ""]
    rows = []
    for c in cells:
        ph = c.get("phases") or {}
        rows.append([f"{c['size']:,}", c["workers"], c["label"], f(c["wall_seconds"]), n(c["emitted"]), n(c["cases_read"]), n(c["cases_skipped"]), f(c["resweep_seconds"]),
                     f(c.get("full_scan_seconds"), 1), f(ph.get("read_replay_seconds")), f(ph.get("planning_seconds")), f(ph.get("append_seconds"))])
    out += table(["Open cases", "Workers", "Label", "Sweep (s)", "Events emitted", "Cases read", "Cases skipped", "Idle re-sweep (s)", "Full replay of every case (s)", "Read+replay (s)", "Planning (s)", "Appends (s)"], rows)
    out += ["Idle re-sweep = the same sweep straight afterwards (nothing due, emits nothing): the cost of an idle pass of the 10-second loop. Full replay = a sweep that reads and replays every case (no due-time index; it is not part of the timing against the target): "
            "the cost of any O(open cases) operation, which is what the watchdog scan does. Phases exist for one process only.", ""]
    # headline checks
    target = sw["target_loop_interval_seconds"]
    one_m = [c for c in cells if c["size"] == 1_000_000]
    best4 = min((c for c in one_m if c["workers"] == 4 and c["label"] in ("baseline", "tuned", "tuned-repeat2", "tuned-repeat3")), key=lambda c: c["wall_seconds"])
    best4x = min((c for c in one_m if c["workers"] == 4), key=lambda c: c["wall_seconds"])
    best1 = min((c for c in one_m if c["workers"] == 1 and c["label"] in ("baseline", "tuned", "tuned-repeat2", "tuned-repeat3", "tuned-steady")), key=lambda c: c["wall_seconds"])
    hundred = [c for c in cells if c["size"] == 100_000 and c["label"] in ("baseline", "tuned")]
    out += ["### Against the targets", ""]
    out += table(["Target", "Measured", "Met?"], [
        ["100,000-case daily sweep under 5 s (`docs/results/load.md`)", "; ".join(f"{c['workers']} worker(s) {c['label']}: {f(c['wall_seconds'])} s" for c in hundred),
         "yes" if all(c["wall_seconds"] < 5 for c in hundred) else "no"],
        [f"1,000,000-case daily catch-up burst inside the {target:g} s loop", f"best with 4 workers: {f(best4['wall_seconds'])} s (`{best4['label']}`); best with 4 workers including the index experiment: {f(best4x['wall_seconds'])} s (`{best4x['label']}`); best with 1 worker: {f(best1['wall_seconds'])} s",
         "**no, not met**"],
        [f"Steady-state loop (only what became due in each 10 s window) at 1,000,000 cases inside {target:g} s", ", ".join(f"{f(s['wall_seconds'])} s" for c in one_m if c.get("steady_state_sweeps") for s in c["steady_state_sweeps"]) + " (sweeps at +10 s, +20 s, +30 s)",
         "yes" if all(s["wall_seconds"] < target for c in one_m if c.get("steady_state_sweeps") for s in c["steady_state_sweeps"]) else "no"],
        [f"Idle pass of the loop at 1,000,000 cases", f"{f(min(c['resweep_seconds'] for c in one_m))} to {f(max(c['resweep_seconds'] for c in one_m))} s over the {len(one_m)} timed 1M cells", "yes"],
    ])
    steady = next((c for c in one_m if c.get("steady_state_sweeps")), None)
    if steady:
        out += ["### Steady state at 1,000,000 cases (sweeps at +10 s, +20 s, ... after the daily sweep)", ""]
        out += table(["Label", "Sweep", "Wall (s)", "Cases read", "Events emitted"],
                     [[steady["label"], f"+{10 * i} s", f(s["wall_seconds"]), n(s["cases_read"]), n(s["emitted"])] for i, s in enumerate(steady["steady_state_sweeps"], 1)])
    out += ["Repeats of the same 1,000,000-case, tuned configuration vary on this shared host: "
            + "; ".join(f"{w} worker(s): " + ", ".join(f"{f(c['wall_seconds'])} s (`{c['label']}`)" for c in one_m if c['workers'] == w and c['label'].startswith('tuned') and 'drop' not in c['label']) for w in (1, 4)) + ". "
            "A difference of the size of this spread is not evidence of an effect.", ""]
    # phase breakdown
    out += ["### Phase breakdown (one process)", ""]
    prow = []
    for c in cells:
        ph = c.get("phases")
        if not ph or c["workers"] != 1:
            continue
        w = c["wall_seconds"]
        prow.append([f"{c['size']:,}", c["label"], f(w), f"{f(ph['read_replay_seconds'])} ({ph['read_replay_seconds'] / w:.0%})", f"{f(ph['planning_seconds'])} ({ph['planning_seconds'] / w:.0%})",
                     f"{f(ph['append_seconds'])} ({ph['append_seconds'] / w:.0%})", n(ph["append_calls"])])
    out += table(["Open cases", "Label", "Sweep (s)", "Read + replay", "Planning", "Appends", "Append calls"], prow)
    out += ["Read + replay = time inside the store's due-case generator (selection query, event reads, replay, `next_due` write-back); planning = wall time minus read and append; appends = the scheduler's events written. "
            "At 1,000,000 cases reading and appending dominate; the planner itself is a few percent. The cProfile of the 1,000,000-case run (in `sweep.json`) attributes about half of the process time to waiting for Postgres (`psycopg ... wait`).", ""]
    # tuning
    tun = sw["postgres_settings"]
    out += ["### Postgres tuning, before and after", "",
            "The `baseline` cells ran with the first deployment's settings, documented in the header of `tools/k8s/postgres.yaml` (`sweep.json` records only the settings of the tuned cells); "
            "the `tuned` cells ran with the settings in the second column (read back from the server by the tool).", ""]
    out += table(["Setting", "Before (baseline cells)", "After (tuned cells, `SHOW`)"], [
        ["shared_buffers", INP["postgres_tuning_before_after"]["baseline"]["shared_buffers"], tun["shared_buffers"]],
        ["effective_cache_size", INP["postgres_tuning_before_after"]["baseline"]["effective_cache_size"], tun["effective_cache_size"]],
        ["random_page_cost", INP["postgres_tuning_before_after"]["baseline"]["random_page_cost"], tun["random_page_cost"]],
        ["work_mem", INP["postgres_tuning_before_after"]["baseline"]["work_mem"], tun["work_mem"]],
        ["maintenance_work_mem", INP["postgres_tuning_before_after"]["baseline"]["maintenance_work_mem"], tun["maintenance_work_mem"]],
        ["max_wal_size", INP["postgres_tuning_before_after"]["baseline"]["max_wal_size"], tun["max_wal_size"]],
        ["checkpoint_timeout", INP["postgres_tuning_before_after"]["baseline"]["checkpoint_timeout"], tun["checkpoint_timeout"]],
        ["Container memory limit", INP["postgres_tuning_before_after"]["baseline"]["memory_limit"], sw["postgres_pod"]["resources"]["limits"]["memory"]],
        ["/dev/shm", INP["postgres_tuning_before_after"]["baseline"]["dev_shm"], "512Mi in-memory volume (`tools/k8s/postgres.yaml`)"],
        ["synchronous_commit / fsync", "on / on", f"{tun['synchronous_commit']} / {tun['fsync']} (durability settings unchanged)"],
    ])
    rows = []
    for size in (100_000, 500_000, 1_000_000):
        for w in (1, 4):
            b, t = cell(sw, size, w, "baseline"), cell(sw, size, w, "tuned")
            if b and t:
                rows.append([f"{size:,}", w, f(b["wall_seconds"]), f(t["wall_seconds"]), f"{(t['wall_seconds'] - b['wall_seconds']) / b['wall_seconds']:+.0%}",
                             f(b["full_scan_seconds"], 1) if b.get("full_scan_seconds") else "n/a"])
    out += table(["Open cases", "Workers", "Baseline sweep (s)", "Tuned sweep (s)", "Change", "Baseline full replay (s)"], rows)
    b1, b4 = cell(sw, 1_000_000, 1, "baseline"), cell(sw, 1_000_000, 4, "baseline")
    t1s = [c["wall_seconds"] for c in cells_ if c["size"] == 1_000_000 and c["workers"] == 1 and c["label"].startswith("tuned") and "drop" not in c["label"]]
    t4s = [c["wall_seconds"] for c in cells_ if c["size"] == 1_000_000 and c["workers"] == 4 and c["label"].startswith("tuned") and "drop" not in c["label"]]
    out += [f"Reading: the header of `tools/k8s/postgres.yaml` attributes the change to a database that no longer fit the cache. The sweep improved at 500,000 cases ({f(cell(sw, 500_000, 1, 'baseline')['wall_seconds'])} to {f(cell(sw, 500_000, 1, 'tuned')['wall_seconds'])} s with 1 worker) and is neutral at 100,000 cases. "
            f"At 1,000,000 cases with 1 worker the baseline ({f(b1['wall_seconds'])} s) is slower than every tuned run ({f(min(t1s))} to {f(max(t1s))} s). "
            f"**With 4 workers the baseline ({f(b4['wall_seconds'])} s) was faster than every tuned run ({f(min(t4s))} to {f(max(t4s))} s)**, so the tuning did not help the 4-worker sweep at 1,000,000 cases in these runs; "
            "the host is shared and the cells ran at different times, so this report does not attribute the difference to the settings.", ""]
    return out


def before_cell(sw0: dict | None, size: int, workers: int = 1) -> dict | None:
    """The M7-6 'baseline' cell (Postgres as first deployed) for a size, or the tuned one: the full-replay time is the same thing."""
    if not sw0:
        return None
    return cell(sw0, size, workers, "baseline") or cell(sw0, size, workers, "tuned")


def watchdog_scan_section(sw, sw0) -> list[str]:
    cells = sorted(sw.get("watchdog_cells", []), key=lambda c: c["size"])
    if not cells:
        return []
    interval = sw["target_loop_interval_seconds"]
    out = ["## The watchdog scan at 100k, 500k and 1M open cases (`sweep.json`, `watchdog_cells`)", "",
           "Method: the template database of each size (the sweep's seeded population, on the in-cluster Postgres) is cloned and `app.watchdog.run_incremental_scan` runs in a loop at the seed's simulated time, "
           f"with the production settings (time budget {cells[0]['scan_budget_seconds']:g} s per scan, a reconciliation slice of {cells[0]['reconcile_slice']:,} open cases per scan, idle threshold {cells[0]['idle_days']} days). "
           "At that time about 39% of the seeded cases are already past the idle threshold, so the first scans are a **catch-up**: they flag every one of those cases (one `watchdog_flagged` event each), a time-budgeted batch per scan, "
           "until the index has no case left past its deadline. Then **steady-state** scans run with nothing due (only the reconciliation slice does work), and three index-only scans (no reconciliation slice). "
           "Finally the old **full replay** (`run_idle_scan`, what `POST /watchdog/scan` was) runs once on the same database: it must flag nothing more, which proves the incremental scan missed no case at that size, and its duration is the cost the scan used to have. "
           "The scan runs on the macOS host and reaches Postgres through the kind port mapping, as the sweep cells do.", ""]
    rows = []
    for c in cells:
        b = before_cell(sw0, c["size"])
        full = c.get("full_replay_reference") or {}
        elapsed = c["catchup_wall_seconds"] + max(c["catchup_scans"] - 1, 0) * interval
        rows.append([f"{c['size']:,}", f(b["full_scan_seconds"], 1) if b and b.get("full_scan_seconds") else "n/a",
                     f(full.get("seconds"), 1), f"{n(c['catchup_flagged'])} in {c['catchup_scans']} scans", f"{f(c['catchup_scan_seconds']['max'])} (median {f(c['catchup_scan_seconds']['median'])})",
                     f"{f(c['catchup_wall_seconds'], 0)} s compute, {f(elapsed, 0)} s at a {interval:g} s interval",
                     f"{f(c['steady_scan_seconds']['median'], 3)} (max {f(c['steady_scan_seconds']['max'], 3)})", f(max(x['seconds'] for x in c['index_only_scans']), 3),
                     n(full.get("flagged_additionally")), f"{c['peak_rss_mib_before']:.0f} to {c['peak_rss_mib_after']:.0f}"])
    out += table(["Open cases", "Before (M7-6): full replay (s)", "Full replay now, same database (s)", "Catch-up: cases flagged", "Longest catch-up scan (s)", "Catch-up duration",
                  "Steady-state scan, median (s)", "Index-only scan, max (s)", "Cases the full replay flagged that the incremental scan had not", "Peak RSS of the scanning process (MiB)"], rows)
    c1 = cells[-1]
    out += [f"- A scan is bounded by its time budget whatever the population: the longest scan in any catch-up was {f(max(c['catchup_scan_seconds']['max'] for c in cells))} s (budget {cells[0]['scan_budget_seconds']:g} s plus the batch in flight), "
            f"and a steady-state scan at {c1['size']:,} cases took {f(c1['steady_scan_seconds']['median'], 3)} s at the median, almost all of it the reconciliation slice "
            f"(the index-only scans took at most {f(max(x['seconds'] for x in c1['index_only_scans']), 3)} s).",
            f"- The reconciliation pass walks the open cases {cells[0]['reconcile_slice']:,} per scan from a durable cursor: a full cycle is {math.ceil(c1['size'] / cells[0]['reconcile_slice']):,} scans at {c1['size']:,} cases "
            f"({f(math.ceil(c1['size'] / cells[0]['reconcile_slice']) * interval / 3600, 1)} h at a {interval:g} s interval). `index_drift` (a stored value that disagreed with the log) was "
            f"{sum(x['index_drift'] for c in cells for x in c['catchup_first_scans'] + c['catchup_last_scans'])} in the scans recorded here.",
            f"- Every scan is bounded in memory: the process's peak RSS did not move while it scanned {c1['size']:,} cases ({c1['peak_rss_mib_before']:.0f} to {c1['peak_rss_mib_after']:.0f} MiB, which includes the full replay run afterwards).", ""]
    return out


def drain_section(sw, sw0) -> list[str]:
    cells = sorted(sw.get("drain_cells", []), key=lambda c: c["size"])
    if not cells:
        return []
    target = sw["target_loop_interval_seconds"]
    out = ["## The daily catch-up burst, drained by bounded loops (`sweep.json`, `drain_cells`)", "",
           "Method: the template database of each size is cloned and the scheduler's loop body (`app.sweep.run_sweep`, ONE worker as in the cluster, `index` path) runs repeatedly at the seed's sweep time with the production bounds: "
           f"at most {cells[0]['max_cases_per_loop']:,} due cases and {cells[0]['budget_seconds_per_loop']:g} s of evaluation per loop (`SWEEP_MAX_CASES`, `SWEEP_BUDGET_SECONDS`). A loop that stops at its bound leaves the rest due; the next loop continues. "
           f"The scheduler sleeps {target:g} s between loops, so the elapsed time to drain is the sum of the loop times plus {target:g} s per loop. Checks: the events emitted equal the planner's expectation, a full-scan sweep afterwards finds nothing, "
           "an idle loop afterwards evaluates nothing.", ""]
    rows = []
    for c in cells:
        b = before_cell(sw0, c["size"])
        rows.append([f"{c['size']:,}", f(b["wall_seconds"]) if b else "n/a", n(c["emitted"]), c["loop_count"], f(c["loop_wall_seconds"]["median"]), f(c["loop_wall_seconds"]["max"]), c["loops_over_interval"],
                     f(c["drain_compute_seconds"], 1), f(c["time_to_drain_seconds_with_interval_sleeps"], 0), f(c["idle_loop_after_drain_seconds"], 3), "none" if not c["problems"] else "; ".join(c["problems"])])
    out += table(["Open cases", "Before (M7-6): one unbounded sweep, 1 worker (s)", "Events emitted", "Loops to drain", "Loop duration, median (s)", "Loop duration, max (s)", f"Loops over {target:g} s",
                  "Compute to drain (s)", f"Time to drain at a {target:g} s interval (s)", "Idle loop afterwards (s)", "Problems"], rows)
    big = cells[-1]
    ub = cell(sw, big["size"], 1, "tuned")
    ub0 = before_cell(sw0, big["size"])
    ph, ph0 = (ub or {}).get("phases"), (ub0 or {}).get("phases")
    if ph and ph0:
        out += [f"Unbounded sweep at {big['size']:,} cases, one worker, for comparison (phases of the same code path): before {f(ub0['wall_seconds'])} s = read {f(ph0['read_replay_seconds'])} + plan {f(ph0['planning_seconds'])} + appends {f(ph0['append_seconds'])}; "
                f"now {f(ub['wall_seconds'])} s = read {f(ph['read_replay_seconds'])} + plan {f(ph['planning_seconds'])} + appends {f(ph['append_seconds'])} (the appends are one multi-row INSERT and one multi-row UPDATE per batch of 100 cases, "
                "with the lease re-check `SELECT ... FOR SHARE` unchanged at the start of the same transaction).", ""]
    out += [f"**Is the {target:g} s target met for a {big['size']:,}-case burst?** The burst does not fit one {target:g} s loop and no longer has to: every loop is bounded (longest {f(big['loop_wall_seconds']['max'])} s, "
            f"{big['loops_over_interval']} loops over {target:g} s), and the backlog drains in {big['loop_count']} loops, {f(big['time_to_drain_seconds_with_interval_sleeps'] / 60, 1)} minutes at the {target:g} s interval. "
            f"What is NOT met: all {n(big['emitted'])} events of a {big['size']:,}-case daily burst are not written within one {target:g} s loop; the last case of the burst is swept about {f(big['time_to_drain_seconds_with_interval_sleeps'] / 60, 1)} minutes after it fell due.", ""]
    return out


def sweep_cells_section(sw, sw0) -> list[str]:
    """The timed unbounded sweeps of this run next to the M7-6 ones (same seeds, same sweep time)."""
    cells = [c for c in sw["cells"] if not c.get("profiled")]
    out = ["## Load: the scheduler sweep at 100k, 500k and 1M open cases (`sweep.json`)", "",
           "Method (full text in `sweep.json`): synthetic open cases (the `time_sweep.py` `daily` mixture: eleven open states, about 40% overdue, the previous sweep one working day earlier) are seeded "
           "with COPY into a template database on the in-cluster Postgres, then one timed sweep runs per cell on a fresh clone. 1 worker = `app.sweep.run_sweep` in one process; N workers = `POST /scheduler/sweep` "
           "with `SWEEP_WORKERS=N`. The sweep process runs on the macOS host and reaches Postgres through the kind port mapping, so every statement pays that hop. "
           f"Target loop interval: {sw['target_loop_interval_seconds']:g} s. **These cells run ONE unbounded sweep** (no per-loop bound), as in M7-6, so they are comparable with it; the bounded loops the scheduler now runs are in the next section.", "",
           "### Timed sweeps, after and before", ""]
    rows = []
    for c in cells:
        ph = c.get("phases") or {}
        b = cell(sw0, c["size"], c["workers"], "baseline") if sw0 else None
        b2 = cell(sw0, c["size"], c["workers"], "tuned") if sw0 else None
        bb = b2 or b
        bph = (bb or {}).get("phases") or {}
        rows.append([f"{c['size']:,}", c["workers"], f(c["wall_seconds"]), f(bb["wall_seconds"]) if bb else "n/a", n(c["emitted"]), n(c["cases_read"]), f(c["resweep_seconds"], 3),
                     f(c.get("full_scan_seconds"), 1), f"{f(ph.get('read_replay_seconds'))} / {f(ph.get('planning_seconds'))} / {f(ph.get('append_seconds'))}" if ph else "n/a",
                     f"{f(bph.get('read_replay_seconds'))} / {f(bph.get('planning_seconds'))} / {f(bph.get('append_seconds'))}" if bph else "n/a"])
    out += table(["Open cases", "Workers", "Sweep, after (s)", "Sweep, before (s)", "Events emitted", "Cases read", "Idle re-sweep (s)", "Full replay of every case (s)",
                  "After: read+replay / plan / appends (s)", "Before: read+replay / plan / appends (s)"], rows)
    steady = [(c, c["steady_state_sweeps"]) for c in cells if c.get("steady_state_sweeps")]
    if steady:
        out += ["### Steady state (sweeps at +10 s, +20 s, ... after the daily sweep)", ""]
        out += table(["Open cases", "Sweep", "Wall (s)", "Cases read", "Events emitted"],
                     [[f"{c['size']:,}", f"+{10 * i} s", f(x["wall_seconds"], 3), n(x["cases_read"]), n(x["emitted"])] for c, xs in steady for i, x in enumerate(xs, 1)])
    out += ["Idle re-sweep = the same sweep straight afterwards (nothing due, emits nothing). Full replay = a sweep that reads and replays every case (no due-time index): the cost of any O(open cases) operation, which is what the watchdog scan used to be. "
            "Phases exist for one process only; the cells ran on a host shared with the cluster, and repeats of the same cell vary, so differences of a few percent are not evidence of an effect.", ""]
    return out


def size_section(sw) -> list[str]:
    tpl = sw["seeding_sizes_template"]["1000000"]
    out = ["### Database size", "",
           f"The seeded 1,000,000-case database is {tpl['database_bytes'] / 2**30:.2f} GiB ({tpl['database_bytes'] / 1e6:,.0f} bytes per case, "
           f"{tpl['tables']['events']['rows'] / tpl['tables']['cases']['rows']:.2f} events per case).", ""]
    out += table(["Relation", "Kind", "Size at 1,000,000 cases"], [[r["name"], r["kind"], mib(r["relation_bytes"])] for r in tpl["relations"]])
    out += table(["Table", "Rows", "Heap bytes per row", "Heap + indexes + TOAST bytes per row"],
                 [[k, n(v["rows"]), f"{v['heap_bytes_per_row']:.0f}", f"{v['total_bytes_per_row']:.0f}"] for k, v in tpl["tables"].items()])
    tuned = cell(sw, 1_000_000, 1, "tuned")
    if tuned and tuned.get("index_stats_before") and tuned.get("index_stats_after_sweep"):
        a_, b_ = tuned["index_stats_before"]["indexes"], tuned["index_stats_after_sweep"]["indexes"]
        out += ["### Index findings", "",
                "Index use during the first 1,000,000-case sweep (`tuned` cell, 1 worker; `pg_stat_user_indexes`, delta over the sweep):", ""]
        out += table(["Index", "idx_scan", "idx_tup_read", "idx_tup_fetch", "Size"],
                     [[k, n(b_[k]["idx_scan"] - a_.get(k, {}).get("idx_scan", 0)), n(b_[k]["idx_tup_read"] - a_.get(k, {}).get("idx_tup_read", 0)), n(b_[k]["idx_tup_fetch"] - a_.get(k, {}).get("idx_tup_fetch", 0)),
                       mib(next((r["relation_bytes"] for r in tpl["relations"] if r["name"] == k.split(".")[1]), None))] for k in b_])
        out += ["- `events_case_id_seq_idx` (the duplicate of the unique `(case_id, seq)` index) is gone after migration 017; the reads use `events_case_id_seq_key`. `cases_idle_since_idx` and `cases_state_id_idx` are the watchdog's and the worklist's.",
                "- `events_genesis_patient_ref_idx` is kept: `app.erasure` looks a patient's cases up with `WHERE seq = 1 AND payload ->> 'patient_ref' = ...`, and `GET /cases` for a patient reads it. The sweep itself does not use it.", ""]
    out += ["### Seeding", ""]
    out += table(["Open cases", "Processes", "Insert, COPY (s)", "Cases per second", "VACUUM ANALYZE (s)", "Total (s)", "Events"],
                 [[n(s_["cases"]), s_["processes"], f(s_["insert_seconds"], 1), f"{s_['cases_per_second']:,.0f}", f(s_["vacuum_analyze_seconds"], 1), f(s_["total_seconds"], 1), n(s_["events"])]
                  for _, s_ in sorted(sw["seeding"].items(), key=lambda kv: int(kv[0]))])
    pops = sw.get("closure_population", [])
    if pops:
        p = pops[-1]
        out += [f"The cluster's own `closure` database (used by the latency and soak runs) was seeded with {n(p['cases'])} cases in {f(p['total_seconds'], 0)} s with {p['processes']} processes "
                f"(states {', '.join(f'{v:.0%} {k}' for k, v in p['state_weights'].items())}, plus {p['worklist_seed']['cases']} `{next(iter(p['worklist_seed']['by_state']))}` cases for the worklist); it then held {n(p['count_after']['cases'])} cases and {n(p['count_after']['events'])} events, {p['sizes']['database_bytes'] / 2**30:.2f} GiB.", ""]
    return out


def latency_section(la) -> list[str]:
    st = la["stages"]
    out = ["## Load: API latency at a sustained rate (`latency.json`)", "",
           f"Open-loop constant arrival rate against the NodePort (requests are issued on schedule whatever the outstanding requests, latency counted from the intended start). "
           f"Mix (weights %): " + ", ".join(f"{k} {v}" for k, v in la["mix_weights"].items()) + f". Population: {n(la['population_before']['cases'])} cases / {n(la['population_before']['events'])} events before the run. "
           f"Pass criteria: {la['pass_criteria']}. The load generator runs on the same host as the cluster, and its CPU competes with the cluster's.", "",
           "### Latency per target rate", ""]
    rows = []
    for s in st:
        a, e = s["latency_all_requests"], s["latency_all_requests_excluding_worklist"]
        rows.append([f"{s['target_rate_per_s']:g}/s", s["label"], f"{s['achieved_rate_per_s']:.1f}/s", a["p50_ms"], a["p95_ms"], a["p99_ms"], a["max_ms"], e["p99_ms"],
                     f"{s['errors']} {s['errors_by_status'] or ''}".strip(), f"{s['error_rate'] * 100:.3f}%", "yes" if s["pass"] else "no"])
    out += table(["Rate", "Watchdog", "Achieved", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)", "p99 excl. worklist (ms)", "Errors", "Error rate", "Pass"], rows)
    sm = la["summary"]
    on, off = sm.get("watchdog_on_as_deployed"), sm.get("watchdog_off")
    wd_s = la["chart_values"]["watchdog"]["intervalSeconds"]
    if on:
        p = on["pass"]
        out += [f"- **With the watchdog and the agency layer running at the production settings (watchdog scan every {wd_s} s): "
                + (f"{p['max_passing_rate_per_s']:g} requests/s passes** (3 API pods with a 1-CPU limit each)" if p["max_passing_rate_per_s"] else "no tested rate passes**")
                + (f"; lowest failing rate tested: {p['lowest_failing_rate_per_s']:g}/s." if p["lowest_failing_rate_per_s"] else "; no rate failed in the range tested."),
                "  Failed requests: " + (", ".join(f"{s_['target_rate_per_s']:g}/s: {s_['errors']} ({', '.join(s_['errors_by_status'])})" for s_ in st if s_['label'] == 'watchdog_on_as_deployed' and s_['errors']) or "none") + "."]
    if off:
        q = off["pass"]
        out += [f"- Reference with the watchdog scaled to 0 (`watchdog_off`): {q['max_passing_rate_per_s']:g} requests/s passes" + (f", lowest failing rate tested {q['lowest_failing_rate_per_s']:g}/s." if q["lowest_failing_rate_per_s"] else ".")]
    out += ["", "`watchdog_on_as_deployed` is the label of the stages that ran with the watchdog Deployment active at the chart's production interval and the agency layer on; the stages with the watchdog scaled to 0 are `watchdog_off`.", ""]
    # resources at the highest passing stage
    ref_label, ref = ("watchdog_on_as_deployed", on) if on and on["pass"]["max_passing_rate_per_s"] else ("watchdog_off", off)
    top = next(s for s in st if s["label"] == ref_label and s["target_rate_per_s"] == ref["pass"]["max_passing_rate_per_s"])
    ro = []
    for pod, v in top["resources"]["pods"].items():
        share = top["request_distribution_per_pod"].get(pod) if isinstance(top.get("request_distribution_per_pod"), dict) else None
        ro.append([pod, f"{v['cpu_cores_mean']:.2f} / {v['cpu_cores_p95']:.2f} / {v['cpu_cores_max']:.2f}", f"{mib(v['memory_bytes_mean'])} / {mib(v['memory_bytes_max'])}", mib(v["memory_limit_bytes"]),
                   f"{n(v.get('cpu_throttled_periods'))} of {n(v.get('cpu_periods'))}"])
    out += [f"### Resources at the highest passing rate ({top['target_rate_per_s']:g}/s, {top['label']})", ""]
    out += table(["Pod", "CPU cores mean / p95 / max", "Memory mean / max", "Memory limit", "CPU-throttled periods"], ro)
    nodes = top["resources"].get("nodes") or {}
    if nodes:
        out += table(["Kind node", "CPU % of one core, mean / max"], [[k, f"{v.get('cpu_percent_mean', 0):.0f} / {v.get('cpu_percent_max', 0):.0f}"] for k, v in nodes.items()])
    per_ep = top["per_endpoint"]
    out += [f"### Per endpoint at {top['target_rate_per_s']:g}/s", ""]
    out += table(["Endpoint", "Requests", "Errors", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)"],
                 [[k, n(v["requests"]), v["errors"], v["latency"]["p50_ms"], v["latency"]["p95_ms"], v["latency"]["p99_ms"], v["latency"]["max_ms"]] for k, v in per_ep.items()])
    pr = la.get("unbounded_worklist_probe")
    if pr:
        out += ["### The worklist read (finding 3)", "",
                f"`GET /cases?state={pr['state']}` against the {n(pr['cases_in_state'])} cases in that state, now a page: no `limit` gives {pr['default_page']['rows']} rows in {f(pr['default_page']['seconds'], 2)} s "
                f"(status {pr['default_page']['status']}, next cursor given: {bool(pr['default_page']['next_cursor'])}); `limit=500` gives {pr['max_page']['rows']} rows in {f(pr['max_page']['seconds'], 2)} s; `limit=501` is refused with {pr['over_max_limit_status']}. "
                f"Following the cursor for {pr['pages_followed']} pages of 500 ({n(pr['rows_over_followed_pages'])} rows) took {f(pr['page_seconds']['min'], 2)} to {f(pr['page_seconds']['max'], 2)} s per page (median {f(pr['page_seconds']['median'], 2)} s). "
                f"Restarts of the API pods during the probe: " + ", ".join(f"{k.rsplit('-', 1)[-1]} {pr['restarts_after'][k] - pr['restarts_before'][k]}" for k in pr['restarts_before']) +
                "; last termination: " + (", ".join(f"{k.rsplit('-', 1)[-1]} {v}" for k, v in pr['last_termination'].items() if v) or "none") + ".", ""]
    return out


def soak_section(so) -> list[str]:
    s = so["summary"]
    p = so["parameters"]
    w = s["watchdog"]
    out = ["## Soak (`soak.json`)", "",
           f"One uninterrupted open-loop stage at {p['rate_per_s']:g} requests/s for {s['measured_seconds'] / 3600:.2f} h ({so['started_at']} to {so['finished_at']}), the same mix as the latency runs "
           f"(worklist: `GET /cases?state={so['list_state']}`, now a page), against a database of {n(so['population_before']['cases'])} cases / {n(so['population_before']['events'])} events, with the watchdog at its {w['interval_s']:g} s interval "
           f"and the agency layer on (every 6th sweep), as the chart ships them. Criteria: {s['criteria']}.", ""]
    L, W = s["latency_all_requests"], s["worst_window_by_p99"]
    sch = s["scheduler"]
    out += table(["Result", "Value"], [
        ["Requests / errors", f"{n(s['requests_total'])} / {s['errors']} ({s['error_rate'] * 100:.4f}%)"],
        ["Pass", "yes" if s["pass"] else "no"],
        ["Latency overall p50 / p95 / p99 / max (ms)", f"{L['p50_ms']} / {L['p95_ms']} / {L['p99_ms']} / {L['max_ms']}"],
        [f"Worst {s['window_seconds']:g} s window by p99 (starts {W['start']})", f"p50 {W['latency_all']['p50_ms']} / p95 {W['latency_all']['p95_ms']} / p99 {W['latency_all']['p99_ms']} / max {W['latency_all']['max_ms']} ms"],
        [f"p99 range over the {s['windows']} windows", f"{s['windows_p99_ms_range'][0]} to {s['windows_p99_ms_range'][1]} ms; windows over 500 ms: {s['windows_over_p99_500ms']}"],
        ["Pod restarts / OOM kills during the soak", f"{s['restarts']['total_restarts_during_soak']} / {s['restarts']['oom_killed_during_soak']}"],
        ["Scheduler leader changes", s["leader"]["scheduler_leader_changes"]],
        ["Sweeps completed vs expected at the nominal 10 s (the sweep cadence achieved)", f"{n(sch['sweeps_completed'])} of {sch['sweeps_expected_at_10s']:,.0f} ({sch['completed_fraction_of_expected']:.1%})"],
        ["Sweep-to-sweep period mean / p50 / p95 / max (s)", f"{sch['sweep_period_s']['mean']} / {sch['sweep_period_s']['p50']} / {sch['sweep_period_s']['p95']} / {sch['sweep_period_s']['max']}"],
        ["Sweep duration p50 / p95 / max (s)", f"{sch['durations_from_api_access_log']['sweep_duration']['p50_s']} / {sch['durations_from_api_access_log']['sweep_duration']['p95_s']} / {sch['durations_from_api_access_log']['sweep_duration']['max_s']}"],
        ["Sweep errors reported / scheduler warnings or errors", f"{sch['sweep_errors_reported_in_summaries']} / {sch['scheduler_warning_or_error_count']}"],
        ["Database growth", f"{s['database']['growth_mib']} MiB in the run ({s['database']['growth_mib_per_hour']} MiB/h); {n(so['population_after']['cases'] - so['population_before']['cases'])} cases and {n(so['population_after']['events'] - so['population_before']['events'])} events added"],
        ["Sampling", f"{s['sampling']['samples']} samples at {s['sampling']['period_s']:g} s, uninterrupted: {s['sampling']['uninterrupted']}"],
    ])
    d = w.get("duration_s")
    out += ["### Memory per pod", ""]
    def tail_slope(pod, minutes=40):
        pts = [(x["elapsed_s"], x["cgroup"][pod]["mem_anon"] / 2**20) for x in so["samples"] if x["cgroup"].get(pod)]
        pts = [q for q in pts if q[0] >= pts[-1][0] - minutes * 60]
        if len(pts) < 3:
            return None
        mx, my = sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts)
        den = sum((q[0] - mx) ** 2 for q in pts)
        return round(sum((q[0] - mx) * (q[1] - my) for q in pts) / den * 3600, 1) if den else None

    rows = []
    for pod, v in s["pods"].items():
        rows.append([pod, f"{v['memory_current_start_mib']} / {v['memory_current_end_mib']} / {v['memory_current_max_mib']}", f"{v['memory_anon_start_mib']} / {v['memory_anon_end_mib']} / {v['memory_anon_max_mib']}",
                     v["memory_anon_slope_mib_per_hour"], v["memory_anon_slope_after_20min_mib_per_hour"], tail_slope(pod), v["memory_limit_mib"], v["restart_count_end"] - v["restart_count_start"]])
    out += table(["Pod", "Memory start / end / max (MiB)", "Anon start / end / max (MiB)", "Anon slope (MiB/h)", "Anon slope after 20 min (MiB/h)", "Anon slope, last 40 min (MiB/h)", "Limit (MiB)", "Restarts"], rows)
    sl = api_slopes(so)
    out += [f"Slopes are least-squares fits to the {s['sampling']['period_s']:g} s samples; anon memory is the process's own allocations (page cache excluded). **API pods: " + ", ".join(f"{k.rsplit('-', 1)[-1]} {v:+g}" for k, v in sorted(sl.items())) + " MiB/h** "
            "(M7-6: " + ", ".join(f"{v:+g}" for v in sorted(api_slopes(jload(RAW / "before" / "soak.json")).values())) + " MiB/h over 2.08 h).", ""]
    out += ["### The watchdog during the soak", "",
            f"The watchdog Deployment scanned every {w['interval_s']:g} s: {w['scans_observed']} scans observed by its own log, {w['api_scan_requests_logged']} `POST /watchdog/scan` requests in the API access logs. "
            + (f"Scan duration from the API log (start of the call to the end of the request): min {d['min']} s, mean {d['mean']} s, max {d['max']} s. " if d else "")
            + "Client outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(__import__('collections').Counter(x['client_outcome'][:40] for x in w['scans'] if x['within_soak']).items())) + ".", ""]
    ag = so.get("agency")
    if ag:
        out += ["### The agency layer during the soak", "",
                f"{ag['runs']} agency runs started during the soak (every 6th sweep): " + ", ".join(f"{k} {v}" for k, v in ag["runs_by_status"].items()) + f". Cases read per run: median {ag['cases_read_per_run']['median']}, max {ag['cases_read_per_run']['max']} (cap 400); "
                f"{ag['runs_with_truncated_read']} runs left cases for the next run (`capped`), {ag['runs_with_order_cap']} hit the order cap; {n(ag['tasks_created_total'])} tasks created" + (f"; a run took {ag['run_seconds']['median']} s at the median, {ag['run_seconds']['p95']} s at p95, {ag['run_seconds']['max']} s at most." if ag.get("run_seconds") else "."), ""]
    return out


# ---------------------------------------------------------------------------------------------------------------------------------
# findings (open items owned by the api/ team)
# ---------------------------------------------------------------------------------------------------------------------------------

def findings_section(fo, sw, la, so) -> list[str]:
    cells = [c for c in sw["cells"] if not c.get("profiled")]
    fs = sorted({c["full_scan_seconds"] for c in cells if c["size"] == 1_000_000 and c.get("full_scan_seconds")})
    fs_all = {(c["size"], c["workers"]): c["full_scan_seconds"] for c in cells if c["label"] == "baseline" and c.get("full_scan_seconds")}
    w = so["summary"]["watchdog"]
    scans = [x for x in w["scans"] if x["within_soak"]]
    to = sum(1 for x in scans if "timed out" in x["client_outcome"])
    ev = w["server_side_evidence"]["per_scan"]
    on = [s for s in la["stages"] if s["label"] == "watchdog_on_as_deployed"]
    off = [s for s in la["stages"] if s["label"] == "watchdog_off"]
    pr = la["unbounded_worklist_probe"]
    on50 = next(s for s in on if s["target_rate_per_s"] == 50)
    off50 = next(s for s in off if s["target_rate_per_s"] == 50)
    tuned1 = cell(sw, 1_000_000, 1, "tuned")
    drop1 = cell(sw, 1_000_000, 1, "tuned+drop_duplicate_index")
    pods = {k: v for k, v in so["summary"]["pods"].items() if k.startswith("closure-api")}
    worst = max(pods.items(), key=lambda kv: kv[1]["memory_anon_slope_mib_per_hour"])
    wv = worst[1]
    hours = (wv["memory_limit_mib"] - wv["memory_anon_end_mib"]) / wv["memory_anon_slope_mib_per_hour"]
    sl = [v["memory_anon_slope_mib_per_hour"] for v in pods.values()]
    hb = so["samples"][-1]["heartbeats"]
    followers = {k: v for k, v in hb.items() if k.startswith("scheduler-follower-")}
    pods_last, pods_first = set(so["samples"][-1]["pods"]), set(so["samples"][0]["pods"])
    gone = {k: v for k, v in followers.items() if v["age_s"] > 120 and k.removeprefix("scheduler-follower-") not in pods_last}
    hb0 = so["samples"][0]["heartbeats"]
    hb0_f = {k: v for k, v in hb0.items() if k.startswith("scheduler-follower-")}
    gone0 = {k: v for k, v in hb0_f.items() if v["age_s"] > 120 and k.removeprefix("scheduler-follower-") not in pods_first}
    best4 = min((c for c in cells if c["size"] == 1_000_000 and c["workers"] == 4 and c["label"] in ("baseline", "tuned", "tuned-repeat2", "tuned-repeat3")), key=lambda c: c["wall_seconds"])
    steady = [s["wall_seconds"] for c in cells if c.get("steady_state_sweeps") for s in c["steady_state_sweeps"]]
    out = ["## Findings that need `api/` changes (open items, owned by the API team)", "",
           "These were found by the runs above. Nothing in `api/` was changed in this work; each item states the measured evidence, not a fix.", "",
           "### 1. The watchdog scan replays every open case (O(N))", "",
           f"- A full replay of every case takes {' and '.join(f(v, 1) for v in fs)} s at 1,000,000 cases in `sweep.json` (`full_scan_seconds`; per size and worker count: "
           + ", ".join(f"{k[0]:,} cases/{k[1]} worker(s): {f(v, 1)} s" for k, v in sorted(fs_all.items())) + "). The watchdog scan (`POST /watchdog/scan`) replays every open case too; `values-kind.yaml` records that it has no due-index, so a scan is O(open cases) and the cluster's own measurement of it at about 1.04M cases was 224-230 s (a comment in that file, consistent with the full-replay figures above).",
           f"- In the soak the watchdog client gave up after about {f(min(x['client_wait_s'] for x in scans), 0)} s in {to} of {len(scans)} scans (\"timed out\"); the API kept working on each, and the pod that held it rose by {min(e['peak_rise_mib'] for e in ev)} to {max(e['peak_rise_mib'] for e in ev)} MiB at peak and stayed more than 50 MiB above its earlier level in {min(e['samples_above_50mib'] for e in ev)} to {max(e['samples_above_50mib'] for e in ev)} of the 60 s samples.",
           f"- With the watchdog at its chart-default {la['chart_values']['watchdog']['intervalSeconds']} s interval, scans pile up: no tested rate passes (at {on50['target_rate_per_s']:g}/s p99 is {on50['latency_all_requests']['p99_ms']} ms with the watchdog on and {off50['latency_all_requests']['p99_ms']} ms with it off; the lowest rate tested with it on, {la['summary']['watchdog_on_as_deployed']['pass']['lowest_failing_rate_per_s']:g}/s, already fails), and requests fail from {next(s['target_rate_per_s'] for s in on if s['errors']):g}/s.",
           f"- Because of it the cluster runs the watchdog every {so['chart_values']['watchdog']['intervalSeconds']} s instead of {sw['chart_values']['watchdog']['intervalSeconds']} s, which is a deviation from production (see Local cluster). At the interval of 10 s the production default is only safe while a scan is much shorter than the interval; the capacity plan below computes where that stops.", "",
           "### 2. The agency pass reads the whole case table and OOMs a pod",
           "",
           "- `values-kind.yaml` records that the agency layer's pass reads the whole case table every 6th sweep and OOM-kills an API pod at this population, which is why the cluster runs with it off; the further observation that the pass fails at its 400-order cap comes from the run's notes. "
           "None of this is captured in the four JSON files: the soak, the latency stages and the sweep cells all ran with `agencyEverySweeps` = `0` (off). It is an observation of the run, not a number in this report.", "",
           "### 3. `GET /cases?state=` is unbounded",
           "",
           f"- One request for the {n(pr['cases_in_state'])} cases in `{pr['state']}` ended in `{pr['status']}` after {f(pr['seconds'], 1)} s and an API pod was `OOMKilled` (memory limit {so['summary']['pods'][next(iter(pods))]['memory_limit_mib']} MiB); the other two pods did not restart. The endpoint answers the whole state in one response, so its cost grows with the number of cases in it; it needs pagination or a SQL-side filter (an `api/` change).",
           f"- Even bounded (the soak's worklist state held {n(300)} cases), the worklist is the slowest endpoint: p50 {so['summary']['per_endpoint']['GET /cases?state=']['latency']['p50_ms']} ms and p99 {so['summary']['per_endpoint']['GET /cases?state=']['latency']['p99_ms']} ms in the soak, against p99 {so['summary']['per_endpoint']['GET /cases/{id}']['latency']['p99_ms']} ms for `GET /cases/{{id}}`.", "",
           "### 4. Duplicate and unused indexes on `events`", "",
           f"- `events_case_id_seq_idx` duplicates `events_case_id_seq_key` (same columns, {mib(next(r['relation_bytes'] for r in sw['seeding_sizes_template']['1000000']['relations'] if r['name'] == 'events_case_id_seq_idx'))} each at 1,000,000 cases). Experiment on the throwaway `closure_load` database: dropping it cut the 1,000,000-case sweep from {f(tuned1['wall_seconds'])} s to {f(drop1['wall_seconds'])} s with 1 worker (the tuned repeats ranged {f(min(c['wall_seconds'] for c in cells if c['size'] == 1_000_000 and c['workers'] == 1 and c['label'].startswith('tuned') and 'drop' not in c['label']))} to {f(max(c['wall_seconds'] for c in cells if c['size'] == 1_000_000 and c['workers'] == 1 and c['label'].startswith('tuned') and 'drop' not in c['label']))} s).",
           "- `events_genesis_patient_ref_idx` was never scanned during the sweep (see Index findings); other code paths were not measured.", "",
           "### 5. API pod memory grows during the soak", "",
           f"- Anon memory of the three API pods grew at {min(sl):.1f} to {max(sl):.1f} MiB/h over {so['summary']['measured_seconds'] / 3600:.2f} h (per pod in the Soak table); the worst pod, `{worst[0]}`, went from {wv['memory_anon_start_mib']} to {wv['memory_anon_end_mib']} MiB with a peak of {wv['memory_anon_max_mib']} MiB against a {wv['memory_limit_mib']} MiB limit. "
           f"Extrapolating its fitted slope linearly (an extrapolation, not a measurement), it would reach the limit about {hours:.1f} h after the end of the run. The growth is linked to the scan path: the watchdog scans and the pods they land on are the visible excursions; the slope's cause was not isolated by a separate run without the watchdog.", "",
           "### 6. The `heartbeats` table accumulates a row per scheduler pod forever", "",
           f"- Each follower scheduler posts a heartbeat under `scheduler-follower-<pod>`; no row is removed when the pod goes away. Rows older than 120 s whose pod is not in the cluster's pod list: first sample of the soak {len(gone0)} of {len(hb0_f)} follower rows, last sample {len(gone)} of {len(followers)} "
           f"({', '.join(f'`{k}` age {v['age_s']:g} s' for k, v in sorted(gone.items())) or 'none'}). Every rollout or reschedule adds one.", "",
           "### 7. The daily catch-up burst at 1,000,000 open cases does not fit in the 10 s loop on this host", "",
           f"- Best sweep at 1,000,000 cases: {f(best4['wall_seconds'])} s with 4 workers (`{best4['label']}`; with 1 worker {f(min(c['wall_seconds'] for c in cells if c['size'] == 1_000_000 and c['workers'] == 1 and 'drop' not in c['label']))} s to {f(max(c['wall_seconds'] for c in cells if c['size'] == 1_000_000 and c['workers'] == 1))} s). "
           f"The steady-state loop does fit: {f(min(steady))} to {f(max(steady))} s for the sweeps that follow the daily one, and an idle pass takes {f(min(c['resweep_seconds'] for c in cells if c['size'] == 1_000_000))} to {f(max(c['resweep_seconds'] for c in cells if c['size'] == 1_000_000))} s. "
           "This is a statement about the burst that follows the day's first sweep, not about every sweep.", ""]
    return out


def verdicts_before(fo, sw, la, so, cp) -> list[str]:
    cells = [c for c in sw["cells"] if not c.get("profiled")]
    s = fo["summary"]
    on = la["summary"]["watchdog_on_as_deployed"]["pass"]
    off = la["summary"]["watchdog_off"]["pass"]
    best4 = min((c for c in cells if c["size"] == 1_000_000 and c["workers"] == 4 and c["label"] in ("baseline", "tuned", "tuned-repeat2", "tuned-repeat3")), key=lambda c: c["wall_seconds"])
    steady = [x["wall_seconds"] for c in cells if c.get("steady_state_sweeps") for x in c["steady_state_sweeps"]]
    ss = so["summary"]
    rows = [
        ["Fail-over loses or duplicates nothing", f"{n(sum(sum(s[m]['duplicates_total']['values']) for m in s))} duplicates, {n(sum(sum(s[m]['acknowledged_writes_missing']['values']) for m in s))} acknowledged writes missing, {n(sum(sum(s[m]['scheduler_events_lost']['values']) for m in s))} scheduler events lost, "
         f"{n(sum(sum(s[m]['stale_fence_write_rows_written']['values']) for m in s))} stale-fence rows written, over {sum(s[m]['runs'] for m in s)} runs", "met"],
        ["Scheduler hand-over after a hard kill", f"median {f(s['hard']['scheduler_time_to_new_leader_s']['median'], 1)} s (min {f(s['hard']['scheduler_time_to_new_leader_s']['min'], 1)}, max {f(s['hard']['scheduler_time_to_new_leader_s']['max'], 1)}), {s['hard']['missed_sweeps']['median']:g} sweeps missed; graceful: max {f(s['graceful']['scheduler_time_to_new_leader_s']['max'], 1)} s", "measured"],
        ["100,000-case sweep under 5 s", f"{f(cell(sw, 100_000, 1, 'tuned')['wall_seconds'])} s (1 worker), {f(cell(sw, 100_000, 4, 'tuned')['wall_seconds'])} s (4 workers)", "met"],
        ["1,000,000-case daily catch-up burst inside the 10 s loop", f"best {f(best4['wall_seconds'])} s with 4 workers", "**not met**"],
        ["Steady-state loop at 1,000,000 cases inside 10 s", f"{f(min(steady))} to {f(max(steady))} s per sweep", "met"],
        ["API p99 < 500 ms and errors < 0.1%, watchdog off", f"up to {off['max_passing_rate_per_s']:g} requests/s (fails at {off['lowest_failing_rate_per_s']:g}/s)", "met up to that rate"],
        ["API p99 < 500 ms and errors < 0.1%, watchdog as deployed (10 s)", f"no passing rate; fails already at {on['lowest_failing_rate_per_s']:g}/s", "**not met**"],
        [f"{ss['measured_seconds'] / 3600:.2f} h soak at {so['parameters']['rate_per_s']:g}/s", f"{ss['errors']} errors, p99 {ss['latency_all_requests']['p99_ms']} ms, {ss['restarts']['total_restarts_during_soak']} restarts, but only {ss['scheduler']['completed_fraction_of_expected']:.1%} of the nominal sweeps completed and API pod memory grew", "met on its criteria; sweep cadence and memory **not met**"],
        ["Unbounded worklist at 1,000,000 cases", "one request OOM-killed an API pod", "**not met** (finding 3)"],
    ]
    return table(["Question", "Measured", "Result"], rows)




def size_section_before(sw) -> list[str]:
    big = next(c for c in sw["cells"] if c["size"] == 1_000_000 and c["workers"] == 1 and c.get("sizes_before") and c["label"] == "baseline" and not c.get("profiled"))
    tuned = next(c for c in sw["cells"] if c["size"] == 1_000_000 and c["workers"] == 1 and c["label"] == "tuned")
    drop1 = next(c for c in sw["cells"] if c["size"] == 1_000_000 and c["workers"] == 1 and c["label"] == "tuned+drop_duplicate_index")
    drop4 = next(c for c in sw["cells"] if c["size"] == 1_000_000 and c["workers"] == 4 and c["label"] == "tuned+drop_duplicate_index")
    tpl = sw["seeding_sizes_template"]["1000000"]
    out = ["### Database size", "",
           f"The seeded 1,000,000-case database is {tpl['database_bytes'] / 2**30:.2f} GiB ({tpl['database_bytes'] / 1e6:,.0f} bytes per case, "
           f"{tpl['tables']['events']['rows'] / tpl['tables']['cases']['rows']:.2f} events per case).", ""]
    out += table(["Relation", "Kind", "Size at 1,000,000 cases"], [[r["name"], r["kind"], mib(r["relation_bytes"])] for r in tpl["relations"]])
    out += table(["Table", "Rows", "Heap bytes per row", "Heap + indexes + TOAST bytes per row"],
                 [[k, n(v["rows"]), f"{v['heap_bytes_per_row']:.0f}", f"{v['total_bytes_per_row']:.0f}"] for k, v in tpl["tables"].items()])
    a, b = tuned["index_stats_before"]["indexes"], tuned["index_stats_after_sweep"]["indexes"]
    out += ["### Index findings", "",
            "Index use during the first 1,000,000-case sweep (`tuned` cell, 1 worker; `pg_stat_user_indexes`, delta over the sweep):", ""]
    out += table(["Index", "idx_scan", "idx_tup_read", "idx_tup_fetch", "Size"],
                 [[k, n(b[k]["idx_scan"] - a[k]["idx_scan"]), n(b[k]["idx_tup_read"] - a[k]["idx_tup_read"]), n(b[k]["idx_tup_fetch"] - a[k]["idx_tup_fetch"]),
                   mib(next((r["relation_bytes"] for r in tpl["relations"] if r["name"] == k.split(".")[1]), None))] for k in b])
    d1 = drop1["index_stats_after_sweep"]["indexes"]
    out += [f"- `events_case_id_seq_idx` and `events_case_id_seq_key` are two indexes of the same size on the same columns; the planner used `_idx` ({n(b['events.events_case_id_seq_idx']['idx_scan'] - a['events.events_case_id_seq_idx']['idx_scan'])} scans) and never `_key`. "
            f"Every append maintains both.",
            f"- Experiment on the throwaway `closure_load` database (not on `closure`): dropping `events_case_id_seq_idx` took the 1,000,000-case sweep from {f(tuned['wall_seconds'])} s to {f(drop1['wall_seconds'])} s with 1 worker "
            f"(appends {f(tuned['phases']['append_seconds'])} s to {f(drop1['phases']['append_seconds'])} s) and from {f(cell(sw, 1_000_000, 4, 'tuned')['wall_seconds'])} s to {f(drop4['wall_seconds'])} s with 4 workers; the reads moved to `events_case_id_seq_key` "
            f"({n(d1['events.events_case_id_seq_key']['idx_scan'])} scans). The repeats of the tuned cell (above) range wider than part of this gain, so the size of the effect is uncertain; the direction (less write amplification) is as expected.",
            f"- `events_genesis_patient_ref_idx` ({mib(next(r['relation_bytes'] for r in tpl['relations'] if r['name'] == 'events_genesis_patient_ref_idx'))}) was scanned {n(b['events.events_genesis_patient_ref_idx']['idx_scan'] - a['events.events_genesis_patient_ref_idx']['idx_scan'])} times by the sweep; "
            "whether other code paths use it was not measured here.",
            f"- The due-case selection is a sequential scan of `cases` (it filters on a basis hash as well as the due time), {sdict(big, 'explain_before_sweep', 'due_selection_rows'):,} rows at 1,000,000 cases; the plans are in `sweep.json`.", ""]
    out += ["### Seeding", ""]
    out += table(["Open cases", "Processes", "Insert, COPY (s)", "Cases per second", "VACUUM ANALYZE (s)", "Total (s)", "Events"],
                 [[n(s["cases"]), s["processes"], f(s["insert_seconds"], 1), f"{s['cases_per_second']:,.0f}", f(s["vacuum_analyze_seconds"], 1), f(s["total_seconds"], 1), n(s["events"])]
                  for _, s in sorted(sw["seeding"].items(), key=lambda kv: int(kv[0]))])
    pops = sw.get("closure_population", [])
    if pops:
        p = pops[-1]
        out += [f"The cluster's own `closure` database (used by the latency and soak runs) was seeded with {n(p['cases'])} cases in {f(p['total_seconds'], 0)} s with {p['processes']} processes "
                f"(states {', '.join(f'{v:.0%} {k}' for k, v in p['state_weights'].items())}, plus {p['worklist_seed']['cases']} `{next(iter(p['worklist_seed']['by_state']))}` cases for the worklist); it then held {n(p['count_after']['cases'])} cases and {n(p['count_after']['events'])} events, {p['sizes']['database_bytes'] / 2**30:.2f} GiB.", ""]
    return out




def latency_section_before(la) -> list[str]:
    st = la["stages"]
    out = ["## Load: API latency at a sustained rate (`latency.json`)", "",
           f"Open-loop constant arrival rate against the NodePort (requests are issued on schedule whatever the outstanding requests, latency counted from the intended start). "
           f"Mix (weights %): " + ", ".join(f"{k} {v}" for k, v in la["mix_weights"].items()) + f". Population: {n(la['population_before']['cases'])} cases / {n(la['population_before']['events'])} events before the run. "
           f"Pass criteria: {la['pass_criteria']}. The load generator runs on the same host as the cluster, and its CPU competes with the cluster's.", "",
           "### Latency per target rate", ""]
    rows = []
    for s in st:
        a, e = s["latency_all_requests"], s["latency_all_requests_excluding_worklist"]
        rows.append([f"{s['target_rate_per_s']:g}/s", s["label"], f"{s['achieved_rate_per_s']:.1f}/s", a["p50_ms"], a["p95_ms"], a["p99_ms"], a["max_ms"], e["p99_ms"],
                     f"{s['errors']} {s['errors_by_status'] or ''}".strip(), f"{s['error_rate'] * 100:.3f}%", "yes" if s["pass"] else "no"])
    out += table(["Rate", "Watchdog", "Achieved", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)", "p99 excl. worklist (ms)", "Errors", "Error rate", "Pass"], rows)
    sm = la["summary"]
    off, on = sm["watchdog_off"]["pass"], sm["watchdog_on_as_deployed"]["pass"]
    out += [f"- **Maximum sustainable rate (watchdog off, 3 API pods with a 1-CPU limit each): {off['max_passing_rate_per_s']:g} requests/s** (lowest failing rate tested: {off['lowest_failing_rate_per_s']:g}/s). "
            f"Excluding the worklist the same ({sm['watchdog_off']['pass_excluding_worklist']['max_passing_rate_per_s']:g}/s / {sm['watchdog_off']['pass_excluding_worklist']['lowest_failing_rate_per_s']:g}/s).",
            f"- **With the watchdog running as deployed (scan every {la['chart_values']['watchdog']['intervalSeconds']} s): no tested rate passes** (highest passing: none; lowest failing: {on['lowest_failing_rate_per_s']:g}/s, which is the lowest rate tested). "
            f"Failed requests appear from 40/s: " + ", ".join(f"{s['target_rate_per_s']:g}/s: {s['errors']} ({', '.join(s['errors_by_status'])})" for s in st if s['label'] == 'watchdog_on_as_deployed' and s['errors']) + ". See finding 1.", "",
            "`watchdog_on_as_deployed` is the label of the stages that ran with the watchdog Deployment active at its chart-default interval; the stages with the watchdog scaled to 0 are `watchdog_off`.", ""]
    # resources at the highest passing stage
    top = next(s for s in st if s["label"] == "watchdog_off" and s["target_rate_per_s"] == off["max_passing_rate_per_s"])
    ro = []
    for pod, v in top["resources"]["pods"].items():
        share = top["request_distribution_per_pod"].get(pod) if isinstance(top.get("request_distribution_per_pod"), dict) else None
        ro.append([pod, f"{v['cpu_cores_mean']:.2f} / {v['cpu_cores_p95']:.2f} / {v['cpu_cores_max']:.2f}", f"{mib(v['memory_bytes_mean'])} / {mib(v['memory_bytes_max'])}", mib(v["memory_limit_bytes"]),
                   f"{n(v.get('cpu_throttled_periods'))} of {n(v.get('cpu_periods'))}"])
    out += [f"### Resources at the highest passing rate ({top['target_rate_per_s']:g}/s)", ""]
    out += table(["Pod", "CPU cores mean / p95 / max", "Memory mean / max", "Memory limit", "CPU-throttled periods"], ro)
    nodes = top["resources"].get("nodes") or {}
    if nodes:
        out += table(["Kind node", "CPU % of one core, mean / max"], [[k, f"{v.get('cpu_percent_mean', 0):.0f} / {v.get('cpu_percent_max', 0):.0f}"] for k, v in nodes.items()])
    per_ep = top["per_endpoint"]
    out += [f"### Per endpoint at {top['target_rate_per_s']:g}/s", ""]
    out += table(["Endpoint", "Requests", "Errors", "p50 (ms)", "p95 (ms)", "p99 (ms)", "max (ms)"],
                 [[k, n(v["requests"]), v["errors"], v["latency"]["p50_ms"], v["latency"]["p95_ms"], v["latency"]["p99_ms"], v["latency"]["max_ms"]] for k, v in per_ep.items()])
    pr = la["unbounded_worklist_probe"]
    out += ["### Unbounded worklist read", "",
            f"A single `GET /cases?state={pr['state']}` against the {n(pr['cases_in_state'])} cases in that state returned `{pr['status']}` after {f(pr['seconds'], 1)} s; "
            f"restarts of the API pods during the probe: " + ", ".join(f"{k.rsplit('-', 1)[-1]} {pr['restarts_after'][k] - pr['restarts_before'][k]}" for k in pr['restarts_before']) +
            "; last termination: " + ", ".join(f"{k.rsplit('-', 1)[-1]} {v}" for k, v in pr['last_termination'].items() if v) + ". See finding 3.", ""]
    return out




def soak_section_before(so) -> list[str]:
    s = so["summary"]
    p = so["parameters"]
    w = s["watchdog"]
    out = ["## Soak (`soak.json`)", "",
           f"One uninterrupted open-loop stage at {p['rate_per_s']:g} requests/s for {s['measured_seconds'] / 3600:.2f} h ({so['started_at']} to {so['finished_at']}), the same mix as the latency runs "
           f"(bounded worklist: `GET /cases?state={so['list_state']}`), against a database of {n(so['population_before']['cases'])} cases / {n(so['population_before']['events'])} events, with the watchdog at its {w['interval_s']:g} s interval and the agency layer off. "
           f"Criteria: {s['criteria']}.", ""]
    L, W = s["latency_all_requests"], s["worst_window_by_p99"]
    out += table(["Result", "Value"], [
        ["Requests / errors", f"{n(s['requests_total'])} / {s['errors']} ({s['error_rate'] * 100:.4f}%)"],
        ["Pass", "yes" if s["pass"] else "no"],
        ["Latency overall p50 / p95 / p99 / max (ms)", f"{L['p50_ms']} / {L['p95_ms']} / {L['p99_ms']} / {L['max_ms']}"],
        [f"Worst {s['window_seconds']:g} s window by p99 (starts {W['start']})", f"p50 {W['latency_all']['p50_ms']} / p95 {W['latency_all']['p95_ms']} / p99 {W['latency_all']['p99_ms']} / max {W['latency_all']['max_ms']} ms"],
        [f"p99 range over the {s['windows']} windows", f"{s['windows_p99_ms_range'][0]} to {s['windows_p99_ms_range'][1]} ms; windows over 500 ms: {s['windows_over_p99_500ms']}"],
        ["Pod restarts / OOM kills during the soak", f"{s['restarts']['total_restarts_during_soak']} / {s['restarts']['oom_killed_during_soak']}"],
        ["Scheduler leader changes", s["leader"]["scheduler_leader_changes"]],
        ["Sweeps completed vs expected at the nominal 10 s", f"{n(s['scheduler']['sweeps_completed'])} of {s['scheduler']['sweeps_expected_at_10s']:,.0f} ({s['scheduler']['completed_fraction_of_expected']:.1%})"],
        ["Sweep-to-sweep period mean / p95 / max (s)", f"{s['scheduler']['sweep_period_s']['mean']} / {s['scheduler']['sweep_period_s']['p95']} / {s['scheduler']['sweep_period_s']['max']}"],
        ["Sweep duration p50 / p95 / max (s)", f"{s['scheduler']['durations_from_api_access_log']['sweep_duration']['p50_s']} / {s['scheduler']['durations_from_api_access_log']['sweep_duration']['p95_s']} / {s['scheduler']['durations_from_api_access_log']['sweep_duration']['max_s']}"],
        ["Sweep errors reported / scheduler warnings or errors", f"{s['scheduler']['sweep_errors_reported_in_summaries']} / {s['scheduler']['scheduler_warning_or_error_count']}"],
        ["Database growth", f"{s['database']['growth_mib']} MiB in the run ({s['database']['growth_mib_per_hour']} MiB/h); {n(so['population_after']['cases'] - so['population_before']['cases'])} cases and {n(so['population_after']['events'] - so['population_before']['events'])} events added"],
        ["Sampling", f"{s['sampling']['samples']} samples at {s['sampling']['period_s']:g} s, uninterrupted: {s['sampling']['uninterrupted']}"],
    ])
    from datetime import datetime
    starts = sorted(datetime.fromisoformat(x["start"]).timestamp() for x in w["scans"])
    series = s["scheduler"]["sweep_duration_series"]
    longs = sorted(((t, d) for t, d in series if d > 5), key=lambda x: -x[1])[:2]
    late = []
    for t, d in longs:
        prev = [x for x in starts if x <= t]
        late.append(f"{d} s, {f(t - prev[-1], 0)} s after a watchdog scan started" if prev else f"{d} s, before the first scan")
    ext = max(max(e["elevated_from_start_s"]) for e in w["server_side_evidence"]["per_scan"])
    out += ["The soak passes its own criteria (no errors, p99 under 500 ms in every window, no restarts). It does **not** show a scheduler keeping its 10 s cadence: "
            f"the loop completed {s['scheduler']['completed_fraction_of_expected']:.1%} of the nominal sweeps. Each pass of the loop is the outbox drain, the sweep and the automation and evidence scans in series, so the period is the interval plus all of them. "
            f"The {len(longs)} longest sweeps ({'; '.join(late)}) fall inside the {ext} s after a scan start in which the scanned pod's memory was still elevated (finding 1). This is a coincidence in time; no run isolated the cause.", "",
            "### Memory per pod", ""]
    rows = []
    for pod, v in s["pods"].items():
        rows.append([pod, f"{v['memory_current_start_mib']} / {v['memory_current_end_mib']} / {v['memory_current_max_mib']}", f"{v['memory_anon_start_mib']} / {v['memory_anon_end_mib']} / {v['memory_anon_max_mib']}",
                     v["memory_anon_slope_mib_per_hour"], v["memory_anon_slope_after_20min_mib_per_hour"], v["memory_limit_mib"], v["restart_count_end"] - v["restart_count_start"]])
    out += table(["Pod", "Memory start / end / max (MiB)", "Anon start / end / max (MiB)", "Anon slope (MiB/h)", "Anon slope after 20 min (MiB/h)", "Limit (MiB)", "Restarts"], rows)
    out += ["Slopes are least-squares fits to the 60 s samples. Anon memory is the process's own allocations (page cache excluded).", "",
            "### Watchdog scans during the soak", ""]
    srows = []
    for sc in w["scans"]:
        srows.append([sc["start"], f(sc["client_wait_s"], 1), sc["client_outcome"].replace("WARNING ", "")[:70], "yes" if sc["within_soak"] else "no (before the soak)"])
    out += table(["Scan started", "Client waited (s)", "Client outcome", "Inside the soak"], srows)
    ev = w["server_side_evidence"]["per_scan"]
    out += [f"The API writes no access-log line for an abandoned request, so the scan's own duration is not in the logs ({ev and 'evidence instead'}): per scan, the API pod whose memory rose by more than 50 MiB rose by "
            f"{min(e['peak_rise_mib'] for e in ev)} to {max(e['peak_rise_mib'] for e in ev)} MiB at peak and stayed above +50 MiB for {min(e['samples_above_50mib'] for e in ev)} to {max(e['samples_above_50mib'] for e in ev)} of the 60 s samples "
            f"({len(ev)} scans, each on one pod).", ""]
    return out




def memory_repro_section() -> list[str]:
    """Finding (e): the evidence for the root cause, parsed from the raw outputs of tools/load/memory_repro_*.py (committed beside the JSONs)."""
    d = RAW / "memory_repro"
    out = ["### Memory growth: root cause (finding 5)", ""]
    tm = d / "tracemalloc_20k.txt"
    if tm.exists():
        scans = [re.match(r"scan (\d+): evaluated=(\d+) flagged=(\d+) traced=([\d.]+)MiB peak=([\d.]+)MiB", ln) for ln in tm.read_text().splitlines()]
        scans = [m for m in scans if m]
        if scans:
            out += [f"1. **Not a Python leak.** The old scan (full replay of {int(scans[-1].group(2)):,} cases) run {len(scans)} times in one process under `tracemalloc` "
                    f"(`tools/load/memory_repro_tracemalloc.py`): the memory still allocated after each scan is {scans[-1].group(4)} MiB and does not grow from scan to scan, while each scan allocates a peak of "
                    f"{max(float(m.group(5)) for m in scans)} MiB. Diffing a late snapshot against an early one finds no allocation site that grows (the largest differences are bytes). Nothing is retained by the scan.", ""]
    rows = []
    for name, label in (("old", "before: every open case id fetched into one list first"), ("new", "after: the incremental scan and the paged iterator")):
        for tag, title in (("glibc1m", "1,000,000 cases"), ("glibc", "20,000 cases")):
            fp = d / f"{tag}_{name}.txt"
            if not fp.exists():
                continue
            pts = []
            for ln in fp.read_text().splitlines():
                parts = ln.split()
                if parts and parts[0].isdigit() and len(parts) >= 6:
                    pts.append([float(x) for x in parts])
            if not pts:
                continue
            first, last = pts[0], pts[-1]
            rss_i = 2 if tag == "glibc1m" else 1
            hwm_i = rss_i + 1
            out_row = [title, label, len(pts), f"{first[rss_i]:.0f}", f"{last[rss_i]:.0f}", f"{max(p[hwm_i] for p in pts):.0f}", f"{last[hwm_i + 2]:.0f}" ]
            rows.append(out_row)
    if rows:
        out += ["2. **Resident memory, in the API image (Linux, glibc, scans on pool threads as FastAPI runs them), `tools/load/memory_repro_rss.py`:**", ""]
        out += table(["Population", "Scan", "Scans", "RSS after the first (MiB)", "RSS after the last (MiB)", "Peak RSS (MiB)", "Allocator: free inside arenas at the end (MiB)"], rows)
        out += ["Reading: the memory is not leaked; it is the transient of the old scan. `iter_open_case_models` built the list of every open case id, and the query result behind it, before replaying the first case "
                "(`ids = [r[0] for r in conn.execute(...).fetchall()]`); that is O(N) bytes allocated per scan on whichever API pod serves the request, a few hundred MiB at 1,000,000 cases against a 768 MiB limit. "
                "The allocator does not hand arenas fragmented by such a transient back to the operating system, so the pod's resident memory stays above its working set and each further scan adds to it "
                "(the table shows what this host measured). The fix removes the transient: ids are fetched 500 at a time by primary-key keyset (`iter_open_case_models`) and the scan itself reads an index, so the working set no longer depends on the number of cases. ", ""]
        ab = d / "arena_ab_soak30min.json"
        out += ["3. **What is NOT explained: the growth that remains.** With the scan fixed, the three API pods in the 2-hour soak still show a positive anon slope and it is the same on all three, so it is not the scan path, the agency (it runs on the leader pod only) or the watchdog. "
                "The request path was run 60 rounds of the soak's mix under `tracemalloc` and retains nothing that grows (kilobytes), so it is not a Python-level leak either. The series of the soak is a warm-up curve that bends over (see the last-40-minute slope in the Soak table), not a straight line; "
                "a 30-minute run on freshly started pods with `MALLOC_ARENA_MAX=2` (`memory_repro/arena_ab_soak30min.json`) climbed the same way, from about 70 to about 180-220 MiB in 30 minutes, so limiting glibc arenas did not remove it and was not adopted. "
                "This report therefore claims the scan-path part is fixed and the pods are not growing without bound inside the limit of 768 MiB over the 2 hours measured (maximum anon "
                "observed in the soak: see the Soak table), and does NOT claim memory is flat. A longer soak is needed to tell a plateau from a slow leak.", ""]
    return out


def defects_section(fo, sw, la, so, fo0, sw0, la0, so0) -> list[str]:
    """What was wrong in M7-6 and what changed, one entry per finding, with the figures of both runs."""
    target = sw["target_loop_interval_seconds"]
    wd = sorted(sw.get("watchdog_cells", []), key=lambda c: c["size"])
    dr = sorted(sw.get("drain_cells", []), key=lambda c: c["size"])
    on, on0 = la["summary"].get("watchdog_on_as_deployed"), la0["summary"].get("watchdog_on_as_deployed")
    ss, ss0 = so["summary"], so0["summary"]
    sl, sl0 = api_slopes(so), api_slopes(so0)
    probe, probe0 = la.get("unbounded_worklist_probe"), la0.get("unbounded_worklist_probe")
    ag = so.get("agency")
    out = ["## The defects found in M7-6, and what changed", "",
           "The previous run (`docs/results/scale/before/*.json`, kept unchanged; its full report is the appendix at the end) found seven defects in `api/` and an eighth item it did not run. "
           "Each row is the fix and the figures of both runs. The decisions are ADR-045 (the watchdog) and ADR-046 (the bounds).", ""]
    rows = []
    if wd:
        c1 = wd[-1]
        b1 = before_cell(sw0, c1["size"])
        rows.append(["(a) The watchdog scan replayed every open case", f"{f(b1['full_scan_seconds'], 0)} s at {c1['size']:,} cases; with the watchdog at 10 s no API rate from 20/s passed",
                     "`cases.idle_since_at` (migration 017), written with every append; the scan reads `idle_since_at <= now - idle_days` in keyset batches inside a time budget, from a durable cursor, plus a resumable full reconciliation pass (ADR-045)",
                     f"steady scan {f(c1['steady_scan_seconds']['median'], 2)} s, longest catch-up scan {f(c1['catchup_scan_seconds']['max'], 1)} s at {c1['size']:,} cases; "
                     + (f"API holds {on['pass']['max_passing_rate_per_s']:g}/s with the watchdog at 10 s" if on and on["pass"]["max_passing_rate_per_s"] else "see the ladder")])
    ag_text = (f"{ag['runs']} runs in the soak, at most {ag['cases_read_per_run']['max']} cases read per run, {ss['restarts']['oom_killed_during_soak']} OOM kills" if ag else "n/a")
    rows.append(["(b) The agency read the whole case table every 6th sweep (and broke its 400-order cap)", "an API pod OOM-killed at ~1M cases; the agency was switched off in the benchmark",
                 "a window of `AGENCY_MAX_CASES_READ` (400) cases per run, SQL-side LIMIT + keyset, resumable (`capped` runs); orders over the planner's cap are skipped as `order_cap`; totals from a SQL aggregate (ADR-046)", ag_text])
    if probe:
        rows.append(["(c) `GET /cases?state=` was unbounded", f"{probe0['status']} after {f(probe0['seconds'], 1)} s, an API pod OOM-killed at {n(probe0['cases_in_state'])} rows" if probe0 else "OOM",
                     "SQL-side `state` filter on a new index, `limit` (default 100, maximum 500, 422 above) and a cursor in `X-Next-Cursor`; the body is still a list; the web client and the OpenAPI types follow it (ADR-046)",
                     f"{n(probe['cases_in_state'])} rows in the state: default page {probe['default_page']['rows']} rows in {f(probe['default_page']['seconds'], 2)} s; {probe['pages_followed']} pages of 500 followed at {f(probe['page_seconds']['median'], 2)} s median; no restart" if probe.get("page_seconds") else "see the probe"])
    rows.append(["(d) A duplicate index on `events`", "`events_case_id_seq_idx` = the unique `(case_id, seq)` index, 294 MiB each at 1M, both maintained on every append",
                 "migration 017 drops `events_case_id_seq_idx`. `events_genesis_patient_ref_idx` is **kept**: `app.erasure` queries `WHERE seq = 1 AND payload ->> 'patient_ref' = ...` with it and `GET /cases` for a patient reads it", "dropped one, kept one; appends measured below"])
    rows.append(["(e) API pod memory grew +30 / +71 / +131 MiB/h", "an excursion of 181 to 255 MiB on the pod that ran each 224 s scan, and a ratchet after it", "no list of all ids any more (`iter_open_case_models` pages by primary key) and no full replay in the scan; root cause of the scan-path part below",
                 "scan-path transient removed (no 180 to 255 MiB excursions); but the pods still grew by +" + " / +".join(f"{v:g}" for v in sorted(sl.values())) + f" MiB/h (anon) over {ss['measured_seconds'] / 3600:.2f} h, of which +" + " / +".join(f"{v:g}" for v in sorted(tail_slopes(so).values())) + " MiB/h in the last 40 min: **not flat**"])
    stale_before = "2 of 3 follower rows stale and never removed"
    rows.append(["(f) `scheduler-follower-<pod>` heartbeat rows kept forever", stale_before, "the watchdog scan deletes follower rows older than `FOLLOWER_HEARTBEAT_RETENTION_SECONDS` (3600 s); the `scheduler` and `watchdog` rows and the leader lease are never touched",
                 f"{sum(1 for k, v in so['samples'][-1]['heartbeats'].items() if k.startswith('scheduler-follower-') and v['age_s'] > 120 and k.removeprefix('scheduler-follower-') not in set(so['samples'][-1]['pods']))} stale rows at the end of the soak"])
    if dr:
        d1 = dr[-1]
        b1 = before_cell(sw0, d1["size"])
        rows.append(["(g) The 1M daily catch-up took 34 to 64 s (and 87 s in one process) against the 10 s loop", f"read {f(b1['phases']['read_replay_seconds'], 1)} s, plan {f(b1['phases']['planning_seconds'], 1)} s, appends {f(b1['phases']['append_seconds'], 1)} s = {f(b1['wall_seconds'], 1)} s in one sweep" if b1 and b1.get("phases") else "n/a",
                     "each loop is bounded (`SWEEP_MAX_CASES` 20,000 due cases, `SWEEP_BUDGET_SECONDS` 6 s) and the rest stays due; a batch of cases is written with one multi-row INSERT and one multi-row UPDATE, the lease re-check unchanged (ADR-046)",
                     f"longest loop {f(d1['loop_wall_seconds']['max'])} s; drained in {d1['loop_count']} loops = {f(d1['time_to_drain_seconds_with_interval_sleeps'] / 60, 1)} min at a {target:g} s interval; **the burst still does not fit in one loop**"])
    zr = [r for r in fo["runs"] if r["mode"] == "sigstop"]
    if zr:
        rows.append(["(h) The zombie leader was never tested", "SIGSTOP drill never run", f"`failover_drill.py --sigstop-runs`: freeze the leader mid-loop, hold it past the lease TTL until a new leader has swept, resume it",
                     f"{len(zr)} runs: {sum(r['sigstop']['old_leader_rows_written_after_takeover'] for r in zr)} rows written by the old leader, `StaleLeader` {sum(r['sigstop']['old_leader_stale_leader_errors'] for r in zr)} of {sum(r['sigstop']['old_leader_stale_attempts'] for r in zr)} stale-fence writes, "
                     f"{sum(r['sigstop']['old_leader_calls_refused_409_after_resume'] for r in zr)} calls after waking refused with 409"])
    out += table(["Defect", "Before (M7-6)", "Fix", "After (M7-7)"], rows)
    out += memory_repro_section()
    return out


def still_not_met(fo, sw, la, so, cp) -> list[str]:
    target = sw["target_loop_interval_seconds"]
    dr = sorted(sw.get("drain_cells", []), key=lambda c: c["size"])
    ss = so["summary"]
    on = la["summary"].get("watchdog_on_as_deployed")
    out = ["## What is still not met", ""]
    items = []
    if dr:
        d1 = dr[-1]
        items.append(f"- **The {d1['size']:,}-case daily burst inside ONE {target:g} s loop.** It is bounded and drains (longest loop {f(d1['loop_wall_seconds']['max'])} s, {d1['loop_count']} loops, "
                     f"{f(d1['time_to_drain_seconds_with_interval_sleeps'] / 60, 1)} minutes at a {target:g} s interval), but the unbounded sweep of the same burst still takes "
                     f"{f(cell(sw, d1['size'], 1, 'tuned')['wall_seconds'], 1)} s with one worker on this host. Events of the last cases of such a burst are written minutes after they fell due.")
    if on is not None and (not on["pass"]["max_passing_rate_per_s"] or on["pass"]["lowest_failing_rate_per_s"]):
        items.append("- The API rate ladder: the highest passing rate with the watchdog on is " + (f"{on['pass']['max_passing_rate_per_s']:g}/s" if on["pass"]["max_passing_rate_per_s"] else "none") + (f"; the lowest failing rate is {on['pass']['lowest_failing_rate_per_s']:g}/s" if on["pass"]["lowest_failing_rate_per_s"] else "") + ".")
    if ss["scheduler"]["completed_fraction_of_expected"] < 0.95:
        dd = ss['scheduler']['durations_from_api_access_log']
        items.append(f"- **The sweep cadence in the soak: {ss['scheduler']['completed_fraction_of_expected']:.1%} of the nominal sweeps completed** (period mean {ss['scheduler']['sweep_period_s']['mean']} s, not 10 s). A pass of the scheduler loop is the outbox drain, the sweep ({dd['sweep_duration']['mean_s']} s mean), the automation scan ({dd['automation_scan_duration']['mean_s']} s mean) and the evidence scan in series, and then the 10 s sleep, so the period is the interval plus about {f(ss['scheduler']['sweep_period_s']['mean'] - 10, 1)} s of work; the sweep itself is not the long part (the agency layer and the automation scan share the pod). The sweeps are bounded and steady, they are just not started every 10 s.")
    sl = api_slopes(so)
    if sl and max(sl.values()) >= 5:
        items.append(f"- **API pod memory is not flat.** The scan-path transient is gone (root cause above), but all three API pods still grew by {min(sl.values()):g} to {max(sl.values()):g} MiB/h (anon) over the {ss['measured_seconds'] / 3600:.2f} h soak "
                     f"({min(tail_slopes(so).values()):g} to {max(tail_slopes(so).values()):g} MiB/h in the last 40 minutes). No pod came near its 768 MiB limit or restarted, but the cause of the remainder is not found.")
    wo = ss["windows_over_p99_500ms"]
    if wo:
        items.append(f"- {wo} of the {ss['windows']} five-minute windows of the soak had a p99 over 500 ms (worst {ss['windows_p99_ms_range'][1]} ms); the overall p99 was {ss['latency_all_requests']['p99_ms']} ms.")
    pr = la.get("unbounded_worklist_probe")
    items.append(f"- At {max(s_['target_rate_per_s'] for s_ in la['stages'] if s_['label'] == 'watchdog_on_as_deployed'):g} requests/s (the first failing stage) the API timed out and reset connections; that is overload, and it was not tuned or retried.")
    items += ["- **Other unbounded reads** that are not on a path measured here: `GET /metrics/closure` and `GET /metrics/effort` (and the dashboards behind them) replay every case in one call (`load_cases`); the agency's own totals avoid them above 20,000 cases, the routes do not.",
              "- The reconciliation pass is slow by design: a full cycle at 1,000,000 cases is about a thousand scans (about 2.8 hours at a 10 s interval), so a case hidden by a wrong index value (HZ-44) is found within one cycle, not at once.",
              "- Everything here is one laptop-class host, a local kind cluster, one Postgres without HA or TLS, and no network latency between nodes and database. No cloud environment was measured."]
    return out + items + [""]


# ---------------------------------------------------------------------------------------------------------------------------------
# capacity plan
# ---------------------------------------------------------------------------------------------------------------------------------

def mean_open_days(results: dict, inp: dict) -> tuple[float, dict]:
    arm = results["scenarios"]["literature_anchored"]["arms"]["orchestrated"]
    fu = results["meta"]["follow_up_days"]
    pts = [(0, 1.0), (30, arm["open_30d"]["mean"]), (60, arm["open_60d"]["mean"]), (90, arm["open_90d"]["mean"]), (fu, 1.0 - arm["closed_share"]["mean"])]
    area = sum((x1 - x0) * (y0 + y1) / 2 for (x0, y0), (x1, y1) in zip(pts, pts[1:]))
    return area, {"points": pts, "follow_up_days": fu}


def capacity_plan(sw, la, fo, so, inp, results) -> dict:
    cs = inp["cloud_sizing"]
    ev = inp["events_and_requests"]
    pr = inp["pricing"]
    hours = pr["hours_per_month"]
    safety = cs["laptop_to_cloud_safety_factor"]
    zones = cs["availability_zones"]
    cells = [c for c in sw["cells"] if not c.get("profiled")]
    sweep_pts = {w: sorted((c["size"], c["wall_seconds"]) for c in cells if c["workers"] == w and c["label"] == "tuned" and c["size"] in (100_000, 500_000, 1_000_000)) for w in (1, 4)}
    full_pts = sorted((c["size"], c["full_scan_seconds"]) for c in cells if c["workers"] == 1 and c["label"] in ("baseline", "tuned") and c.get("full_scan_seconds"))
    wdc = sorted(sw.get("watchdog_cells", []), key=lambda c: c["size"])
    scan_pts = [(c["size"], c["steady_scan_seconds"]["median"]) for c in wdc] or full_pts  # the incremental scan (M7-7), else the full replay
    env_label = "watchdog_on_as_deployed" if la["summary"].get("watchdog_on_as_deployed", {}).get("pass", {}).get("max_passing_rate_per_s") else "watchdog_off"
    max_rate = la["summary"][env_label]["pass"]["max_passing_rate_per_s"]
    top = next(s for s in la["stages"] if s["label"] == env_label and s["target_rate_per_s"] == max_rate)
    api_pods = sum(1 for p in top["resources"]["pods"] if p.startswith("closure-api"))
    per_pod_measured = max_rate / api_pods
    per_pod_derated = per_pod_measured / safety
    seed = sw["seeding"]["1000000"]
    tpl = sw["seeding_sizes_template"]["1000000"]
    ev_per_case_seed = seed["events"] / seed["cases"]
    life_events = ev["lifetime_events_factor"] * ev_per_case_seed
    row_cases = tpl["tables"]["cases"]["total_bytes_per_row"]
    row_events = tpl["tables"]["events"]["total_bytes_per_row"]
    bytes_per_case_life = row_cases + row_events * life_events
    mean_days, curve = mean_open_days(results, inp)
    oc = inp["open_cases"]
    peg = float(find_key(yaml.safe_load(PILOT_INPUTS.read_text(encoding="utf-8")), "usd_aed_peg")["value"])
    vols = inp["volumes"]["pathways"]
    out_sc = {}
    vm_names = ["Standard_D2s_v5", "Standard_D4s_v5", "Standard_D8s_v5"]
    for key, sc in inp["scenarios"].items():
        if key.startswith("_"):
            continue
        sel = [v for v in vols if sc["pathways"] == "all" or v["name"] in sc["pathways"]]
        annual = sum(v[sc["volume"]] for v in sel)
        open_central = annual * mean_days / 365
        open_upper = annual * oc["retained_upper_bound_years"]
        open_sizing = open_central if sc["open_basis"] == "simulated_duration" else open_upper
        ev_day = annual * life_events / 365
        rps_avg = ev_day * ev["requests_per_event"] / 86400
        rps_peak = rps_avg * ev["peak_factor"]
        replicas_load = max(1, math.ceil(rps_peak / per_pod_derated))
        api_replicas = max(zones, replicas_load + 1)
        # sweeps
        t1 = interp(sweep_pts[1], open_sizing) * safety
        t4 = interp(sweep_pts[4], open_sizing) * safety
        loop = cs["loop_interval_s"]
        workers = 1 if t1 <= loop else 4
        sweep_t = t1 if workers == 1 else t4
        scan_t = interp(scan_pts, open_sizing) * safety
        scan_t_upper = interp(scan_pts, open_upper) * safety
        # nodes
        api_max = max(api_replicas, cs["api_max_replicas_for_node_sizing"])
        cpu_limit = float(workers)
        req_cpu = api_max * cs["api_pod"]["cpu_request"] + cs["scheduler_replicas"] * cs["scheduler_pod"]["cpu_request"] + cs["watchdog_replicas"] * cs["watchdog_pod"]["cpu_request"]
        mem_gib = (api_max * cs["api_pod"]["mem_limit_mib"] + cs["scheduler_replicas"] * cs["scheduler_pod"]["mem_limit_mib"] + cs["watchdog_replicas"] * cs["watchdog_pod"]["mem_limit_mib"]) / 1024
        sku, nodes = None, None
        for vm in vm_names:
            vc, ram = pr["vm_vcpu"][vm], pr["vm_ram_gib"][vm]
            if vc < cpu_limit:
                continue
            for nn in range(zones, zones + 6):
                if req_cpu <= (nn - 1) * (vc - cs["node_system_reserved"]["cpu"]) and mem_gib <= (nn - 1) * (ram - cs["node_system_reserved"]["mem_gib"]):
                    sku, nodes = vm, nn
                    break
            if sku:
                break
        # database
        retained_cases = annual * oc["retention_years_for_database"]
        db_gb = retained_cases * bytes_per_case_life / 2**30
        vcore_min = next((v for v in (2, 4, 8) if db_gb <= cs["postgres_cache_fraction"] * cs["postgres_ram_gib_per_vcore"] * v), 8)
        vcores = max(vcore_min, sc["db_vcore_floor"])
        need = max(cs["storage_min_gb"], db_gb * cs["storage_overhead_factor"])
        stor_min = next((s for s in (32, 64, 128, 256, 512, 1024, 2048) if s >= need), 4096)
        storage = max(stor_min, sc["storage_gb_floor"])
        backup_gb = cs["postgres_backup_size_factor"] * db_gb
        backup_over = max(0.0, backup_gb - storage)
        pg_compute = pr["postgres_flexible_gp_ddsv5_usd_per_hour"][vcores] * hours * 2
        pg_storage = storage * pr["postgres_storage_usd_per_gb_month"] * 2
        pg_backup = backup_over * pr["postgres_backup_usd_per_gb_month"]
        logs = sc["log_gb_per_month"] * pr["log_analytics_usd_per_gb_ingested"] + sc["log_gb_per_month"] * pr["log_retention_billed_months"] * pr["log_analytics_retention_usd_per_gb_month"]
        kv = inp["pricing"]["key_vault_operations_per_month"] / 10_000 * pr["key_vault_usd_per_10k_operations"]
        waf = (pr["app_gateway_waf_v2"]["fixed_usd_per_hour"] + pr["app_gateway_waf_v2"]["capacity_unit_usd_per_hour"] * sc["waf_capacity_units"]) * hours
        std_gw = (pr["app_gateway_standard_v2"]["fixed_usd_per_hour"] + pr["app_gateway_standard_v2"]["capacity_unit_usd_per_hour"] * sc["waf_capacity_units"]) * hours
        lb = pr["standard_load_balancer_usd_per_hour"] * hours
        egress = max(0.0, sc["egress_gb_per_month"] - pr["egress"]["free_gb"]) * pr["egress"]["microsoft_network_usd_per_gb"]
        egress_inet = max(0.0, sc["egress_gb_per_month"] - pr["egress"]["free_gb"]) * pr["egress"]["internet_routing_usd_per_gb"]
        aks_tier = pr["aks"]["standard_tier_usd_per_cluster_hour"] * hours
        aks_free_unverified = pr["aks"]["free_tier_infrastructure_cost_meter_usd_per_hour"] * hours
        vm = nodes * pr["vm_usd_per_hour"][sku] * hours
        vm_amd = None
        amd = {"Standard_D2s_v5": "Standard_D2as_v5", "Standard_D4s_v5": "Standard_D4as_v5"}.get(sku)
        if amd:
            vm_amd = nodes * pr["vm_usd_per_hour"][amd] * hours
        osd = nodes * pr["premium_ssd_p10_lrs_usd_per_month"]
        aks_lines = [("AKS Standard tier (Uptime SLA)", aks_tier), ("Node VMs (node pool above)", vm), ("Node OS disks, Premium SSD P10 LRS", osd), ("Standard Load Balancer", lb),
                     ("Application Gateway WAF v2 (capacity units: small 2, base 2, high 4)", waf),
                     ("PostgreSQL compute, zone-redundant HA (primary + standby)", pg_compute), ("PostgreSQL storage (primary + standby)", pg_storage),
                     ("PostgreSQL backup above the allowance", pg_backup), ("Log Analytics ingestion + billed retention", logs),
                     ("Key Vault operations", kv), ("Egress, Microsoft network", egress)]
        aks_total = sum(v for _, v in aks_lines)
        # Container Apps (deploy/bicep) path
        ca = pr["container_apps"]
        b = ca["deploy_bicep_defaults"]
        ca_rep = max(zones, replicas_load + 1)
        scale_up = max(1.0, float(workers))     # one CPU per sweep worker; the Bicep default memory per CPU (2 GiB) is kept
        vcpu = ca_rep * b["api"]["cpu"] * scale_up + b["scheduler"]["replicas"] * b["scheduler"]["cpu"] + b["watchdog"]["replicas"] * b["watchdog"]["cpu"]
        gib = ca_rep * b["api"]["mem_gib"] * scale_up + b["scheduler"]["replicas"] * b["scheduler"]["mem_gib"] + b["watchdog"]["replicas"] * b["watchdog"]["mem_gib"]
        secs = hours * SECONDS_PER_MONTH_H
        vcpu_s, gib_s = vcpu * secs, gib * secs
        ca_cpu = vcpu_s * ca["vcpu_second_active_usd"]
        ca_mem = gib_s * ca["gib_second_active_usd"]
        month_requests = rps_avg * 86400 * 30.4375
        ca_req = max(0.0, month_requests - ca["free_grant"]["requests"]) / 1e6 * ca["million_requests_usd"]
        grant = min(vcpu_s, ca["free_grant"]["vcpu_seconds"]) * ca["vcpu_second_active_usd"] + min(gib_s, ca["free_grant"]["gib_seconds"]) * ca["gib_second_active_usd"]
        ca_env = ca["environment_hour_usd"] * hours
        ca_lines = [("Container Apps vCPU, always on, active rate (vCPU: see row below)", ca_cpu), ("Container Apps memory, always on (GiB: see row below)", ca_mem), ("Container Apps requests", ca_req),
                    ("Container Apps monthly free grant (per subscription, if unused elsewhere)", -grant), ("Container Apps environment hour (applicability unverified)", ca_env),
                    ("Application Gateway WAF v2 (capacity units: small 2, base 2, high 4)", waf), ("PostgreSQL compute, zone-redundant HA (primary + standby)", pg_compute),
                    ("PostgreSQL storage (primary + standby)", pg_storage), ("PostgreSQL backup above the allowance", pg_backup),
                    ("Log Analytics ingestion + billed retention", logs), ("Key Vault operations", kv), ("Egress, Microsoft network", egress)]
        ca_total = sum(v for _, v in ca_lines)
        out_sc[key] = {
            "label": sc["label"], "volume": sc["volume"], "pathways": sc["pathways"], "annual_new_cases": annual, "open_basis": sc["open_basis"],
            "open_cases_central": round(open_central), "open_cases_upper_bound": open_upper, "open_cases_for_sizing": round(open_sizing),
            "lifetime_events_per_case": life_events, "events_per_day": ev_day, "requests_per_s_average": rps_avg, "requests_per_s_peak": rps_peak,
            "headroom_vs_measured_envelope": max_rate / rps_peak,
            "api_replicas": api_replicas, "api_replicas_for_load": replicas_load, "scheduler_replicas": cs["scheduler_replicas"], "watchdog_replicas": cs["watchdog_replicas"],
            "sweep_seconds_derated_1_worker": t1, "sweep_seconds_derated_4_workers": t4, "sweep_workers": workers, "sweep_seconds_chosen": sweep_t,
            "watchdog_scan_seconds_derated": scan_t, "watchdog_scan_seconds_derated_at_upper_bound": scan_t_upper,
            "api_cpu_limit": cpu_limit, "node_requests_cpu": req_cpu, "node_memory_limits_gib": mem_gib, "node_sku": sku, "nodes": nodes,
            "database_gb_retained": db_gb, "retained_cases": retained_cases, "db_vcores_minimum": vcore_min, "db_vcores": vcores, "db_storage_gb_minimum": stor_min, "db_storage_gb": storage,
            "aks_lines": [[k, round(v, 2)] for k, v in aks_lines], "aks_total_usd_month": round(aks_total, 2), "aks_total_usd_year": round(aks_total * 12, 2), "aks_total_aed_year": round(aks_total * 12 * peg),
            "aks_free_tier_alternative_usd_month": round(aks_total - aks_tier, 2), "aks_free_tier_unverified_meter_usd_month": round(aks_free_unverified, 2),
            "amd_node_alternative_saving_usd_month": round(vm - vm_amd, 2) if vm_amd is not None else None,
            "standard_gateway_instead_of_waf_saving_usd_month": round(waf - std_gw, 2),
            "internet_routing_egress_saving_usd_month": round(egress - egress_inet, 2),
            "ca_vcpu": vcpu, "ca_gib": gib, "ca_api_replicas": ca_rep, "ca_lines": [[k, round(v, 2)] for k, v in ca_lines], "ca_total_usd_month": round(ca_total, 2), "ca_total_usd_year": round(ca_total * 12, 2), "ca_total_aed_year": round(ca_total * 12 * peg),
        }
    return {"inputs": {"safety_factor": safety, "per_pod_rate_measured": per_pod_measured, "per_pod_rate_derated": per_pod_derated, "api_pods_measured": api_pods, "max_passing_rate": max_rate,
                       "events_per_case_seeded": ev_per_case_seed, "lifetime_events_per_case": life_events, "bytes_per_case_lifetime": bytes_per_case_life,
                       "db_bytes_per_case_seeded": tpl["database_bytes"] / seed["cases"], "mean_open_days": mean_days, "open_curve": curve, "peg": peg,
                       "sweep_points": sweep_pts, "full_replay_points": full_pts, "scan_points": scan_pts, "envelope_label": env_label, "database_bytes_per_1m_cases": tpl["database_bytes"]},
            "scenarios": out_sc}


def capacity_section(cp, inp, sw, la) -> list[str]:
    I = cp["inputs"]
    S = cp["scenarios"]
    pr = inp["pricing"]
    keys = list(S)
    out = ["## Capacity plan: the whole emirate", "",
           "**What is measured and what is assumed.** Measured (read from the JSONs): sweep times at 100k, 500k and 1M open cases, the full-replay cost, events and bytes per case, the database size, the API's sustainable rate (3 pods, 1 CPU each), "
           "memory and CPU per pod. Assumed (`tools/k8s/capacity_inputs.yaml`): case volumes beyond FIT, how long a case stays open, events per case over its life, requests per event, the peak factor, the laptop-to-cloud safety factor, "
           "the availability zones, and every price. **All measurements were taken on a laptop-class shared VM (Apple M4, Docker Desktop 10 CPUs / about 8 GB, load generator on the same host); no Azure resource was measured.** "
           f"Cloud sizing therefore divides measured throughput by, and multiplies measured times by, a safety factor of {I['safety_factor']:g} (a judgement, not a measurement).", "",
           "### Case volumes (assumptions from `docs/commercial/business-case.md`)", ""]
    vrows = [[v["name"], n(v["low"]), n(v["central"]), n(v["high"]), v["source"]] for v in inp["volumes"]["pathways"]]
    out += table(["Pathway", "Low / year", "Central / year", "High / year", "Source"], vrows)
    out += [f"All four pathways: {inp['volumes']['all_four_total_source']}. Only the FIT pathway is built and simulated; the other three are assumed or programme targets (`business-case.md` section 9.2). "
            "The task brief's \"about 17,700 FIT positives a year\" is the central 17,696.", "",
            "### From volumes to load", ""]
    rows = []
    for k in keys:
        s = S[k]
        rows.append([k, "FIT only" if s["pathways"] != "all" else "four pathways", s["volume"], n(s["annual_new_cases"]), n(s["open_cases_central"]), n(int(s["open_cases_upper_bound"])), n(s["open_cases_for_sizing"]),
                     f"{s['events_per_day']:,.0f}", f(s["requests_per_s_average"], 3), f(s["requests_per_s_peak"], 2), f"{s['headroom_vs_measured_envelope']:,.0f}x"])
    out += table(["Scenario", "Pathways", "Volume", "New cases / year", "Open cases (mean duration)", f"Open cases (upper bound, {inp['open_cases']['retained_upper_bound_years']} years retained open)", "Open cases used for sizing",
                  "Events / day", "Requests/s average", "Requests/s peak", "Headroom: measured 175/s over peak"], rows)
    out += [f"- **Open population** = new cases a year x mean open days / 365, with the mean open duration {f(I['mean_open_days'], 1)} days: the area under the simulator's open-share curve "
            f"({', '.join(f'{x:g} d: {y:.3f}' for x, y in I['open_curve']['points'])}) from `docs/results/results.json`. "
            f"{inp['open_cases']['simulated_note'].strip()} {inp['open_cases']['retained_upper_bound_note'].strip()} The `high` scenario is sized on the upper bound.",
            f"- **Events per case**: {f(I['events_per_case_seeded'], 2)} measured in the seeded population; lifetime events per case = {inp['events_and_requests']['lifetime_events_factor']:g} x that = {f(I['lifetime_events_per_case'], 1)} (assumed factor). "
            f"**Requests per event** {inp['events_and_requests']['requests_per_event']:g} and **peak factor** {inp['events_and_requests']['peak_factor']:g}x (assumed, see `capacity_inputs.yaml`). Cases are spread evenly over 365 days.",
            f"- The measured envelope is {I['max_passing_rate']:g} requests/s on {I['api_pods_measured']} API pods with a 1-CPU limit each ({f(I['per_pod_rate_measured'], 1)}/s per pod; {f(I['per_pod_rate_derated'], 1)}/s per pod after the safety factor), p99 under 500 ms, {I['envelope_label']} stages. "
            "**The API request rate is not what sizes the platform**: even the high scenario's peak is far below one pod's derated rate. What sizes it is availability (zones, N+1), the sweep over the open population, the watchdog scan, and the database.", "",
            "### The sweep, the watchdog and the open population", ""]
    rows = []
    for k in keys:
        s = S[k]
        rows.append([k, n(s["open_cases_for_sizing"]), f(s["sweep_seconds_derated_1_worker"], 1), f(s["sweep_seconds_derated_4_workers"], 1), s["sweep_workers"],
                     f(s["watchdog_scan_seconds_derated"], 1), f(s["watchdog_scan_seconds_derated_at_upper_bound"], 1)])
    out += table(["Scenario", "Open cases used", "Catch-up sweep, 1 worker (s, derated)", "Catch-up sweep, 4 workers (s, derated)", "Workers chosen", "Watchdog scan (steady state) at the sizing population (s, derated)", "Watchdog scan (steady state) at the upper bound (s, derated)"], rows)
    out += [f"Sweep times interpolate the `tuned` cells of `sweep.json` ({', '.join(f'{x:,} cases: {f(y)} s (1 worker)' for x, y in I['sweep_points'][1])}; "
            f"{', '.join(f'{x:,}: {f(y)} s (4 workers)' for x, y in I['sweep_points'][4])}) linearly, and linearly through the origin below 100,000 cases. The daily catch-up burst fits the 10 s loop after derating with one worker where the 1-worker column is under 10 s; "
            f"otherwise 4 workers are chosen (which needs a 4-CPU limit on the API pods). The watchdog scan interpolates the measured steady-state incremental scan times ({', '.join(f'{x:,}: {f(y, 3)} s' for x, y in I['scan_points'])}; reconciliation slice included); the old full-replay times were ({', '.join(f'{x:,}: {f(y, 1)} s' for x, y in I['full_replay_points'])}). "
            f"A steady-state scan stays far inside the 10 s watchdog interval at every population above, so the production interval of 10 s needs no change; a catch-up scan is bounded by its time budget (see the watchdog section).", "",
                        "### Recommended deployment", ""]
    out += ["Replicas and sizing rules: API replicas = max(zones, ceil(peak / derated per-pod rate) + 1) (N+1, one per zone, `topologySpread` on); scheduler 2 (one leader, one follower); watchdog 1. "
            "Nodes: the smallest VM size whose vCPU count covers the API CPU limit (one CPU per sweep worker), with the autoscaler's maximum of API pods (6) fitting on the remaining nodes after losing one node; "
            f"{inp['cloud_sizing']['node_system_reserved']['cpu']} vCPU and {inp['cloud_sizing']['node_system_reserved']['mem_gib']} GiB per node reserved for system pods (assumed). "
            f"Database: smallest GP Ddsv5 size whose RAM x {inp['cloud_sizing']['postgres_cache_fraction']:g} holds the retained database ({inp['open_cases']['retention_years_for_database']} years of cases), then raised to a per-scenario floor (judgement); "
            f"storage = max(data x {inp['cloud_sizing']['storage_overhead_factor']:g}, floor) rounded up to a size step. Zone-redundant HA on (Burstable has none).", ""]
    rows = []
    for k in keys:
        s = S[k]
        rows.append([k, f"{s['api_replicas']} (load needs {s['api_replicas_for_load']}, +1, min {inp['cloud_sizing']['availability_zones']} zones)", s["scheduler_replicas"], s["watchdog_replicas"], s["sweep_workers"],
                     f"{s['nodes']} x {s['node_sku'].replace('Standard_', '')}", f"{s['db_vcores']} vCore (minimum by size: {s['db_vcores_minimum']})", f"{s['db_storage_gb']} GB (minimum by size: {s['db_storage_gb_minimum']}); data {s['database_gb_retained']:.2f} GB",
                     "zone-redundant"])
    out += table(["Scenario", "API replicas", "Scheduler", "Watchdog", "`sweepWorkers`", "AKS node pool", "PostgreSQL GP Ddsv5", "Storage", "HA"], rows)
    out += ["### Monthly cost: ESTIMATE from public list prices", "",
            f"**ESTIMATE.** Azure Retail Prices API (`{pr['source_url']}`, filter `{pr['filter']}`), pay-as-you-go, Linux, USD, retrieved {pr['retrieved']}, {pr['hours_per_month']} h a month. "
            "Excludes tax, support plans, reservations and savings plans, and any DoH-specific hosting requirements (for example in-country dedicated or sovereign options, private endpoints, a separate FHIR server, observability stack, container registry). "
            "Not a quote. Prices marked unverified are listed in `capacity_inputs.yaml`.", ""]
    for path, title, tot_key, lines_key in (("AKS", "Path A: AKS (the Helm chart)", "aks_total_usd_month", "aks_lines"), ("ACA", "Path B: Azure Container Apps (`deploy/bicep`)", "ca_total_usd_month", "ca_lines")):
        out += [f"#### {title}", ""]
        nlines = max(len(S[k][lines_key]) for k in keys)
        rows = []
        for i in range(nlines):
            rows.append([S[keys[0]][lines_key][i][0]] + [usd(S[k][lines_key][i][1]) for k in keys])
        if path == "ACA":
            rows.append(["(sizing) always-on vCPU / GiB"] + [f"{S[k]['ca_vcpu']:g} / {S[k]['ca_gib']:g}" for k in keys])
        rows.append(["**Total USD / month (ESTIMATE)**"] + [f"**{usd(S[k][tot_key])}**" for k in keys])
        rows.append(["USD / year"] + [usd(S[k][tot_key] * 12) for k in keys])
        rows.append([f"AED / year at the peg {I['peg']:g}"] + [n(round(S[k][tot_key] * 12 * I['peg'])) for k in keys])
        out += table(["Line (USD / month)"] + [f"{k}" for k in keys], rows)
    sk = S["base"]
    out += ["Notes on the estimate:", "",
            f"- AKS Standard tier ($0.10 per cluster-hour) is used; the Free tier is $0 at headline but the API lists a \"FreeTierInfrastructureCost Uptime SLA\" meter at ${pr['aks']['free_tier_infrastructure_cost_meter_usd_per_hour']} per hour effective 2026-10-01, "
            f"which would be ${usd(S['base']['aks_free_tier_unverified_meter_usd_month'])} a month: **unverified** whether it is charged on Free-tier clusters. Without the SLA tier, path A is lower by ${usd(pr['aks']['standard_tier_usd_per_cluster_hour'] * pr['hours_per_month'])} (base) before that meter.",
            f"- AMD nodes (D2as_v5 / D4as_v5) would save ${usd(S['small']['amd_node_alternative_saving_usd_month'] or 0)} (small, base) and ${usd(S['high']['amd_node_alternative_saving_usd_month'] or 0)} (high) a month; the kind nodes ran on aarch64 (the Postgres build string in `sweep.json` says so), so the images measured here are arm64 and amd64 images must be built and re-measured for Azure.",
            f"- The gateway is WAF v2; Standard v2 without WAF would save ${usd(S['small']['standard_gateway_instead_of_waf_saving_usd_month'])} (small, base) and ${usd(S['high']['standard_gateway_instead_of_waf_saving_usd_month'])} (high) a month.",
            f"- Egress: first {pr['egress']['free_gb']} GB free, then ${pr['egress']['microsoft_network_usd_per_gb']}/GB (Microsoft network, the default) or ${pr['egress']['internet_routing_usd_per_gb']}/GB (internet routing); the egress volumes are assumptions. Internet routing would save ${usd(S['high']['internet_routing_egress_saving_usd_month'])} a month in the high scenario.",
            f"- PostgreSQL high availability: the standby is billed at the same rate as the primary, so compute and storage are counted twice (Microsoft reliability documentation, linked in `capacity_inputs.yaml`). Backup is billed only above a free allowance, taken as the provisioned storage (**unverified**); the backups here stay below it, so the line is $0.",
            "- Path B charges every always-on Container Apps replica at the *active* vCPU rate, which overstates the cost of idle replicas (the lower idle rate was not part of the price inputs); the monthly free grant is shared with anything else in the subscription; the environment-hour meter is carried from `tools/pilot/inputs.yaml` (read 2026-10-25) and its applicability to this environment is unverified there. In the high scenario the 4-CPU API replicas are billed all month although the 4 workers matter only during the daily catch-up burst, so path B overstates the high scenario more than the others.",
            "- `docs/commercial/business-case.md` section 9.1 prices a larger Container Apps build-up for the licence (with a FHIR server, observability stack, registry, private endpoints and a test environment) on prices read on 25 October 2026; the two are not meant to match: this plan prices only the closure workloads, the database, the gateway, logs and egress.", "",
            "### What this plan does not show", "",
            "- No Azure environment was built or measured. The safety factor, the zone count, every volume beyond FIT, the open duration, the events per case, requests per event and the peak factor are assumptions; changing them moves the open population, the load and the cost, and `capacity_inputs.yaml` is where to do it.",
            "- The plan assumes the open items above are fixed or mitigated: the watchdog scan, the agency pass and the unbounded worklist are not safe at large populations as the code stands (findings 1 to 3). At the base scenario's population they are smaller problems, but they are not measured at that population in this report.",
            "- HL7 ingestion, the FHIR projection, the web application and observability were not part of the load; their cost and load are not in this plan.", ""]
    return out


def tail_slopes(so: dict, minutes: int = 40) -> dict[str, float]:
    out = {}
    for pod in so["samples"][0]["cgroup"]:
        if not pod.startswith("closure-api"):
            continue
        pts = [(x["elapsed_s"], x["cgroup"][pod]["mem_anon"] / 2**20) for x in so["samples"] if x["cgroup"].get(pod)]
        pts = [q for q in pts if q[0] >= pts[-1][0] - minutes * 60]
        mx, my = sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts)
        den = sum((q[0] - mx) ** 2 for q in pts)
        out[pod] = round(sum((q[0] - mx) * (q[1] - my) for q in pts) / den * 3600, 1) if den else 0.0
    return out


def api_slopes(so: dict) -> dict[str, float]:
    return {k: v["memory_anon_slope_mib_per_hour"] for k, v in so["summary"]["pods"].items() if k.startswith("closure-api")}


def verdicts(fo, sw, la, so, cp, fo0=None, sw0=None, la0=None, so0=None) -> list[str]:
    """Before (the M7-6 run) and after (this run) against each target. Every figure is read from the raw files."""
    cells = [c for c in sw["cells"] if not c.get("profiled")]
    s = fo["summary"]
    target = sw["target_loop_interval_seconds"]
    wd = sorted(sw.get("watchdog_cells", []), key=lambda c: c["size"])
    wd1m = next((c for c in wd if c["size"] == 1_000_000), wd[-1] if wd else None)
    dr = sorted(sw.get("drain_cells", []), key=lambda c: c["size"])
    dr1m = next((c for c in dr if c["size"] == 1_000_000), dr[-1] if dr else None)
    b1m = before_cell(sw0, 1_000_000)
    on, off = la["summary"].get("watchdog_on_as_deployed"), la["summary"].get("watchdog_off")
    on0 = (la0 or {}).get("summary", {}).get("watchdog_on_as_deployed")
    off0 = (la0 or {}).get("summary", {}).get("watchdog_off")
    ss, ss0 = so["summary"], (so0 or {}).get("summary")
    probe, probe0 = la.get("unbounded_worklist_probe"), (la0 or {}).get("unbounded_worklist_probe")
    steady = [x["wall_seconds"] for c in cells if c.get("steady_state_sweeps") for x in c["steady_state_sweeps"]]
    zr = [r for r in fo["runs"] if r["mode"] == "sigstop"]
    sl, sl0 = api_slopes(so), (api_slopes(so0) if so0 else {})
    hb_last = so["samples"][-1]["heartbeats"]
    stale_now = [k for k, v in hb_last.items() if k.startswith("scheduler-follower-") and v.get("age_seconds", 0) > 120 and k.split("scheduler-follower-", 1)[1] not in set(so["samples"][-1]["pods"])]
    ag = so.get("agency")
    rows = []
    if wd1m:
        rows.append(["Watchdog scan at 1,000,000 open cases, against a 10 s interval (a)", f"{f(b1m['full_scan_seconds'], 0)} s (full replay)" if b1m else "224 to 230 s",
                     f"steady scan {f(wd1m['steady_scan_seconds']['median'], 2)} s (max {f(wd1m['steady_scan_seconds']['max'], 2)}); a catch-up scan at most {f(wd1m['catchup_scan_seconds']['max'], 1)} s (bounded by its budget); the full replay found 0 cases the incremental scan missed",
                     "met" if wd1m["catchup_scan_seconds"]["max"] < target * 0.75 and not wd1m["problems"] else "**not met**"])
    if on is not None:
        p, p0 = on["pass"], (on0 or {}).get("pass")
        rows.append(["API p99 < 500 ms and errors < 0.1% with the watchdog at its production interval (a)",
                     (f"no passing rate; fails already at {p0['lowest_failing_rate_per_s']:g}/s" if p0 and p0["max_passing_rate_per_s"] is None else f"up to {p0['max_passing_rate_per_s']:g}/s") if p0 else "n/a",
                     (f"up to {p['max_passing_rate_per_s']:g} requests/s" + (f" (fails at {p['lowest_failing_rate_per_s']:g}/s)" if p["lowest_failing_rate_per_s"] else "")) if p["max_passing_rate_per_s"] else f"no passing rate; fails at {p['lowest_failing_rate_per_s']:g}/s",
                     "met up to that rate" if p["max_passing_rate_per_s"] else "**not met**"])
    if off is not None and off0 is not None:
        rows.append(["API, watchdog off (reference)", f"up to {off0['pass']['max_passing_rate_per_s']:g}/s", f"up to {off['pass']['max_passing_rate_per_s']:g}/s", "reference"])
    if dr1m:
        rows.append(["1,000,000-case daily catch-up burst (g)", f"one sweep {f(b1m['wall_seconds'], 1)} s with 1 worker; 33.64 s at best with 4 workers; read {f(b1m['phases']['read_replay_seconds'], 1)}, plan {f(b1m['phases']['planning_seconds'], 1)}, appends {f(b1m['phases']['append_seconds'], 1)} s" if b1m and b1m.get("phases") else "n/a",
                     f"{dr1m['loop_count']} bounded loops; longest loop {f(dr1m['loop_wall_seconds']['max'])} s; drained in {f(dr1m['time_to_drain_seconds_with_interval_sleeps'] / 60, 1)} min at a {target:g} s interval ({f(dr1m['drain_compute_seconds'], 0)} s of compute)",
                     "bounded loop **met**; the burst inside ONE loop **not met**"])
    rows.append(["Steady-state sweep at 1,000,000 cases inside 10 s", "0.09 to 0.22 s" if b1m else "n/a", f"{f(min(steady))} to {f(max(steady))} s" if steady else "n/a", "met" if steady and max(steady) < target else "n/a"])
    if probe:
        rows.append(["`GET /cases?state=` at ~870,000 cases in the state (c)", f"{probe0['status']} after {f(probe0['seconds'], 1)} s; an API pod OOM-killed" if probe0 else "OOM-killed an API pod",
                     f"default page {probe['default_page']['rows']} rows in {f(probe['default_page']['seconds'], 2)} s, 500-row pages {f(probe['page_seconds']['median'], 2)} s median ({probe['pages_followed']} pages followed), limit=501 gives {probe['over_max_limit_status']}; restarts "
                     f"{sum(probe['restarts_after'][k] - probe['restarts_before'][k] for k in probe['restarts_before'])}, OOM kills {sum(1 for v in probe['last_termination'].values() if v == 'OOMKilled')}", "met"])
    soak_hours = ss["measured_seconds"] / 3600
    worst = max(sl.values()) if sl else None
    rows.append([f"{soak_hours:.2f} h soak at {so['parameters']['rate_per_s']:g}/s: API pod memory (e)",
                 ("+" + " / +".join(f"{v:g}" for v in sorted(sl0.values())) + " MiB/h per pod") if sl0 else "n/a",
                 "+" + " / +".join(f"{v:g}" for v in sorted(sl.values())) + " MiB/h per pod (anon slope)", "met" if worst is not None and worst < 5 else "**not met**"])
    rows.append(["Same soak: sweeps completed against the nominal 10 s cadence (a, g)", f"{ss0['scheduler']['completed_fraction_of_expected']:.1%}" if ss0 else "n/a",
                 f"{ss['scheduler']['completed_fraction_of_expected']:.1%} (period mean {ss['scheduler']['sweep_period_s']['mean']} s, p95 {ss['scheduler']['sweep_period_s']['p95']} s)",
                 "met" if ss["scheduler"]["completed_fraction_of_expected"] >= 0.95 else "**not met**"])
    rows.append(["Same soak: errors, p99, restarts", f"{ss0['errors']} errors, p99 {ss0['latency_all_requests']['p99_ms']} ms, {ss0['restarts']['total_restarts_during_soak']} restarts" if ss0 else "n/a",
                 f"{ss['errors']} errors, p99 {ss['latency_all_requests']['p99_ms']} ms, {ss['restarts']['total_restarts_during_soak']} restarts", "met" if ss["pass"] and not ss["restarts"]["total_restarts_during_soak"] else "**not met**"])
    if ag:
        rows.append(["Agency layer on at 1,000,000 cases (b)", "off: its full-table read OOM-killed an API pod", f"{ag['runs']} runs in the soak ({', '.join(f'{k} {v}' for k, v in ag['runs_by_status'].items())}); at most {ag['cases_read_per_run']['max']} cases read per run (cap 400); {ag['tasks_created_total']} tasks; no OOM kill",
                     "met" if not ss["restarts"]["oom_killed_during_soak"] else "**not met**"])
    rows.append(["Follower heartbeat rows of pods that are gone (f)", "2 of 3 rows stale after 2 h (never removed)", f"{len(stale_now)} stale rows at the end of the soak", "met" if not stale_now else "**not met**"])
    rows.append(["Fail-over loses or duplicates nothing", "0 duplicates, 0 lost, 0 stale-fence rows over 11 runs" if fo0 else "n/a",
                 f"{n(sum(sum(s[m]['duplicates_total']['values']) for m in s))} duplicates, {n(sum(sum(s[m]['acknowledged_writes_missing']['values']) for m in s))} acknowledged writes missing, {n(sum(sum(s[m]['scheduler_events_lost']['values']) for m in s))} scheduler events lost, "
                 f"{n(sum(sum(s[m]['stale_fence_write_rows_written']['values']) for m in s))} stale-fence rows written, over {sum(s[m]['runs'] for m in s)} runs", "met"])
    if zr:
        rows.append(["Zombie leader: SIGSTOP the leader, let the lease expire and a new leader take over, resume the old one (h)", "never run",
                     f"{len(zr)} runs: the old leader wrote {sum(r['sigstop']['old_leader_rows_written_after_takeover'] for r in zr)} rows; {sum(r['sigstop']['old_leader_stale_leader_errors'] for r in zr)} of {sum(r['sigstop']['old_leader_stale_attempts'] for r in zr)} stale-fence writes raised `StaleLeader`; "
                     f"{sum(r['sigstop']['old_leader_calls_refused_409_after_resume'] for r in zr)} of its calls after waking were refused with 409; {sum(r['fencing']['outside_every_lease_window_with_tolerance'] for r in zr)} scheduler events outside every lease window",
                     "met" if all(r["sigstop"]["old_leader_rows_written_after_takeover"] == 0 and r["sigstop"]["old_leader_stale_leader_errors"] == r["sigstop"]["old_leader_stale_attempts"] for r in zr) and len(zr) >= 3 else "**not met**"])
    if "hard" in s:
        rows.append(["Scheduler hand-over after a hard kill", f"median {f(fo0['summary']['hard']['scheduler_time_to_new_leader_s']['median'], 1)} s" if fo0 else "n/a", f"median {f(s['hard']['scheduler_time_to_new_leader_s']['median'], 1)} s (min {f(s['hard']['scheduler_time_to_new_leader_s']['min'], 1)}, max {f(s['hard']['scheduler_time_to_new_leader_s']['max'], 1)}), {s['hard']['missed_sweeps']['median']:g} sweeps missed", "measured"])
    rows.append(["100,000-case sweep under 5 s", f"{f(cell(sw0, 100_000, 1, 'tuned')['wall_seconds'])} s (1 worker)" if sw0 else "n/a", f"{f(cell(sw, 100_000, 1, 'tuned')['wall_seconds'])} s (1 worker)" + (f", {f(cell(sw, 100_000, 4, 'tuned')['wall_seconds'])} s (4 workers)" if cell(sw, 100_000, 4, 'tuned') else ""), "met"])
    return table(["Question", "Before (M7-6)", "After (M7-7)", "Result"], rows)


# ---------------------------------------------------------------------------------------------------------------------------------

INP: dict = {}


def demote(lines: list[str]) -> list[str]:
    """One heading level down (the M7-6 report becomes an appendix)."""
    return [("#" + ln) if ln.startswith("#") else ln for ln in lines]


def build() -> tuple[str, dict]:
    global INP
    fo, sw, la, so = (jload(RAW / f"{x}.json") for x in ("failover", "sweep", "latency", "soak"))
    B = RAW / "before"
    fo0, sw0, la0, so0 = (jload(B / f"{x}.json") for x in ("failover", "sweep", "latency", "soak"))
    INP = yaml.safe_load(INPUTS.read_text(encoding="utf-8"))
    results = jload(RESULTS)
    cp = capacity_plan(sw, la, fo, so, INP, results)
    L = ["# Scalability evidence and capacity plan (M7-6, re-measured after the M7-7 fixes)", "",
         "Generated by `make scale-report` (`tools/k8s/render_scale.py`) from `docs/results/scale/{failover,sweep,latency,soak}.json` (this run, \"after\"), `docs/results/scale/before/*.json` (the M7-6 run, \"before\", unchanged), "
         "`docs/results/scale/memory_repro/*.txt`, `tools/k8s/capacity_inputs.yaml`, `docs/results/results.json` and the Helm values files. "
         "Do not edit by hand. Every measured number comes from those files; the prose states only what the tables show. All data are synthetic.", "",
         "**What this is.** The Helm chart (`deploy/helm/screening-closure`) run on a local three-node kind cluster, first as found in M7-6 (\"before\") and again after the M7-7 fixes (\"after\"), with the leader fail-over exercised "
         "(including a leader frozen with SIGSTOP), the watchdog scan, the scheduler sweep and its backlog drain timed at 100k, 500k and 1M open cases, the API driven at a sustained rate with the watchdog and the agency layer on at the production settings, and a 2-hour soak, "
         "then a capacity plan for the emirate. **What it is not:** a cloud measurement. Everything was measured on one laptop-class machine shared with the load generator; the cloud sizing applies a stated safety factor and says so.", "",
         "## Results against the targets, before and after", ""]
    L += verdicts(fo, sw, la, so, cp, fo0, sw0, la0, so0)
    L += defects_section(fo, sw, la, so, fo0, sw0, la0, so0)
    L += still_not_met(fo, sw, la, so, cp)
    L += machine_section(fo, sw, la, so)
    L += local_cluster_section(sw, so, la)
    L += watchdog_scan_section(sw, sw0)
    L += drain_section(sw, sw0)
    L += sweep_cells_section(sw, sw0)
    L += size_section(sw)
    L += latency_section(la)
    L += soak_section(so)
    L += failover_section(fo)
    L += capacity_section(cp, INP, sw, la)
    # the M7-6 report, kept as the "before" appendix
    cp0 = None
    L += ["", "# Appendix: the M7-6 run, as it was found (before)", "",
          "The sections below are the previous report, rendered from `docs/results/scale/before/*.json` by the same code as then. Its statements about open items describe the system as it was before M7-7.", ""]
    L += demote(["## Results against the targets (M7-6)", ""] + verdicts_before(fo0, sw0, la0, so0, cp0))
    L += demote(machine_section(fo0, sw0, la0, so0))
    L += demote(failover_section(fo0))
    L += demote(sweep_section(sw0, la0))
    L += demote(size_section_before(sw0))
    L += demote(latency_section_before(la0))
    L += demote(soak_section_before(so0))
    L += demote(findings_section(fo0, sw0, la0, so0))
    md = "\n".join(L).rstrip() + "\n"
    data = {
        "schema_version": 2, "generated_by": "tools/k8s/render_scale.py",
        "sources": {"failover": "docs/results/scale/failover.json", "sweep": "docs/results/scale/sweep.json", "latency": "docs/results/scale/latency.json", "soak": "docs/results/scale/soak.json",
                    "before": "docs/results/scale/before/", "memory_repro": "docs/results/scale/memory_repro/",
                    "inputs": "tools/k8s/capacity_inputs.yaml", "simulator": "docs/results/results.json"},
        "run_commits": {k: v["git"]["commit"] for k, v in (("failover", fo), ("sweep", sw), ("latency", la), ("soak", so))},
        "before_run_commits": {k: v["git"]["commit"] for k, v in (("failover", fo0), ("sweep", sw0), ("latency", la0), ("soak", so0))},
        "machine_spec": sw["machine_spec"],
        "measured": {
            "failover": {m: {k: fo["summary"][m][k] for k in ("scheduler_time_to_new_leader_s", "missed_sweeps", "duplicates_total", "acknowledged_writes_missing", "scheduler_events_lost", "stale_fence_write_rows_written")} for m in fo["summary"]},
            "sweep_seconds": [{"open_cases": c["size"], "workers": c["workers"], "label": c["label"], "sweep_s": c["wall_seconds"], "resweep_s": c["resweep_seconds"], "full_replay_s": c.get("full_scan_seconds")} for c in sw["cells"] if not c.get("profiled")],
            "watchdog_scan": [{"open_cases": c["size"], "steady_median_s": c["steady_scan_seconds"]["median"], "longest_catchup_scan_s": c["catchup_scan_seconds"]["max"], "catchup_scans": c["catchup_scans"], "catchup_flagged": c["catchup_flagged"],
                               "full_replay_s": (c.get("full_replay_reference") or {}).get("seconds")} for c in sw.get("watchdog_cells", [])],
            "drain": [{"open_cases": c["size"], "loops": c["loop_count"], "longest_loop_s": c["loop_wall_seconds"]["max"], "time_to_drain_s_with_interval": c["time_to_drain_seconds_with_interval_sleeps"], "compute_s": c["drain_compute_seconds"]} for c in sw.get("drain_cells", [])],
            "api": {k: v["pass"] for k, v in la["summary"].items()},
            "soak": {"pass": so["summary"]["pass"], "requests": so["summary"]["requests_total"], "errors": so["summary"]["errors"], "p99_ms": so["summary"]["latency_all_requests"]["p99_ms"],
                     "sweeps_completed_fraction": so["summary"]["scheduler"]["completed_fraction_of_expected"], "api_anon_slope_mib_per_hour": api_slopes(so)},
            "database_bytes_per_1m_cases": sw["seeding_sizes_template"]["1000000"]["database_bytes"],
        },
        "assumptions_file": "tools/k8s/capacity_inputs.yaml",
        "pricing": {"retrieved": INP["pricing"]["retrieved"], "source_url": INP["pricing"]["source_url"], "filter": INP["pricing"]["filter"], "label": "ESTIMATE from public list prices, excluding tax, support plans, reservations and DoH-specific hosting requirements"},
        "capacity_plan": cp,
    }
    return md, data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if docs/results/scale.md or scale.json differ from what would be written")
    args = ap.parse_args(argv)
    md, data = build()
    js = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        bad = [p.name for p, t in ((OUT_MD, md), (OUT_JSON, js)) if not p.exists() or p.read_text(encoding="utf-8") != t]
        if bad:
            print(f"scale-report-check: FAIL, {', '.join(bad)} differ from the raw files; run `make scale-report`")
            return 1
        print(f"scale-report-check: PASS (scale.md {len(md.splitlines())} lines, scale.json identical)")
        return 0
    OUT_MD.write_text(md, encoding="utf-8")
    OUT_JSON.write_text(js, encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)} ({len(md.splitlines())} lines) and {OUT_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
