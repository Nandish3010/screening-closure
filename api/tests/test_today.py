"""T-C22 and T-A102 (C14): the coordinator's Today screen, `GET /today`.

Seven queues of what needs a person now, from the read model. The tests build a seeded population (the agency cohort of
22 cases walked to a spread of states, then breaches and escalations from the sweep, watchdog flags, patient replies, a
call-back, a closure proposal, a declined closure, and an urgent result on the chronic-conditions pack) and prove that:

* each queue's membership equals what the read model says, computed independently here from `GET /cases` and `GET /cases/{id}`;
* the answer is bounded: a request reads at most SCAN_BUDGET open cases, pages by an opaque cursor, and following the cursors
  gives exactly the unpaged answer;
* it is read-only, role-gated (coordinator and supervisor only), never carries a reply's text, and says "complete" only when
  every open case has been read, so an empty queue can be called all clear.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

import agency_world as world
from api_helpers import login_headers, make_client
from app import today as today_module
from app.agency import runner
from app.calendar import default_calendar
from app.clock import clock
from app.events import Event
from app.pathway import PathwayRegistry, load_pathway
from app.routers import cases, replies, today as today_router
from app.sweep import run_sweep
from app.watchdog import run_idle_scan

ROOT = Path(__file__).resolve().parents[2]
CHRONIC = load_pathway(ROOT / "config/pathways/chronic_conditions.yaml")
client = make_client()
COORD = login_headers(client, "coordinator", "today coord")
SUPER = login_headers(client, "supervisor", "today super")
GROUPS = ["urgent", "overdue", "escalations", "replies", "flagged", "signatures", "agency"]
CASE_GROUPS = GROUPS[:-1]


@pytest.fixture
def seeded(monkeypatch):
    world.fresh_world()
    monkeypatch.setattr(cases, "registry", PathwayRegistry([cases.pathway, CHRONIC]))
    ids = world.build_cohort()
    # a breach and its escalations: 12 days on, the sweep marks overdue and climbs the ladder
    clock.advance(days=12)
    run_sweep(cases.store, cases.pathway, default_calendar(), clock.now())
    # the watchdog flags the cases nobody has touched for the idle days
    run_idle_scan(cases.store, cases.pathway, clock.now(), 7)
    yield ids
    clock.reset()


def post_event(case_id, type_, headers, payload=None):
    r = client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def add_reply(case_id, text="ok"):
    r = client.post(f"/cases/{case_id}/replies", json={"channel": "whatsapp", "text": text}, headers=COORD)
    assert r.status_code == 201, r.text
    return r.json()


def pull(headers=COORD, limit=10, group=None, max_pages=500):
    """Follow the cursor to the end. -> ({group: [items]}, number of pages, last answer)."""
    got: dict[str, list] = {g: [] for g in (group or GROUPS)}
    cursor, pages = None, 0
    while True:
        params = {"limit": limit, **({"cursor": cursor} if cursor else {}), **({"group": group} if group else {})}
        r = client.get("/today", params=params, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        pages += 1
        for g in body["groups"]:
            got[g["group"]].extend(g["items"])
        cursor = body["cursor"]
        if cursor is None or pages >= max_pages:
            return got, pages, body
        group = [g for g in (group or CASE_GROUPS) if g != "agency"] or group  # the agency queue is paged apart


def all_case_details():
    rows, cursor = [], None
    while True:
        r = client.get("/cases", params={"limit": 500, **({"cursor": cursor} if cursor else {})}, headers=COORD)
        rows += r.json()
        cursor = r.headers.get("X-Next-Cursor")
        if not cursor:
            break
    return {row["case_id"]: client.get(f"/cases/{row['case_id']}", headers=COORD).json() for row in rows}


def callback_pending(detail):
    pending = False
    for e in detail["events"]:
        if e["type"] == "callback_requested":
            pending = True
        elif e["type"] == "contact_patient":
            pending = False
    return pending


def expected_from_read_model(detail) -> set[str]:
    """The queues a case page's read model puts the case in. Written from the documented rules, not from app.today."""
    if detail["state"] == "closed":
        return set()
    rm = detail["read_model"]
    last_human = rm["last_human_activity_at"]
    events = detail["events"]
    flag_index = max((i for i, e in enumerate(events) if e["type"] == "review_requested" and e["actor"] == "system:triage"), default=None)
    acted_after = flag_index is not None and any(e["actor_kind"] == "human" for e in events[flag_index + 1:])
    out = set()
    prio = detail.get("priority")
    if prio and prio["rank"] == 0 and last_human == detail["created_at"]:  # nobody acted after the case was created
        out.add("urgent")
    if detail["overdue"]:
        out.add("overdue")
    ladders = [rm] + list(rm["milestones"].values())
    if any(x["escalation_level"] > 0 and not x["escalation_accepted"] for x in ladders):
        out.add("escalations")
    flag_open = rm["triage_review_reason"] is not None and not acted_after
    if flag_open or callback_pending(detail):
        out.add("replies")
    if detail["watchdog_flagged"] is not None:
        out.add("flagged")
    if rm["closure_proposal"] or detail["state"] == "declined_pending_signoff":
        out.add("signatures")
    return out


def seed_everything(ids):
    """Queues the sweep and the scan do not fill: replies, a call-back, a closure proposal, a declined closure, urgent."""
    by_state = {t: [i for i, s in zip(ids, world.TARGETS, strict=True) if s == t] for t in set(world.TARGETS)}
    reply_cases = by_state["patient_contacted"][:2] + by_state["owner_accepted"][:1]
    for cid in reply_cases:
        add_reply(cid, "hello")  # no model on this machine: the fail-safe sends every reply to a person
    # a person acts on one of them after the flag: it is no longer waiting
    acted = reply_cases[0]
    post_event(acted, "contact_updated", COORD, {"contact_valid": True})
    post_event(by_state["patient_acknowledged"][0], "callback_requested", COORD, {"reason": "other", "lang": "en"})
    world._add(by_state["confirmatory_completed"][0], "closure_proposed", "system:automation",
               {"evidence_ref": "Procedure/colonoscopy-x", "auto": True})
    world._add(by_state["patient_acknowledged"][1], "record_informed_refusal", world.COORD,
               {"counselling_ref": "DocumentReference/counselling-1", "counselled_by": world.SPEC, "reason": "personal_choice"})
    r = client.post("/cases", json={"patient_ref": "Patient/urgent-1", "pathway": "chronic_conditions", "results": [
        {"kind": "blood_pressure", "values": {"systolic_mmhg": 182, "diastolic_mmhg": 104}}]}, headers=COORD)
    assert r.status_code == 201, r.text
    soon = client.post("/cases", json={"patient_ref": "Patient/soon-1", "pathway": "chronic_conditions", "results": [
        {"kind": "blood_pressure", "values": {"systolic_mmhg": 165, "diastolic_mmhg": 80}}]}, headers=COORD)
    assert soon.status_code == 201, soon.text
    # a second urgent result that a person has already acted on (a lab acknowledges it an hour later): not in the queue
    touched = client.post("/cases", json={"patient_ref": "Patient/urgent-2", "pathway": "chronic_conditions", "results": [
        {"kind": "blood_pressure", "values": {"systolic_mmhg": 190, "diastolic_mmhg": 110}}]}, headers=COORD).json()["case_id"]
    clock.advance(hours=1)
    post_event(touched, "acknowledge", login_headers(client, "lab", "today lab"))
    runner.run_agency(trigger="manual")
    return {"urgent": r.json()["case_id"], "touched": touched, "acted": acted, "replies": reply_cases}


# ---- membership equals the read model -------------------------------------------------------------------------------


def test_t_a102_each_queue_holds_exactly_the_cases_the_read_model_puts_in_it(seeded):
    info = seed_everything(seeded)
    details = all_case_details()
    expected = {g: set() for g in CASE_GROUPS}
    for cid, detail in details.items():
        for g in expected_from_read_model(detail):
            expected[g].add(cid)
    got, pages, last = pull(limit=50)
    assert last["complete"] is True
    for g in CASE_GROUPS:
        assert expected[g], f"the seed must put at least one case in {g}"  # the comparison is not vacuous
        ids = [i["case_id"] for i in got[g]]
        assert len(ids) == len(set(ids)), g
        assert set(ids) == expected[g], g
    assert info["urgent"] in {i["case_id"] for i in got["urgent"]}
    assert info["touched"] not in {i["case_id"] for i in got["urgent"]}  # a person has acted on it
    assert str(info["acted"]) not in {i["case_id"] for i in got["replies"]}  # a person acted after the flag
    assert {str(c) for c in info["replies"][1:]} <= {i["case_id"] for i in got["replies"]}


def test_t_a102_the_agency_queue_is_the_inbox(seeded):
    seed_everything(seeded)
    inbox = client.get("/agency/inbox", headers=COORD).json()
    assert inbox, "the agency run must leave tasks that need a person"
    got, _, _ = pull(limit=50, group=["agency"])
    assert [t["task_id"] for t in got["agency"]] == [t["task_id"] for t in inbox]  # both newest first
    assert {t["case_id"] for t in got["agency"]} == {t["case_id"] for t in inbox}
    assert all("open_case" in t["actions"] for t in got["agency"] if t["case_id"])


def test_a_closed_case_is_in_no_queue_and_a_case_can_be_in_several(seeded):
    seed_everything(seeded)
    got, _, _ = pull(limit=50)
    closed = {str(c) for c, s in zip(seeded, world.TARGETS, strict=True) if s == "closed"}
    assert closed and not closed & {i["case_id"] for g in CASE_GROUPS for i in got[g]}
    multi = [cid for cid in {i["case_id"] for g in CASE_GROUPS for i in got[g]}
             if sum(cid in {i["case_id"] for i in got[g]} for g in CASE_GROUPS) > 1]
    assert multi  # an overdue case that is also flagged is in both


# ---- the items ------------------------------------------------------------------------------------------------------


def test_every_item_says_who_what_and_why_and_offers_only_known_actions(seeded):
    seed_everything(seeded)
    got, _, _ = pull(limit=50)
    known = {"open_case", "accept_escalation", "take_ownership", "confirm_contact", "record_callback"}
    for g in CASE_GROUPS:
        for item in got[g]:
            assert {"case_id", "pathway", "state", "priority", "owner", "since", "why", "actions"} <= set(item)
            assert item["why"]["code"] and "open_case" in item["actions"] and set(item["actions"]) <= known
        assert [i["since"] for i in got[g]] == sorted(i["since"] for i in got[g])  # longest waiting first within a page
    esc = got["escalations"][0]
    assert esc["escalation"]["rung"] and esc["escalation"]["accepted_by"] and "accept_escalation" in esc["actions"]
    assert all(i["why"]["days"] >= 0 for i in got["overdue"])
    urgent = [i for i in got["urgent"] if i["pathway"] == "chronic_conditions"]
    assert urgent and urgent[0]["priority"]["tier"] == "urgent" and urgent[0]["priority"]["reason"]["en"]


def test_a_reply_item_carries_the_triage_reason_and_never_the_text(seeded, monkeypatch):
    secret = "ابي اغير الموعد وايد"  # a Gulf-dialect message
    monkeypatch.setattr(replies.triage, "classify", lambda text, hint=None: {
        "intent": "reschedule", "confidence": 0.95, "model_id": "test-model", "needs_human": True})
    cid = [i for i, s in zip(seeded, world.TARGETS, strict=True) if s == "patient_contacted"][0]
    add_reply(cid, secret)
    got, _, _ = pull(limit=50, group=["replies"])
    item = next(i for i in got["replies"] if i["case_id"] == str(cid))
    assert item["reply"]["reason"] == "triage_uncertain"
    assert item["reply"]["confidence"] == 0.95 and item["reply"]["holds"] == [] and item["reply"]["intent"] == "reschedule"
    assert item["reply"]["forced_reason"] == "ar_gulf"  # why a person reads it: the Gulf-dialect rule
    assert secret not in client.get("/today", params={"group": "replies", "limit": 50}, headers=COORD).text


def test_a_wrong_number_reply_offers_to_confirm_the_contact_and_a_callback_offers_to_record_it(seeded, monkeypatch):
    monkeypatch.setattr(replies.triage, "classify", lambda text, hint=None: {
        "intent": "wrong_number", "confidence": 0.99, "model_id": "test-model", "needs_human": False})
    by_state = {t: [i for i, s in zip(seeded, world.TARGETS, strict=True) if s == t] for t in set(world.TARGETS)}
    wn = by_state["patient_contacted"][0]
    add_reply(wn, "this is not my number")
    cb = by_state["patient_acknowledged"][0]
    post_event(cb, "callback_requested", COORD, {"reason": "other", "lang": "en"})
    got, _, _ = pull(limit=50, group=["replies"])
    by_case = {i["case_id"]: i for i in got["replies"]}
    assert by_case[str(wn)]["actions"][0] == "confirm_contact" and by_case[str(wn)]["reply"]["holds"] == ["wrong_number"]
    assert by_case[str(cb)]["reply"]["callback"] is True and by_case[str(cb)]["why"]["code"] == "callback_waiting"
    # the action works as the case page does it: contact_updated clears the flag, and the case leaves the queue
    post_event(wn, "contact_updated", COORD, {"contact_valid": True})
    again, _, _ = pull(limit=50, group=["replies"])
    assert str(wn) not in {i["case_id"] for i in again["replies"]}


# ---- bounded --------------------------------------------------------------------------------------------------------


def test_following_the_cursors_gives_the_same_answer_as_one_big_page(seeded):
    seed_everything(seeded)
    whole, _, _ = pull(limit=50)
    small, pages, _ = pull(limit=2)
    assert pages > 1
    for g in CASE_GROUPS:
        assert {i["case_id"] for i in small[g]} == {i["case_id"] for i in whole[g]}, g


def test_one_request_reads_at_most_the_scan_budget(seeded, monkeypatch):
    seed_everything(seeded)
    monkeypatch.setattr(today_router, "SCAN_BUDGET", 5)
    monkeypatch.setattr(today_router, "PAGE", 2)
    first = client.get("/today", params={"limit": 50}, headers=COORD).json()
    assert first["scanned"] <= 5 and first["cursor"] is not None and first["complete"] is False
    assert all(g["more"] for g in first["groups"] if g["group"] != "agency")
    whole = {g: {i["case_id"] for i in items} for g, items in pull(limit=50)[0].items()}
    paged, pages, last = pull(limit=50)
    assert last["complete"] and pages > 1
    assert {g: {i["case_id"] for i in items} for g, items in paged.items()} == whole


def test_a_full_queue_stops_the_scan_early(seeded):
    seed_everything(seeded)
    r = client.get("/today", params={"limit": 1}, headers=COORD).json()
    assert r["cursor"] is not None and all(g["count"] <= 1 for g in r["groups"])


def test_the_scan_never_reads_closed_cases(seeded, monkeypatch):
    seen = []
    real = cases.store.load_cases_page

    def spy(pathway, state=None, *a, **k):
        seen.append(state)
        return real(pathway, state, *a, **k)

    monkeypatch.setattr(cases.store, "load_cases_page", spy)
    pull(limit=50, group=["overdue"])
    assert seen and "closed" not in seen and all(s is not None for s in seen)


def test_the_agency_queue_pages_by_its_own_cursor(seeded):
    seed_everything(seeded)
    first = client.get("/today", params={"group": "agency", "limit": 2}, headers=COORD).json()
    agency = first["groups"][0]
    assert agency["count"] == 2 and agency["more"] is True and first["agency_cursor"] and first["scanned"] == 0
    second = client.get("/today", params={"group": "agency", "limit": 2, "agency_cursor": first["agency_cursor"]}, headers=COORD).json()
    ids = [t["task_id"] for t in agency["items"] + second["groups"][0]["items"]]
    assert len(ids) == len(set(ids)) == 4


def test_a_bad_cursor_group_or_limit_is_a_422(seeded):
    for params in ({"cursor": "not-a-cursor"}, {"agency_cursor": "zzz"}, {"group": "nope"}, {"limit": 0}, {"limit": 51}):
        assert client.get("/today", params=params, headers=COORD).status_code == 422, params


# ---- all clear, read-only, RBAC -------------------------------------------------------------------------------------


def test_an_empty_world_is_complete_and_every_queue_is_empty():
    world.fresh_world()
    body = client.get("/today", headers=COORD).json()
    assert body["complete"] is True and body["cursor"] is None
    assert [g["group"] for g in body["groups"]] == GROUPS
    assert all(g["items"] == [] and g["count"] == 0 and g["more"] is False for g in body["groups"])


def test_it_changes_nothing(seeded):
    seed_everything(seeded)
    before = {str(c): len(cases.store.events_for(c)) for c in seeded}
    pull(limit=2)
    assert {str(c): len(cases.store.events_for(c)) for c in seeded} == before


@pytest.mark.parametrize("role,status", [("coordinator", 200), ("supervisor", 200), ("lab", 403), ("primary_care_owner", 403),
                                         ("specialist_owner", 403), ("patient", 403)])
def test_only_the_coordinator_and_the_supervisor_may_read_it(role, status):
    world.fresh_world()
    headers = login_headers(client, role, f"today {role}", "Patient/x" if role == "patient" else None)
    assert client.get("/today", headers=headers).status_code == status


def test_it_needs_a_login():
    assert client.get("/today").status_code == 401


def test_the_groups_module_and_the_route_agree_on_the_seven_queues():
    assert list(today_module.GROUPS) == GROUPS


# ---- the worklist's at_risk is the metrics' lost_to_follow_up (deep link /coordinator?at_risk=1) ----------------------------


def test_t_a103_at_risk_rows_are_exactly_the_cases_the_closure_metrics_count_as_lost(seeded):
    seed_everything(seeded)
    clock.advance(days=40)  # idle past 30 days without progress, on top of the lost-contact case of the cohort
    rows = client.get("/cases", params={"limit": 500}, headers=COORD).json()
    flagged = {r["case_id"] for r in rows if r["at_risk"]}
    assert flagged and len(flagged) < len(rows)
    default_pack = {r["case_id"] for r in rows if r["at_risk"] and r["pathway"] == cases.pathway.name}  # the report is the default pathway's
    assert len(default_pack) == client.get("/metrics/closure", headers=COORD).json()["lost_to_follow_up"]
    # the case page agrees with its row, and a patient's row carries no such field
    cid = next(iter(flagged))
    assert client.get(f"/cases/{cid}", headers=COORD).json()["at_risk"] is True
