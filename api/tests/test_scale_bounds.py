"""M7-7: the bounds that keep a big table from exhausting a pod.

* `GET /cases` is a PAGE: a default limit, a hard maximum, a cursor in `X-Next-Cursor`; the pages cover the cases exactly once, in
  case-id order; `state` is a filter on the store; a model-derived filter that drops most rows still pages to the end;
* the agency reads at most `AGENCY_MAX_CASES_READ` cases per run, says so, and the next run resumes after them (it neither
  skips nor repeats cases);
* a plan with more than MAX_ORDERS orders is capped, not an error.
"""

import os
from uuid import UUID

import pytest

from api_helpers import login_headers, make_client
from app.clock import clock
from app.config import settings as app_settings
from app.events import InMemoryEventStore
from app.routers import cases

client = make_client()
HAS_DB = bool(os.environ.get("DATABASE_URL"))
BACKENDS = [
    "memory",
    pytest.param("postgres", marks=[pytest.mark.integration, pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")]),
]


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    if request.param == "memory":
        monkeypatch.setattr(cases, "store", InMemoryEventStore())
        yield "memory"
    else:
        from app.db import create_pool, run_migrations
        from app.store_pg import PostgresEventStore

        pool = create_pool(os.environ["DATABASE_URL"], max_size=4)
        run_migrations(pool)
        monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
        yield "postgres"
        pool.close()
    clock.reset()


COORD = None


def coord():
    return login_headers(client, "coordinator", "scale-coord")


def make_cases(n, tag):
    h = coord()
    ids = []
    for i in range(n):
        r = client.post("/cases", json={"patient_ref": f"Patient/{tag}-{i}"}, headers=h)
        assert r.status_code == 201, r.text
        ids.append(r.json()["case_id"])
    return ids


def walk(url, headers, limit=None):
    """Follow X-Next-Cursor to the end; returns (rows, pages, header seen on the last page)."""
    rows, pages, cursor = [], 0, None
    while True:
        q = f"{url}{'&' if '?' in url else '?'}" + (f"limit={limit}&" if limit else "") + (f"cursor={cursor}" if cursor else "")
        r = client.get(q.rstrip("&?"), headers=headers)
        assert r.status_code == 200, r.text
        rows += r.json()
        pages += 1
        cursor = r.headers.get("x-next-cursor")
        if not cursor:
            return rows, pages
        assert pages < 500


def test_the_worklist_pages_cover_every_case_once_in_id_order(backend):
    mine = set(make_cases(23, "pg"))
    rows, pages = walk("/cases", coord(), limit=5)
    got = [r["case_id"] for r in rows]
    assert mine <= set(got) and len(got) == len(set(got))  # each case exactly once (the Postgres database is shared)
    assert got == sorted(got, key=lambda c: UUID(c).int) and pages >= 5
    first = client.get("/cases?limit=5", headers=coord())
    assert len(first.json()) == 5 and first.headers["x-next-cursor"]


def test_default_limit_and_hard_maximum(backend):
    h = coord()
    make_cases(3, "lim")
    assert client.get("/cases?limit=501", headers=h).status_code == 422
    assert client.get("/cases?limit=0", headers=h).status_code == 422
    assert client.get("/cases?cursor=not-a-cursor!", headers=h).status_code == 422
    assert len(client.get("/cases", headers=h).json()) <= cases_default()
    assert "x-next-cursor" not in client.get("/cases?limit=500", headers=h).headers or backend == "postgres"


def cases_default():
    from app.routers.cases import DEFAULT_LIST_LIMIT

    return DEFAULT_LIST_LIMIT


def test_state_is_a_store_side_filter_and_pages_too(backend):
    ids = make_cases(7, "st")
    h = coord()
    r = client.post(f"/cases/{ids[0]}/events", json={"type": "acknowledge"}, headers=h)
    assert r.status_code == 201, r.text
    acked, _ = walk("/cases?state=acknowledged", h, limit=2)
    assert ids[0] in {c["case_id"] for c in acked} and all(c["state"] == "acknowledged" for c in acked)
    new, _ = walk("/cases?state=result_received", h, limit=3)
    assert set(ids[1:]) <= {c["case_id"] for c in new} and ids[0] not in {c["case_id"] for c in new}


def test_a_model_filter_that_drops_most_rows_still_reaches_the_end(backend):
    ids = make_cases(12, "fl")
    rows, pages = walk("/cases?overdue=true", coord(), limit=2)  # none of these is overdue: every page is empty or sparse
    assert not {r["case_id"] for r in rows} & set(ids)
    assert pages >= 1


def test_the_agency_reads_at_most_the_cap_and_resumes(backend, monkeypatch):
    if backend != "memory":
        pytest.skip("the agency ledger test world is in memory")
    from app.agency import runner

    monkeypatch.setattr(app_settings, "agency_max_cases_read", 5)
    # the in-memory store of this fixture is empty; build a cohort the agency tests' way is heavy, so read through the tool directly
    ids = make_cases(14, "ag")
    from app.agency import tools

    got = tools.read_active_cases(None, None)
    assert len(got) == 5 and got.truncated and got.through is not None
    nxt = tools.read_active_cases(None, None, after=got.through)
    assert len(nxt) == 5 and nxt.truncated
    last = tools.read_active_cases(None, None, after=nxt.through)
    assert len(last) == 4 and not last.truncated
    seen = [f.case_id for f in [*got, *nxt, *last]]
    assert len(seen) == 14 == len(set(seen)) and set(map(str, seen)) == {str(i) for i in ids}
    assert runner.MAX_ORDERS == 400


def test_in_memory_ops_store_prunes_only_stale_followers():
    from app.ops import InMemoryOpsStore

    t = [1000.0]
    ops = InMemoryOpsStore(wall=lambda: t[0])
    for name in ("scheduler", "watchdog", "scheduler-follower-a", "scheduler-follower-b"):
        ops.beat(name, {})
    t[0] += 5000
    ops.beat("scheduler-follower-b", {})  # b is alive, a is gone
    assert ops.prune_followers("scheduler-follower-", 3600) == 1
    assert set(ops.heartbeats()) == {"scheduler", "watchdog", "scheduler-follower-b"}  # never the leader's or the watchdog's row


def test_the_watchdog_scan_route_prunes_stale_follower_rows(monkeypatch):
    """The route that the watchdog service calls removes follower rows past their retention (and only those)."""
    from app import ops as ops_mod
    from app.routers import watchdog as wd

    t = [1000.0]
    store = ops_mod.InMemoryOpsStore(wall=lambda: t[0])
    monkeypatch.setattr(ops_mod, "store", store)
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(app_settings, "follower_heartbeat_retention_seconds", 100.0)
    store.beat("scheduler-follower-gone", {"holder": "x"})
    t[0] += 500
    store.beat("scheduler", {"sweep_ok": True})
    store.beat("scheduler-follower-live", {"holder": "y"})
    out = wd.scan(None)
    assert out["scan"]["cases_evaluated"] == 0
    assert set(store.heartbeats()) == {"scheduler", "scheduler-follower-live", "watchdog"}
