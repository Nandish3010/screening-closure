#!/usr/bin/env python3
"""Finding (e), step 2 (M7-7): the resident memory of a Linux/glibc process across repeated watchdog scans run on pool threads (as a
FastAPI threadpool does), with the allocator's own accounting (`mallinfo2`): bytes in use versus bytes free inside the arenas.
Run it INSIDE the API image (python 3.12, glibc, the app code in /app) against a database of open cases:

    docker run --rm --add-host=host.docker.internal:host-gateway -e REPRO_URL=postgresql://USER:PW@host.docker.internal:PORT/closure_load \
      -e REPRO_NOW=2026-03-03T08:00:00+00:00 -e APP_ENV=dev -e PATHWAY_PATH=/config/pathways/colorectal_fit.yaml -e CALENDAR_PATH=/config/calendar.yaml \
      -v $PWD/tools/load:/m77:ro -v $PWD/config:/config:ro --entrypoint python screening-closure-api:m76 /m77/memory_repro_rss.py old|new SCANS [THREADS]

`old` patches in the pre-M7-7 `iter_open_case_models` (every open case id fetched into one list first) and runs the full replay scan;
`new` runs the incremental scan (the paged iterator and the index). REPRO_NOW before the seed's sweep time keeps the scans from flagging
anything, so every scan does the same work. Results: docs/results/scale/memory_repro/."""
import ctypes, gc, os, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
sys.path.insert(0, "/app")
from app.config import settings
from app.db import create_pool
from app.events import Event
from app.pathway import load_pathway
from app.state_machine import InvalidTransition, replay_model
from app.store_pg import PostgresEventStore
from app.watchdog import run_idle_scan, run_incremental_scan

mode, scans = sys.argv[1], int(sys.argv[2])
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 8
pool = create_pool(os.environ["REPRO_URL"], max_size=4)
store = PostgresEventStore(pool)
P = load_pathway(settings.pathway_path)
now = datetime.fromisoformat(os.environ.get("REPRO_NOW", "2026-03-10T08:00:00+00:00"))


def old_iter(self, pathway, only=None, chunk=500, shard=None, states=None):
    """api/app/store_pg.py before M7-7: every open case id is fetched into one list, then replayed chunk by chunk."""
    sql = "SELECT id FROM cases WHERE pathway = %s AND current_state <> %s"
    params = [pathway.name, pathway.terminal_state]
    with self._pool.connection() as conn:
        ids = [r[0] for r in conn.execute(sql + " ORDER BY id", params).fetchall()]
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        by_case = self._read_chunk(part)
        for case_id in part:
            try:
                yield case_id, replay_model(by_case.get(case_id, []), pathway), None
            except InvalidTransition as exc:
                yield case_id, None, str(exc)


class MI(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


libc = ctypes.CDLL("libc.so.6")
libc.mallinfo2.restype = MI


def vm(key):
    for line in open("/proc/self/status"):
        if line.startswith(key):
            return int(line.split()[1]) / 1024


if mode == "old":
    PostgresEventStore.iter_open_case_models = old_iter
import time as _t0
T0 = _t0.time()
ex = ThreadPoolExecutor(threads)
print("scan, elapsed s, VmRSS MiB, VmHWM MiB, malloc in-use MiB, malloc free-in-arenas MiB, evaluated", flush=True)
for i in range(scans):
    r = ex.submit(lambda: run_idle_scan(store, P, now, 7) if mode in ("old", "pagedfull") else run_incremental_scan(store, P, now, 7, budget_seconds=1e9, reconcile_slice=1000)).result()
    gc.collect()
    m = libc.mallinfo2()
    import time as _t
    print(i, round(_t.time() - T0), round(vm("VmRSS"), 1), round(vm("VmHWM"), 1), round((m.uordblks + m.hblkhd) / 2**20, 1), round(m.fordblks / 2**20, 1), r.cases_evaluated, flush=True)
