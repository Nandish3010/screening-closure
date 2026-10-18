"""T-A33 (A12), unit half: the mapping from events to FHIR resources, idempotency, the retry
queue, and `GET /cases/{id}/fhir`, against a fake FHIR server that enforces referential integrity.
The same walk runs against the real HAPI server in test_fhir_projection_hapi.py."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from api_helpers import actor_of, login_headers, make_client, user_id_of
from app import fhir_projection as fp
from app import notify, ops, users
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.sweep import run_sweep
from fhirtest import FakeFhirClient

PATHWAY = load_pathway(app_settings.pathway_path)
client = make_client()
TAG = {"system": "https://screening-closure.example/test", "code": "unit"}


def login(role, name, patient_ref=None, provider=None):
    body = {"role": role, "name": name, **({"patient_ref": patient_ref} if patient_ref else {}),
            **({"provider": provider} if provider else {})}
    return {"Authorization": f"Bearer {client.post('/auth/dev-login', json=body).json()['token']}"}


@pytest.fixture
def stack(monkeypatch):
    """A projecting store over an in-memory one, wired to a fake FHIR server."""
    fake = FakeFhirClient(existing={"Patient/p-1", "DiagnosticReport/r-1"})
    inner = InMemoryEventStore()
    projector = fp.Projector(fake, inner, PATHWAY, default_calendar(), fp.InMemoryProjectionQueue(), tag=TAG)
    store = fp.ProjectingStore(inner, projector)
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    clock.reset()
    yield fake, projector, store
    clock.reset()


def post(cid, type_, headers, payload=None):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == 201, (type_, r.text)
    return r.json()


def walk(patient_ref="Patient/p-1", result_ref="DiagnosticReport/r-1"):
    """One case from result to closure with a referral, a consent change, a message and an overdue."""
    coord, lab = login("coordinator", "fp coord"), login("lab", "fp lab")
    pco = login("primary_care_owner", "Dr Proj A")
    spec = login("specialist_owner", "Dr Proj B", provider="fp-endo")
    pat = login("patient", "fp pat", patient_ref=patient_ref)
    cid = client.post("/cases", json={"patient_ref": patient_ref, "result_ref": result_ref}, headers=coord).json()["case_id"]
    post(cid, "acknowledge", lab)
    post(cid, "accept_ownership", pco)
    post(cid, "contact_patient", pco)
    draft = str(uuid4())  # ADR-030: a message_sent needs a draft a human approved
    cases.store.append(Event(UUID(cid), "message_approved", clock.now(), actor_of("primary_care_owner", "Dr Proj A"),
                             {"draft_id": draft}), PATHWAY)
    notify.send(UUID(cid), "sms", "en", "first_contact", "Dear Fatima, please call us", draft_id=draft)  # message_sent
    clock.advance(days=12)  # patient_acknowledge (7 wd) breached: mark_overdue + reminder + supervisor rung
    run_sweep(cases.store, PATHWAY, default_calendar(), clock.now(), only=[UUID(cid)])
    post(cid, "patient_acknowledge", pat)
    post(cid, "consent_updated", pat, {"channel_sms": False})
    post(cid, "handoff_requested", pco, {"to_provider": "fp-endo"})
    post(cid, "handoff_accepted", spec)
    post(cid, "book_confirmatory", spec)
    post(cid, "complete_confirmatory", spec)
    post(cid, "clinician_review", spec)
    signer = f"Practitioner/{user_id_of('specialist_owner', 'Dr Proj B')}"
    post(cid, "close", spec, {"evidence_ref": result_ref, "signed_by": signer})
    return cid


def test_t_a33_a_case_walk_projects_each_resource_type(stack):
    fake, projector, store = stack
    cid = walk()
    events = store.events_for(UUID(cid))
    by_type = {t: fake.of_type(t) for t in ("Task", "ServiceRequest", "Appointment", "Communication", "Consent",
                                           "Provenance", "Practitioner", "Device")}
    task = by_type["Task"][0]
    assert len(by_type["Task"]) == 1 and task["id"] == f"task-{cid}"
    assert task["status"] == "completed" and task["businessStatus"]["coding"][0]["code"] == "closed"
    assert task["intent"] == "order" and task["for"] == {"reference": "Patient/p-1"}
    assert task["focus"] == {"reference": "DiagnosticReport/r-1"}
    assert task["owner"] == {"reference": f"Practitioner/{user_id_of('specialist_owner', 'Dr Proj B')}"}
    assert task["identifier"] == [{"system": fp.SYSTEM_CASE, "value": cid}]
    assert task["meta"]["tag"] == [TAG]
    # the referral: requested by the primary care owner, accepted by the specialist
    (sr,) = by_type["ServiceRequest"]
    assert sr["status"] == "active" and sr["intent"] == "order" and sr["subject"] == {"reference": "Patient/p-1"}
    assert sr["requester"] == {"reference": f"Practitioner/{user_id_of('primary_care_owner', 'Dr Proj A')}"}
    assert sr["performer"] == [{"reference": f"Practitioner/{user_id_of('specialist_owner', 'Dr Proj B')}", "display": "fp-endo"}]
    (appt,) = by_type["Appointment"]
    assert appt["status"] == "proposed" and appt["basedOn"] == [{"reference": f"ServiceRequest/{sr['id']}"}]
    assert appt["participant"][0]["actor"] == {"reference": "Patient/p-1"}
    (comm,) = by_type["Communication"]
    assert comm["status"] == "completed" and comm["medium"][0]["text"] == "sms" and comm["subject"] == {"reference": "Patient/p-1"}
    assert comm["sender"] == {"reference": "Device/system-notifier"}
    (consent,) = by_type["Consent"]
    assert consent["id"] == f"consent-{cid}" and consent["status"] == "active" and consent["provision"]["type"] == "permit"
    sms_rule = next(p for p in consent["provision"]["provision"] if p["code"][0]["text"] == "channel_sms")
    assert sms_rule["type"] == "deny"
    # Provenance: one per event, entity = the event id, agent = the actor
    assert len(by_type["Provenance"]) == len(events)
    prov = {p["entity"][0]["what"]["identifier"]["value"]: p for p in by_type["Provenance"]}
    assert set(prov) == {str(e.event_id) for e in events}
    for e in events:
        p = prov[str(e.event_id)]
        assert p["id"] == f"prov-{e.event_id}" and p["recorded"] == e.at.isoformat()
        assert p["activity"]["coding"][0]["code"] == e.type and f"Task/task-{cid}" in [t["reference"] for t in p["target"]]
        role, uid = users.split_actor(e.actor)
        who = p["agent"][0]["who"]
        if role == "patient":
            assert who == {"reference": "Patient/p-1"}
        elif role == "system":
            assert who == {"reference": f"Device/system-{uid}"}
        else:
            assert who == {"reference": f"Practitioner/{uid}"} and p["agent"][0]["role"] == [{"text": role}]
    # Practitioners are created on the fly with the display name; devices for the automated actors
    names = {p["id"]: p["name"][0]["text"] for p in by_type["Practitioner"]}
    assert names[user_id_of("primary_care_owner", "Dr Proj A")] == "Dr Proj A"
    assert {d["id"] for d in by_type["Device"]} >= {"system-intake", "system-scheduler", "system-notifier"}
    assert projector.queue.count() == 0


def test_t_a33_the_message_text_and_other_free_text_never_reach_the_fhir_store(stack):
    fake, _, _ = stack
    walk()
    blob = json.dumps(list(fake.resources.values()))
    assert "Fatima" not in blob and "please call us" not in blob


def test_t_a33_the_task_status_follows_the_state_at_every_step(stack):
    fake, _, store = stack
    coord, lab = login("coordinator", "fp coord"), login("lab", "fp lab")
    cid = client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=coord).json()["case_id"]
    task = lambda: fake.resources[f"Task/task-{cid}"]  # noqa: E731
    assert (task()["status"], task()["businessStatus"]["coding"][0]["code"]) == ("requested", "result_received")
    assert "owner" not in task() and "focus" not in task()
    post(cid, "acknowledge", lab)
    assert (task()["status"], task()["businessStatus"]["coding"][0]["code"]) == ("received", "acknowledged")
    pco = login("primary_care_owner", "Dr Proj A")
    post(cid, "accept_ownership", pco)
    assert (task()["status"], task()["businessStatus"]["coding"][0]["code"]) == ("accepted", "owner_accepted")
    assert task()["owner"]["reference"].startswith("Practitioner/")
    post(cid, "consent_updated", login("patient", "fp pat", patient_ref="Patient/p-1"), {"withdrawn": True})
    assert (task()["status"], task()["businessStatus"]["coding"][0]["code"]) == ("on-hold", "consent_withdrawn")
    assert next(e for e in task()["extension"] if e["url"].endswith("consent-frozen"))["valueBoolean"] is True
    consent = fake.resources[f"Consent/consent-{cid}"]
    assert consent["status"] == "inactive" and consent["provision"]["type"] == "deny"
    for state in ("lost_contact", "referral_rejected", "reopened"):
        assert fp.TASK_STATUS[state] in ("on-hold", "in-progress")
    assert set(fp.TASK_STATUS) == set(PATHWAY.states) | set(PATHWAY.side_states)


def test_t_a33_a_rejected_referral_revokes_the_service_request_with_the_reason(stack):
    fake, _, _ = stack
    coord, lab = login("coordinator", "fp coord"), login("lab", "fp lab")
    pco = login("primary_care_owner", "Dr Proj A")
    spec = login("specialist_owner", "Dr Proj B", provider="fp-endo")
    cid = client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=coord).json()["case_id"]
    for type_, h in [("acknowledge", lab), ("accept_ownership", pco), ("contact_patient", pco)]:
        post(cid, type_, h)
    post(cid, "patient_acknowledge", login("patient", "fp pat", patient_ref="Patient/p-1"))
    post(cid, "handoff_requested", pco, {"to_provider": "fp-endo"})
    (sr,) = fake.of_type("ServiceRequest")
    assert sr["status"] == "draft" and sr["performer"] == [{"display": "fp-endo"}]  # awaiting the receiver
    post(cid, "handoff_rejected", spec, {"reason": "capacity"})
    (sr,) = fake.of_type("ServiceRequest")
    assert sr["status"] == "revoked" and sr["note"] == [{"text": "handoff_rejected: capacity"}]
    assert fake.resources[f"Task/task-{cid}"]["businessStatus"]["coding"][0]["code"] == "referral_rejected"


def test_t_a33_projecting_an_event_again_changes_nothing(stack):
    fake, projector, store = stack
    cid = walk()
    before = json.dumps(fake.resources, sort_keys=True)
    n = fake.transactions
    for ev in store.events_for(UUID(cid)):  # every event, twice more
        projector.project_now(UUID(cid), ev)
        projector.project_now(UUID(cid), ev)
    assert fake.transactions == n + 2 * len(store.events_for(UUID(cid)))
    assert json.dumps(fake.resources, sort_keys=True) == before


def test_t_a33_a_reference_to_a_missing_patient_is_an_identifier_not_a_dangling_link(stack):
    fake, _, _ = stack
    cid = walk(patient_ref="Patient/not-in-hapi", result_ref="DiagnosticReport/also-missing")
    task = fake.resources[f"Task/task-{cid}"]
    assert task["for"]["identifier"] == {"system": fp.SYSTEM_PATIENT_REF, "value": "Patient/not-in-hapi"}
    assert "reference" not in task["for"] and "reference" not in task["focus"]
    comm = fake.of_type("Communication")[0]
    assert "reference" not in comm["subject"]
    consent = fake.of_type("Consent")[0]
    assert "reference" not in consent["patient"]
    # the patient's own actions have no Patient resource to point at: display only
    patient_prov = [p for p in fake.of_type("Provenance") if p["agent"][0]["role"] == [{"text": "patient"}]]
    assert patient_prov and all("reference" not in p["agent"][0]["who"] for p in patient_prov)


# ---- failure: the log is never blocked, the queue catches up -----------------------------------------------


def test_t_a33_when_hapi_is_down_events_still_append_and_queue_and_catch_up_later(stack):
    fake, projector, store = stack
    fake.down = True
    cid = walk()  # the whole walk with HAPI down
    events = store.events_for(UUID(cid))
    assert store.current_state(UUID(cid)) == "closed" and len(events) >= 12  # the log is intact
    assert fake.resources == {} and projector.queue.count(UUID(cid)) == len(events)
    assert client.get(f"/cases/{cid}/fhir", headers=login("coordinator", "fp coord")).json()["events_waiting"] == len(events)
    out = projector.drain()  # still down: nothing lost, nothing hammered
    assert out["projected"] == 0 and out["still_queued"] == len(events)
    fake.down = False
    out = projector.drain()
    assert out == {"tried": len(events), "projected": len(events), "still_queued": 0}
    assert fake.resources[f"Task/task-{cid}"]["status"] == "completed"  # the latest state, not the first event's
    assert len(fake.of_type("Provenance")) == len(events)
    assert projector.queue.count() == 0


def test_t_a33_after_an_outage_is_detected_later_appends_are_queued_without_calling_hapi(stack):
    fake, projector, store = stack
    coord = login("coordinator", "fp coord")
    fake.down = True
    cid = client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=coord).json()["case_id"]
    calls = fake.transactions
    exists_calls = len(fake.exists_calls)
    post(cid, "acknowledge", login("lab", "fp lab"))
    post(cid, "accept_ownership", login("primary_care_owner", "Dr Proj A"))
    assert len(fake.exists_calls) == exists_calls and fake.transactions == calls  # the backoff window skips HAPI
    assert projector.queue.count(UUID(cid)) == 3


def test_t_a33_a_refusal_from_hapi_is_queued_and_does_not_fail_the_request(stack):
    fake, projector, store = stack
    fake.refuse = "HAPI-0000 something is wrong with the resource"
    coord = login("coordinator", "fp coord")
    r = client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=coord)
    assert r.status_code == 201
    cid = r.json()["case_id"]
    assert projector.queue.count(UUID(cid)) == 1 and fake.resources == {}
    fake.refuse = None
    assert projector.drain()["projected"] == 1 and f"Task/task-{cid}" in fake.resources


def test_t_a33_a_bug_in_the_mapping_cannot_block_the_log(stack, monkeypatch):
    fake, projector, store = stack
    monkeypatch.setattr(fp.Mapper, "resources_for", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    r = client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=login("coordinator", "fp coord"))
    assert r.status_code == 201 and projector.queue.count() == 1


# ---- GET /cases/{id}/fhir ----------------------------------------------------------------------------------------


def test_t_a33_the_fhir_route_returns_the_resource_ids(stack):
    fake, _, store = stack
    cid = walk()
    body = client.get(f"/cases/{cid}/fhir", headers=login("coordinator", "fp coord")).json()
    assert body["task"] == f"Task/task-{cid}" and body["consent"] == f"Consent/consent-{cid}"
    assert body["projection_enabled"] is True and body["events_waiting"] == 0
    assert len(body["provenance"]) == len(store.events_for(UUID(cid)))
    assert len(body["service_requests"]) == len(body["appointments"]) == len(body["communications"]) == 1
    # exactly what the projection wrote, nothing more or less
    written = {f"{r['resourceType']}/{r['id']}" for r in fake.resources.values()}
    listed = ({body["task"], body["consent"], *body["service_requests"], *body["appointments"], *body["communications"],
               *body["provenance"], *body["practitioners"], *body["devices"]})
    assert written == listed
    for p in body["practitioners"]:
        assert fake.resources[p]["resourceType"] == "Practitioner"


def test_t_a33_the_fhir_route_follows_case_access(stack):
    cid = walk()
    for role in ("lab", "patient"):
        assert client.get(f"/cases/{cid}/fhir", headers=login(role, f"fp {role}", patient_ref="Patient/p-1" if role == "patient" else None)).status_code == 403
    assert client.get(f"/cases/{cid}/fhir").status_code == 401
    assert client.get(f"/cases/{UUID(int=3)}/fhir", headers=login("coordinator", "fp coord")).status_code == 404
    assert client.get(f"/cases/{cid}/fhir", headers=login("supervisor", "fp sup")).status_code == 200
    # a clinician who does not own the case is refused, the owner is not
    assert client.get(f"/cases/{cid}/fhir", headers=login("primary_care_owner", "Dr Stranger")).status_code == 403
    assert client.get(f"/cases/{cid}/fhir", headers=login("specialist_owner", "Dr Proj B", provider="fp-endo")).status_code == 200


def test_t_a33_without_a_projector_the_route_still_lists_the_ids(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    cid = client.post("/cases", json={"patient_ref": "Patient/p-1"}, headers=login("coordinator", "fp coord")).json()["case_id"]
    body = client.get(f"/cases/{cid}/fhir", headers=login("coordinator", "fp coord")).json()
    assert body["projection_enabled"] is False and body["task"] == f"Task/task-{cid}" and body["events_waiting"] == 0


def test_t_a33_the_actor_names_in_fhir_are_the_only_place_a_person_s_name_appears(stack):
    fake, _, _ = stack
    walk()
    assert actor_of("primary_care_owner", "Dr Proj A").split(":")[1] in json.dumps(fake.of_type("Provenance"))
    assert "Dr Proj A" not in json.dumps(fake.of_type("Provenance"))  # the log-side records stay opaque
    assert "Dr Proj A" in json.dumps(fake.of_type("Practitioner"))  # the erasable FHIR store holds the name


def test_t_d5_a_rejection_is_projected_as_provenance_with_the_rejecting_human(stack, monkeypatch):
    """A16: `message_rejected` is an event like any other, so it becomes a Provenance on the case's
    Task with the rejecting owner as agent, and the reason code in the log entry's payload."""
    from app.ai.draft_store import InMemoryDraftStore
    from app.routers import drafts

    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    fake, projector, store = stack
    coord, lab = login("coordinator", "fp coord"), login("lab", "fp lab")
    pco = login("primary_care_owner", "Dr Proj A")
    cid = client.post("/cases", json={"patient_ref": "Patient/p-1", "result_ref": "DiagnosticReport/r-1"},
                      headers=coord).json()["case_id"]
    post(cid, "acknowledge", lab)
    post(cid, "accept_ownership", pco)
    body = {"purpose": "result_notification", "channel": "sms", "language": "en"}
    draft_id = client.post(f"/cases/{cid}/drafts", json=body, headers=coord).json()["draft_id"]
    rejected = client.post(f"/drafts/{draft_id}/reject", json={"reason": "wrong_language"}, headers=pco)
    assert rejected.status_code == 200, rejected.text

    events = {e.type: e for e in store.events_for(UUID(cid))}
    prov = {p["entity"][0]["what"]["identifier"]["value"]: p for p in fake.of_type("Provenance")}
    p = prov[str(events["message_rejected"].event_id)]
    assert p["activity"]["coding"][0]["code"] == "message_rejected"
    assert f"Task/task-{cid}" in [t["reference"] for t in p["target"]]
    rejecter = actor_of("primary_care_owner", "Dr Proj A")
    assert events["message_rejected"].actor == rejecter and events["message_rejected"].payload["reason"] == "wrong_language"
    assert p["agent"][0]["who"] == {"reference": f"Practitioner/{rejecter.split(':')[1]}"}
    row = drafts.draft_store().get(UUID(draft_id))
    assert row.rejected_by == rejecter and row.rejection_reason == "wrong_language"


def test_t_d5_a_draft_and_its_approval_are_projected_as_provenance(stack, monkeypatch):
    """T-D5, FHIR half: `message_drafted` (agent: the model) and `message_approved` (agent: the
    approving owner) each become a Provenance on the case's Task, and approval fills the draft's
    approver and time. The approval goes through `append(..., within=...)` on the projecting store."""
    from app.ai.draft_store import InMemoryDraftStore
    from app.routers import drafts

    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    fake, projector, store = stack
    coord, lab = login("coordinator", "fp coord"), login("lab", "fp lab")
    pco = login("primary_care_owner", "Dr Proj A")
    cid = client.post("/cases", json={"patient_ref": "Patient/p-1", "result_ref": "DiagnosticReport/r-1"},
                      headers=coord).json()["case_id"]
    post(cid, "acknowledge", lab)
    post(cid, "accept_ownership", pco)
    body = {"purpose": "result_notification", "channel": "sms", "language": "en"}
    r = client.post(f"/cases/{cid}/drafts", json=body, headers=coord)
    assert r.status_code == 201, r.text
    draft_id = r.json()["draft_id"]
    approved = client.post(f"/drafts/{draft_id}/approve", headers=pco)
    assert approved.status_code == 200, approved.text

    events = {e.type: e for e in store.events_for(UUID(cid))}
    prov = {p["entity"][0]["what"]["identifier"]["value"]: p for p in fake.of_type("Provenance")}
    for type_ in ("message_drafted", "message_approved", "message_sent"):
        p = prov[str(events[type_].event_id)]
        assert p["activity"]["coding"][0]["code"] == type_
        assert f"Task/task-{cid}" in [t["reference"] for t in p["target"]]
    approver = actor_of("primary_care_owner", "Dr Proj A")
    assert events["message_drafted"].actor == "model:template" and events["message_approved"].actor == approver
    assert prov[str(events["message_approved"].event_id)]["agent"][0]["who"] == {
        "reference": f"Practitioner/{approver.split(':')[1]}"}
    row = drafts.draft_store().get(UUID(draft_id))
    assert row.approved_by == approver and row.approved_at is not None
    assert "text" not in events["message_drafted"].payload  # the log and the FHIR side carry no draft text
