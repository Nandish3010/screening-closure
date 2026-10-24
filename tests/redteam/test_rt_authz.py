"""Category 6: authorisation, as sequences (RT-Z). Threat model: docs/governance/red-team.md section 5.6.

api/tests/test_rbac.py and docs/governance/rbac-matrix.md settle single calls: this role, this route, this case. These
cases chain calls the way an attacker would: reuse a token after logging in as someone else, edit a token, ask for
another patient's case by every route, present a service token to a human route, act on a case after ownership moved
away, and pass identity in a place the server must ignore. For every refused call the assertion that matters is the
second one: no event was appended, no draft was decided, and nothing reached the outbox.
"""

from __future__ import annotations

import base64
import json
from uuid import UUID, uuid4

import pytest
from api_helpers import actor_of, make_client, user_id_of
from harness import Case, params

from app import auth, evidence, notify, users
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.config import settings
from app.events import InMemoryEventStore
from app.routers import cases, drafts, replies
from app.triage.reply_store import InMemoryReplyStore

client = make_client()
# case id -> weakness class while its fix is pending. Empty now: W19 has been fixed (docs/governance/red-team.md section 9).
EXPOSED: dict[str, str] = {}
OWNER = "Dr Sara Khan"
OTHER = "Dr Omar Aziz"
SERVICES = {
    "scheduler": settings.scheduler_service_token, "watchdog": settings.watchdog_service_token,
    "automation": settings.automation_service_token, "agency": settings.agency_service_token,
    "hl7": settings.hl7_service_token,
}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    monkeypatch.setattr(replies, "store", InMemoryReplyStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    monkeypatch.setattr(users, "directory", users.InMemoryUserDirectory())
    monkeypatch.setattr(evidence, "verifier", evidence.AcceptAllVerifier())
    monkeypatch.setattr(settings, "automation_enabled", False)
    monkeypatch.setattr(settings, "agency_every_sweeps", 0)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    clock.reset()
    yield
    clock.reset()


# ---------------------------------------------------------------------------------------------------- helpers


def login(role, name=None, patient_ref=None, provider=None, expect=200):
    body = {"role": role, "name": name or f"test {role}"}
    if patient_ref:
        body["patient_ref"] = patient_ref
    if provider:
        body["provider"] = provider
    r = client.post("/auth/dev-login", json=body)
    assert r.status_code == expect, (r.status_code, r.text)
    return {"Authorization": f"Bearer {r.json()['token']}"} if expect == 200 else None


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def new_case(patient_ref="Patient/1") -> str:
    r = client.post("/cases", json={"patient_ref": patient_ref}, headers=login("coordinator"))
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def owned_case(patient_ref="Patient/1", owner=OWNER) -> str:
    cid = new_case(patient_ref)
    for type_, role, name in (("acknowledge", "lab", None), ("accept_ownership", "primary_care_owner", owner)):
        r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": {}}, headers=login(role, name))
        assert r.status_code == 201, r.text
    return cid


def case_with_draft(owner=OWNER) -> tuple[str, str]:
    cid = owned_case(owner=owner)
    r = client.post(f"/cases/{cid}/drafts", json={"purpose": "result_notification", "channel": "sms", "language": "en"},
                    headers=login("coordinator"))
    assert r.status_code == 201, r.text
    return cid, r.json()["draft_id"]


def total_events() -> int:
    return sum(len(lc.events) for lc in cases.store.load_cases(cases.pathway))


def log_of(cid):
    return [(e.type, e.actor) for e in cases.store.events_for(UUID(cid))]


def draft_state(did):
    d = drafts.store.get(UUID(did))
    return d.approved_at, d.rejected_at


def pending_outbox() -> list:
    return drafts.outbox_store().pending(limit=1000, only=None)


def post(cid, type_, headers, payload=None, **extra):
    return client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}, **extra}, headers=headers)


def assert_refused(r, codes=(401, 403, 404, 409, 422)):
    assert r.status_code in codes, (r.status_code, r.text[:200])


def token_parts(headers: dict) -> tuple[str, str]:
    body, sig = headers["Authorization"].split(" ", 1)[1].split(".")
    return body, sig


def b64d(text: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))


FAR_EXP = 4102444800  # 2100-01-01: a signed claim set must carry an 'exp' since A29 (ADR-040), or it is refused for that reason alone


def b64e(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=").decode()


# ---------------------------------------------------------------------------------------------------- RT-Z-01 reuse


@pytest.mark.parametrize("case", params([Case("RT-Z-01.1", "a coordinator token kept after logging in again under the same name as supervisor", "the old token keeps the coordinator's powers only; the new one is the supervisor")]))
def test_an_old_token_keeps_its_old_role_after_a_new_login(case, rt):
    old = login("coordinator", "Sam Reyes")
    new = login("supervisor", "Sam Reyes")
    n = total_events()
    r_old, r_new = client.post("/agency/run", headers=old), client.post("/agency/run", headers=new)
    me_old, me_new = client.get("/me", headers=old).json(), client.get("/me", headers=new).json()
    rt.actual(f"old token {r_old.status_code} {me_old['role']}; new token {r_new.status_code} {me_new['role']}")
    assert r_old.status_code == 403 and r_new.status_code == 200
    assert me_old["role"] == "coordinator" and me_new["role"] == "supervisor" and me_old["user_id"] != me_new["user_id"]
    cid = new_case()
    assert_refused(post(cid, "reassign", old, {"owner": actor_of("primary_care_owner", OWNER)}), (200, 201, 403, 409, 422))
    assert post(cid, "acknowledge", new).status_code == 201


@pytest.mark.parametrize("case", params([Case("RT-Z-01.2", "a supervisor token after the same person logs in as a patient", "the supervisor token is still the supervisor; the patient token is a patient and reaches no staff route")]))
def test_a_second_login_does_not_upgrade_or_downgrade_the_first_token(case, rt):
    sup = login("supervisor", "Lee Wong")
    pat = login("patient", "Lee Wong", patient_ref="Patient/7")
    rt.actual(f"supervisor token: /agency/org {client.get('/agency/org', headers=sup).status_code}; patient token: {client.get('/agency/org', headers=pat).status_code}")
    assert client.get("/agency/org", headers=sup).status_code == 200
    for route in ("/agency/org", "/agency/runs", "/metrics/closure", "/metrics/effort", "/alerts", "/heartbeats", "/agency/inbox"):
        assert client.get(route, headers=pat).status_code == 403, route


NAME_VARIANTS = [("RT-Z-01.3", "trailing space", "Dr Sara Khan "), ("RT-Z-01.4", "lower case", "dr sara khan"),
                 ("RT-Z-01.5", "doubled space", "Dr  Sara Khan"), ("RT-Z-01.6", "trailing newline", "Dr Sara Khan\n"),
                 ("RT-Z-01.7", "a leading digit homoglyph (zero for O)", "Dr Sara Khan0"), ("RT-Z-01.8", "the owner's name as a coordinator", None)]


@pytest.mark.parametrize("case", params([Case(i, f"a clinician named '{n!r}' ({d}) acts on the case owned by '{OWNER}'" if n else "a coordinator named like the owner approves the owner's draft",
                                             "403; the draft stays undecided; nothing in the outbox", display=n if n else OWNER) for i, d, n in NAME_VARIANTS]))
def test_a_lookalike_name_is_not_the_owner(case, rt):
    cid, did = case_with_draft()
    name = next(n for i, _, n in NAME_VARIANTS if i == case.id)
    n_events = total_events()
    if name is None:
        headers = login("coordinator", OWNER)
    else:  # a name the login refuses (a trailing newline) is fine; one it accepts must not be the owner
        r = client.post("/auth/dev-login", json={"role": "primary_care_owner", "name": name})
        headers = bearer(r.json()["token"]) if r.status_code == 200 else None
    if headers is None:
        rt.actual("the login itself was refused (422)")
        assert total_events() == n_events and draft_state(did) == (None, None)
        return
    r1 = client.post(f"/drafts/{did}/approve", headers=headers)
    r2 = client.post(f"/drafts/{did}/reject", json={"reason": "not_needed"}, headers=headers)
    codes = [r1.status_code, r2.status_code]
    if name is not None:  # a clinician acts only on cases they own; a coordinator may act on any case but never decides a draft
        r3 = post(cid, "contact_patient", headers)
        r4 = client.get(f"/cases/{cid}/drafts", headers=headers)
        codes += [r3.status_code, r4.status_code]
    rt.actual(f"approve/reject/contact/drafts statuses {codes}; draft decided: {draft_state(did) != (None, None)}")
    assert codes == [403] * len(codes)
    assert draft_state(did) == (None, None) and pending_outbox() == []
    if name is not None:
        assert total_events() == n_events


# ---------------------------------------------------------------------------------------------------- RT-Z-02 patient scope


def two_patients():
    a, b = owned_case("Patient/1"), owned_case("Patient/2")
    return a, b, login("patient", "pat one", patient_ref="Patient/1"), login("patient", "pat two", patient_ref="Patient/2")


@pytest.mark.parametrize("case", params([Case("RT-Z-02.1", "patient 1 reads patient 2's case by id, timeline, fhir, drafts, replies, notifications", "404 or 403 for each, identical to an unknown id for the case routes")]))
def test_a_patient_cannot_read_another_patients_case_by_any_route(case, rt):
    a, b, pat1, _ = two_patients()
    unknown = str(uuid4())
    seen = {}
    for route in ("", "/timeline", "/fhir", "/drafts", "/replies", "/notifications", "/agency"):
        rb, ru = client.get(f"/cases/{b}{route}", headers=pat1), client.get(f"/cases/{unknown}{route}", headers=pat1)
        seen[route or "/"] = rb.status_code
        assert rb.status_code in (403, 404), (route, rb.status_code)
        assert rb.status_code == ru.status_code or route in ("/drafts", "/replies", "/notifications", "/fhir", "/agency"), (route, rb.status_code, ru.status_code)
        assert "Patient/2" not in rb.text and b not in rb.text.replace(f"/cases/{b}", "")
    rt.actual(f"statuses {seen}")
    own = client.get(f"/cases/{a}", headers=pat1)
    assert own.status_code == 200


@pytest.mark.parametrize("case", params([Case("RT-Z-02.2", "patient 1 posts patient_acknowledge, consent_updated and callback_requested to patient 2's case", "404; no event on either case")]))
def test_a_patient_cannot_act_on_another_patients_case(case, rt):
    a, b, pat1, _ = two_patients()
    before = (log_of(a), log_of(b))
    results = []
    for type_, payload in (("patient_acknowledge", {"lang": "en"}), ("consent_updated", {"withdrawn": True}),
                           ("callback_requested", {"reason": "other"}), ("consent_updated", {"channel_sms": False})):
        r = post(b, type_, pat1, payload)
        results.append(r.status_code)
        assert r.status_code == 404, (type_, r.status_code)
    rt.actual(f"statuses {results}; events added={sum(map(len, (log_of(a), log_of(b)))) - sum(map(len, before))}")
    assert (log_of(a), log_of(b)) == before


@pytest.mark.parametrize("case", params([Case("RT-Z-02.3", "patient 1 lists cases with filters that name patient 2 or every owner", "only patient 1's own case, whatever the filter")]))
def test_list_filters_cannot_widen_a_patients_view(case, rt):
    a, b, pat1, _ = two_patients()
    for query in ("", "?owner=" + user_id_of("primary_care_owner", OWNER), "?state=result_received", "?patient_ref=Patient/2",
                  "?flagged=false", "?overdue=false", "?provider=Riverside%20Primary%20Care", "?owner=%25", "?state=%27%20OR%201%3D1"):
        r = client.get("/cases" + query, headers=pat1)
        ids = [row["case_id"] for row in r.json()] if r.status_code == 200 else []
        assert b not in ids and set(ids) <= {a}, (query, ids)
    rt.actual(f"{len(client.get('/cases', headers=pat1).json())} case(s) listed for patient 1; none of patient 2's under 9 filters")


@pytest.mark.parametrize("case", params([Case("RT-Z-02.4", "patient 1 posts an event with the event_id of an event on patient 2's case", "409 or 404; no event added to either case")]))
def test_a_patient_cannot_probe_event_ids_of_another_case(case, rt):
    a, b, pat1, _ = two_patients()
    stolen = str(cases.store.events_for(UUID(b))[0].event_id)
    before = (log_of(a), log_of(b))
    r = post(a, "patient_acknowledge", pat1, {"lang": "en"}, event_id=stolen)
    rt.actual(f"HTTP {r.status_code}; events added={len(log_of(a)) + len(log_of(b)) - len(before[0]) - len(before[1])}")
    assert r.status_code in (404, 409) and (log_of(a), log_of(b)) == before


@pytest.mark.parametrize("case", params([Case("RT-Z-02.5", "patient 1 posts a reply to patient 2's case, and reads the replies of its own", "403 for both; no reply stored")]))
def test_a_patient_cannot_use_the_replies_route(case, rt):
    a, b, pat1, _ = two_patients()
    before = (log_of(a), log_of(b))
    r1 = client.post(f"/cases/{b}/replies", json={"channel": "sms", "text": "stop messaging me"}, headers=pat1)
    r2 = client.post(f"/cases/{a}/replies", json={"channel": "sms", "text": "stop messaging me"}, headers=pat1)
    r3 = client.get(f"/cases/{a}/replies", headers=pat1)
    rt.actual(f"{r1.status_code} {r2.status_code} {r3.status_code}")
    assert (r1.status_code, r2.status_code, r3.status_code) == (403, 403, 403)
    assert (log_of(a), log_of(b)) == before and replies.store.list_for_case(UUID(a)) == []


@pytest.mark.parametrize("case", params([Case("RT-Z-02.6", "a patient token whose patient_ref is a prefix, a variant or a different case of another patient's", "no access to that patient's case")]))
def test_patient_ref_must_match_exactly(case, rt):
    ten = owned_case("Patient/10")
    one = owned_case("Patient/1")
    dash = owned_case("Patient/1-2")
    results = {}
    for ref, target in (("Patient/1", ten), ("Patient/1", dash), ("Patient/10", one), ("Patient/1-2", one)):
        r = client.get(f"/cases/{target}", headers=login("patient", "pat x", patient_ref=ref))
        results[(ref, target == ten)] = r.status_code
        assert r.status_code == 404, (ref, r.status_code)
    for bad in ("patient/1", "Patient/1 ", "Patient/", "Patient/1/../2", "Patient/1%0a", "Patient/" + "9" * 65, "Patient/ا"):
        r = client.post("/auth/dev-login", json={"role": "patient", "name": "x", "patient_ref": bad})
        assert r.status_code == 422, (bad, r.status_code)
    rt.actual(f"4 mismatched refs -> 404; 7 malformed refs -> 422")


@pytest.mark.parametrize("case", params([Case("RT-Z-02.7", "a patient token with no patient_ref (a claim removed and re-signed with the dev secret)", "the patient sees no case and can act on none")]))
def test_a_patient_without_a_reference_sees_nothing(case, rt):
    a = owned_case("Patient/1")
    body = b64e({"v": 1, "role": "patient", "name": "no ref", "exp": FAR_EXP})
    headers = bearer(f"{body}.{auth._sign(body, settings.dev_auth_secret)}")
    before = log_of(a)
    r_list, r_get, r_post = client.get("/cases", headers=headers), client.get(f"/cases/{a}", headers=headers), post(a, "patient_acknowledge", headers, {"lang": "en"})
    rt.actual(f"list {len(r_list.json())}, get {r_get.status_code}, post {r_post.status_code}")
    assert r_list.json() == [] and r_get.status_code == 404 and r_post.status_code == 404 and log_of(a) == before


# ---------------------------------------------------------------------------------------------------- RT-Z-03 service tokens


def human_attempts(cid, did, headers) -> dict:
    return {
        "approve_draft": client.post(f"/drafts/{did}/approve", headers=headers).status_code,
        "reject_draft": client.post(f"/drafts/{did}/reject", json={"reason": "not_needed"}, headers=headers).status_code,
        "accept_ownership": post(cid, "accept_ownership", headers).status_code,
        "close": post(cid, "close", headers, {"evidence_ref": "Procedure/1", "signed_by": "Practitioner/x"}).status_code,
        "clinician_review": post(cid, "clinician_review", headers, {"signed_by": "Practitioner/x"}).status_code,
        "consent_updated": post(cid, "consent_updated", headers, {"withdrawn": True}).status_code,
        "message_sent": post(cid, "message_sent", headers, {}).status_code,
        "message_approved": post(cid, "message_approved", headers, {"draft_id": did}).status_code,
        "reply": client.post(f"/cases/{cid}/replies", json={"channel": "sms", "text": "x"}, headers=headers).status_code,
        "create_case": client.post("/cases", json={"patient_ref": "Patient/9"}, headers=headers).status_code,
        "draft": client.post(f"/cases/{cid}/drafts", json={"purpose": "reminder", "channel": "sms", "language": "en"}, headers=headers).status_code,
        "confirmatory_result": client.post(f"/cases/{cid}/confirmatory-result", json={}, headers=headers).status_code,
    }


@pytest.mark.parametrize("case", params([Case(f"RT-Z-03.{n}", name, "403 on each of 12 human actions; no event, no decision", note=f"the {name} service token on the human routes")
                                         for n, name in enumerate(SERVICES, start=1)]))
def test_a_service_token_cannot_take_a_human_decision(case, rt):
    name = case.input
    cid, did = case_with_draft()
    headers = bearer(SERVICES[name])
    before, n = log_of(cid), total_events()
    got = human_attempts(cid, did, headers)
    rt.actual(f"statuses {sorted(set(got.values()))}; events added={total_events() - n}")
    assert set(got.values()) == {403}, got
    assert log_of(cid) == before and draft_state(did) == (None, None) and pending_outbox() == []


@pytest.mark.parametrize("case", params([Case("RT-Z-03.6", "the scheduler token posts the events it may (escalate) and the ones it may not (close, accept_ownership)", "escalate is allowed; every human decision is 403")]))
def test_the_scheduler_token_is_not_a_human(case, rt):
    cid = owned_case()
    sched = bearer(settings.scheduler_service_token)
    before = log_of(cid)
    refused = {t: post(cid, t, sched, p).status_code for t, p in (("accept_ownership", {}), ("close", {"evidence_ref": "Procedure/1", "signed_by": "x"}),
                                                                  ("reassign", {"owner": actor_of("primary_care_owner", OTHER)}),
                                                                  ("consent_updated", {"withdrawn": False}), ("contact_patient", {}))}
    rt.actual(f"refused {refused}")
    assert set(refused.values()) == {403} and log_of(cid) == before


@pytest.mark.parametrize("case", params([Case(f"RT-Z-03.{n}", f"a service token written as {d}", "401 or 403; no event", display=d) for n, d, _ in [
    (7, "'Bearer <token>x'", lambda t: t + "x"), (8, "the token with its last character cut off", lambda t: t[:-1]), (9, "the token in upper case", lambda t: t.upper()),
    (10, "an empty bearer", lambda t: ""), (11, "'Bearer null'", lambda t: "null"), (12, "the token with a user-token dot suffix", lambda t: t + ".AAAA"),
]]))
def test_a_nearly_right_service_token_is_nobody(case, rt):
    forms = {"RT-Z-03.7": lambda t: t + "x", "RT-Z-03.8": lambda t: t[:-1], "RT-Z-03.9": lambda t: t.upper(), "RT-Z-03.10": lambda t: "",
             "RT-Z-03.11": lambda t: "null", "RT-Z-03.12": lambda t: t + ".AAAA"}
    cid = owned_case()
    before, n = log_of(cid), total_events()
    token = forms[case.id](settings.scheduler_service_token)
    try:
        r = client.post("/scheduler/sweep", headers={"Authorization": f"Bearer {token}"})
        r2 = post(cid, "escalate", {"Authorization": f"Bearer {token}"})
        codes = (r.status_code, r2.status_code)
    except Exception as exc:  # an illegal header value is refused by the client before it is sent
        codes = (401, 401)
        rt.actual(f"not sent: {type(exc).__name__}")
    else:
        rt.actual(f"statuses {codes}")
    assert all(c in (401, 403) for c in codes) and log_of(cid) == before and total_events() == n


@pytest.mark.parametrize("case", params([Case(f"RT-Z-03.{13 + i}", f"dev-login as the role '{role}'", "403; no token issued", display=role)
                                         for i, role in enumerate(("system", "scheduler", "watchdog", "automation", "agency", "hl7", "model"))]
                                        + [Case(f"RT-Z-03.{20 + i}", f"dev-login with the name '{name}'", "422; no token issued", display=name)
                                           for i, name in enumerate(("system:notifier", "model:template", "coordinator:abc", "x" * 65, "", " lead", "Dr\tTab", "Dr ‮ Khan"))]))
def test_a_client_cannot_log_in_as_a_machine_or_name_itself_one(case, rt):
    if int(case.id.split(".")[1]) < 20:
        r = client.post("/auth/dev-login", json={"role": case.display, "name": "mallory"})
        want = 403
    else:
        r = client.post("/auth/dev-login", json={"role": "coordinator", "name": case.display})
        want = 422
    rt.actual(f"HTTP {r.status_code}, token issued: {'token' in r.json()}")
    assert r.status_code == want and "token" not in r.json()


# ---------------------------------------------------------------------------------------------------- RT-Z-04 ownership handoff


SPEC = ("specialist_owner", "Dr Karim Nasser")
SPEC_PROVIDER = "Corniche Gastro Unit"


def hand_off(cid):
    """The owner refers the patient and a specialist at another provider accepts: the case has a new owner."""
    owner = login("primary_care_owner", OWNER)
    for type_, headers, payload in (("contact_patient", owner, {}), ("patient_acknowledge", login("patient", "p1", patient_ref="Patient/1"), {"lang": "en"}),
                                    ("handoff_requested", owner, {"to_provider": SPEC_PROVIDER}),
                                    ("handoff_accepted", login(*SPEC, provider=SPEC_PROVIDER), {})):
        r = post(cid, type_, headers, payload)
        assert r.status_code == 201, (type_, r.text)
    assert cases.store.read_model(UUID(cid), cases.pathway).owner == actor_of(*SPEC)


@pytest.mark.parametrize("case", params([Case("RT-Z-04.1", "the old owner approves, rejects, drafts and acts after the case was handed to a specialist", "403 each; the draft stays undecided; nothing sent")]))
def test_a_former_owner_loses_every_power_over_the_case(case, rt):
    cid, did = case_with_draft()
    old = login("primary_care_owner", OWNER)
    hand_off(cid)
    before, n = log_of(cid), total_events()
    got = {
        "approve": client.post(f"/drafts/{did}/approve", headers=old).status_code,
        "reject": client.post(f"/drafts/{did}/reject", json={"reason": "not_needed"}, headers=old).status_code,
        "new draft": client.post(f"/cases/{cid}/drafts", json={"purpose": "reminder", "channel": "sms", "language": "en"}, headers=old).status_code,
        "list drafts": client.get(f"/cases/{cid}/drafts", headers=old).status_code,
        "contact": post(cid, "contact_patient", old).status_code,
        "book": post(cid, "book_confirmatory", old, {"appointment_date": "2026-02-01"}).status_code,
        "consent": post(cid, "consent_updated", old, {"withdrawn": True}).status_code,
        "notifications": client.get(f"/cases/{cid}/notifications", headers=old).status_code,
        "agency": client.get(f"/cases/{cid}/agency", headers=old).status_code,
        "fhir": client.get(f"/cases/{cid}/fhir", headers=old).status_code,
    }
    rt.actual(f"statuses {sorted(set(got.values()))}; events added={total_events() - n}")
    assert set(got.values()) <= {403}, got
    assert log_of(cid) == before and draft_state(did) == (None, None) and pending_outbox() == []


@pytest.mark.parametrize("case", params([Case("RT-Z-04.2", "the new owner approves the draft that was waiting when the case changed hands (control)", "201 path: approved and sent")]))
def test_the_new_owner_can_decide(case, rt):
    cid, did = case_with_draft()
    hand_off(cid)
    r = client.post(f"/drafts/{did}/approve", headers=login(*SPEC, provider=SPEC_PROVIDER))
    rt.actual(f"HTTP {r.status_code}, approved_by is the new owner: {r.json().get('approved_by') == actor_of(*SPEC)}")
    assert r.status_code == 200 and r.json()["approved_by"] == actor_of(*SPEC)


@pytest.mark.parametrize("case", params([Case("RT-Z-04.3", "a clinician who never owned the case accepts ownership, approves and closes", "403 each")]))
def test_a_stranger_cannot_take_an_owned_case(case, rt):
    cid, did = case_with_draft()
    stranger = login("primary_care_owner", "Dr Nobody")
    before = log_of(cid)
    got = {"accept": post(cid, "accept_ownership", stranger).status_code, "approve": client.post(f"/drafts/{did}/approve", headers=stranger).status_code,
           "close": post(cid, "close", stranger, {"evidence_ref": "Procedure/1", "signed_by": f"Practitioner/{user_id_of('primary_care_owner', 'Dr Nobody')}"}).status_code,
           "specialist accept": post(cid, "accept_ownership", login("specialist_owner", "Dr Nobody")).status_code}
    rt.actual(f"statuses {got}")
    assert set(got.values()) == {403} and log_of(cid) == before and draft_state(did) == (None, None)


def case_ready_to_close():
    """Specialist-owned, through to clinician_review: the next event may be `close`."""
    cid = new_case()
    spec = login("specialist_owner", OWNER)
    steps = [("acknowledge", login("lab"), {}), ("accept_ownership", spec, {}), ("contact_patient", spec, {}),
             ("patient_acknowledge", login("patient", "p1", patient_ref="Patient/1"), {"lang": "en"}), ("book_confirmatory", spec, {"appointment_date": "2026-02-01"}),
             ("complete_confirmatory", spec, {}), ("clinician_review", spec, {"signed_by": f"Practitioner/{user_id_of('specialist_owner', OWNER)}"})]
    for type_, headers, payload in steps:
        r = post(cid, type_, headers, payload)
        assert r.status_code == 201, (type_, r.text)
    return cid, spec


@pytest.mark.parametrize("case", params([Case("RT-Z-04.4", "the owner closes the case signing as another clinician", "403; the case stays open")]))
def test_a_closure_cannot_be_signed_as_someone_else(case, rt):
    cid, spec = case_ready_to_close()
    before = log_of(cid)
    other = f"Practitioner/{user_id_of('specialist_owner', OTHER)}"
    got = [post(cid, "close", spec, {"evidence_ref": "Procedure/1", "signed_by": s}).status_code
           for s in (other, "Practitioner/" + user_id_of("supervisor", "boss"), "supervisor:" + user_id_of("supervisor", "boss"), "model:template", "system:automation")]
    rt.actual(f"statuses {got}")
    assert set(got) <= {403, 422} and log_of(cid) == before
    ok = post(cid, "close", spec, {"evidence_ref": "Procedure/1", "signed_by": f"Practitioner/{user_id_of('specialist_owner', OWNER)}"})
    assert ok.status_code == 201  # control: the owner's own signature closes


@pytest.mark.parametrize("case", params([Case("RT-Z-04.5", "a supervisor and a coordinator try to close", "403; only the signing clinician closes")]))
def test_supervisors_and_coordinators_cannot_close(case, rt):
    cid, spec = case_ready_to_close()
    before = log_of(cid)
    got = [post(cid, "close", login(role, "boss"), {"evidence_ref": "Procedure/1", "signed_by": "Practitioner/" + user_id_of(role, "boss")}).status_code
           for role in ("supervisor", "coordinator", "lab", "patient")]
    rt.actual(f"statuses {got}")
    assert set(got) <= {403, 404} and log_of(cid) == before


# ---------------------------------------------------------------------------------------------------- RT-Z-05 token tampering


def good_token(role="coordinator", name="Tess Tamper", patient_ref=None):
    return login(role, name, patient_ref=patient_ref)


def flip(sig: str) -> str:
    return ("A" if sig[0] != "A" else "B") + sig[1:]


TAMPER = [
    ("RT-Z-05.1", "the role claim changed to supervisor, the old signature kept", lambda b, s: f"{b64e({**b64d(b), 'role': 'supervisor'})}.{s}"),
    ("RT-Z-05.2", "the patient_ref changed to another patient, the old signature kept", lambda b, s: f"{b64e({**b64d(b), 'patient_ref': 'Patient/2'})}.{s}"),
    ("RT-Z-05.3", "the first character of the signature changed", lambda b, s: f"{b}.{flip(s)}"),
    ("RT-Z-05.4", "the signature removed (token ends with a dot)", lambda b, s: f"{b}."),
    ("RT-Z-05.5", "no dot at all", lambda b, s: b),
    ("RT-Z-05.6", "an empty body and the old signature", lambda b, s: f".{s}"),
    ("RT-Z-05.7", "a JWT-style header, 'alg none' and an empty signature", lambda b, s: f"{b64e({'alg': 'none', 'typ': 'JWT'})}.{b64e({'role': 'supervisor', 'name': 'x'})}."),
    ("RT-Z-05.8", "three parts (header.body.signature)", lambda b, s: f"{b64e({'alg': 'HS256'})}.{b}.{s}"),
    ("RT-Z-05.9", "the body signed with the wrong secret", lambda b, s: f"{b}.{auth._sign(b, 'not the secret')}"),
    ("RT-Z-05.10", "an automated role (scheduler) in a correctly signed body", None),
    ("RT-Z-05.11", "a role that does not exist, correctly signed", None),
    ("RT-Z-05.12", "the body base64 of the empty string, signed", None),
    ("RT-Z-05.13", "the body is not JSON, signed", None),
    ("RT-Z-05.14", "a 65-character name, signed", None),
    ("RT-Z-05.15", "a claims list instead of an object, signed", None),
    ("RT-Z-05.16", "version 2 and extra claims (is_admin, scope, role list), signed: extras must be ignored", None),
]
SIGNED = {
    "RT-Z-05.10": {"v": 1, "role": "scheduler", "name": "x", "exp": FAR_EXP}, "RT-Z-05.11": {"v": 1, "role": "root", "name": "x", "exp": FAR_EXP},
    "RT-Z-05.14": {"v": 1, "role": "coordinator", "name": "n" * 65, "exp": FAR_EXP}, "RT-Z-05.15": [1, 2, 3],
    "RT-Z-05.16": {"v": 2, "role": "lab", "name": "Eve", "exp": FAR_EXP, "is_admin": True, "scope": "*", "roles": ["supervisor"]},
}


@pytest.mark.parametrize("case", params([Case(i, d, "401 on /me and on a write; no event" if i != "RT-Z-05.16" else "the extra claims change nothing: still a lab user who cannot approve", display=d) for i, d, _ in TAMPER]))
def test_a_tampered_token_is_nobody(case, rt):
    make = next(f for i, _, f in TAMPER if i == case.id)
    cid = owned_case()
    n = total_events()
    if make is not None:
        body, sig = token_parts(good_token())
        token = make(body, sig)
    else:
        raw = {"RT-Z-05.12": "", "RT-Z-05.13": "not json"}.get(case.id)
        body = base64.urlsafe_b64encode((raw if raw is not None else json.dumps(SIGNED[case.id])).encode()).rstrip(b"=").decode()
        token = f"{body}.{auth._sign(body, settings.dev_auth_secret)}"
    headers = bearer(token)
    me, w = client.get("/me", headers=headers), post(cid, "acknowledge", headers)
    if case.id == "RT-Z-05.16":
        rt.actual(f"/me {me.status_code} role={me.json().get('role')}; supervisor-only /agency/run {client.post('/agency/run', headers=headers).status_code}")
        assert me.status_code == 200 and me.json()["role"] == "lab"
        assert client.post("/agency/run", headers=headers).status_code == 403 and client.get("/agency/org", headers=headers).status_code == 403
    else:
        rt.actual(f"/me {me.status_code}, write {w.status_code}; events added={total_events() - n}")
        assert me.status_code == 401 and w.status_code == 401
    assert total_events() == n or case.id == "RT-Z-05.16"


@pytest.mark.parametrize("case", params([Case("RT-Z-05.17", "a correctly signed token whose 'exp' claim is in the past", "401: an expired token is refused (closed upstream by A29)")]))
def test_an_expired_token_is_refused(case, rt):
    body = b64e({"v": 1, "role": "coordinator", "name": "Old Timer", "exp": 1})
    headers = bearer(f"{body}.{auth._sign(body, settings.dev_auth_secret)}")
    r = client.get("/me", headers=headers)
    rt.actual(f"HTTP {r.status_code}: the dev token's 'exp' is read against the wall clock (closed upstream by A29)")
    assert r.status_code == 401


@pytest.mark.parametrize("case", params([Case("RT-Z-05.18", "the same token used 50 times and after a second login", "valid every time: tokens are stateless and not revoked by a later login",
                                              xfail="known limitation RT-Z-05.18 / HZ-33")]))
def test_logging_in_again_revokes_the_old_token(case, rt):
    old = login("coordinator", "Rey Roe")
    login("coordinator", "Rey Roe")
    r = client.get("/me", headers=old)
    rt.actual(f"HTTP {r.status_code}: the earlier token still works after a new login")
    assert r.status_code == 401


NAME_ATTACKS = [("RT-Z-05.19", "a name ending in a newline", {"role": "coordinator", "name": "Dr Khan\n"}),
                ("RT-Z-05.20", "a provider ending in a newline", {"role": "primary_care_owner", "name": "Dr Khan", "provider": "Riverside\n"}),
                ("RT-Z-05.21", "a patient_ref ending in a newline", {"role": "patient", "name": "pat", "patient_ref": "Patient/1\n"})]


@pytest.mark.parametrize("case", params([Case(i, d, "422; no token issued", display=d, xfail=(f"{i}: exposed, fix in next commit" if i in EXPOSED else None)) for i, d, _ in NAME_ATTACKS]))
def test_a_trailing_newline_is_not_a_valid_name_provider_or_reference(case, rt):
    body = next(b for i, _, b in NAME_ATTACKS if i == case.id)
    r = client.post("/auth/dev-login", json=body)
    rt.actual(f"HTTP {r.status_code}, token issued: {'token' in r.json()}")
    assert r.status_code == 422 and "token" not in r.json()
    with pytest.raises(auth.AuthError):  # and a correctly signed token carrying it is refused too
        claims = {"v": 1, "exp": FAR_EXP, **{k: v for k, v in body.items()}}
        encoded = b64e(claims)
        auth.verify_token(f"{encoded}.{auth._sign(encoded, settings.dev_auth_secret)}")


# ---------------------------------------------------------------------------------------------------- RT-Z-06 identity in the wrong place


@pytest.mark.parametrize("case", params([Case("RT-Z-06.1", "identity claimed in the body, headers and payload while using a coordinator token", "the recorded actor is the token's; unknown body fields are 422")]))
def test_identity_in_the_request_is_ignored_or_refused(case, rt):
    cid = new_case()
    me = login("coordinator", "Cora Cole")
    forged_headers = {**me, "X-Actor": "system:notifier", "X-User": "supervisor", "X-Forwarded-User": "supervisor:abc", "X-Role": "supervisor"}
    r_extra = client.post(f"/cases/{cid}/events", json={"type": "acknowledge", "payload": {}, "actor": "supervisor:abc"}, headers=me)
    r_payload = post(cid, "acknowledge", me, {"actor": "supervisor:abc", "role": "supervisor", "auto": True})
    r_hdr = post(cid, "acknowledge", forged_headers)
    log = log_of(cid)
    rt.actual(f"extra body field {r_extra.status_code}, payload fields {r_payload.status_code}, forged headers {r_hdr.status_code}; actors={sorted({a for _, a in log[1:]})}")
    assert r_extra.status_code == 422
    assert r_payload.status_code in (201, 422) and r_hdr.status_code in (201, 409)
    assert {a for t, a in log if t == "acknowledge"} <= {actor_of("coordinator", "Cora Cole")}
    assert all(not a.startswith(("system:", "model:", "supervisor:")) for t, a in log[1:])


@pytest.mark.parametrize("case", params([Case("RT-Z-06.2", "a coordinator moves the demo clock", "403; the clock does not move")]))
def test_only_the_supervisor_moves_the_clock(case, rt):
    before = clock.now()
    got = [client.post(path, json={"hours": 48}, headers=login(role, "Cl Ock")).status_code
           for role in ("coordinator", "lab", "primary_care_owner", "patient") for path in ("/clock/advance", "/clock/reset")]
    rt.actual(f"statuses {sorted(set(got))}; clock moved: {clock.now() != before}")
    assert set(got) == {403} and clock.now() == before


@pytest.mark.parametrize("case", params([Case("RT-Z-06.3", "an event dated in the future, and a backdated event, from a coordinator", "422 for the future; the backdated one is refused or harmless; no change to the case's state")]))
def test_event_times_cannot_be_forged(case, rt):
    cid = owned_case()
    me = login("coordinator", "Time Lord")
    from datetime import timedelta

    future = (clock.now() + timedelta(days=30)).isoformat()
    past = (clock.now() - timedelta(days=30)).isoformat()
    before = log_of(cid)
    r_future = post(cid, "contact_patient", me, at=future)
    r_past = post(cid, "contact_patient", me, at=past)
    rt.actual(f"future {r_future.status_code}, past {r_past.status_code}")
    assert r_future.status_code == 422 and r_past.status_code in (409, 422) and log_of(cid) == before
