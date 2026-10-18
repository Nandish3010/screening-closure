"""SEC-1 fix 4: FHIR reference hygiene and a retry queue that cannot be starved by poison rows.

Unit tests (fake FHIR server, in-memory stores). The Postgres half is test_fhir_queue_pg.py.
"""

from uuid import uuid4

import pytest
from api_helpers import login_headers, make_client
from fhirtest import FakeFhirClient, PoisonFake

from app import fhir_projection as fp
from app import notify, ops, payloads
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases

PATHWAY = load_pathway(app_settings.pathway_path)
client = make_client()
COORD = login_headers(client, "coordinator", "hygiene coord")


# ---- references -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    ["Patient/..", "Patient/.", "Patient/", "Patient/a.b", "Patient/a/b", "Patient/../x", "patient/1", "Patient/%2e%2e",
     "Patient/" + "a" * 65, "..", "Practitioner/1", "Patient/ 1", "Patient/1\n"],
)
def test_a_bad_patient_ref_is_422_and_writes_nothing(ref):
    r = client.post("/cases", json={"patient_ref": ref}, headers=COORD)
    assert r.status_code == 422, ref


@pytest.mark.parametrize(
    "ref",
    ["DiagnosticReport/..", "DiagnosticReport/.", "DiagnosticReport/../Patient/1", "DiagnosticReport/a.b",
     "Procedure/1", "diagnosticreport/1", "DiagnosticReport/" + "r" * 65, "DiagnosticReport/"],
)
def test_a_bad_result_ref_is_422(ref):
    assert client.post("/cases", json={"result_ref": ref}, headers=COORD).status_code == 422, ref


@pytest.mark.parametrize("pref", ["Patient/1", "Patient/p-1", "Patient/" + "a" * 64, "Patient/0b8e6d5a-3c1f-4a52-8a77-9d4e2f6b1c33"])
@pytest.mark.parametrize("rref", ["DiagnosticReport/r-1", "DiagnosticReport/" + "r" * 64])
def test_well_formed_refs_are_accepted(pref, rref):
    assert client.post("/cases", json={"patient_ref": pref, "result_ref": rref}, headers=COORD).status_code == 201


@pytest.mark.parametrize("ref", ["Procedure/..", "Procedure/.", "DiagnosticReport/...", "Procedure/a/..", "Procedure/"])
def test_dots_only_ids_are_refused_in_evidence_and_result(ref):
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("close", {"evidence_ref": ref, "signed_by": "Practitioner/x"})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("complete_confirmatory", {"result_ref": ref})


@pytest.mark.parametrize("signer", ["Practitioner/..", "Practitioner/.", "Practitioner/..."])
def test_a_dots_only_practitioner_id_cannot_sign(signer):
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("close", {"evidence_ref": "Procedure/ok", "signed_by": signer})
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("clinician_review", {"signed_by": signer})


def test_appointment_ref_cannot_be_a_traversal():
    for ref in ("a/../b", "a/./b", "a//b"):
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("book_confirmatory", {"appointment_ref": ref})
    payloads.validate("book_confirmatory", {"appointment_ref": "Appointment/appt-1.2"})


def test_dev_login_takes_only_the_same_patient_ref_shape():
    for ref in ("Patient/..", "../etc", "Patient/a.b", "1"):
        r = client.post("/auth/dev-login", json={"role": "patient", "name": "x", "patient_ref": ref})
        assert r.status_code == 422, ref
    r = client.post("/auth/dev-login", json={"role": "patient", "name": "x", "patient_ref": "Patient/p-1"})
    assert r.status_code == 200


# ---- nothing unsafe reaches a URL -----------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["Patient/..", "Patient/.", "Patient/../x", "../Patient/1", "Patient/a b", "Patient/a?b=1",
                                 "Patient/1#x", "Patient/%2e%2e", "Patient", "", "Patient/" + "a" * 65])
def test_the_http_client_refuses_an_unsafe_reference_before_any_request(ref, monkeypatch):
    c = fp.HttpFhirClient("http://hapi.invalid/fhir")
    monkeypatch.setattr(c, "_call", lambda *a, **k: pytest.fail("a request was made"))
    with pytest.raises(fp.FhirError):
        c.exists(ref)


def test_the_http_client_builds_a_quoted_url_for_a_good_reference():
    assert fp.safe_ref_url("http://h/fhir", "Patient/p-1.x") == "http://h/fhir/Patient/p-1.x"
    assert fp.is_safe_ref("Procedure/colonoscopy-0b8e6d5a-3c1f-4a52-8a77-9d4e2f6b1c33")
    assert not fp.is_safe_ref(None) and not fp.is_safe_ref(5)


def test_a_legacy_log_with_an_unsafe_ref_projects_as_an_identifier_without_a_lookup():
    """Events written before the stricter schema may hold anything; the projection must neither look it up nor choke."""
    fake = FakeFhirClient()
    store = InMemoryEventStore()
    case_id = uuid4()
    genesis = Event(case_id, CASE_CREATED, clock.now(), "system:intake", {"patient_ref": "Patient/..", "result_ref": "DiagnosticReport/../x"})
    store.create_case(case_id, PATHWAY.name, genesis, PATHWAY.initial_state)
    projector = fp.Projector(fake, store, PATHWAY, default_calendar(), fp.InMemoryProjectionQueue())
    projector.project_now(case_id, genesis)
    assert fake.exists_calls == []
    task = fake.resources[f"Task/task-{case_id}"]
    assert "reference" not in task["for"] and task["for"]["identifier"]["value"] == "Patient/.."


def test_fhir_ids_are_never_dots_only():
    assert fp._fhir_id("..") == "unknown" and fp._fhir_id(".") == "unknown" and fp._fhir_id("") == "unknown"
    assert fp._fhir_id("a.b") == "a.b" and fp._fhir_id("a b") == "a-b"


def test_the_confirmatory_recorder_does_not_probe_an_unsafe_patient_ref():
    from datetime import UTC, datetime

    from app import confirmatory

    fake = FakeFhirClient(existing={"Patient/p-1"})
    rec = confirmatory.FhirRecorder(fake)
    rec.record(uuid4(), "Patient/..", datetime(2026, 1, 5, tzinfo=UTC))
    assert fake.exists_calls == []
    rec.record(uuid4(), "Patient/p-1", datetime(2026, 1, 5, tzinfo=UTC))
    assert fake.exists_calls == ["Patient/p-1"]


# ---- the retry policy and queue -----------------------------------------------------------------------------------


def test_retry_policy_backs_off_exponentially_and_caps():
    p = fp.RetryPolicy(base_seconds=30, cap_seconds=600, max_attempts=6)
    assert [p.delay_after(n) for n in range(1, 9)] == [0, 30, 60, 120, 240, 480, 600, 600]
    assert [p.is_dead(n) for n in (1, 5, 6, 7)] == [False, False, True, True]
    for bad in ({"base_seconds": -1}, {"cap_seconds": 1, "base_seconds": 5}, {"max_attempts": 0}):
        with pytest.raises(ValueError):
            fp.RetryPolicy(**bad)


class Tick:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_the_queue_orders_by_next_attempt_and_respects_due_only():
    tick = Tick()
    q = fp.InMemoryProjectionQueue(fp.RetryPolicy(30, 3600, 10), clock=tick)
    a, b, c, case = uuid4(), uuid4(), uuid4(), uuid4()
    for e in (a, b, c):
        q.add(e, case, "x")
        tick.t += 1
    assert [e for e, _, _ in q.pending()] == [a, b, c]
    q.add(a, case, "again")  # a's second failure: due in 30 s, so it falls behind b and c
    assert [e for e, _, _ in q.pending()] == [b, c, a]
    assert [e for e, _, _ in q.pending(due_only=True)] == [b, c]
    tick.t += 31
    assert [e for e, _, _ in q.pending(due_only=True)] == [b, c, a]
    assert [x[2] for x in q.pending()] == [1, 1, 2]
    assert q.count() == 3 and q.dead_count() == 0


def test_an_event_is_dead_lettered_after_max_attempts_and_can_be_revived():
    tick = Tick()
    q = fp.InMemoryProjectionQueue(fp.RetryPolicy(1, 10, 3), clock=tick)
    e, case = uuid4(), uuid4()
    assert [q.add(e, case, "boom") for _ in range(3)] == [False, False, True]
    assert q.count() == 0 and q.dead_count() == 1 and q.pending() == []
    assert q.revive_dead() == 1
    assert q.count() == 1 and q.dead_count() == 0 and q.pending()[0][2] == 0
    assert q.add(e, case, "boom") is False  # a fresh set of attempts


# ---- poison rows cannot starve the queue ---------------------------------------------------------------------------------


@pytest.fixture
def poisoned(monkeypatch):
    fake = PoisonFake(existing={"Patient/p-1"})
    tick = Tick()
    queue = fp.InMemoryProjectionQueue(fp.RetryPolicy(30, 3600, 5), clock=tick)
    inner = InMemoryEventStore()
    projector = fp.Projector(fake, inner, PATHWAY, default_calendar(), queue)
    store = fp.ProjectingStore(inner, projector)
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    clock.reset()
    yield fake, projector, tick
    clock.reset()


def make_cases(fake, poison_count: int, good_count: int = 1):
    """Create the poison cases first and the good one(s) last, all while HAPI refuses everything, so every
    genesis event is queued in that order. Then HAPI recovers except for the poison cases."""
    fake.refuse = "HAPI-0000 down for maintenance"
    poison = [client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=COORD).json()["case_id"] for _ in range(poison_count)]
    good = [client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=COORD).json()["case_id"] for _ in range(good_count)]
    fake.refuse = None
    fake.poison = set(poison)
    return poison, good


def test_101_poison_rows_and_one_good_row_the_good_row_projects(poisoned):
    fake, projector, tick = poisoned
    poison, (good,) = make_cases(fake, 101)
    assert projector.queue.count() == 102
    projector.drain(limit=100)  # the 100 oldest are poison: all fail and go to the back of the line
    assert f"Task/task-{good}" not in fake.resources
    out = projector.drain(limit=100)  # now the good row (and the 101st poison) are the earliest due
    assert f"Task/task-{good}" in fake.resources, "the good row was starved by the poison rows"
    assert out["projected"] == 1 and out["still_queued"] == 101


def test_the_background_loop_pass_does_not_starve_either(poisoned):
    fake, projector, tick = poisoned
    poison, (good,) = make_cases(fake, 101)
    projector.drain(limit=100, due_only=True)
    # the next pass comes a drain interval later: the 100 failed ones wait 30 s, the 101st poison and the good row are due
    tick.t += 20
    projector.drain(limit=100, due_only=True)
    assert f"Task/task-{good}" in fake.resources


def test_a_poison_row_is_dead_lettered_and_stops_costing_attempts(poisoned):
    fake, projector, tick = poisoned
    poison, (good,) = make_cases(fake, 3)
    calls = []
    for _ in range(8):
        calls.append(fake.transactions)
        projector.drain(limit=100)
        tick.t += 4000
    assert projector.queue.count() == 0 and projector.queue.dead_count() == 3
    assert f"Task/task-{good}" in fake.resources
    n = fake.transactions
    projector.drain()
    assert fake.transactions == n  # dead rows are not tried again


def test_due_only_waits_for_the_backoff(poisoned):
    fake, projector, tick = poisoned
    make_cases(fake, 1, good_count=0)
    assert projector.drain(due_only=True)["tried"] == 1  # first retry: due at once
    assert projector.drain(due_only=True)["tried"] == 0  # second: 30 s away
    tick.t += 31
    assert projector.drain(due_only=True)["tried"] == 1


def test_get_alerts_reports_the_dead_lettered_count(poisoned):
    fake, projector, tick = poisoned
    make_cases(fake, 2, good_count=0)
    assert [a for a in client.get("/alerts", headers=COORD).json() if a["kind"] == fp.DEAD_LETTER_ALERT] == []
    for _ in range(6):
        projector.drain()
        tick.t += 4000
    alerts = [a for a in client.get("/alerts", headers=COORD).json() if a["kind"] == fp.DEAD_LETTER_ALERT]
    assert len(alerts) == 1
    assert alerts[0]["detail"] == {"dead_lettered": 2} and alerts[0]["cleared_at"] is None and alerts[0]["derived"] is True
    assert client.get("/alerts", params={"active": "true"}, headers=COORD).json()[0]["kind"] == fp.DEAD_LETTER_ALERT
    projector.queue.revive_dead()
    assert [a for a in client.get("/alerts", headers=COORD).json() if a["kind"] == fp.DEAD_LETTER_ALERT] == []


def test_get_alerts_is_unchanged_without_a_projection(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    assert client.get("/alerts", headers=COORD).json() == []
    assert fp.dead_letter_alert(cases.store) is None


def test_wrap_store_accepts_a_minimal_settings_object_and_uses_the_default_retry_policy():
    class S:  # what a test or a tool passes: no retry knobs
        fhir_projection = True
        fhir_base_url = "http://hapi.invalid/fhir"
        fhir_projection_drain_seconds = 3600.0

    wrapped = fp.wrap_store(InMemoryEventStore(), S, PATHWAY, default_calendar())
    try:
        assert (wrapped.projector.queue.policy.base_seconds, wrapped.projector.queue.policy.max_attempts) == (30.0, 12)
    finally:
        wrapped.projector.stop()
