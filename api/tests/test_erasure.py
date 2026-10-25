"""T-A79 (A32, ADR-025): erasure of a patient's personal data while the append-only audit trail survives.

Integration (`make test-db`, needs HAPI reachable at TEST_FHIR_URL with `hapi.fhir.expunge_enabled`, docker-compose.yml):
a scratch database, a real HAPI, the real routes. A patient with TWO closed cases gets marker data in every place personal data
can live (docs/governance/erasure.md section 1); a second patient ("bystander") gets the same shape with different markers.
After the two-supervisor erasure of the first, every table of the database, HAPI (reads, searches, history) and every API
response is scanned for the first patient's markers and reference: zero hits. The bystander's data is all still there, the
event log still replays, metrics still compute to the same numbers and the closure history is still auditable.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors, sql

import fhirtest
from api_helpers import DRAFTED, login_headers, make_client
from app import erasure, fhir_projection as fp
from app.calendar import default_calendar
from app.config import settings as app_settings
from app.db import APP_ROLE, create_pool
from app.events import CASE_CREATED, Event, replay_model
from app.hl7 import contacts
from app.pathway import load_pathway
from app.routers import cases
from app.store_pg import PostgresEventStore, PostgresProjectionQueue
from scratchdb import scratch_database

PATHWAY = load_pathway(app_settings.pathway_path)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
    pytest.mark.skipif(not fhirtest.reachable(), reason=f"HAPI not reachable at {fhirtest.FHIR_URL}"),
]

AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
OWNER = "primary_care_owner:dr_erasure"
SIGNED = {"signed_by": "Practitioner/dr_erasure"}
CATEGORIES_ALL = {"fhir_resources", "event_patient_reference", "event_notes", "draft_text", "prompt_text", "notification_body",
                  "reply_text", "agency_ledger", "contact_registry", "projection_queue"}


# ---- the world -------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def url():
    with scratch_database("erasure") as u:
        yield u


@pytest.fixture(scope="module")
def pool(url):
    p = create_pool(url, max_size=8)
    yield p
    p.close()


@pytest.fixture
def world(pool, monkeypatch):
    """The Postgres store wrapped in a projector that writes to the real HAPI, and the API client."""
    inner = PostgresEventStore(pool)
    projector = fp.Projector(fp.HttpFhirClient(fhirtest.FHIR_URL, timeout=30), inner, PATHWAY, default_calendar(),
                             PostgresProjectionQueue(pool, fp.RetryPolicy()), tag=fhirtest.TAG)
    store = fp.ProjectingStore(inner, projector)
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(cases, "pathway", PATHWAY)
    monkeypatch.setattr(app_settings, "fhir_base_url", fhirtest.FHIR_URL)
    contacts.reset()
    client = make_client()
    yield {"store": store, "inner": inner, "client": client, "pool": pool}
    contacts.reset()


def H(client, name="sup one", role="supervisor"):
    return login_headers(client, role, name)


def put(resource: dict):
    status, body = fhirtest.put_tagged(resource)
    assert status in (200, 201), body
    return body


class Subject:
    """One patient: ids, markers and the places they were put."""

    def __init__(self, tag: str):
        self.tag = tag
        self.pid = f"era-{uuid.uuid4().hex[:12]}"
        self.ref = f"Patient/{self.pid}"
        self.marker = f"ZQMARK{uuid.uuid4().hex[:10].upper()}"  # the name, phone, text: anything personal
        self.cases: list[uuid.UUID] = []
        self.report = f"era-dr-{uuid.uuid4().hex[:10]}"
        self.procedure = f"era-pr-{uuid.uuid4().hex[:10]}"
        self.drafts: list[uuid.UUID] = []
        self.event_ids: list[uuid.UUID] = []


def seed_hapi(s: Subject):
    put({"resourceType": "Patient", "id": s.pid, "active": True,
         "identifier": [{"system": "urn:test:mrn", "value": f"MRN-{s.marker}"}],
         "name": [{"family": s.marker, "given": ["Test"]}],
         "telecom": [{"system": "phone", "value": f"+971500{s.marker}"}],
         "address": [{"line": [f"{s.marker} Street"], "city": "Abu Dhabi"}]})
    put({"resourceType": "DiagnosticReport", "id": s.report, "status": "final", "code": {"text": "FIT (test)"},
         "conclusion": f"positive {s.marker}", "subject": {"reference": s.ref}})
    put({"resourceType": "Procedure", "id": s.procedure, "status": "completed", "code": {"text": "Colonoscopy (test)"},
         "subject": {"reference": s.ref}, "note": [{"text": f"note {s.marker}"}]})


def drive_to_closed(store, s: Subject, case_id: uuid.UUID, *, full: bool):
    """A closed case with a note carrying the marker; `full` adds the message, consent and booking events."""
    t = AT
    def ev(kind, payload=None, actor=OWNER, minutes=0):
        nonlocal t
        t = t + timedelta(minutes=minutes or 1)
        e = Event(case_id, kind, t, actor, payload or {}, uuid.uuid4())
        s.event_ids.append(e.event_id)
        return e

    store.create_case(case_id, PATHWAY.name,
                      Event(case_id, CASE_CREATED, AT, "system:intake",
                            {"patient_ref": s.ref, "result_ref": f"DiagnosticReport/{s.report}"}, uuid.uuid4()),
                      PATHWAY.initial_state)
    steps = [ev("acknowledge", {"note": f"called {s.marker}"}, "coordinator:c1"), ev("accept_ownership"),
             ev("contact_patient", {"channel": "sms"}, "coordinator:c1")]
    if full:
        draft = uuid.uuid4()
        s.drafts.append(draft)
        steps += [ev("consent_updated", {"channel_whatsapp": False}, "coordinator:c1"),
                  ev("message_drafted", {**DRAFTED, "draft_id": str(draft)}, "model:template"),
                  ev("message_approved", {"draft_id": str(draft)}),
                  ev("message_sent", {"notification_id": str(uuid.uuid4()), "channel": "sms", "language": "en",
                                      "purpose": "outreach", "draft_id": str(draft)}, "system:notifier")]
    steps += [ev("patient_acknowledge", {}, "coordinator:c1"),
              ev("book_confirmatory", {"appointment_ref": f"Appointment/era-{uuid.uuid4().hex[:8]}",
                                       "appointment_date": "2026-02-02"}, "coordinator:c1"),
              ev("complete_confirmatory", {"result_ref": f"Procedure/{s.procedure}"}, "specialist_owner:dr_s"),
              ev("clinician_review"),
              ev("close", {"evidence_ref": f"DiagnosticReport/{s.report}", **SIGNED})]
    for e in steps:
        store.append(e, PATHWAY)
    if full:
        # the draft/notification ids the log names get their text in the erasable tables; the first draft is also approved
        pass


def seed_sql(pool, s: Subject):
    """Personal data in the tables the log never holds: drafts, prompts, notifications, replies, agency, outbox, queues."""
    case = s.cases[0]
    with pool.connection() as conn:
        for draft in s.drafts:
            ph = uuid.uuid4().hex + uuid.uuid4().hex
            conn.execute("INSERT INTO prompts (prompt_hash, system_prompt, prompt_text) VALUES (%s, 'system', %s)",
                         (ph, f"write to {s.marker} about {s.ref}"))
            conn.execute(
                "INSERT INTO drafts (draft_id, case_id, purpose, channel, language, text, provider, model_id, fallback_used,"
                " prompt_hash, created_by, created_at, approved_at, approved_by)"
                " VALUES (%s, %s, 'outreach', 'sms', 'en', %s, 'template', 'template-1', false, %s, 'model:template', %s, %s, %s)",
                (draft, case, f"Dear {s.marker}, your test found blood", ph, AT, AT, OWNER))
            notification = uuid.uuid4()
            conn.execute("INSERT INTO notifications (id, case_id, channel, language, purpose, body, status, event_id, created_at)"
                         " VALUES (%s, %s, 'sms', 'en', 'outreach', %s, 'sent', %s, %s)",
                         (notification, case, f"SMS to {s.marker}: please call", uuid.uuid4(), AT))
            conn.execute("INSERT INTO outbox (draft_id, case_id, channel, language, purpose, approved_at, status, completed_at,"
                         " notification_id, last_error) VALUES (%s, %s, 'sms', 'en', 'outreach', %s, 'sent', %s, %s, %s)",
                         (draft, case, AT, AT, notification, f"gateway said no for {s.ref}"))
        # a rejected draft with a free-text reason
        conn.execute("INSERT INTO prompts (prompt_hash, system_prompt, prompt_text) VALUES (%s, 's', %s)",
                     (rh := uuid.uuid4().hex + uuid.uuid4().hex, f"prompt {s.marker}"))
        conn.execute(
            "INSERT INTO drafts (draft_id, case_id, purpose, channel, language, text, provider, model_id, fallback_used,"
            " prompt_hash, created_by, created_at, rejected_at, rejected_by, rejection_reason)"
            " VALUES (%s, %s, 'outreach', 'sms', 'en', %s, 'template', 't', false, %s, 'model:template', %s, %s, %s, %s)",
            (uuid.uuid4(), case, f"rejected text {s.marker}", rh, AT, AT, OWNER, f"wrong name {s.marker}"))
        conn.execute("INSERT INTO patient_replies (reply_id, case_id, channel, text, intent, confidence, model_id, needs_human,"
                     " logged, received_at, received_by) VALUES (%s, %s, 'sms', %s, 'other', 0.5, 'm', true, true, %s, 'coordinator:c1')",
                     (uuid.uuid4(), case, f"my name is {s.marker} and I live in the villa", AT))
        run = uuid.uuid4()
        conn.execute("INSERT INTO agency_tasks (id, run_id, department, agent_type, case_id, status, input, created_at, created_by)"
                     " VALUES (%s, %s, 'quality_ops', 'xx_check', %s, 'queued', %s, %s, 'system:agency:quality_ops')",
                     (run, run, case, json.dumps({"patient_ref": s.ref}), AT))
        ev = uuid.uuid4()
        conn.execute("INSERT INTO fhir_projection_queue (event_id, case_id, last_error) VALUES (%s, %s, %s)",
                     (ev, case, f"HAPI 400: {s.ref} not found"))
        conn.execute("INSERT INTO fhir_projection_dead_letter (event_id, case_id, attempts, last_error, queued_at)"
                     " VALUES (%s, %s, 9, %s, %s)", (uuid.uuid4(), case, f"dead for {s.ref}", AT))


def build(world, tag: str, *, second_case=True) -> Subject:
    s = Subject(tag)
    seed_hapi(s)
    contacts.record(s.ref, False)
    s.cases = [uuid.uuid4()] + ([uuid.uuid4()] if second_case else [])
    for i, c in enumerate(s.cases):
        drive_to_closed(world["store"], s, c, full=(i == 0))
    seed_sql(world["pool"], s)
    return s


# ---- scanning --------------------------------------------------------------------------------------------------------


def db_hits(url: str, needles: list[str]) -> dict[str, int]:
    """Every row of every table of the database, as text, searched for each needle: {table: hits}."""
    out: dict[str, int] = {}
    with psycopg.connect(url) as c:
        tables = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        for t in tables:
            if t == "schema_migrations":
                continue
            for (row,) in c.execute(sql.SQL("SELECT x::text FROM {} x").format(sql.Identifier(t))):
                if any(n in row for n in needles):
                    out[t] = out.get(t, 0) + 1
    return out


def hapi_hits(s: Subject) -> list[str]:
    """Reads, searches and history in HAPI for anything of the patient. Returns what was found (empty = erased)."""
    found: list[str] = []
    for ref in (s.ref, f"DiagnosticReport/{s.report}", f"Procedure/{s.procedure}"):
        code, _ = fhirtest.get(ref)
        if code != 404:
            found.append(f"GET {ref} -> {code}")
        code, _ = fhirtest.get(f"{ref}/_history")
        if code != 404:
            found.append(f"history {ref} -> {code}")
    searches = [f"Patient?_id={s.pid}", f"Patient?name={s.marker}", f"Patient?identifier=urn:test:mrn|MRN-{s.marker}",
                f"Patient?phone=%2B971500{s.marker}", f"Patient?address={s.marker}"]
    for kind in ("Task", "Communication", "Consent", "Appointment", "DiagnosticReport", "Procedure", "ServiceRequest",
                 "Provenance", "Observation", "Encounter"):
        searches.append(f"{kind}?patient={s.ref}")
    for case in s.cases:
        searches.append(f"Task?identifier=urn:screening-closure:case|{case}")
    for q in searches:
        code, bundle = fhirtest.get(q)
        if code == 200 and bundle.get("total", len(bundle.get("entry", []))):
            found.append(f"search {q} -> {len(bundle.get('entry', []))}")
        elif code not in (200, 400):  # 400: a parameter that type does not have, nothing to find with it
            found.append(f"search {q} -> HTTP {code}")
    code, bundle = fhirtest.get(f"Patient/{s.pid}/$everything")
    if code == 200 and bundle.get("entry"):
        found.append("$everything still returns entries")
    return found


def api_texts(world, s: Subject) -> dict[str, str]:
    """Every read route that can return something about the patient's cases, as text."""
    c = world["client"]
    sup, coord = H(c), login_headers(c, "coordinator", "coord one")
    out = {"GET /cases": c.get("/cases", headers=sup).text, "GET /metrics/closure": c.get("/metrics/closure", headers=sup).text,
           "GET /metrics/effort": c.get("/metrics/effort", headers=sup).text}
    for case in s.cases:
        for path in ("", "/timeline", "/notifications", "/fhir", "/replies", "/agency"):
            r = c.get(f"/cases/{case}{path}", headers=coord if path == "/replies" else sup)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])
            out[f"GET /cases/{{id}}{path}"] = out.get(f"GET /cases/{{id}}{path}", "") + r.text
    return out


# ---- the proof -------------------------------------------------------------------------------------------------------


def snapshot(world, cases_: list[uuid.UUID]):
    """What must not change: per case the log's shape (seq, type, at, actor, event id) and the replayed state."""
    shape = {}
    for case in cases_:
        events = world["inner"].events_for(case)
        model = replay_model(events, PATHWAY)
        shape[case] = ([(e.seq, e.type, e.at, e.actor, e.event_id) for e in events], model.state)
    return shape


def request_and_approve(world, s: Subject, reason="data_subject_request"):
    c = world["client"]
    r = c.post("/erasures", json={"patient_ref": s.ref, "reason": reason}, headers=H(c, "sup one"))
    assert r.status_code == 202, r.text
    eid = r.json()["erasure_id"]
    a = c.post(f"/erasures/{eid}/approve", headers=H(c, "sup two"))
    assert a.status_code == 200, a.text
    return eid, a.json()


def test_t_a79_erasure_leaves_no_trace_of_the_patient_and_keeps_the_audit_trail(world, url):
    s = build(world, "erased")
    keep = build(world, "bystander")
    needles = [s.marker, s.pid]

    # positive control: before the erasure the scan DOES find the data (a scan that cannot find proves nothing)
    before_db = db_hits(url, needles)
    for table in ("events", "drafts", "prompts", "notifications", "patient_replies", "agency_tasks", "outbox",
                  "fhir_projection_queue", "fhir_projection_dead_letter"):
        assert table in before_db, f"the seed put nothing in {table}"
    assert len(hapi_hits(s)) > 5
    assert any(s.pid in t or s.marker in t for t in api_texts(world, s).values())
    assert contacts.lookup(s.ref) is False

    log_before = snapshot(world, s.cases)
    keep_before = snapshot(world, keep.cases)
    metrics_before = world["client"].get("/metrics/closure", headers=H(world["client"])).json()
    keep_hits_before = db_hits(url, [keep.marker, keep.pid])

    eid, done = request_and_approve(world, s)

    # 1. the register row: opaque ids, categories, counts
    assert done["status"] == "completed" and done["reason"] == "data_subject_request" and done["cases"] == 2
    assert set(done["categories"]) <= CATEGORIES_ALL
    assert {"fhir_resources", "event_patient_reference", "event_notes", "draft_text", "prompt_text", "notification_body",
            "reply_text", "agency_ledger", "contact_registry", "projection_queue"} == set(done["categories"])
    assert done["requested_by"] != done["approved_by"]
    assert done["counts"]["fhir_resources"] >= 6 and done["counts"]["draft_text"] >= 2 and done["counts"]["reply_text"] == 1
    assert s.marker not in json.dumps(done) and s.pid not in json.dumps(done)

    # 2. zero hits: every table, HAPI, every API response, the contact registry
    assert db_hits(url, needles) == {}
    assert hapi_hits(s) == []
    for name, text in api_texts(world, s).items():
        assert s.marker not in text and s.pid not in text, name
    assert contacts.lookup(s.ref) is None
    with world["pool"].connection() as conn:
        assert conn.execute("SELECT count(*) FROM erasure_map").fetchone()[0] == 0
        row = conn.execute("SELECT status, case_ids, categories FROM erasures WHERE erasure_id = %s", (eid,)).fetchone()
    assert row[0] == "completed" and sorted(row[1]) == sorted(s.cases)

    # 3. what must remain: ids, hashes, the log's shape, the replay, metrics, the bystander
    after = snapshot(world, s.cases)
    for case in s.cases:
        events_before, state_before = log_before[case]
        events_after, state_after = after[case]
        assert events_after[:len(events_before)] == events_before  # nothing changed or went
        assert len(events_after) == len(events_before) + 1 and events_after[-1][1] == "erasure_completed"
        assert events_after[-1][3] == "system:erasure"
        assert state_after == state_before == "closed"
    assert snapshot(world, keep.cases) == keep_before
    assert db_hits(url, [keep.marker, keep.pid]) == keep_hits_before  # the bystander's rows are all still there
    assert len(hapi_hits(keep)) > 5
    assert fhirtest.get(keep.ref)[0] == 200 and fhirtest.get(f"DiagnosticReport/{keep.report}")[0] == 200
    assert contacts.lookup(keep.ref) is False
    assert world["client"].get("/metrics/closure", headers=H(world["client"])).json() == metrics_before

    with world["pool"].connection() as conn:
        # hashes and ids stay
        assert conn.execute("SELECT count(*) FROM drafts WHERE case_id = ANY(%s) AND text = %s",
                            (s.cases, erasure.tombstone(eid))).fetchone()[0] >= 2
        assert conn.execute("SELECT count(*) FROM drafts WHERE case_id = ANY(%s) AND length(prompt_hash) = 64",
                            (s.cases,)).fetchone()[0] >= 2
        assert conn.execute("SELECT count(*) FROM prompts WHERE prompt_text = %s", (erasure.tombstone(eid),)).fetchone()[0] >= 2
        genesis = conn.execute("SELECT payload FROM events WHERE case_id = %s AND seq = 1", (s.cases[0],)).fetchone()[0]
        last = conn.execute("SELECT payload FROM events WHERE case_id = %s ORDER BY seq DESC LIMIT 1", (s.cases[0],)).fetchone()[0]
        close = conn.execute("SELECT payload FROM events WHERE case_id = %s AND type = 'close'", (s.cases[0],)).fetchone()[0]
    assert genesis["patient_ref"].startswith("Patient/erased-") and genesis["patient_ref"] != s.ref
    assert set(last) == {"erasure_id", "categories"} and last["erasure_id"] == eid
    assert close["evidence_ref"] == f"DiagnosticReport/{s.report}" and close["signed_by"] == "Practitioner/dr_erasure"  # auditable

    # the erased case accepts nothing more, and is not projected again
    r = world["client"].post(f"/cases/{s.cases[0]}/events", json={"type": "reopen", "payload": {"reason": "new_finding"}},
                             headers=login_headers(world["client"], "primary_care_owner", "dr erasure"))
    assert r.status_code in (403, 409, 422), r.text
    assert fhirtest.get(f"Task?identifier=urn:screening-closure:case|{s.cases[0]}")[1]["total"] == 0


def test_the_erasure_event_names_only_the_erasure_and_the_categories(world):
    s = build(world, "ev", second_case=False)
    eid, _ = request_and_approve(world, s)
    ev = [e for e in world["inner"].events_for(s.cases[0]) if e.type == "erasure_completed"]
    assert len(ev) == 1 and set(ev[0].payload) == {"erasure_id", "categories"} and ev[0].payload["erasure_id"] == eid
    from app import payloads
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("erasure_completed", {"erasure_id": eid, "categories": ["draft_text"], "patient_ref": "Patient/x"})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("erasure_completed", {"erasure_id": eid, "categories": ["a name"]})


# ---- the two-person flow and its refusals ----------------------------------------------------------------------------------


def test_the_flow_is_two_supervisors_with_a_reason_from_the_taxonomy(world):
    s = build(world, "flow", second_case=False)
    c = world["client"]
    one, two = H(c, "sup one"), H(c, "sup two")
    url_ = "/erasures"
    ref = s.ref
    assert c.post(url_, json={"patient_ref": ref}, headers=one).status_code == 422
    assert c.post(url_, json={"patient_ref": ref, "reason": "because"}, headers=one).status_code == 422
    assert c.post(url_, json={"patient_ref": ref, "reason": "data_subject_request", "note": "free text"}, headers=one).status_code == 422
    for role in ("coordinator", "primary_care_owner", "specialist_owner", "lab", "patient"):
        assert c.post(url_, json={"patient_ref": ref, "reason": "data_subject_request"}, headers=login_headers(c, role, f"x {role}")).status_code == 403
    assert c.post(url_, json={"patient_ref": ref, "reason": "data_subject_request"}).status_code == 401
    r = c.post(url_, json={"patient_ref": ref, "reason": "collected_in_error"}, headers=one)
    assert r.status_code == 202 and r.json()["status"] == "requested"
    eid = r.json()["erasure_id"]
    # a second request for the same patient while one is open
    assert c.post(url_, json={"patient_ref": ref, "reason": "collected_in_error"}, headers=two).status_code == 409
    # no patient reference: 422; a malformed one: 422
    assert c.post(url_, json={"reason": "collected_in_error"}, headers=two).status_code == 422
    assert c.post(url_, json={"patient_ref": "Patient/bad id", "reason": "collected_in_error"}, headers=two).status_code == 422
    assert c.post(url_, json={"patient_ref": s.pid, "reason": "collected_in_error"}, headers=two).status_code == 422
    # nothing is erased by a request
    assert fhirtest.get(s.ref)[0] == 200
    # the requester cannot approve their own; nobody but a supervisor can approve
    assert c.post(f"/erasures/{eid}/approve", headers=one).status_code == 403
    assert c.post(f"/erasures/{eid}/approve", headers=login_headers(c, "coordinator", "coord one")).status_code == 403
    assert c.get(f"/erasures/{eid}", headers=login_headers(c, "coordinator", "coord one")).status_code == 403
    assert fhirtest.get(s.ref)[0] == 200
    pending = c.get(f"/erasures/{eid}", headers=two).json()
    assert pending["status"] == "requested" and pending["approved_by"] is None
    ok = c.post(f"/erasures/{eid}/approve", headers=two)
    assert ok.status_code == 200 and ok.json()["status"] == "completed" and ok.json()["reason"] == "collected_in_error"
    assert c.post(f"/erasures/{eid}/approve", headers=two).status_code == 409  # once
    assert c.get(f"/erasures/{uuid.uuid4()}", headers=two).status_code == 404


def test_an_open_case_and_an_unknown_patient_are_refused(world):
    c = world["client"]
    s = Subject("open")
    seed_hapi(s)
    case = uuid.uuid4()
    world["store"].create_case(case, PATHWAY.name, Event(case, CASE_CREATED, AT, "system:intake", {"patient_ref": s.ref},
                                                         uuid.uuid4()), PATHWAY.initial_state)
    r = c.post("/erasures", json={"patient_ref": s.ref, "reason": "data_subject_request"}, headers=H(c))
    assert r.status_code == 409 and "not closed" in r.json()["detail"]
    assert fhirtest.get(s.ref)[0] == 200
    assert c.post("/erasures", json={"patient_ref": "Patient/no-such-patient-zz9", "reason": "data_subject_request"}, headers=H(c)).status_code == 404


def test_a_patient_with_no_case_is_erased_from_hapi(world, url):
    s = Subject("nocase")
    seed_hapi(s)
    eid, done = request_and_approve(world, s)
    assert done["cases"] == 0 and "fhir_resources" in done["categories"]
    assert hapi_hits(s) == []
    assert db_hits(url, [s.marker, s.pid]) == {}


def test_hapi_down_is_a_502_and_changes_nothing_then_a_retry_completes(world, url, monkeypatch):
    s = build(world, "retry", second_case=False)
    c = world["client"]
    r = c.post("/erasures", json={"patient_ref": s.ref, "reason": "data_subject_request"}, headers=H(c, "sup one"))
    eid = r.json()["erasure_id"]
    monkeypatch.setattr(app_settings, "fhir_base_url", "http://127.0.0.1:9/fhir")  # nothing listens there
    bad = c.post(f"/erasures/{eid}/approve", headers=H(c, "sup two"))
    assert bad.status_code == 502 and "nothing in the database was changed" in bad.json()["detail"]
    assert c.get(f"/erasures/{eid}", headers=H(c, "sup two")).json()["status"] == "requested"
    assert db_hits(url, [s.marker, s.pid]) != {}
    with world["pool"].connection() as conn:
        assert conn.execute("SELECT count(*) FROM erasure_map WHERE erasure_id = %s", (eid,)).fetchone()[0] == 1
    monkeypatch.setattr(app_settings, "fhir_base_url", fhirtest.FHIR_URL)
    ok = c.post(f"/erasures/{eid}/approve", headers=H(c, "sup two"))
    assert ok.status_code == 200 and db_hits(url, [s.marker, s.pid]) == {} and hapi_hits(s) == []


def test_a_crash_after_the_cases_but_before_the_register_is_finished_by_a_retry(world, url, monkeypatch):
    s = build(world, "crash", second_case=False)
    c = world["client"]
    eid = c.post("/erasures", json={"patient_ref": s.ref, "reason": "data_subject_request"}, headers=H(c, "sup one")).json()["erasure_id"]

    class Crash(RuntimeError):
        pass

    def boom(*a, **k):
        raise Crash("the process died after the cases were erased, before the register was completed")

    with monkeypatch.context() as m:
        m.setattr(erasure, "Jsonb", boom)  # the register UPDATE builds its counts with this: the last step before the commit
        with pytest.raises(Crash):
            c.post(f"/erasures/{eid}/approve", headers=H(c, "sup two"))
    # HAPI and the case were done (their own commits); the register and the map were rolled back with the crash
    assert c.get(f"/erasures/{eid}", headers=H(c, "sup two")).json()["status"] == "requested"
    with world["pool"].connection() as conn:
        assert conn.execute("SELECT count(*) FROM erasure_map WHERE erasure_id = %s", (eid,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM events WHERE case_id = %s AND type = 'erasure_completed'",
                            (s.cases[0],)).fetchone()[0] == 1
    ok = c.post(f"/erasures/{eid}/approve", headers=H(c, "sup two"))
    assert ok.status_code == 200 and ok.json()["status"] == "completed"
    with world["pool"].connection() as conn:
        assert conn.execute("SELECT count(*) FROM erasure_map").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM events WHERE case_id = %s AND type = 'erasure_completed'",
                            (s.cases[0],)).fetchone()[0] == 1  # not written twice
    assert db_hits(url, [s.marker, s.pid]) == {} and hapi_hits(s) == []


# ---- the append-only guards stay narrow ------------------------------------------------------------------------------------


def as_role(url, role):
    c = psycopg.connect(url, autocommit=True)
    c.execute(f"SET ROLE {role}")
    return c


@pytest.fixture
def one_case(world):
    s = build(world, "guards")
    return s


def test_ordinary_updates_and_deletes_are_still_refused(world, url, one_case):
    s = one_case
    case, draft = s.cases[0], s.drafts[0]
    attempts = [
        ("UPDATE events SET payload = '{}'::jsonb WHERE case_id = %s", (case,)),
        ("DELETE FROM events WHERE case_id = %s", (case,)),
        ("UPDATE notifications SET body = 'x' WHERE case_id = %s", (case,)),
        ("DELETE FROM notifications WHERE case_id = %s", (case,)),
        ("UPDATE prompts SET prompt_text = 'x'", ()),
        ("DELETE FROM prompts", ()),
        ("UPDATE drafts SET text = 'x' WHERE draft_id = %s", (draft,)),
        ("DELETE FROM drafts WHERE draft_id = %s", (draft,)),
        ("UPDATE outbox SET last_error = 'x' WHERE draft_id = %s", (draft,)),
        ("UPDATE agency_tasks SET input = '{}'::jsonb WHERE case_id = %s", (case,)),
        ("TRUNCATE events CASCADE", ()),
        ("UPDATE erasures SET reason = 'consent_withdrawn'", ()),
        ("DELETE FROM erasures", ()),
    ]
    for role in (None, APP_ROLE):  # the owner (a superuser here) and the application role
        conn = as_role(url, role) if role else psycopg.connect(url, autocommit=True)
        with conn:
            for stmt, params in attempts:
                with pytest.raises((errors.RaiseException, errors.InsufficientPrivilege)):
                    conn.execute(stmt, params)
    # nothing changed
    assert db_hits(url, [s.marker]) != {}


def test_the_erasure_role_cannot_be_reached_from_outside(world, url, one_case):
    with as_role(url, APP_ROLE) as c:
        # (SET ROLE would work from a superuser's session, so ask about membership: a real login as closure_app cannot switch)
        assert c.execute("SELECT pg_has_role('closure_app', 'closure_erasure', 'member')").fetchone()[0] is False
        assert c.execute("SELECT pg_has_role('closure_app', 'closure_erasure', 'usage')").fetchone()[0] is False
        # setting a session variable does not open the guard either
        c.execute("SELECT set_config('erasure.active', 'on', false)")
        with pytest.raises((errors.RaiseException, errors.InsufficientPrivilege)):
            c.execute("UPDATE notifications SET body = 'x'")
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute("SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls"
                        " FROM pg_roles WHERE rolname = 'closure_erasure'").fetchone()
        assert row == (False, False, False, False, False, False)
        fn = c.execute("SELECT prosecdef, pg_get_userbyid(proowner), proacl::text FROM pg_proc WHERE proname = 'erasure_scrub_case'").fetchone()
        assert fn[0] is True and fn[1] == "closure_erasure"
        assert not any(entry.strip("{}").startswith("=") for entry in fn[2].split(","))  # PUBLIC has no EXECUTE
        # the role's grants name exactly the erasable columns: no DELETE anywhere, no UPDATE on a table's other columns
        grants = c.execute("SELECT table_name, privilege_type FROM information_schema.table_privileges"
                           " WHERE grantee = 'closure_erasure' AND privilege_type <> 'SELECT'").fetchall()
        assert grants == []  # column-level only
        cols = {(t, col) for t, col in c.execute(
            "SELECT table_name, column_name FROM information_schema.column_privileges"
            " WHERE grantee = 'closure_erasure' AND privilege_type = 'UPDATE'")}
        assert cols == {("events", "payload"), ("drafts", "text"), ("drafts", "rejection_reason"), ("prompts", "prompt_text"),
                        ("notifications", "body"), ("patient_replies", "text"), ("agency_tasks", "input"),
                        ("agency_tasks", "output"), ("agency_tasks", "resolution"), ("outbox", "last_error")}
        assert c.execute("SELECT count(*) FROM information_schema.table_privileges WHERE grantee = 'closure_erasure'"
                         " AND privilege_type IN ('DELETE', 'TRUNCATE', 'INSERT')").fetchone()[0] == 0


def test_the_function_touches_one_case_of_one_patient_only(world, url, one_case):
    s = one_case
    other = build(world, "other", second_case=False)
    tomb = erasure.tombstone(uuid.uuid4())
    token = erasure.new_token()
    with as_role(url, APP_ROLE) as c:
        # a reference that is not this case's is refused, whoever calls
        with pytest.raises(errors.RaiseException, match="does not belong"):
            c.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (s.cases[0], other.ref, token, tomb))
        with pytest.raises(errors.RaiseException):
            c.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (s.cases[0], s.ref, "Patient/x", tomb))
        with pytest.raises(errors.RaiseException):
            c.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (s.cases[0], s.ref, token, "drop everything"))
    with psycopg.connect(url, autocommit=True) as c:
        assert db_hits(url, [other.marker]) != {} and db_hits(url, [s.marker]) != {}
        # the owner role has EXECUTE only by being the owner; PUBLIC does not
        c.execute("CREATE ROLE era_nobody NOLOGIN") if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='era_nobody'").fetchone() else None
        c.execute("SET ROLE era_nobody")
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (s.cases[0], s.ref, token, tomb))
        c.execute("RESET ROLE")
        c.execute("DROP ROLE era_nobody")
    # the scrub is the application role's to call for the right patient: it works, and a second call changes nothing
    with as_role(url, APP_ROLE) as c:
        first = c.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (other.cases[0], other.ref, token, tomb)).fetchone()[0]
        second = c.execute("SELECT erasure_scrub_case(%s, %s, %s, %s)", (other.cases[0], other.ref, token, tomb)).fetchone()[0]
    assert first["events_reference"] >= 1 and first["draft_text"] >= 2 and first["reply_text"] == 1
    assert all(v == 0 for k, v in second.items()), second


def test_the_taxonomy_matches_the_database_constraint(pool):
    with pool.connection() as conn:
        definition = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'erasures'::regclass"
                                  " AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%reason%'").fetchone()[0]
    for reason in erasure.REASONS:
        assert f"'{reason}'" in definition
    assert definition.count("'") == 2 * len(erasure.REASONS)
