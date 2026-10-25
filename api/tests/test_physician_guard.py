"""E20a F1: the physician rule is in the state machine, not only in app.policy.

`close` (either outcome) and `clinician_review` must be written by a physician role (`PHYSICIAN_ROLES`: primary care owner or
specialist owner). The HTTP route refuses anyone else first (EVENT_POLICY), but an in-process caller (automation code, the HL7
adapter, a tool, a future route) reaches `step` directly, so the machine refuses it too."""

import pytest

from app.state_machine import PHYSICIAN_ROLES, InvalidTransition, step
from test_state_machine import FULL_RUN, PATHWAY, drive, make_event

REF = "DiagnosticReport/1"
NON_PHYSICIANS = ("patient:p1", "lab:l1", "coordinator:c1", "supervisor:s1")
REVIEWED = drive(*FULL_RUN[:-1])
COMPLETED = drive(*FULL_RUN[:-2])


@pytest.mark.parametrize("actor", NON_PHYSICIANS)
def test_a_non_physician_cannot_close_even_signed_as_themselves(actor):
    assert REVIEWED.state == "clinician_reviewed"
    with pytest.raises(InvalidTransition, match="physician"):
        step(REVIEWED, make_event("close", actor, {"evidence_ref": REF, "signed_by": actor}), PATHWAY)


@pytest.mark.parametrize("actor", NON_PHYSICIANS)
@pytest.mark.parametrize("signed", [False, True])
def test_a_non_physician_cannot_write_a_clinician_review(actor, signed):
    assert COMPLETED.state == "confirmatory_completed"
    payload = {"signed_by": actor} if signed else {}
    with pytest.raises(InvalidTransition, match="physician"):
        step(COMPLETED, make_event("clinician_review", actor, payload), PATHWAY)


@pytest.mark.parametrize("role", PHYSICIAN_ROLES)
def test_a_physician_reviews_and_closes(role):
    actor = f"{role}:dr1"
    reviewed = step(COMPLETED, make_event("clinician_review", actor, {"signed_by": actor}), PATHWAY)
    assert reviewed.state == "clinician_reviewed"
    assert step(reviewed, make_event("close", actor, {"evidence_ref": REF, "signed_by": actor}), PATHWAY).state == "closed"


def test_the_simulator_closers_map_to_physicians():
    from sim.actors import machine_actor  # simulator/ is on PYTHONPATH in `make test`

    for logged in ("clinician:cl-1", "owner:pcp-3", "endoscopy:unit-0"):
        assert machine_actor(logged).split(":", 1)[0] in PHYSICIAN_ROLES
