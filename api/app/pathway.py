"""Pathway config loader. SLAs, transitions, annotations and the escalation ladder live in YAML.

Transition rows may use shorthand that the loader expands into one `Transition` per source
state, so the rest of the code only ever sees concrete rows:

* `from: [a, b]`          several source states
* `from: any_open`        every open state (main and side) except the target state
* `to: resume_state`      return to the state remembered when the case entered a side state
* `when: {key: value}`    the row applies only when the event payload has these values
* `min: {key: n}`         the payload value must be a number >= n
* `requires: [key, ...]`  the payload value must be non-empty

The loader refuses configs that could weaken the closure guard (see `_validate`).
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ANY_OPEN = "any_open"
RESUME_STATE = "resume_state"
# Where an SLA clock may start (A13): the event that entered the state, or the latest referral
# acceptance (`handoff_accepted`) with a fallback to state entry.
CLOCK_STATE_ENTRY = "state_entry"
CLOCK_HANDOFF_ACCEPTED = "handoff_accepted"
CLOCK_STARTS = (CLOCK_STATE_ENTRY, CLOCK_HANDOFF_ACCEPTED)


class PathwayError(ValueError):
    pass


@dataclass(frozen=True)
class Transition:
    from_state: str
    event: str
    to_state: str
    requires: tuple[str, ...] = ()
    when: tuple[tuple[str, object], ...] = ()
    minimums: tuple[tuple[str, float], ...] = ()

    def matches(self, payload: dict) -> bool:
        # Strict on type so that `1` or "true" never satisfies `withdrawn: true`.
        return all(
            k in payload and type(payload[k]) is type(v) and payload[k] == v for k, v in self.when
        )


@dataclass(frozen=True)
class EscalationRung:
    rung: str
    after_breach_days: int
    notify: str
    target: str
    action: str
    accepted_by: tuple[str, ...]


@dataclass(frozen=True)
class Pathway:
    name: str
    version: str
    description: str
    states: tuple[str, ...]
    side_states: tuple[str, ...]
    transitions: tuple[Transition, ...]
    annotations: tuple[str, ...] = ()
    human_only_events: tuple[str, ...] = ()
    slas_working_days: dict[str, int] = field(default_factory=dict)
    escalation_ladder: tuple[EscalationRung, ...] = ()
    closure_rule: str = ""
    handoff_response_working_days: int = 3
    handoff_reject_reasons: tuple[str, ...] = ()
    handoff_bounce_reason: str = "timeout"
    # SLA event -> clock start. Absent means CLOCK_STATE_ENTRY (A13).
    sla_clock_start: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def initial_state(self) -> str:
        return self.states[0]

    @property
    def terminal_state(self) -> str:
        return self.states[-1]

    @property
    def open_states(self) -> tuple[str, ...]:
        """Every non-terminal state, main and side."""
        return (*self.states[:-1], *self.side_states)

    @property
    def event_types(self) -> tuple[str, ...]:
        """Every event type the pathway understands (transitions and annotations)."""
        return tuple(dict.fromkeys([*(t.event for t in self.transitions), *self.annotations]))

    @property
    def main_transitions(self) -> tuple[Transition, ...]:
        """The forward chain states[0] -> states[1] -> ... -> terminal, in order."""
        chain = []
        for a, b in zip(self.states, self.states[1:]):
            row = next((t for t in self.transitions if t.from_state == a and t.to_state == b), None)
            if row is not None:
                chain.append(row)
        return tuple(chain)

    @property
    def resumable_states(self) -> frozenset[str]:
        """Side states that remember the state they interrupted and resume to it."""
        return frozenset(t.from_state for t in self.transitions if t.to_state == RESUME_STATE)

    def transitions_for(self, state: str, event: str) -> list[Transition]:
        return [t for t in self.transitions if t.from_state == state and t.event == event]

    def find_transition(self, state: str, event: str, payload: dict | None = None):
        for t in self.transitions_for(state, event):
            if t.matches(payload or {}):
                return t
        return None

    def sla_for_state(self, state: str) -> tuple[str, int] | None:
        """(awaited event, working days) for a state, or None if the state has no SLA.

        The awaited event is the state's outgoing transition event that has an entry in
        `slas_working_days`; states without one (referral_rejected, consent_withdrawn,
        closed) carry no scheduler SLA.
        """
        for t in self.transitions:
            if t.from_state == state and t.event in self.slas_working_days:
                return t.event, self.slas_working_days[t.event]
        return None

    def clock_start_for(self, sla_event: str) -> str:
        """Where the SLA for `sla_event` starts counting: `state_entry` (default) or
        `handoff_accepted` (the latest referral acceptance, else state entry)."""
        return self.sla_clock_start.get(sla_event, CLOCK_STATE_ENTRY)

    def state_clock_start(self, state: str) -> str:
        """Clock start of the SLA that applies to `state` (`state_entry` if it has none)."""
        sla = self.sla_for_state(state)
        return self.clock_start_for(sla[0]) if sla else CLOCK_STATE_ENTRY

    def rung(self, name: str) -> EscalationRung | None:
        return next((r for r in self.escalation_ladder if r.rung == name), None)

    def rung_level(self, name: str) -> int:
        """1-based position of a rung in the ladder (0 if unknown)."""
        for i, r in enumerate(self.escalation_ladder, start=1):
            if r.rung == name:
                return i
        return 0

    def rung_at(self, level: int) -> EscalationRung | None:
        return self.escalation_ladder[level - 1] if 1 <= level <= len(self.escalation_ladder) else None


def _expand_sources(row: dict, states, side_states) -> list[str]:
    src = row["from"]
    if src == ANY_OPEN:
        open_states = [*states[:-1], *side_states]
        return [s for s in open_states if s != row["to"]]
    return list(src) if isinstance(src, list) else [src]


def load_pathway(path: str | Path) -> Pathway:
    raw = yaml.safe_load(Path(path).read_text())
    try:
        states = tuple(raw["states"])
        side_states = tuple(raw.get("side_states", []))
        transitions = []
        for t in raw["transitions"]:
            for source in _expand_sources(t, states, side_states):
                transitions.append(
                    Transition(
                        from_state=source,
                        event=t["event"],
                        to_state=t["to"],
                        requires=tuple(t.get("requires", [])),
                        when=tuple(sorted((t.get("when") or {}).items())),
                        minimums=tuple(sorted((t.get("min") or {}).items())),
                    )
                )
        pw = Pathway(
            name=raw["pathway"],
            version=str(raw["version"]),
            description=raw.get("description", ""),
            states=states,
            side_states=side_states,
            transitions=tuple(transitions),
            annotations=tuple(raw.get("annotations", [])),
            human_only_events=tuple(raw.get("human_only_events", [])),
            slas_working_days=dict(raw.get("slas_working_days", {})),
            escalation_ladder=tuple(
                EscalationRung(
                    rung=r["rung"],
                    after_breach_days=r["after_breach_days"],
                    notify=r["notify"],
                    target=r["target"],
                    action=r["action"],
                    accepted_by=tuple(r["accepted_by"]),
                )
                for r in raw.get("escalation_ladder", [])
            ),
            closure_rule=raw.get("closure_rule", ""),
            handoff_response_working_days=raw.get("handoff_response_working_days", 3),
            handoff_reject_reasons=tuple(raw.get("handoff_reject_reasons", [])),
            handoff_bounce_reason=raw.get("handoff_bounce_reason", "timeout"),
            sla_clock_start=dict(raw.get("sla_clock_start") or {}),
            raw=raw,
        )
    except KeyError as exc:
        raise PathwayError(f"pathway config missing required key: {exc}") from exc
    _validate(pw)
    return pw


def _validate(pw: Pathway) -> None:
    if not pw.states:
        raise PathwayError("pathway defines no states")
    known = set(pw.states) | set(pw.side_states)
    if len(known) != len(pw.states) + len(pw.side_states):
        raise PathwayError("state names must be unique across states and side_states")
    for t in pw.transitions:
        if t.from_state not in known:
            raise PathwayError(f"transition {t.event!r} references unknown state {t.from_state!r}")
        if t.to_state != RESUME_STATE and t.to_state not in known:
            raise PathwayError(f"transition {t.event!r} references unknown state {t.to_state!r}")
        if t.from_state == pw.terminal_state and t.to_state == pw.terminal_state:
            raise PathwayError(f"transition {t.event!r} loops on the terminal state")
        if t.to_state == RESUME_STATE and t.from_state not in pw.side_states:
            raise PathwayError(f"{t.event!r}: `to: resume_state` is only valid from a side state")

    events = {t.event for t in pw.transitions}
    for ev in pw.slas_working_days:
        if ev not in events:
            raise PathwayError(f"SLA references unknown event {ev!r}")

    for ev, start in pw.sla_clock_start.items():
        if ev not in pw.slas_working_days:
            raise PathwayError(f"sla_clock_start references an event with no SLA: {ev!r}")
        if start not in CLOCK_STARTS:
            raise PathwayError(f"sla_clock_start for {ev!r} must be one of {list(CLOCK_STARTS)}, got {start!r}")

    # Closure guard at config level: only the main-path state before the terminal may close,
    # and the row must demand both evidence and a sign-off. No side state reaches `closed`.
    terminal = pw.terminal_state
    penultimate = pw.states[-2] if len(pw.states) > 1 else None
    for t in pw.transitions:
        if t.to_state == terminal:
            if t.from_state != penultimate:
                raise PathwayError(
                    f"{t.event!r}: only {penultimate!r} may transition to {terminal!r}, "
                    f"not {t.from_state!r}"
                )
            if not {"evidence_ref", "signed_by"} <= set(t.requires):
                raise PathwayError(
                    f"{t.event!r}: a transition to {terminal!r} must require "
                    "evidence_ref and signed_by"
                )

    closing_events = {t.event for t in pw.transitions if t.to_state == terminal}
    main_events = {t.event for t in pw.main_transitions} if len(pw.states) > 1 else set()
    seen: set[str] = set()
    for ev in pw.annotations:
        if ev in seen:
            raise PathwayError(f"annotation {ev!r} listed twice")
        seen.add(ev)
        if ev in closing_events:
            raise PathwayError(f"annotation {ev!r} may not be a closing event")
        if ev in main_events:
            raise PathwayError(f"annotation {ev!r} is also a main-path transition event")
    for ev in pw.human_only_events:
        if ev not in events and ev not in seen:
            raise PathwayError(f"human_only_events references unknown event {ev!r}")

    # Every side state must be enterable and leavable, so none can trap a case.
    for side in pw.side_states:
        if not any(t.to_state == side for t in pw.transitions):
            raise PathwayError(f"side state {side!r} cannot be entered")
        if not any(t.from_state == side for t in pw.transitions):
            raise PathwayError(f"side state {side!r} cannot be left")

    rungs = [r.rung for r in pw.escalation_ladder]
    if len(rungs) != len(set(rungs)):
        raise PathwayError("escalation ladder rung names must be unique")
    offsets = [r.after_breach_days for r in pw.escalation_ladder]
    if any(not isinstance(o, int) or o < 0 for o in offsets) or offsets != sorted(offsets):
        raise PathwayError("escalation ladder offsets must be non-negative and non-decreasing")
    for r in pw.escalation_ladder:
        if not r.accepted_by:
            raise PathwayError(f"rung {r.rung!r} needs at least one accepted_by role")

    days = pw.handoff_response_working_days
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise PathwayError("handoff_response_working_days must be an integer >= 1")
    reasons = pw.handoff_reject_reasons
    if len(reasons) != len(set(reasons)) or not all(isinstance(r, str) and r for r in reasons):
        raise PathwayError("handoff_reject_reasons must be unique non-empty strings")
    if pw.handoff_bounce_reason in reasons:
        raise PathwayError(
            "handoff_bounce_reason is reserved for the scheduler and may not be a human reason"
        )
