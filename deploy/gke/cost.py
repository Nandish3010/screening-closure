#!/usr/bin/env python3
"""Budget guard for the GKE load test (deploy/gke/loadtest.sh): the expected cost from the machine types, node counts and duration.

Prints the cost table with its arithmetic and exits 2 when the WORST CASE (the wall-clock cap, with the contingency factor) is above
--max-usd (default 30). No cloud calls: the prices below are a table in this file, and the table is an ESTIMATE.

Prices: us-central1 on-demand list prices as the author knew them (e2 machine types, pd-ssd / pd-balanced per GB-month, the GKE
cluster management fee of USD 0.10 per cluster-hour). They were NOT fetched when this was written (no cloud access, no
sign-in). A region factor scales them: me-central1 (Doha) 1.25 from the third-party tracker figure in docs/governance/hosting-gcp.md
(e2-standard-2 USD 0.0814/h there against 0.067 in us-central1 = 1.215, rounded up), me-central2 (Dammam) 1.60 from the same
document (0.1072/0.067), any region not listed 1.60. A CONTINGENCY factor (default 1.25) covers price drift, a longer wall clock
than planned, logging and the lines this table leaves out (see LEFT_OUT); the worst case also assumes the run lasts the whole cap.
Check the Google Cloud pricing calculator before the run.

    python3 deploy/gke/cost.py                       # table for the defaults / the environment
    python3 deploy/gke/cost.py --max-usd 30 --json   # machine-readable, exit 2 when over budget
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# USD per hour, us-central1, on demand
MACHINE_USD_H = {"e2-standard-2": 0.067006, "e2-standard-4": 0.134012, "e2-standard-8": 0.268024, "e2-standard-16": 0.536048}
VCPU = {"e2-standard-2": 2, "e2-standard-4": 4, "e2-standard-8": 8, "e2-standard-16": 16}
DISK_USD_GB_MONTH = {"pd-ssd": 0.17, "pd-balanced": 0.10}
MGMT_FEE_USD_H = 0.10          # one zonal Standard cluster; the free-tier credit (USD 74.40 a month) is NOT assumed
AR_USD_GB_MONTH = 0.10
HOURS_PER_MONTH = 730.0
REGION_FACTOR = {"us-central1": 1.0, "europe-west4": 1.10, "europe-west1": 1.0, "me-central1": 1.25, "me-central2": 1.60}
DEFAULT_REGION_FACTOR = 1.60
LEFT_OUT = ("Cloud Logging (the cluster is created with SYSTEM logging only), network egress (none leaves the zone: the load generator is in the "
            "cluster), tax, support plans, committed-use discounts, the free tier. Nothing here is a quote.")


def region_factor(region: str) -> float:
    return REGION_FACTOR.get(region, DEFAULT_REGION_FACTOR)


def compute(cfg: dict) -> dict:
    f = region_factor(cfg["region"])
    pools = []
    hourly_nodes = 0.0
    for name, machine, count in (("app", cfg["app_machine"], cfg["app_nodes"]), ("data", cfg["data_machine"], cfg["data_nodes"]),
                                 ("load", cfg["load_machine"], cfg["load_nodes"])):
        if machine not in MACHINE_USD_H:
            raise SystemExit(f"cost.py: no price for machine type {machine!r}; add it to MACHINE_USD_H after checking the price list")
        unit = MACHINE_USD_H[machine] * f
        pools.append({"pool": name, "machine": machine, "nodes": count, "vcpu": VCPU[machine] * count,
                      "usd_per_node_hour": round(unit, 5), "usd_per_hour": round(unit * count, 5),
                      "arithmetic": f"{count} x {MACHINE_USD_H[machine]} x {f} (region factor)"})
        hourly_nodes += unit * count
    nodes_total = cfg["app_nodes"] + cfg["data_nodes"] + cfg["load_nodes"]
    boot_gb = nodes_total * cfg["boot_disk_gb"]
    boot_h = boot_gb * DISK_USD_GB_MONTH["pd-balanced"] * f / HOURS_PER_MONTH
    pg_h = cfg["pg_disk_gb"] * DISK_USD_GB_MONTH["pd-ssd"] * f / HOURS_PER_MONTH
    hourly = hourly_nodes + MGMT_FEE_USD_H + boot_h + pg_h
    lines = [
        {"item": "GKE cluster management fee (1 zonal Standard cluster)", "usd_per_hour": MGMT_FEE_USD_H, "arithmetic": "0.10 per cluster-hour"},
        {"item": "Node boot disks", "usd_per_hour": round(boot_h, 5),
         "arithmetic": f"{nodes_total} nodes x {cfg['boot_disk_gb']} GB = {boot_gb} GB x 0.10 / 730 x {f}"},
        {"item": "Postgres volume (pd-ssd)", "usd_per_hour": round(pg_h, 5), "arithmetic": f"{cfg['pg_disk_gb']} GB x 0.17 / 730 x {f}"},
    ]
    ar = cfg["registry_gb"] * AR_USD_GB_MONTH * f * (cfg["cap_hours"] / HOURS_PER_MONTH)
    expected = hourly * cfg["expected_hours"] + ar
    cap_cost = hourly * cfg["cap_hours"] + ar
    worst = cap_cost * cfg["contingency"]
    return {
        "config": cfg, "region_factor": f, "pools": pools, "other_lines": lines, "node_vcpu_total": sum(p["vcpu"] for p in pools),
        "usd_per_hour": round(hourly, 4), "registry_usd": round(ar, 4),
        "expected_usd": round(expected, 2), "cap_usd": round(cap_cost, 2), "worst_case_usd": round(worst, 2),
        "arithmetic": {
            "expected": f"{hourly:.4f} USD/h x {cfg['expected_hours']} h + registry {ar:.4f} = {expected:.2f}",
            "cap": f"{hourly:.4f} USD/h x {cfg['cap_hours']} h (the wall-clock cap) + registry {ar:.4f} = {cap_cost:.2f}",
            "worst_case": f"{cap_cost:.2f} x {cfg['contingency']} (contingency) = {worst:.2f}",
        },
        "left_out": LEFT_OUT,
    }


def render(r: dict, max_usd: float) -> str:
    c = r["config"]
    out = [f"GKE load test cost estimate (region {c['region']}, zone {c.get('zone') or c['region'] + '-?'}; prices are ESTIMATES, see deploy/gke/cost.py)", "",
           f"{'Line':<56}{'USD/hour':>10}  Arithmetic"]
    for p in r["pools"]:
        out.append(f"{'Node pool ' + p['pool'] + ': ' + str(p['nodes']) + ' x ' + p['machine'] + ' (' + str(p['vcpu']) + ' vCPU)':<56}{p['usd_per_hour']:>10.4f}  {p['arithmetic']}")
    for ln in r["other_lines"]:
        out.append(f"{ln['item']:<56}{ln['usd_per_hour']:>10.4f}  {ln['arithmetic']}")
    out += [f"{'Total per hour':<56}{r['usd_per_hour']:>10.4f}", "",
            f"Expected  ({c['expected_hours']:g} h):  USD {r['expected_usd']:.2f}   = {r['arithmetic']['expected']}",
            f"Cap       ({c['cap_hours']:g} h):  USD {r['cap_usd']:.2f}   = {r['arithmetic']['cap']}",
            f"Worst case (cap x contingency): USD {r['worst_case_usd']:.2f}   = {r['arithmetic']['worst_case']}",
            f"Budget limit: USD {max_usd:g}  ->  {'OK' if r['worst_case_usd'] <= max_usd else 'OVER BUDGET: refusing to run'}",
            f"vCPU requested in the region: {r['node_vcpu_total']} (check the regional CPUS quota)", f"Left out: {r['left_out']}"]
    return "\n".join(out)


def config_from_env() -> dict:
    e = os.environ
    region = e.get("REGION", "me-central1")
    return {
        "region": region, "zone": e.get("ZONE", ""),
        "app_machine": e.get("APP_MACHINE", "e2-standard-2"), "app_nodes": int(e.get("APP_NODES", "3")),
        "data_machine": e.get("DATA_MACHINE", "e2-standard-8"), "data_nodes": int(e.get("DATA_NODES", "1")),
        "load_machine": e.get("LOAD_MACHINE", "e2-standard-4"), "load_nodes": int(e.get("LOAD_NODES", "1")),
        "boot_disk_gb": int(e.get("BOOT_DISK_GB", "30")), "pg_disk_gb": int(e.get("PG_DISK_GB", "120")), "registry_gb": float(e.get("REGISTRY_GB", "2")),
        "expected_hours": float(e.get("EXPECTED_HOURS", "3")), "cap_hours": float(e.get("WALL_CAP_HOURS", "4")),
        "contingency": float(e.get("COST_CONTINGENCY", "1.25")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max-usd", type=float, default=float(os.environ.get("MAX_USD", "30")))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = compute(config_from_env())
    print(json.dumps(r, indent=2) if a.json else render(r, a.max_usd))
    return 0 if r["worst_case_usd"] <= a.max_usd else 2


if __name__ == "__main__":
    sys.exit(main())
