"""E20a F12: `reassign` names an owner whose role may own the state the case lands in.

The payload schema (`payloads.OwnerActor`) only says the target is staff-shaped. The two reassign rows of the pathway hand the
case to a physician: `referral_rejected -> owner_accepted` (the owner books the colonoscopy and later reviews and closes) and
`declined_pending_signoff` (the owner signs the declined closure). A lab, coordinator or supervisor owner could never close
it, so the machine refuses that target from any caller."""

import pytest

from app.state_machine import PHYSICIAN_ROLES, InvalidTransition, step
from test_state_machine import OWNER, PATHWAY, TO_CONTACTED, drive, make_event

REJECTED = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}),
                 ("handoff_requested", OWNER, {"to_provider": "endo-1"}),
                 ("handoff_rejected", "specialist_owner:s", {"reason": "capacity"}))
REFUSED = drive(*TO_CONTACTED[:2], ("record_informed_refusal", OWNER, {
    "counselling_ref": "DocumentReference/c1", "counselled_by": OWNER, "reason": "personal_choice"}))


@pytest.mark.parametrize("model", [REJECTED, REFUSED], ids=["referral_rejected", "declined_pending_signoff"])
@pytest.mark.parametrize("target", ["lab:l1", "coordinator:c1", "supervisor:s1"])
def test_a_reassign_to_a_role_that_cannot_own_the_state_is_refused(model, target):
    with pytest.raises(InvalidTransition, match="reassign"):
        step(model, make_event("reassign", "coordinator:c9", {"owner": target}), PATHWAY)


@pytest.mark.parametrize("model", [REJECTED, REFUSED], ids=["referral_rejected", "declined_pending_signoff"])
@pytest.mark.parametrize("role", PHYSICIAN_ROLES)
def test_a_reassign_to_a_physician_is_accepted(model, role):
    out = step(model, make_event("reassign", "coordinator:c9", {"owner": f"{role}:dr2"}), PATHWAY)
    assert out.owner == f"{role}:dr2"


def test_the_states_are_the_ones_the_pathway_reassigns_from():
    assert REJECTED.state == "referral_rejected" and REFUSED.state == "declined_pending_signoff"
    assert {t.from_state for t in PATHWAY.transitions if t.event == "reassign"} == {"referral_rejected", "declined_pending_signoff"}
