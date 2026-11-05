"""ADR-059 (F5): need-to-know reads of a case and its timeline.

An independent review found that any clinician and the lab could read any case page and timeline (200), while `/fhir`,
`/notifications` and `/agency` already answered 403. Now: coordinators and supervisors read every case (they run the
worklist), a patient reads their own, a clinician reads a case they own, may take (nobody owns it), have a referral
waiting on, or have acted on, and the lab gets a minimal projection (state, result reference, acknowledgement time) and no
timeline at all.
"""

from __future__ import annotations

from api_helpers import actor_of, login_headers, make_client

client = make_client()


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post(cid, type_, headers, payload=None):
    return client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def owned_case(owner="Dr A", patient_ref="Patient/vs-1"):
    cid = client.post("/cases", json={"patient_ref": patient_ref, "result_ref": "DiagnosticReport/fit-77"},
                      headers=H("coordinator")).json()["case_id"]
    assert post(cid, "acknowledge", H("lab")).status_code == 201
    assert post(cid, "accept_ownership", H("primary_care_owner", owner)).status_code == 201
    return cid


def test_a_clinician_cannot_read_another_clinicians_case_or_timeline():
    cid = owned_case("Dr A")
    other = H("primary_care_owner", "Dr B")
    assert client.get(f"/cases/{cid}", headers=other).status_code == 403
    assert client.get(f"/cases/{cid}/timeline", headers=other).status_code == 403
    assert client.get(f"/cases/{cid}", headers=H("specialist_owner", "Dr S")).status_code == 403
    mine = H("primary_care_owner", "Dr A")
    assert client.get(f"/cases/{cid}", headers=mine).status_code == 200
    assert client.get(f"/cases/{cid}/timeline", headers=mine).status_code == 200


def test_the_worklist_of_a_clinician_holds_only_the_cases_they_need_to_know():
    mine, theirs = owned_case("Dr A", "Patient/vs-2"), owned_case("Dr C", "Patient/vs-3")
    unowned = client.post("/cases", json={"patient_ref": "Patient/vs-4"}, headers=H("coordinator")).json()["case_id"]
    ids = {r["case_id"] for r in client.get("/cases", headers=H("primary_care_owner", "Dr A")).json()}
    assert mine in ids and unowned in ids and theirs not in ids  # an unowned case can be taken, so it can be seen
    assert {mine, theirs, unowned} <= {r["case_id"] for r in client.get("/cases", headers=H("coordinator")).json()}
    assert {mine, theirs, unowned} <= {r["case_id"] for r in client.get("/cases", headers=H("supervisor")).json()}


def test_an_unowned_case_can_be_read_to_be_taken_and_a_participant_keeps_reading_it():
    cid = client.post("/cases", json={"patient_ref": "Patient/vs-5"}, headers=H("coordinator")).json()["case_id"]
    post(cid, "acknowledge", H("lab"))
    a = H("primary_care_owner", "Dr A")
    assert client.get(f"/cases/{cid}", headers=a).status_code == 200            # nobody owns it yet
    assert post(cid, "accept_ownership", a).status_code == 201
    assert client.get(f"/cases/{cid}", headers=H("primary_care_owner", "Dr B")).status_code == 403
    assert post(cid, "handoff_requested", a, {"to_provider": "endo-a15"}).status_code == 201
    token = client.post("/auth/dev-login", json={"role": "specialist_owner", "name": "Dr Endo", "provider": "endo-a15"}).json()["token"]
    endo = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/cases/{cid}", headers=endo).status_code == 200          # a referral is waiting for their provider
    assert client.get(f"/cases/{cid}", headers=H("specialist_owner", "Dr S")).status_code == 403  # another provider's clinician
    assert post(cid, "handoff_accepted", endo).status_code == 201
    assert client.get(f"/cases/{cid}", headers=a).status_code == 200            # Dr A acted on it: still in the loop
    assert client.get(f"/cases/{cid}", headers=H("primary_care_owner", "Dr B")).status_code == 403


def test_the_lab_sees_state_result_reference_and_acknowledgement_only():
    cid = owned_case("Dr A")
    lab = H("lab")
    page = client.get(f"/cases/{cid}", headers=lab)
    assert page.status_code == 200
    body = page.json()
    assert body["state"] == "owner_accepted" and body["result_ref"] == "DiagnosticReport/fit-77" and body["acknowledged_at"]
    assert body["events"] == [] and body["risk"] is None
    assert not {"owner", "patient_ref", "actors", "effort", "priority", "consent"} & set(body)
    assert client.get(f"/cases/{cid}/timeline", headers=lab).status_code == 403
    rows = client.get("/cases", headers=lab).json()
    assert rows and all(set(r) == {"case_id", "pathway", "state", "created_at", "updated_at"} for r in rows)
    # the answer to a lab's own event is the same projection, not the log
    cid2 = client.post("/cases", json={"patient_ref": "Patient/vs-lab"}, headers=H("coordinator")).json()["case_id"]
    sent = post(cid2, "acknowledge", lab)
    assert sent.status_code == 201 and sent.json()["events"] == [] and "owner" not in sent.json()


def test_the_patient_still_reads_only_their_own_case():
    cid = owned_case("Dr A", "Patient/vs-9")
    mine = H("patient", "pat", "Patient/vs-9")
    assert client.get(f"/cases/{cid}", headers=mine).status_code == 200
    assert client.get(f"/cases/{cid}", headers=H("patient", "pat2", "Patient/other")).status_code == 404
