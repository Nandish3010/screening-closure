#!/usr/bin/env python3
"""Finding (e), step 1 (M7-7): is the growth of the API pods' memory a Python-level LEAK? Repeats the full-replay watchdog scan
(`run_idle_scan`, the scan that ran in the API) in one process against the throw-away database `closure_load` on the compose
Postgres (seed it first: `python tools/load/seed_cases.py --size 20000`), takes a tracemalloc snapshot after each scan and
diffs a late one against an early one. Result recorded in docs/results/scale/memory_repro/tracemalloc_20k.txt: the memory still
allocated after a scan is flat (0.07 MiB) while every scan allocates a peak of about 7.5 MiB, so objects are not retained; step 2
(tools/load/memory_repro_rss.py) measures the process's resident memory under glibc.

    PYTHONPATH=api .venv/bin/python tools/load/memory_repro_tracemalloc.py
"""
import gc, os, sys, tracemalloc, resource
sys.path.insert(0, "api")
from datetime import datetime, UTC
from app.config import settings
from app.db import create_pool
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore
from app.watchdog import run_idle_scan
import subprocess

url = "postgresql://closure:closure_dev_password@localhost:5433/closure_load"
pool = create_pool(url, max_size=2)
store = PostgresEventStore(pool)
P = load_pathway(settings.pathway_path)
now = datetime(2026, 3, 10, 8, tzinfo=UTC)
rss_now = lambda: int(subprocess.run(['ps','-o','rss=','-p',str(os.getpid())],capture_output=True,text=True).stdout)/1024
tracemalloc.start(25)
snaps = []
for i in range(7):
    r = run_idle_scan(store, P, now, 7)
    gc.collect()
    cur, peak = tracemalloc.get_traced_memory()
    rss = rss_now()
    print(f"scan {i}: evaluated={r.cases_evaluated} flagged={r.flagged} traced={cur/2**20:.1f}MiB peak={peak/2**20:.1f}MiB rss={rss:.0f}MiB", flush=True)
    snaps.append(tracemalloc.take_snapshot())
    tracemalloc.reset_peak()
print("--- growth between scan 2 and scan 6 (top by size diff) ---")
for st in snaps[6].compare_to(snaps[2], "lineno")[:12]:
    print(st)
print("--- by traceback of the biggest grower ---")
top = snaps[6].compare_to(snaps[2], "traceback")[:1]
for st in top:
    print(st)
    for l in st.traceback.format()[-14:]:
        print(l)
