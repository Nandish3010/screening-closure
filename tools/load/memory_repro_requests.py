#!/usr/bin/env python3
"""Finding (e), step 3 (M7-7): the soak mix (create, event, case, timeline, notifications, worklist) in process under tracemalloc against the test database: what do the requests retain? PYTHONPATH=api .venv/bin/python tools/load/memory_repro_requests.py"""
import os, sys, gc, tracemalloc, random, uuid
sys.path.insert(0, "api"); sys.path.insert(0, "api/tests")
os.environ.setdefault("DATABASE_URL", "postgresql://closure:closure_dev_password@localhost:5433/closure_test")
from api_helpers import make_client, login_headers
from app.db import create_pool, run_migrations
from app.store_pg import PostgresEventStore
from app.routers import cases
pool = create_pool(os.environ["DATABASE_URL"], max_size=4); run_migrations(pool)
cases.store = PostgresEventStore(pool)
client = make_client()
H = login_headers(client, "coordinator", "leak-coord")
HP = login_headers(client, "primary_care_owner", "Dr. Layla Haddad")
ids = []
def round_():
    for _ in range(40):
        r = client.post("/cases", json={"patient_ref": f"Patient/{uuid.uuid4().hex[:8]}"}, headers=H); ids.append(r.json()["case_id"])
    for cid in random.sample(ids, 40):
        client.post(f"/cases/{cid}/events", json={"type": "acknowledge", "payload": {}, "event_id": str(uuid.uuid4())}, headers=H)
        client.get(f"/cases/{cid}", headers=H); client.get(f"/cases/{cid}/timeline", headers=H); client.get(f"/cases/{cid}/notifications", headers=H)
    client.get("/cases?state=lost_contact", headers=H)
for _ in range(5): round_()
gc.collect(); tracemalloc.start(8); base = tracemalloc.take_snapshot()
for _ in range(60): round_()
gc.collect(); snap = tracemalloc.take_snapshot()
print("traced now MiB", tracemalloc.get_traced_memory()[0]/2**20)
for st in snap.compare_to(base, "lineno")[:12]: print(st)
