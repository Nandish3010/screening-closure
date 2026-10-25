"""A36 (ADR-044): a referral is held while consent is withdrawn or the patient does not allow sharing with the specialist.

No referral is requested or accepted while it is held; a request already pending is kept (not cancelled), the scheduler's
bounce timer does not run, and the request can be accepted again once a person records consent, with a fresh response
window from that moment. In the state machine, so every caller is held to it; the sweep and the automation follow."""

from uuid import uuid4

import pytest

from app import automation
from app.state_machine import InvalidTransition, referral_hold, step
from app.sweep import SCHEDULER_ACTOR, handoff_due_at, next_due_at, plan_bounce
from test_handoff import PATHWAY, UAE, at, requested
from test_state_machine import OWNER, TO_CONTACTED, drive, make_event

PATIENT = "patient:p"
SPEC = "specialist_owner:dr_s"
WITHDRAW = {"withdrawn": True}
RESTORE = {"withdrawn": False}
NO_SHARE = {"share_with_specialist": False}
SHARE = {"share_with_specialist": True}
BOUNCE = {"reason": "timeout", "bounced": True}


def consent(model, payload, day):
    return step(model, make_event("consent_updated", PATIENT, payload, at(day)), PATHWAY)


@pytest.mark.parametrize("hold, payload", [("consent_withdrawn", WITHDRAW), ("share_with_specialist_refused", NO_SHARE)])
def test_a_pending_request_is_held_not_cancelled(hold, payload):
    m = requested(on_day=0)
    held = consent(m, payload, 1)
    assert referral_hold(held) == hold
    assert held.pending_handoff == m.pending_handoff  # kept as it was
    assert held.to_json()["referral_held"] == hold
    with pytest.raises(InvalidTransition, match=r"referral held"):
        step(held, make_event("handoff_accepted", SPEC, {}, at(2)), PATHWAY)
    with pytest.raises(InvalidTransition, match=r"bounce timer does not run"):
        step(held, make_event("handoff_rejected", SCHEDULER_ACTOR, BOUNCE, at(20)), PATHWAY)


@pytest.mark.parametrize("payload", [WITHDRAW, NO_SHARE])
def test_the_bounce_timer_does_not_run_while_held(payload):
    held = consent(requested(on_day=0), payload, 1)
    assert handoff_due_at(held, PATHWAY, UAE) is None
    assert plan_bounce(uuid4(), held, PATHWAY, UAE, at(60)) is None


@pytest.mark.parametrize("hold, release", [(WITHDRAW, RESTORE), (NO_SHARE, SHARE)])
def test_restored_consent_makes_the_request_acceptable_with_a_fresh_window(hold, release):
    m = requested(on_day=0)  # Monday: would bounce after Thursday 08:00
    held = consent(m, hold, 1)
    back = consent(held, release, 7)  # Monday a week later
    assert referral_hold(back) is None
    assert back.pending_handoff["requested_at"] == m.pending_handoff["requested_at"]
    assert back.pending_handoff["resumed_at"] == at(7).isoformat()
    # the response window starts again, in full, from the restoration: three working days, not already expired
    assert handoff_due_at(back, PATHWAY, UAE) == at(10)
    assert plan_bounce(uuid4(), back, PATHWAY, UAE, at(9)) is None
    assert plan_bounce(uuid4(), back, PATHWAY, UAE, at(10, 1)) is not None
    acc = step(back, make_event("handoff_accepted", SPEC, {}, at(8)), PATHWAY)
    assert acc.owner == SPEC and acc.pending_handoff is None


def test_no_new_request_while_sharing_is_refused():
    m = consent(drive(*TO_CONTACTED, ("patient_acknowledge", PATIENT, {})), NO_SHARE, 1)
    with pytest.raises(InvalidTransition, match=r"referral held \(share_with_specialist_refused\)"):
        step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(2)), PATHWAY)
    ok = consent(m, SHARE, 3)
    assert step(ok, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(4)), PATHWAY).pending_handoff


def test_a_withdrawn_case_refuses_a_new_request_with_the_freeze_reason():
    m = consent(drive(*TO_CONTACTED), WITHDRAW, 1)
    with pytest.raises(InvalidTransition, match="consent withdrawn"):
        step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}, at(2)), PATHWAY)


def test_the_receiver_may_still_decline_a_held_request():
    """A rejection shares nothing and contacts no one: the receiver can still say no while the request waits."""
    held = consent(requested(), NO_SHARE, 1)
    rej = step(held, make_event("handoff_rejected", SPEC, {"reason": "capacity"}, at(2)), PATHWAY)
    assert rej.pending_handoff is None and rej.state == "referral_rejected"


def test_an_unrelated_consent_change_does_not_restart_the_timer():
    m = requested(on_day=0)
    sms = consent(m, {"channel_sms": False}, 1)
    assert "resumed_at" not in sms.pending_handoff and handoff_due_at(sms, PATHWAY, UAE) == at(3)


def test_next_due_has_no_bounce_trigger_while_held():
    held = consent(requested(on_day=0), NO_SHARE, 1)
    due = next_due_at(held, PATHWAY, UAE)
    assert due is None or due != handoff_due_at(requested(on_day=0), PATHWAY, UAE)


def test_the_automation_does_not_reroute_a_held_case():
    rej = step(requested(), make_event("handoff_rejected", SPEC, {"reason": "capacity"}, at(1)), PATHWAY)
    held = consent(rej, NO_SHARE, 2)

    class NoStore:
        def events_for(self, case_id):  # pragma: no cover - must not be reached
            raise AssertionError("the reroute looked further than the hold")

    result = automation.ScanResult()
    automation._reroute(NoStore(), PATHWAY, uuid4(), held, at(3), result)
    assert result.counts == {"reroute_held": 1}
