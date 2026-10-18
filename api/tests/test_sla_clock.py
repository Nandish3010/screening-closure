"""T-A34 and T-A35 (A13, ADR-020, A18, ADR-028): the `close` SLA, and the colonoscopy clock measured from referral
acceptance with a fallback to state entry. Since A18 that clock is the `confirmatory_completed` milestone, not a
per-state SLA; the milestones are tested further in test_milestones.py."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.pathway import PathwayError, load_pathway
from app.state_machine import step
from app.sweep import SCHEDULER_ACTOR, due_at_for, milestone_due_at, plan_case
from test_state_machine import OWNER, TO_CONTACTED, drive, make_event

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday
CASE = uuid4()
SPEC = "specialist_owner:dr_s"


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


def to_reviewed():
    """Days 1-3 contact, 4 ack, 5 book, 6 complete, 7 review (clock for `close` starts day 7)."""
    return drive(
        *TO_CONTACTED,
        ("patient_acknowledge", "patient:p", {}),
        ("book_confirmatory", OWNER, {}),
        ("complete_confirmatory", SPEC, {}),
        ("clinician_review", OWNER, {}),
    )


# ---- T-A34: every open state has a timer; `close` can breach --------------------------------------


def test_t_a34_close_has_an_sla_in_the_yaml_so_clinician_reviewed_has_a_timer():
    assert PATHWAY.slas_working_days["close"] == 3
    assert PATHWAY.sla_for_state("clinician_reviewed") == ("close", 3)


def test_t_a34_a_reviewed_case_goes_overdue_and_walks_the_ladder():
    m = to_reviewed()
    assert m.state == "clinician_reviewed" and m.clock_started_at == at(7)
    due = due_at_for(m, PATHWAY, UAE)
    assert due == UAE.add_working_days(at(7), 3)
    assert plan_case(CASE, m, PATHWAY, UAE, due) == []  # exactly at the due time is not late
    planned = plan_case(CASE, m, PATHWAY, UAE, due + timedelta(minutes=1))
    assert [e.type for e in planned] == ["mark_overdue", "escalate"]
    assert planned[0].payload["sla_event"] == "close" and planned[0].payload["sla_working_days"] == 3
    assert all(e.actor == SCHEDULER_ACTOR for e in planned)
    for e in planned:
        m = step(m, e, PATHWAY)
    assert m.state == "clinician_reviewed" and m.overdue and m.escalation_level == 1
    assert m.breach_due_at == due


def test_t_a34_every_open_state_of_the_main_path_has_a_timer():
    # a per-state SLA, or (confirmatory_booked) the milestone that awaits the event leaving the state (A18)
    for state in PATHWAY.states[:-1]:
        assert PATHWAY.sla_for_state(state) is not None or PATHWAY.milestone_awaiting(state), state
    assert [m.name for m in PATHWAY.milestone_awaiting("confirmatory_booked")] == ["confirmatory_completed"]
    assert PATHWAY.sla_for_state("confirmatory_booked") is None
    assert PATHWAY.sla_for_state("referral_rejected") == ("reassign", 2)


# ---- T-A35: the colonoscopy clock starts at referral acceptance --------------------------------------


def colonoscopy(m):
    return m.milestones["confirmatory_completed"]


def test_t_a35_the_yaml_declares_the_colonoscopy_milestone_not_a_state_sla():
    d = PATHWAY.milestones["confirmatory_completed"]
    assert (d.from_event, d.until_event, d.working_days) == ("handoff_accepted", "complete_confirmatory", 31)
    assert "National Guideline Appendix 2" in d.source  # the 31 comes from the guideline's KPI table, not IFHAS
    assert "complete_confirmatory" not in PATHWAY.slas_working_days
    for event in PATHWAY.slas_working_days:
        assert PATHWAY.clock_start_for(event) == "state_entry", event


def booked_after_referral():
    """Referral accepted on day 6, then booked on day 7."""
    m = drive(
        *TO_CONTACTED,
        ("patient_acknowledge", "patient:p", {}),
    )
    m = step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(5)), PATHWAY)
    m = step(m, make_event("handoff_accepted", SPEC, {}, at(6)), PATHWAY)
    return step(m, make_event("book_confirmatory", SPEC, {}, at(7)), PATHWAY)


def test_t_a35_the_31_working_days_run_from_referral_acceptance_not_from_booking():
    m = booked_after_referral()
    assert m.state == "confirmatory_booked"
    assert m.referral_accepted_at == at(6) and colonoscopy(m).started_at == at(6)
    due = milestone_due_at(m, "confirmatory_completed", PATHWAY, UAE)
    assert due == UAE.add_working_days(at(6), 31)
    assert due <= UAE.add_working_days(at(7), 31)
    # measured from acceptance: overdue the minute after that due time, whatever the booking date
    assert plan_case(CASE, m, PATHWAY, UAE, due) == []
    planned = plan_case(CASE, m, PATHWAY, UAE, due + timedelta(minutes=1))
    assert planned[0].type == "mark_overdue" and planned[0].payload["due_at"] == due.isoformat()
    assert planned[0].payload["milestone"] == "confirmatory_completed" and "state" not in planned[0].payload
    m = step(m, planned[0], PATHWAY)
    assert colonoscopy(m).breach_due_at == due and colonoscopy(m).overdue
    assert not m.overdue  # the state-level flag is untouched: confirmatory_booked has no per-state SLA


def test_t_a35_acceptance_early_in_the_pathway_counts_even_though_booking_is_much_later():
    # accepted on day 6, booked three weeks (15 working days) later: the clock is already running
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}))
    m = step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(5)), PATHWAY)
    m = step(m, make_event("handoff_accepted", SPEC, {}, at(6)), PATHWAY)
    m = step(m, make_event("book_confirmatory", SPEC, {}, at(27)), PATHWAY)
    assert colonoscopy(m).started_at == at(6)
    assert milestone_due_at(m, "confirmatory_completed", PATHWAY, UAE) == UAE.add_working_days(at(6), 31)


def test_t_a35_without_a_handoff_the_clock_falls_back_to_state_entry():
    m = drive(
        *TO_CONTACTED,
        ("patient_acknowledge", "patient:p", {}),
        ("book_confirmatory", OWNER, {}),
    )
    assert m.referral_accepted_at is None and colonoscopy(m).started_at == at(5)
    assert milestone_due_at(m, "confirmatory_completed", PATHWAY, UAE) == UAE.add_working_days(at(5), 31)
    # and before the case reaches the state that awaits the colonoscopy, the milestone has not started
    earlier = drive(*TO_CONTACTED)
    assert colonoscopy(earlier).started_at is None
    assert milestone_due_at(earlier, "confirmatory_completed", PATHWAY, UAE) is None


def test_t_a35_other_slas_ignore_a_handoff_and_start_at_state_entry():
    m = drive(*TO_CONTACTED)
    m = step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(4)), PATHWAY)
    m = step(m, make_event("handoff_accepted", SPEC, {}, at(5)), PATHWAY)
    assert m.state == "patient_contacted" and m.clock_started_at == at(3)  # unchanged: contact entered day 3
    m = step(m, make_event("patient_acknowledge", "patient:p", {}, at(6)), PATHWAY)
    assert m.clock_started_at == at(6)  # patient_acknowledged is a state_entry SLA too


def test_t_a35_a_new_acceptance_restarts_a_running_confirmatory_clock():
    m = booked_after_referral()
    due = milestone_due_at(m, "confirmatory_completed", PATHWAY, UAE)
    late = due + timedelta(days=1)
    for e in plan_case(CASE, m, PATHWAY, UAE, late):
        m = step(m, e, PATHWAY)
    assert colonoscopy(m).overdue and colonoscopy(m).escalation_level >= 1
    # the case is handed to another endoscopy unit, which accepts: a new obligation
    m = step(m, make_event("handoff_requested", SPEC, {"to_provider": "endo-2"}, late), PATHWAY)
    m = step(m, make_event("handoff_accepted", "specialist_owner:dr_t", {}, late + timedelta(hours=1)), PATHWAY)
    ms = colonoscopy(m)
    assert not ms.overdue and ms.escalation_level == 0 and not ms.escalation_accepted and ms.breach_due_at is None
    assert ms.started_at == late + timedelta(hours=1)
    assert m.referral_accepted_at == ms.started_at
    assert milestone_due_at(m, "confirmatory_completed", PATHWAY, UAE) == UAE.add_working_days(late + timedelta(hours=1), 31)


def test_t_a35_a_reroute_acceptance_from_referral_rejected_leaves_the_next_state_on_state_entry():
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}))
    m = step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(5)), PATHWAY)
    m = step(m, make_event("handoff_rejected", SPEC, {"reason": "capacity"}, at(6)), PATHWAY)
    assert m.state == "referral_rejected"
    m = step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-2"}, at(7)), PATHWAY)
    m = step(m, make_event("handoff_accepted", SPEC, {}, at(8)), PATHWAY)
    assert m.state == "owner_accepted" and m.clock_started_at == at(8)  # accept_ownership: state_entry


def test_t_a35_the_loader_still_supports_a_per_sla_clock_start_and_refuses_bad_ones(tmp_path):
    """No shipped SLA uses `sla_clock_start` since the colonoscopy clock became a milestone (A18), but the loader
    keeps the A13 mechanism, validated."""
    raw = Path(app_settings.pathway_path).read_text()
    ok = tmp_path / "ok.yaml"
    ok.write_text(raw + "sla_clock_start:\n  book_confirmatory: handoff_accepted\n")
    assert load_pathway(ok).clock_start_for("book_confirmatory") == "handoff_accepted"
    bad_value = tmp_path / "bad_value.yaml"
    bad_value.write_text(raw + "sla_clock_start:\n  book_confirmatory: whenever\n")
    with pytest.raises(PathwayError, match="sla_clock_start"):
        load_pathway(bad_value)
    no_sla = tmp_path / "no_sla.yaml"
    no_sla.write_text(raw + "sla_clock_start:\n  complete_confirmatory: handoff_accepted\n")  # a milestone, not an SLA
    with pytest.raises(PathwayError, match="no SLA"):
        load_pathway(no_sla)
