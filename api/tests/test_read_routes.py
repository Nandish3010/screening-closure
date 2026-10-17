"""T-A27 and T-A28 (A9): the worklist `GET /cases` with its filters, and `GET /cases/{id}/timeline`.

Each test runs against the in-memory store (`make test`) and against Postgres (`make test-db`,
the `postgres` parameter), because the list is built by a batch read that differs per store.
The Postgres database is shared and cumulative, so every expectation is computed over the
cases the test created itself.
"""

import os
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from api_helpers import actor_of, login_headers, make_client, user_id_of
from app.calendar import default_calendar
from app.main import app
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.routers import cases
from app.sweep import run_sweep

client = make_client()
HAS_DB = bool(os.environ.get("DATABASE_URL"))
BACKENDS = [
    "memory",
    pytest.param("postgres", marks=[pytest.mark.integration,
                                    pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")]),
]
KEYS = {"case_id", "pathway", "state", "created_at", "updated_at", "patient_ref", "owner", "provider", "due_at",
        "overdue", "escalation_level", "consent_frozen", "last_event_at", "watchdog_flagged", "pending_handoff",
        "risk_score"}


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


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def login(role, name, provider=None):
    body = {"role": role, "name": name, **({"provider": provider} if provider else {})}
    return {"Authorization": f"Bearer {client.post('/auth/dev-login', json=body).json()['token']}"}


COORD = H("coordinator", "rr coord")
LAB = H("lab", "rr lab")


def post(cid, type_, headers, payload=None):
    return client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def new_case(patient_ref):
    r = client.post("/cases", json={"patient_ref": patient_ref}, headers=COORD)
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def sweep(cids, days=0):
    run_sweep(cases.store, cases.pathway, default_calendar(), clock.now() + timedelta(days=days),
              only=[UUID(c) for c in cids])


def flag(cid, days):
    """A watchdog flag written straight to the store (the route arrives with A11)."""
    cases.store.append(Event(UUID(cid), "watchdog_flagged", clock.now() + timedelta(days=days), "system:watchdog",
                             {"reason": "idle", "idle_days": 7}), cases.pathway)


def build_dataset(seed=7, n=30):
    """Random cases at random depths with random owners and providers, some overdue, some flagged."""
    rng = random.Random(seed)
    pcos = [login("primary_care_owner", f"rr pco {i}") for i in range(3)]
    specs = [login("specialist_owner", f"rr spec {i}", provider=f"rr-endo-{i}") for i in range(2)]
    created = []
    for i in range(n):
        pref = f"Patient/rr-{seed}-{i}"
        cid = new_case(pref)
        created.append(cid)
        depth = rng.randint(0, 9)
        pco, spec = rng.choice(pcos), rng.choice(specs)
        provider = f"rr-endo-{specs.index(spec)}"
        steps = [("acknowledge", LAB, {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
                 ("patient_acknowledge", H("patient", "rr pat", pref), {}),
                 ("handoff_requested", pco, {"to_provider": provider}), ("handoff_accepted", spec, {}),
                 ("book_confirmatory", spec, {}), ("complete_confirmatory", spec, {}), ("clinician_review", spec, {})]
        for type_, headers, payload in steps[:depth]:
            assert post(cid, type_, headers, payload).status_code == 201, (type_, depth)
    sweep(rng.sample(created, 12), rng.choice([10, 20, 40]))
    for cid in rng.sample(created, 5):
        flag(cid, rng.choice([1, 2]))
    return created


def detail(cid):
    return client.get(f"/cases/{cid}", headers=COORD).json()


def listing(headers=COORD, **params):
    r = client.get("/cases", params={k: v for k, v in params.items() if v is not None}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_t_a27_every_filter_equals_a_brute_force_filter_over_the_cases(backend):
    mine = build_dataset()
    details = {c: detail(c) for c in mine}
    order = sorted(mine, key=lambda c: (details[c]["events"][0]["at"], c))  # stable: genesis time, then id

    def expect(state=None, owner=None, overdue=None, provider=None, flagged=None):
        out = []
        for c in order:
            rm = details[c]["read_model"]
            if state is not None and rm["state"] != state:
                continue
            if owner is not None and owner not in (rm["owner"], (rm["owner"] or ":").split(":", 1)[1]):
                continue
            if overdue is not None and rm["overdue"] is not overdue:
                continue
            if provider is not None and rm["provider"] != provider:
                continue
            if flagged is not None and (rm["watchdog_flag"] is not None) is not flagged:
                continue
            out.append(c)
        return out

    def got(**kw):
        return [r["case_id"] for r in listing(**{k: (str(v).lower() if isinstance(v, bool) else v) for k, v in kw.items()})
                if r["case_id"] in set(mine)]

    states = sorted({d["read_model"]["state"] for d in details.values()})
    owners = sorted({d["read_model"]["owner"] for d in details.values() if d["read_model"]["owner"]})
    providers = sorted({d["read_model"]["provider"] for d in details.values() if d["read_model"]["provider"]})
    assert len(states) >= 5 and len(owners) >= 2 and providers and any(d["read_model"]["overdue"] for d in details.values())
    assert any(d["read_model"]["watchdog_flag"] for d in details.values())

    assert got() == expect()
    for st in states:
        assert got(state=st) == expect(state=st), st
    for ow in owners:
        assert got(owner=ow) == expect(owner=ow), ow
        assert got(owner=ow.split(":", 1)[1]) == expect(owner=ow)  # by user id
    # F8-1: a display name never matches (it would put a staff member's name in the URL and the access log)
    assert got(owner="rr pco 0") == []
    assert got(owner="rr pco 0".upper()) == [] and got(owner="primary_care_owner:rr pco 0") == []
    assert got(owner="") == expect()  # an empty filter is no filter, as before
    for pv in providers:
        assert got(provider=pv) == expect(provider=pv), pv
    assert got(provider="nowhere") == []
    for ov in (True, False):
        assert got(overdue=ov) == expect(overdue=ov)
        assert got(flagged=ov) == expect(flagged=ov)
    rng = random.Random(3)
    for _ in range(40):  # combined filters
        kw = {"state": rng.choice([None, *states]), "owner": rng.choice([None, *owners]),
              "overdue": rng.choice([None, True, False]), "provider": rng.choice([None, *providers]),
              "flagged": rng.choice([None, True, False])}
        assert got(**kw) == expect(**kw), kw


def test_t_a27_the_row_shape_and_the_resolved_owner(backend):
    cid = new_case("Patient/rr-shape")
    post(cid, "acknowledge", LAB)
    pco = login("primary_care_owner", "Dr Row Shape", provider="rr-unit")
    post(cid, "accept_ownership", pco, {"provider": "rr-unit"})
    row = next(r for r in listing() if r["case_id"] == cid)
    assert set(row) == KEYS
    me = actor_of("primary_care_owner", "Dr Row Shape")
    assert row["owner"] == {"actor": me, "display_name": "Dr Row Shape", "role": "primary_care_owner"}
    assert row["provider"] == "rr-unit" and row["state"] == "owner_accepted"
    assert row["overdue"] is False and row["escalation_level"] == 0 and row["consent_frozen"] is False
    assert row["watchdog_flagged"] is None and row["pending_handoff"] is None
    assert 0.0 <= row["risk_score"] <= 1.0  # D9: the modelled risk of loss to follow-up; ordering only
    d = detail(cid)
    assert row["last_event_at"] == row["updated_at"] == d["events"][-1]["at"] and row["created_at"] == d["events"][0]["at"]
    # due_at: the current state's SLA (accept_ownership 2 working days -> owner_accepted awaits contact, 5)
    cal = default_calendar()
    entered = datetime.fromisoformat(d["read_model"]["clock_started_at"])
    assert row["due_at"] == cal.add_working_days(entered, 5).astimezone(UTC).isoformat()
    # the case page carries the same worklist fields
    assert {k: d[k] for k in KEYS - {"case_id", "state"}} == {k: row[k] for k in KEYS - {"case_id", "state"}}


def test_t_a27_overdue_rows_report_the_breach_due_date_and_escalation(backend):
    cid = new_case("Patient/rr-overdue")
    sweep([cid], 30)
    row = next(r for r in listing(overdue="true") if r["case_id"] == cid)
    assert row["overdue"] is True and row["escalation_level"] == 3
    assert row["due_at"] == detail(cid)["read_model"]["breach_due_at"]
    assert all(r["overdue"] for r in listing(overdue="true"))


def test_t_a27_closed_and_consent_withdrawn_cases_have_no_due_date(backend):
    cid = new_case("Patient/rr-pat")
    post(cid, "consent_updated", H("patient", "rr pat", "Patient/rr-pat"), {"withdrawn": True})
    row = next(r for r in listing(state="consent_withdrawn") if r["case_id"] == cid)
    assert row["due_at"] is None and row["consent_frozen"] is True


def test_t_a27_a_patient_sees_only_their_own_cases(backend):
    mine, theirs = new_case("Patient/rr-mine"), new_case("Patient/rr-theirs")
    ids = [r["case_id"] for r in listing(headers=H("patient", "rr mine", "Patient/rr-mine"))]
    assert mine in ids and theirs not in ids
    assert all(r["patient_ref"] == "Patient/rr-mine" for r in listing(headers=H("patient", "rr mine", "Patient/rr-mine")))
    assert listing(headers=H("patient", "rr nobody")) == []  # no patient_ref: nothing
    staff = [r["case_id"] for r in listing(headers=COORD)]
    assert mine in staff and theirs in staff


def test_t_a27_the_list_needs_authentication_and_is_stable(backend):
    assert client.get("/cases").status_code == 401
    new_case("Patient/rr-stable")
    first, second = listing(), listing()
    assert [r["case_id"] for r in first] == [r["case_id"] for r in second]
    keys = [(r["created_at"], r["case_id"]) for r in first]
    assert keys == sorted(keys)


# ---- T-A28: the timeline ------------------------------------------------------------------------------


def timeline(cid, headers=COORD):
    r = client.get(f"/cases/{cid}/timeline", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def scripted_case(patient_ref="Patient/rr-tl"):
    """created -> owned -> contacted -> overdue + 2 rungs -> accepted -> patient acks -> referral
    requested and bounced -> flagged idle. The sim clock advances between steps, as in a demo,
    so the log is in time order (the test fixture resets the clock afterwards)."""
    cid = new_case(patient_ref)
    pco = login("primary_care_owner", "Dr Timeline")
    post(cid, "acknowledge", LAB)
    post(cid, "accept_ownership", pco)
    post(cid, "contact_patient", pco)
    clock.advance(days=15)  # patient_acknowledge (7 wd) breached; the reminder and the supervisor rung (+3 wd) are due
    sweep([cid])
    post(cid, "escalation_accepted", login("supervisor", "rr sup"))
    post(cid, "patient_acknowledge", H("patient", "rr pat tl", patient_ref))
    post(cid, "handoff_requested", pco, {"to_provider": "rr-endo-x"})
    clock.advance(days=8)  # the request is unanswered for 3 working days: bounced
    sweep([cid])
    clock.advance(days=1)
    flag(cid, 0)
    return cid, pco


def test_t_a28_the_timeline_equals_the_stored_log_event_for_event(backend):
    cid, _ = scripted_case()
    tl = timeline(cid)
    stored = cases.store.events_for(UUID(cid))
    raw = [i for i in tl["items"] if i["kind"] == "event"]
    assert [(i["seq"], i["event_id"], i["type"], i["at"], i["actor"], i["payload"]) for i in raw] == [
        (e.seq, str(e.event_id), e.type, e.at.isoformat(), e.actor, e.payload) for e in stored]
    assert tl["case_id"] == cid and tl["sim_time"] == clock.now().isoformat()
    ats = [datetime.fromisoformat(i["at"]) for i in tl["items"]]
    assert ats == sorted(ats)  # one list ordered by at
    assert tl["actors"][actor_of("primary_care_owner", "Dr Timeline")]["display_name"] == "Dr Timeline"


def test_t_a28_derived_items_are_in_the_same_list(backend):
    cid, _ = scripted_case()
    items = timeline(cid)["items"]
    kinds = [i["kind"] for i in items]
    assert {"event", "sla_started", "sla_breached", "escalation", "handoff_bounce", "watchdog_flag"} <= set(kinds)
    started = [i for i in items if i["kind"] == "sla_started"]
    assert [s["sla"] for s in started][:4] == ["acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge"]
    assert started[0]["working_days"] == 2 and started[0]["clock_start"] == "state_entry"
    breach = next(i for i in items if i["kind"] == "sla_breached")
    assert breach["sla"] == "patient_acknowledge" and breach["state"] == "patient_contacted"
    assert breach["at"] == breach["due_at"] and breach["detected_at"] > breach["at"]
    rungs = [i for i in items if i["kind"] == "escalation"]
    assert [r["rung"] for r in rungs] == ["reminder", "supervisor"] and [r["level"] for r in rungs] == [1, 2]
    assert rungs[1]["target_role"] == "supervisor" and rungs[1]["after_breach_days"] == 3
    assert rungs[1]["accepted"] is True and rungs[0]["accepted"] is False
    bounce = next(i for i in items if i["kind"] == "handoff_bounce")
    assert bounce["to_provider"] == "rr-endo-x" and bounce["reason"] == "timeout"
    wd = next(i for i in items if i["kind"] == "watchdog_flag")
    assert wd["reason"] == "idle" and wd["idle_days"] == 7 and wd["cleared_at"] is None


def test_t_a28_a_flag_is_cleared_by_the_next_event(backend):
    cid, pco = scripted_case("Patient/rr-tl2")
    clock.advance(days=1)
    post(cid, "handoff_requested", pco, {"to_provider": "rr-endo-y"})  # any new event clears the flag
    wd = next(i for i in timeline(cid)["items"] if i["kind"] == "watchdog_flag")
    assert wd["cleared_at"] is not None
    assert detail(cid)["read_model"]["watchdog_flag"] is None
    assert next(r for r in listing() if r["case_id"] == cid)["watchdog_flagged"] is None


def test_t_a28_the_confirmatory_clock_is_shown_starting_at_referral_acceptance(backend):
    cid = new_case("Patient/rr-ref")
    pco = login("primary_care_owner", "Dr Ref A")
    spec = login("specialist_owner", "Dr Ref B", provider="rr-endo-r")
    for type_, h, p in [("acknowledge", LAB, {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {}),
                        ("patient_acknowledge", H("patient", "rr pat", "Patient/rr-ref"), {}),
                        ("handoff_requested", pco, {"to_provider": "rr-endo-r"}), ("handoff_accepted", spec, {}),
                        ("book_confirmatory", spec, {})]:
        assert post(cid, type_, h, p).status_code == 201, type_
    items = timeline(cid)["items"]
    started = next(i for i in items if i["kind"] == "sla_started" and i["sla"] == "complete_confirmatory")
    accepted = next(i for i in items if i["kind"] == "event" and i["type"] == "handoff_accepted")
    assert started["clock_start"] == "handoff_accepted" and started["at"] == accepted["at"]
    assert started["working_days"] == 31


def test_t_a28_there_is_no_write_route_and_access_follows_the_case(backend):
    cid, _ = scripted_case("Patient/rr-tl3")
    n = len(cases.store.events_for(UUID(cid)))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(f"/cases/{cid}/timeline", headers=COORD).status_code == 405, method
    assert len(cases.store.events_for(UUID(cid))) == n
    assert list(app.openapi()["paths"]["/cases/{case_id}/timeline"]) == ["get"]  # the only method
    assert client.get(f"/cases/{cid}/timeline").status_code == 401
    assert client.get(f"/cases/{UUID(int=5)}/timeline", headers=COORD).status_code == 404
    assert client.get(f"/cases/{cid}/timeline", headers=H("patient", "rr other", "Patient/other")).status_code == 404
    assert client.get(f"/cases/{cid}/timeline", headers=H("patient", "rr own", "Patient/rr-tl3")).status_code == 200
