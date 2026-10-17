"""Pure state-machine functions. No I/O, no clock, no randomness: same input, same output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.pathway import Pathway

if TYPE_CHECKING:  # avoid a circular import; events.py imports apply()
    from app.events import Event

# Actor prefixes that denote automation (scheduler, watchdog, drafting model...).
AUTOMATED_ACTOR_PREFIXES = ("system:", "scheduler", "watchdog", "model:")


class InvalidTransition(Exception):
    pass


def _is_automated(actor: str) -> bool:
    return actor.startswith(AUTOMATED_ACTOR_PREFIXES)


def apply(state: str, event: Event, pathway: Pathway) -> str:
    """Return the new state, or raise InvalidTransition. Never mutates anything."""
    transition = pathway.find_transition(state, event.type)
    if transition is None:
        raise InvalidTransition(f"event {event.type!r} not allowed from state {state!r}")

    # Closure guard: evidence AND human sign-off, never an automated actor.
    if transition.to_state == pathway.terminal_state:
        if _is_automated(event.actor):
            raise InvalidTransition("automated actors may not close a case")
    for key in transition.requires:
        if not event.payload.get(key):
            raise InvalidTransition(f"event {event.type!r} requires non-empty {key!r}")

    return transition.to_state
