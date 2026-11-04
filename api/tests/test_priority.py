"""T-PR1 to T-PR16: result priority tiers (ADR-051).

A pathway pack may rank a screening result by rules over its values (the pack's `priority` section). The tier is derived once at
intake by the generic evaluator `app.priority`, and only the tier and the deciding rule's id are recorded: the values never are.
The tier orders and explains. It changes no state, SLA, escalation or closure, and a patient never sees it. Every threshold in
the shipped packs is a placeholder pending a clinician (H1); these tests use the shipped chronic pack for realistic rules and
synthetic packs for the loader's refusals."""

from __future__ import annotations

import copy
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from app import priority
from app.clock import clock
from app.events import InMemoryEventStore
from app.pathway import PathwayError, PathwayRegistry, load_pathway
from app.routers import cases
from test_second_pathway import H, client

ROOT = Path(__file__).resolve().parents[2]
BOWEL_PATH = ROOT / "config/pathways/colorectal_fit.yaml"
CHRONIC_PATH = ROOT / "config/pathways/chronic_conditions.yaml"
BOWEL, CHRONIC = load_pathway(BOWEL_PATH), load_pathway(CHRONIC_PATH)
CFG = CHRONIC.priority


def bp(sys, dia):
    return {"kind": "blood_pressure", "values": {"systolic_mmhg": sys, "diastolic_mmhg": dia}}


def one(kind, field, value):
    return {"kind": kind, "values": {field: value}}


@pytest.fixture
def engine(monkeypatch):
    reg = PathwayRegistry([BOWEL, CHRONIC])
    monkeypatch.setattr(cases, "registry", reg)
    monkeypatch.setattr(cases, "pathway", reg.default)
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    clock.reset()
    yield reg
    clock.reset()


def make(results=None, pathway="chronic_conditions", patient="Patient/pr1", **extra):
    body = {"patient_ref": patient, "pathway": pathway, **({"results": results} if results is not None else {}), **extra}
    return client.post("/cases", json=body, headers=H("coordinator"))


# ---- the evaluator --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("results,tier,rule", [
    ([bp(182, 104)], "urgent", "bp_very_high"),
    ([bp(130, 125)], "urgent", "bp_very_high"),          # `any`: the diastolic value alone is enough
    ([bp(165, 80)], "soon", "bp_high"),
    ([bp(145, 85)], "routine", "bp_raised"),
    ([one("hba1c", "mmol_per_mol", 52)], "routine", "hba1c_raised"),   # a mildly raised HbA1c is routine
    ([one("hba1c", "mmol_per_mol", 80)], "soon", "hba1c_high"),
    ([one("fasting_glucose", "mmol_per_l", 7.4)], "routine", "fasting_glucose_raised"),
    ([one("ldl_cholesterol", "mmol_per_l", 4.5)], "routine", "ldl_raised"),
    ([one("bmi", "kg_per_m2", 33)], "routine", "bmi_raised"),
    ([bp(120, 80)], "routine", priority.NO_RULE),         # nothing matched: the pack's default tier
    ([one("hba1c", "mmol_per_mol", 48)], "routine", "hba1c_raised"),   # `gte` includes the threshold itself
])
def test_t_pr1_the_tier_comes_from_the_packs_rules(results, tier, rule):
    got = priority.derive(CFG, results)
    assert (got.tier, got.rule) == (tier, rule)


def test_t_pr2_the_highest_tier_of_any_result_wins_and_the_first_rule_breaks_a_tie():
    assert priority.derive(CFG, [one("bmi", "kg_per_m2", 35), one("hba1c", "mmol_per_mol", 80), bp(182, 90)]).rule == "bp_very_high"
    assert priority.derive(CFG, [one("bmi", "kg_per_m2", 35), bp(145, 85)]).rule == "bp_raised"  # same tier: listed first wins
    both = priority.derive(CFG, [bp(165, 80), one("hba1c", "mmol_per_mol", 80)])
    assert (both.tier, both.rule) == ("soon", "bp_high")


@pytest.mark.parametrize("results,reason", [
    ([], "1 to 8"),
    ([bp(1, 1)] * 9, "1 to 8"),
    ("blood_pressure", "1 to 8"),
    ([{"kind": "blood_pressure"}], "each result is"),
    ([{"kind": "blood_pressure", "values": {"systolic_mmhg": 1}, "x": 1}], "each result is"),
    ([one("cancer_screen", "positive", 1)], "not one this pathway ranks"),
    ([one("blood_pressure", "pulse", 60)], "unknown field"),
    ([{"kind": "blood_pressure", "values": {}}], "at least one field"),
    ([one("bmi", "kg_per_m2", True)], "finite number"),
    ([one("bmi", "kg_per_m2", "33")], "finite number"),
    ([one("bmi", "kg_per_m2", float("nan"))], "finite number"),
    ([one("bmi", "kg_per_m2", 1e9)], "finite number"),
])
def test_t_pr3_results_that_do_not_fit_the_pack_are_refused(results, reason):
    with pytest.raises(priority.PriorityInputError, match=reason):
        priority.derive(CFG, results)


def test_t_pr4_describe_explains_in_both_languages_and_never_a_tier_the_pack_cannot_explain():
    d = priority.describe(CFG, "urgent", "bp_very_high")
    assert d["rank"] == 0 and d["reason"].keys() == {"en", "ar"} and d["basis"].startswith("PLACEHOLDER")
    assert priority.describe(CFG, "routine", priority.NO_RULE)["reason"]["en"].startswith("No priority rule matched")
    assert priority.describe(CFG, "soon", None) is None and priority.describe(CFG, "soon", "gone_rule") is None
    assert priority.describe(CFG, "nope", "bp_high") is None and priority.describe(CFG, None, None) is None
    assert priority.describe(None, "urgent", "bp_high") is None
    assert [CFG.rank(t) for t in ("urgent", "soon", "routine", "other")] == [0, 1, 2, 3]


# ---- the loader -----------------------------------------------------------------------------------------------------


def _pack(tmp_path, mutate):
    (tmp_path / BOWEL_PATH.name).write_text(BOWEL_PATH.read_text())
    section = copy.deepcopy(yaml.safe_load(CHRONIC_PATH.read_text())["priority"])
    mutate(section)
    pack = tmp_path / "x.yaml"
    pack.write_text(yaml.safe_dump({"extends": BOWEL_PATH.name, "pathway": "x", "version": 1, "priority": section}))
    return pack


def _rule(section, i=0):
    return section["rules"][i]


@pytest.mark.parametrize("mutate,reason", [
    (lambda s: s.update(tiers=[]), "non-empty list of distinct"),
    (lambda s: s.update(tiers=["urgent", "urgent"]), "distinct"),
    (lambda s: s.update(tiers=["Urgent"]), "lower-case"),
    (lambda s: s.update(default_tier="nope"), "default_tier"),
    (lambda s: s.update(results={}), "priority.results"),
    (lambda s: s.update(results={"bmi": []}), "non-empty list"),
    (lambda s: s.update(rules=[]), "non-empty list"),
    (lambda s: s.update(surprise=1), "unknown keys"),
    (lambda s: _rule(s).update(tier="nope"), "tier must be one of"),
    (lambda s: _rule(s).update(kind="nope"), "not in priority.results"),
    (lambda s: _rule(s).update(id=_rule(s, 1)["id"]), "unique"),
    (lambda s: _rule(s).update(all=_rule(s)["any"]), "exactly one of"),
    (lambda s: _rule(s).pop("any"), "exactly one of"),
    (lambda s: _rule(s).update(any=[]), "non-empty list of conditions"),
    (lambda s: _rule(s).update(any=[{"field": "pulse", "op": "gte", "value": 1}]), "a condition is"),
    (lambda s: _rule(s).update(any=[{"field": "systolic_mmhg", "op": "near", "value": 1}]), "a condition is"),
    (lambda s: _rule(s).update(any=[{"field": "systolic_mmhg", "op": "gte", "value": "180"}]), "a condition is"),
    (lambda s: _rule(s).update(any=[{"field": "systolic_mmhg", "op": "gte", "value": True}]), "a condition is"),
    (lambda s: _rule(s).update(reason={"en": "only english"}), "both languages"),
    (lambda s: _rule(s).update(reason={"en": "x" * 91, "ar": "ي"}), "at most 90"),
    (lambda s: _rule(s).pop("basis"), "where the threshold comes from"),
    (lambda s: _rule(s).update(basis="  "), "where the threshold comes from"),
    (lambda s: _rule(s).update(surprise=1), "unknown keys"),
])
def test_t_pr5_a_malformed_priority_section_is_refused_at_load(tmp_path, mutate, reason):
    with pytest.raises(PathwayError, match=reason):
        load_pathway(_pack(tmp_path, mutate))


def test_t_pr6_the_pack_without_a_priority_section_loads_unchanged_and_ranks_nothing():
    assert BOWEL.priority is None


# ---- intake ---------------------------------------------------------------------------------------------------------


def test_t_pr7_intake_records_the_tier_and_the_rule_and_never_the_values(engine):
    cid = make([bp(184, 104), one("bmi", "kg_per_m2", 36.5)]).json()["case_id"]
    genesis = cases.store.events_for(UUID(cid))[0]
    assert genesis.actor == "system:intake"
    assert genesis.payload["priority"] == "urgent" and genesis.payload["priority_rule"] == "bp_very_high"
    text = str(genesis.payload)
    assert "184" not in text and "104" not in text and "36.5" not in text and "systolic" not in text and "results" not in text


def test_t_pr8_the_case_row_and_the_case_page_carry_the_priority_and_its_reason(engine):
    cid = make([bp(165, 80)]).json()["case_id"]
    coord = H("coordinator")
    row = next(r for r in client.get("/cases", headers=coord).json() if r["case_id"] == cid)
    page = client.get(f"/cases/{cid}", headers=coord).json()
    for got in (row["priority"], page["priority"]):
        assert got["tier"] == "soon" and got["rank"] == 1 and got["rule"] == "bp_high"
        assert got["reason"]["en"] == "High blood pressure" and got["reason"]["ar"] and got["basis"].startswith("PLACEHOLDER")


def test_t_pr9_a_case_without_results_has_no_priority_and_a_client_cannot_name_one(engine):
    cid = make(None, priority="urgent", priority_rule="bp_very_high").json()["case_id"]  # not part of the request: ignored
    genesis = cases.store.events_for(UUID(cid))[0].payload
    assert "priority" not in genesis and "priority_rule" not in genesis
    page = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    assert page["priority"] is None
    # the events route cannot write a genesis event or an unknown event type
    assert client.post(f"/cases/{cid}/events", json={"type": "case_created", "payload": {"priority": "urgent"}},
                       headers=H("coordinator")).status_code in (403, 409, 422)


@pytest.mark.parametrize("pathway,results,status,reason", [
    ("colorectal_fit", [bp(180, 100)], 422, "has no priority rules"),   # the bowel pack ranks nothing
    ("chronic_conditions", [one("cancer_screen", "positive", 1)], 422, "not one this pathway ranks"),
    ("chronic_conditions", [], 422, "1 to 8"),
    ("chronic_conditions", [{"kind": "bmi", "values": {"kg_per_m2": "33"}}], 422, "finite number"),
])
def test_t_pr10_results_the_pathway_cannot_rank_are_refused_and_no_case_is_made(engine, pathway, results, status, reason):
    r = make(results, pathway)
    assert r.status_code == status and reason in r.json()["detail"]
    assert client.get("/cases", headers=H("coordinator")).json() == []


def test_t_pr11_a_patient_never_sees_the_priority(engine):
    cid = make([bp(184, 104)], patient="Patient/pr2").json()["case_id"]
    patient = H("patient", "pat", "Patient/pr2")
    rows = client.get("/cases", headers=patient).json()
    assert [r["case_id"] for r in rows] == [cid] and "priority" not in rows[0]
    page = client.get(f"/cases/{cid}", headers=patient).json()
    assert "priority" not in page and "priority" not in str(page.get("events"))


def test_t_pr12_the_tier_changes_no_clock_and_no_state(engine):
    urgent = make([bp(184, 104)], patient="Patient/pr3").json()
    routine = make([bp(145, 85)], patient="Patient/pr4").json()
    assert urgent["priority"]["tier"] == "urgent" and routine["priority"]["tier"] == "routine"
    assert urgent["state"] == routine["state"] == "result_received"
    assert urgent["due_at"] == routine["due_at"], "the SLA clock does not depend on the tier (ADR-051)"
    assert urgent["milestones"] == routine["milestones"]


def test_t_pr13_the_server_order_of_the_worklist_is_unchanged_by_priority(engine):
    ids = [make([bp(145, 85)], patient=f"Patient/o{i}").json()["case_id"] for i in range(3)]
    ids.append(make([bp(190, 130)], patient="Patient/o9").json()["case_id"])
    got = [r["case_id"] for r in client.get("/cases", headers=H("coordinator")).json()]
    assert got == sorted(ids, key=lambda i: UUID(i).int), "the page is in case-id order; the web orders by tier"


def test_t_pr14_the_tier_is_in_no_decision_path():
    """The tier orders and explains: nothing that decides reads it (static check, like the risk score's boundary test)."""
    for module in ("state_machine.py", "sweep.py", "automation.py", "policy.py", "evidence.py", "notify.py", "outbox.py",
                   "consent.py", "watchdog.py", "ops.py"):
        text = (ROOT / "api/app" / module).read_text()
        assert "priority" not in text.lower().replace("outreach priority", ""), f"{module} reads the priority"
