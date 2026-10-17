"""T-A33 (A12) against the running HAPI server (`make test-db` with the stack up): every projected
resource is accepted by HAPI and passes `$validate` (R4), references resolve, a second projection
changes nothing, and a queued projection catches up after an outage. Everything the test writes
carries `meta.tag` system|code from fhirtest.TAG, so test resources can be identified."""

import json
from collections import Counter
from uuid import UUID, uuid4

import pytest

import fhirtest
from app import fhir_projection as fp
from app import notify, ops
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from test_fhir_projection import client, login, post, walk

PATHWAY = load_pathway(app_settings.pathway_path)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not fhirtest.reachable(), reason=f"HAPI not reachable at {fhirtest.FHIR_URL}"),
]


@pytest.fixture
def hapi(monkeypatch):
    suffix = uuid4().hex[:12]
    patient, report = f"a12-p-{suffix}", f"a12-r-{suffix}"
    assert fhirtest.put_tagged({"resourceType": "Patient", "id": patient, "active": True,
                                "name": [{"text": "A12 Test Patient"}]})[0] in (200, 201)
    assert fhirtest.put_tagged({"resourceType": "DiagnosticReport", "id": report, "status": "final",
                                "code": {"text": "FIT (test)"}, "subject": {"reference": f"Patient/{patient}"}})[0] in (200, 201)
    inner = InMemoryEventStore()
    http = fp.HttpFhirClient(fhirtest.FHIR_URL, timeout=20)
    projector = fp.Projector(http, inner, PATHWAY, default_calendar(), fp.InMemoryProjectionQueue(), tag=fhirtest.TAG)
    monkeypatch.setattr(cases, "store", fp.ProjectingStore(inner, projector))
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    clock.reset()
    yield {"patient": f"Patient/{patient}", "report": f"DiagnosticReport/{report}", "projector": projector, "inner": inner}
    clock.reset()


def refs_in(node):
    if isinstance(node, dict):
        ref = node.get("reference")
        if isinstance(ref, str) and "/" in ref:
            yield ref
        for v in node.values():
            yield from refs_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from refs_in(v)


def test_t_a33_every_projected_resource_is_accepted_by_hapi_validates_and_resolves(hapi):
    cid = walk(patient_ref=hapi["patient"], result_ref=hapi["report"])
    events = hapi["inner"].events_for(UUID(cid))
    assert hapi["projector"].queue.count() == 0, "some events were queued: HAPI refused them"
    ids = client.get(f"/cases/{cid}/fhir", headers=login("coordinator", "fp coord")).json()
    refs = [ids["task"], ids["consent"], *ids["service_requests"], *ids["appointments"], *ids["communications"],
            *ids["provenance"], *ids["practitioners"], *ids["devices"]]
    counts = Counter(r.split("/")[0] for r in refs)
    print("\nFHIR resources after one case walk (", len(events), "events ):", dict(sorted(counts.items())))
    assert counts["Task"] == 1 and counts["Provenance"] == len(events) and counts["ServiceRequest"] == 1
    assert counts["Appointment"] == counts["Communication"] == counts["Consent"] == 1
    resources = {}
    for ref in refs:
        status, body = fhirtest.get(ref)
        assert status == 200, (ref, body)
        assert fhirtest.TAG in body["meta"]["tag"], ref  # identifiable test data
        resources[ref] = body
    # $validate (R4 base profile): no error or fatal issue on any resource
    for ref, body in resources.items():
        status, outcome = fhirtest.request("POST", f"{body['resourceType']}/$validate", body)
        problems = [i for i in outcome.get("issue", []) if i["severity"] in ("error", "fatal")]
        assert status == 200 and not problems, (ref, json.dumps(problems, indent=1)[:1500])
    # every reference in every resource resolves in HAPI
    for ref, body in resources.items():
        for target in set(refs_in(body)):
            if target.startswith("#"):
                continue
            assert fhirtest.get(target)[0] == 200, f"{ref} points at {target}, which does not resolve"
    # the content
    task = resources[ids["task"]]
    assert task["status"] == "completed" and task["businessStatus"]["coding"][0]["code"] == "closed"
    assert task["for"] == {"reference": hapi["patient"]} and task["focus"] == {"reference": hapi["report"]}
    # search by the case identifier finds exactly the one Task
    status, bundle = fhirtest.get(f"Task?identifier={fp.SYSTEM_CASE}|{cid}")
    assert status == 200 and bundle["total"] == 1
    status, bundle = fhirtest.get(f"Provenance?target=Task/task-{cid}&_summary=count")
    assert bundle["total"] == len(events)


def test_t_a33_projecting_again_creates_no_new_versions(hapi):
    cid = walk(patient_ref=hapi["patient"], result_ref=hapi["report"])
    ids = client.get(f"/cases/{cid}/fhir", headers=login("coordinator", "fp coord")).json()
    refs = [ids["task"], ids["consent"], *ids["service_requests"], *ids["appointments"], *ids["communications"], *ids["provenance"]]
    before = {r: fhirtest.get(r)[1]["meta"]["versionId"] for r in refs}
    for ev in hapi["inner"].events_for(UUID(cid)):
        hapi["projector"].project_now(UUID(cid), ev)
    after = {r: fhirtest.get(r)[1]["meta"]["versionId"] for r in refs}
    assert after == before


def test_t_a33_consent_history_is_hapis_versions(hapi):
    cid = walk(patient_ref=hapi["patient"], result_ref=hapi["report"])
    status, history = fhirtest.get(f"Consent/consent-{cid}/_history")
    assert status == 200 and history["total"] >= 1


def test_t_a33_an_outage_queues_and_the_next_drain_catches_up(hapi):
    down = fp.Projector(fp.HttpFhirClient("http://127.0.0.1:9/fhir", timeout=1), hapi["inner"], PATHWAY,
                        default_calendar(), hapi["projector"].queue, tag=fhirtest.TAG)
    cases.store = fp.ProjectingStore(hapi["inner"], down)
    cid = walk(patient_ref=hapi["patient"], result_ref=hapi["report"])
    events = hapi["inner"].events_for(UUID(cid))
    assert hapi["inner"].current_state(UUID(cid)) == "closed"  # the log was never blocked
    assert hapi["projector"].queue.count(UUID(cid)) == len(events)
    assert fhirtest.get(f"Task/task-{cid}")[0] == 404
    out = hapi["projector"].drain()  # the real client, same queue
    assert out["projected"] >= len(events) and hapi["projector"].queue.count(UUID(cid)) == 0
    status, task = fhirtest.get(f"Task/task-{cid}")
    assert status == 200 and task["status"] == "completed"


def test_t_a33_the_wiring_app_main_uses_projects_from_postgres_and_queues_in_the_table(monkeypatch):
    """`wrap_store` over the Postgres store with the Postgres queue: a case created through the API
    appears in HAPI; with HAPI unreachable the events wait in `fhir_projection_queue` and a drain
    catches up. This is the same call `app.main` makes at start-up."""
    import os

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    from app.db import create_pool, run_migrations
    from app.store_pg import PostgresEventStore, PostgresProjectionQueue

    pool = create_pool(os.environ["DATABASE_URL"], max_size=4)
    run_migrations(pool)
    try:
        class S:  # settings stand-in
            fhir_projection = True
            fhir_base_url = fhirtest.FHIR_URL
            fhir_projection_drain_seconds = 3600.0

        suffix = uuid4().hex[:12]
        patient = f"a12-w-{suffix}"
        assert fhirtest.put_tagged({"resourceType": "Patient", "id": patient, "active": True})[0] in (200, 201)
        pg = PostgresEventStore(pool)
        wrapped = fp.wrap_store(pg, S, PATHWAY, default_calendar(), pool, tag=fhirtest.TAG)
        assert isinstance(wrapped, fp.ProjectingStore) and isinstance(wrapped.projector.queue, PostgresProjectionQueue)
        monkeypatch.setattr(cases, "store", wrapped)
        monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
        coord, lab = login("coordinator", "fp coord"), login("lab", "fp lab")

        # healthy: created and acknowledged cases show up in HAPI, the queue stays empty
        cid = client.post("/cases", json={"patient_ref": f"Patient/{patient}"}, headers=coord).json()["case_id"]
        post(cid, "acknowledge", lab)
        status, task = fhirtest.get(f"Task/task-{cid}")
        assert status == 200 and task["businessStatus"]["coding"][0]["code"] == "acknowledged"
        assert task["meta"]["tag"] == [fhirtest.TAG]
        assert wrapped.projector.queue.count(UUID(cid)) == 0

        # HAPI unreachable: the event is appended, the id waits in the table
        wrapped.projector.client = fp.HttpFhirClient("http://127.0.0.1:9/fhir", timeout=1)
        post(cid, "accept_ownership", login("primary_care_owner", "Dr Wire"))
        assert pg.current_state(UUID(cid)) == "owner_accepted"
        assert wrapped.projector.queue.count(UUID(cid)) == 1
        with pool.connection() as conn:
            row = conn.execute("SELECT attempts, last_error FROM fhir_projection_queue WHERE case_id = %s", (UUID(cid),)).fetchone()
        assert row[0] == 1 and "unreachable" in row[1]
        assert fhirtest.get(f"Task/task-{cid}")[1]["businessStatus"]["coding"][0]["code"] == "acknowledged"  # stale, as expected
        wrapped.projector.client = fp.HttpFhirClient(fhirtest.FHIR_URL, timeout=20)
        assert wrapped.projector.drain()["still_queued"] >= 0
        assert wrapped.projector.queue.count(UUID(cid)) == 0
        assert fhirtest.get(f"Task/task-{cid}")[1]["businessStatus"]["coding"][0]["code"] == "owner_accepted"
    finally:
        wrapped.projector.stop()
        pool.close()


def test_t_a33_projection_off_leaves_the_store_alone():
    class Off:
        fhir_projection = False

    sentinel = object()
    assert fp.wrap_store(sentinel, Off, PATHWAY, default_calendar()) is sentinel
