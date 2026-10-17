"""D11 against Postgres (T-D15 integration half). Run with `make test-db`.

Assertions are before/after differences, so the shared dev database's other drafts do not matter.
"""

import os

import pytest

from api_helpers import login_headers, make_client
from app.ai import provider as prov
from app.ai.provider import DraftResult
from app.db import create_pool, run_migrations
from app.routers import cases, drafts
from app.store_pg import PostgresEventStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=6)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def client(pool, monkeypatch):
    monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
    monkeypatch.setattr(drafts, "_pg", None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    return make_client()


def H(client, role, name=None):
    return login_headers(client, role, name)


def q(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def metric(client):
    return client.get("/metrics/abstention", headers=H(client, "supervisor")).json()


def owned_case(client):
    cid = client.post("/cases", json={"patient_ref": "Patient/pg"}, headers=H(client, "coordinator")).json()["case_id"]
    for type_, role, name in (("acknowledge", "lab", None), ("accept_ownership", "primary_care_owner", "Dr Sara Khan")):
        r = client.post(f"/cases/{cid}/events", json={"type": type_}, headers=H(client, role, name))
        assert r.status_code == 201, r.text
    return cid


def draft(client, cid, **over):
    r = client.post(f"/cases/{cid}/drafts", json={**BODY, **over}, headers=H(client, "coordinator", "Maryam"))
    assert r.status_code == 201, r.text
    return r.json()


class Scripted:
    name = "azure_openai"
    model_id = "scripted"

    def __init__(self, script):
        self.script = list(script)

    def draft(self, req):
        return DraftResult(self.script.pop(0), req.language, self.name, self.model_id, False, None, "x")


def test_abstentions_are_persisted_and_counted_in_the_metric(client, pool, monkeypatch):
    good = "Your screening test found blood. Dr Sara Khan will arrange a colonoscopy and the care team will contact you."
    fake = Scripted([good, "You have cancer. Dr Sara Khan will arrange a colonoscopy.", good])
    monkeypatch.setitem(prov._REGISTRY, "azure_openai", lambda: fake)
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    cid = owned_case(client)
    before = metric(client)
    outs = [draft(client, cid) for _ in range(3)]
    after = metric(client)
    assert after["attempts"] - before["attempts"] == 3
    assert after["abstentions"] - before["abstentions"] == 1
    assert after["by_reason"].get("validation_failed", 0) - before["by_reason"].get("validation_failed", 0) == 1
    rows = q(pool, "SELECT fallback_used, abstention_reason, abstained_at, created_at FROM drafts"
                   " WHERE case_id = %s ORDER BY seq", (cid,))
    assert [r[0] for r in rows] == [False, True, False]
    assert rows[1][1] == outs[1]["abstention_reason"] and rows[1][2] == rows[1][3]
    assert rows[0][1] is None and rows[0][2] is None


def test_the_rate_appears_in_get_metrics_closure(client, monkeypatch):
    """T-D15: GET /metrics/closure carries the same abstention counts as GET /metrics/abstention
    (both read the drafts table; the closure route returns `rate` null only when there are no attempts)."""
    monkeypatch.setenv("LLM_PROVIDER", "template")
    cid = owned_case(client)
    draft(client, cid)
    closure = client.get("/metrics/closure", headers=H(client, "coordinator")).json()["abstention"]
    own = metric(client)
    assert closure is not None and closure["attempts"] >= 1
    assert (closure["attempts"], closure["abstentions"]) == (own["attempts"], own["abstentions"])
    assert closure["rate"] == pytest.approx(closure["abstentions"] / closure["attempts"])
