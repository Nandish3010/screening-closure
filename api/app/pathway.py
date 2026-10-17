"""Pathway config loader. SLAs, transitions and the escalation ladder live in YAML, not code."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class PathwayError(ValueError):
    pass


@dataclass(frozen=True)
class Transition:
    from_state: str
    event: str
    to_state: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class EscalationRung:
    after_breach_days: int
    notify: str
    action: str


@dataclass(frozen=True)
class Pathway:
    name: str
    version: str
    description: str
    states: tuple[str, ...]
    side_states: tuple[str, ...]
    transitions: tuple[Transition, ...]
    slas_working_days: dict[str, int] = field(default_factory=dict)
    escalation_ladder: tuple[EscalationRung, ...] = ()
    closure_rule: str = ""
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def initial_state(self) -> str:
        return self.states[0]

    @property
    def terminal_state(self) -> str:
        return self.states[-1]

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(t.event for t in self.transitions))

    def find_transition(self, state: str, event: str) -> Transition | None:
        for t in self.transitions:
            if t.from_state == state and t.event == event:
                return t
        return None


def load_pathway(path: str | Path) -> Pathway:
    raw = yaml.safe_load(Path(path).read_text())
    try:
        pw = Pathway(
            name=raw["pathway"],
            version=str(raw["version"]),
            description=raw.get("description", ""),
            states=tuple(raw["states"]),
            side_states=tuple(raw.get("side_states", [])),
            transitions=tuple(
                Transition(
                    from_state=t["from"],
                    event=t["event"],
                    to_state=t["to"],
                    requires=tuple(t.get("requires", [])),
                )
                for t in raw["transitions"]
            ),
            slas_working_days=dict(raw.get("slas_working_days", {})),
            escalation_ladder=tuple(
                EscalationRung(r["after_breach_days"], r["notify"], r["action"])
                for r in raw.get("escalation_ladder", [])
            ),
            closure_rule=raw.get("closure_rule", ""),
            raw=raw,
        )
    except KeyError as exc:
        raise PathwayError(f"pathway config missing required key: {exc}") from exc
    _validate(pw)
    return pw


def _validate(pw: Pathway) -> None:
    known = set(pw.states) | set(pw.side_states)
    if not pw.states:
        raise PathwayError("pathway defines no states")
    for t in pw.transitions:
        for s in (t.from_state, t.to_state):
            if s not in known:
                raise PathwayError(f"transition {t.event!r} references unknown state {s!r}")
    events = {t.event for t in pw.transitions}
    for ev in pw.slas_working_days:
        if ev not in events:
            raise PathwayError(f"SLA references unknown event {ev!r}")
