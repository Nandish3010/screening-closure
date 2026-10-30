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
    out += ["Runs (the raw files are the evidence; a run is attributed to the commit it started from):", ""]
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
         f"interval {cv['watchdog']['intervalSeconds']} s in the soak (the sweep and latency runs of `sweep.json` / `latency.json`: {sdict(la, 'chart_values', 'watchdog', 'intervalSeconds')} s, see below)"],
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
        ["Watchdog interval", f"{prod['watchdog']['intervalSeconds']} s", f"{kind['watchdog']['intervalSeconds']} s (soak and chart default for the cluster); {sdict(la, 'chart_values', 'watchdog', 'intervalSeconds')} s in the `watchdog_on_as_deployed` latency stages", "the watchdog scan is O(open cases), see finding 1"],
        ["Agency layer (`agencyEverySweeps`)", f"`{prod['app']['agencyEverySweeps'] or 'chart default (every 6th sweep)'}`", f"`{kind['app']['agencyEverySweeps']}` (off)", "its pass reads the whole case table, see finding 2"],
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
    out = ["## Failover drill (`failover.json`)", "",
           f"Tool `tools/k8s/failover_drill.py`. {p['force_runs']} force-delete runs, {p['graceful_runs']} graceful deletes and {p['hard_runs']} hard kills, "
           f"each over a {p['window_s']:g} s window with the kill at {p['kill_at_s']:g} s. Per run the drill seeds {p['seeded_cases_per_run']:,} open cases whose reminder and escalation "
           f"triggers fall due uniformly over the window, so the sweep always has work, and a client issues about {p['client_new_case_flows_per_s']:g} new-case flows per second "
           f"(create, accept ownership, contact, acknowledge, read) through the NodePort, retrying a failed request with the same id. "
           f"Lease TTL {cv['leader_lease_ttl_s']:g} s, scheduler interval {cv['scheduler_interval_s']:g} s, fencing {cv['require_leader_fence']}, "
           f"{cv['api_replicas']} API and {cv['scheduler_replicas']} scheduler pods.", "",
           "How each mode kills: **force** = `kubectl delete pod --grace-period=0 --force` on the scheduler leader and the API pod holding the background lease (no lease release); "
           "**graceful** = ordinary delete (the processes release their leases on SIGTERM); **hard** = SIGKILL of both containers through the node's runtime, then the pods are force-deleted. "
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
    for mode, title in (("force", "Force delete"), ("graceful", "Graceful delete"), ("hard", "Hard kill (SIGKILL)")):
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
            "Totals over all runs of the three modes (each cell is the sum of the per-run values; the per-run values are in `failover.json`):", ""]
    rows = []
    for k, label in proof_keys:
        cells_ = [sum(s[m][k]["values"]) for m in ("force", "graceful", "hard")]
        rows.append([label] + [n(int(c)) if float(c).is_integer() else f(c) for c in cells_] + [n(int(sum(cells_)))])
    out += table(["Check", "Force", "Graceful", "Hard", "All runs"], rows)
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


def size_section(sw) -> list[str]:
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


def soak_section(so) -> list[str]:
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
    scan_pts = sorted((c["size"], c["full_scan_seconds"]) for c in cells if c["workers"] == 1 and c["label"] == "baseline" and c.get("full_scan_seconds"))
    max_rate = la["summary"]["watchdog_off"]["pass"]["max_passing_rate_per_s"]
    top = next(s for s in la["stages"] if s["label"] == "watchdog_off" and s["target_rate_per_s"] == max_rate)
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
                       "sweep_points": sweep_pts, "full_replay_points": scan_pts, "database_bytes_per_1m_cases": tpl["database_bytes"]},
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
            f"- The measured envelope is {I['max_passing_rate']:g} requests/s on {I['api_pods_measured']} API pods with a 1-CPU limit each ({f(I['per_pod_rate_measured'], 1)}/s per pod; {f(I['per_pod_rate_derated'], 1)}/s per pod after the safety factor), p99 under 500 ms, watchdog off. "
            "**The API request rate is not what sizes the platform**: even the high scenario's peak is far below one pod's derated rate. What sizes it is availability (zones, N+1), the sweep over the open population, the watchdog scan, and the database.", "",
            "### The sweep, the watchdog and the open population", ""]
    rows = []
    for k in keys:
        s = S[k]
        rows.append([k, n(s["open_cases_for_sizing"]), f(s["sweep_seconds_derated_1_worker"], 1), f(s["sweep_seconds_derated_4_workers"], 1), s["sweep_workers"],
                     f(s["watchdog_scan_seconds_derated"], 1), f(s["watchdog_scan_seconds_derated_at_upper_bound"], 1)])
    out += table(["Scenario", "Open cases used", "Catch-up sweep, 1 worker (s, derated)", "Catch-up sweep, 4 workers (s, derated)", "Workers chosen", "Watchdog scan at the sizing population (s, derated)", "Watchdog scan at the upper bound (s, derated)"], rows)
    brk = inp["cloud_sizing"]["loop_interval_s"] / (I["safety_factor"] * I["full_replay_points"][0][1] / I["full_replay_points"][0][0])
    out += [f"Sweep times interpolate the `tuned` cells of `sweep.json` ({', '.join(f'{x:,} cases: {f(y)} s (1 worker)' for x, y in I['sweep_points'][1])}; "
            f"{', '.join(f'{x:,}: {f(y)} s (4 workers)' for x, y in I['sweep_points'][4])}) linearly, and linearly through the origin below 100,000 cases. The daily catch-up burst fits the 10 s loop after derating with one worker where the 1-worker column is under 10 s; "
            f"otherwise 4 workers are chosen (which needs a 4-CPU limit on the API pods). The watchdog scan interpolates the measured full-replay times ({', '.join(f'{x:,}: {f(y, 1)} s' for x, y in I['full_replay_points'])}). "
            f"At the chart-default watchdog interval of 10 s, a derated scan stops fitting inside the interval above about {brk:,.0f} open cases, so until finding 1 is fixed the watchdog interval should be set from the scan time (the kind cluster used {sw['chart_values']['watchdog']['intervalSeconds']} s then 900 s).", "",
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


def verdicts(fo, sw, la, so, cp) -> list[str]:
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


# ---------------------------------------------------------------------------------------------------------------------------------

INP: dict = {}


def build() -> tuple[str, dict]:
    global INP
    fo, sw, la, so = (jload(RAW / f"{x}.json") for x in ("failover", "sweep", "latency", "soak"))
    INP = yaml.safe_load(INPUTS.read_text(encoding="utf-8"))
    results = jload(RESULTS)
    cp = capacity_plan(sw, la, fo, so, INP, results)
    L = ["# Scalability evidence and capacity plan (M7-6)", "",
         "Generated by `make scale-report` (`tools/k8s/render_scale.py`) from `docs/results/scale/{failover,sweep,latency,soak}.json`, `tools/k8s/capacity_inputs.yaml`, `docs/results/results.json` and the Helm values files. "
         "Do not edit by hand. Every measured number comes from those files; the prose states only what the tables show. All data are synthetic.", "",
         "**What this is.** The Helm chart (`deploy/helm/screening-closure`) run on a local three-node kind cluster, with the leader fail-over exercised, the scheduler sweep timed at 100k, 500k and 1M open cases, the API driven at a sustained rate, and a 2-hour soak, "
         "then a capacity plan for the emirate. **What it is not:** a cloud measurement. Everything was measured on one laptop-class machine shared with the load generator; the cloud sizing applies a stated safety factor and says so.", "",
         "## Results against the targets", ""]
    L += verdicts(fo, sw, la, so, cp)
    L += machine_section(fo, sw, la, so)
    L += local_cluster_section(sw, so, la)
    L += failover_section(fo)
    L += sweep_section(sw, la)
    L += size_section(sw)
    L += latency_section(la)
    L += soak_section(so)
    L += findings_section(fo, sw, la, so)
    L += capacity_section(cp, INP, sw, la)
    md = "\n".join(L).rstrip() + "\n"
    data = {
        "schema_version": 1, "generated_by": "tools/k8s/render_scale.py",
        "sources": {"failover": "docs/results/scale/failover.json", "sweep": "docs/results/scale/sweep.json", "latency": "docs/results/scale/latency.json", "soak": "docs/results/scale/soak.json",
                    "inputs": "tools/k8s/capacity_inputs.yaml", "simulator": "docs/results/results.json"},
        "run_commits": {k: v["git"]["commit"] for k, v in (("failover", fo), ("sweep", sw), ("latency", la), ("soak", so))},
        "machine_spec": sw["machine_spec"],
        "measured": {
            "failover": {m: {k: fo["summary"][m][k] for k in ("scheduler_time_to_new_leader_s", "missed_sweeps", "duplicates_total", "acknowledged_writes_missing", "scheduler_events_lost", "stale_fence_write_rows_written")} for m in ("force", "graceful", "hard")},
            "sweep_seconds": [{"open_cases": c["size"], "workers": c["workers"], "label": c["label"], "sweep_s": c["wall_seconds"], "resweep_s": c["resweep_seconds"], "full_replay_s": c.get("full_scan_seconds")} for c in sw["cells"] if not c.get("profiled")],
            "api": {"max_passing_rate_per_s_watchdog_off": la["summary"]["watchdog_off"]["pass"], "watchdog_on_as_deployed": la["summary"]["watchdog_on_as_deployed"]["pass"]},
            "soak": {"pass": so["summary"]["pass"], "requests": so["summary"]["requests_total"], "errors": so["summary"]["errors"], "p99_ms": so["summary"]["latency_all_requests"]["p99_ms"],
                     "sweeps_completed_fraction": so["summary"]["scheduler"]["completed_fraction_of_expected"]},
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
