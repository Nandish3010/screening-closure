"""A36 (ADR-044, superseding the signer part of ADR-021): the signer is the closer, in the state machine.

`close` (both outcomes) and `clinician_review.signed_by` (when given) must name the person writing the event: their actor
string or their `Practitioner/<user_id>`. The route keeps its own check as a second layer (403); the machine refuses the
same thing from any caller, so a store or in-process caller that skips the route cannot sign in someone else's name."""

import pytest

from app.state_machine import InvalidTransition, signer_resolves_to, step
from test_state_machine import FULL_RUN, OWNER, PATHWAY, drive, make_event

REF = "DiagnosticReport/1"
REVIEWED = drive(*FULL_RUN[:-1])


@pytest.mark.parametrize("signed_by", [OWNER, f"Practitioner/{OWNER.split(':', 1)[1]}",
                                       # the same user id under another role name (how the simulator logs a person)
                                       f"clinician:{OWNER.split(':', 1)[1]}"])
def test_the_closer_signs_as_themselves(signed_by):
    assert REVIEWED.state == "clinician_reviewed"
    closed = step(REVIEWED, make_event("close", OWNER, {"evidence_ref": REF, "signed_by": signed_by}), PATHWAY)
    assert closed.state == "closed"


@pytest.mark.parametrize("signed_by", ["Practitioner/9", "primary_care_owner:someone_else", "dr_k", "Practitioner/",
                                       "primary_care_owner:", 7, ["Practitioner/dr_k"]])
def test_a_close_in_someone_elses_name_is_refused_by_the_machine(signed_by):
    with pytest.raises(InvalidTransition, match="signed_by must be the signer's own identity"):
        step(REVIEWED, make_event("close", OWNER, {"evidence_ref": REF, "signed_by": signed_by}), PATHWAY)


def test_a_signed_clinician_review_must_be_the_reviewers_own():
    before = drive(*FULL_RUN[:-2])
    assert before.state == "confirmatory_completed"
    assert step(before, make_event("clinician_review", OWNER, {}), PATHWAY).state == "clinician_reviewed"  # unsigned: fine
    assert step(before, make_event("clinician_review", OWNER, {"signed_by": OWNER}), PATHWAY).state == "clinician_reviewed"
    for other in ("Practitioner/9", ""):
        with pytest.raises(InvalidTransition, match="signed_by must be"):
            step(before, make_event("clinician_review", OWNER, {"signed_by": other}), PATHWAY)


def test_signer_resolution():
    assert signer_resolves_to("specialist_owner:u1", "Practitioner/u1")
    assert signer_resolves_to("specialist_owner:u1", "specialist_owner:u1")
    assert not signer_resolves_to("specialist_owner:u1", "Practitioner/u2")
    assert not signer_resolves_to("specialist_owner:u1", "u1")  # neither form
    assert not signer_resolves_to("nobody", "Practitioner/nobody")  # not an actor string
    assert not signer_resolves_to("specialist_owner:u1", None)
