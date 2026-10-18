"""How the simulator's actor names meet the state machine's actor table.

The state machine is default-deny about who may write what (`app.state_machine.ACTOR_EVENTS`): an actor must be a person in
one of the six user roles, or a named service. The simulator records its events under its own, more descriptive actor names
(`staff:outreach-2`, `owner:pcp-3`, `clinician:cl-1`, `endoscopy:unit`), and those names are part of the event-log hash, so
they do not change. What changes is only the actor handed to the state machine when it is asked whether an event is allowed:
`machine_actor` renames the role part to the user role that person would have in the application. A name the table does not
know is passed through unchanged, so the machine refuses it (an engine bug shows up as an InvalidTransition).
"""

from __future__ import annotations

import dataclasses

# simulator actor prefix -> the user role (app.auth.USER_ROLES) of the person it stands for
SIM_ROLE = {
    "lab": "lab",
    "staff": "coordinator",
    "clinician": "primary_care_owner",
    "owner": "primary_care_owner",
    "patient": "patient",
    "endoscopy": "specialist_owner",
}


def machine_actor(actor: str) -> str:
    prefix, sep, name = actor.partition(":")
    role = SIM_ROLE.get(prefix)
    return f"{role}:{name}" if role and sep and name else actor


def as_machine_event(event):
    """The same event with the actor the state machine's table knows; everything else, including the logged actor of the
    original event, is untouched."""
    mapped = machine_actor(event.actor)
    return event if mapped == event.actor else dataclasses.replace(event, actor=mapped)
