"""Pathway config loader. SLAs, transitions, annotations and the escalation ladder live in YAML.

Transition rows may use shorthand that the loader expands into one `Transition` per source
state, so the rest of the code only ever sees concrete rows:

* `from: [a, b]`          several source states
* `from: any_open`        every open state (main and side) except the target state
* `to: resume_state`      return to the state remembered when the case entered a side state
* `when: {key: value}`    the row applies only when the event payload has these values
* `min: {key: n}`         the payload value must be a number >= n
* `requires: [key, ...]`  the payload value must be non-empty

Milestones (A18) are clocks that span states, from one event to another, against a national standard.

The loader refuses configs that could weaken the closure guard (see `_validate`).
"""

import re
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
# The genesis event: a milestone may start there (the result release, as far as the case is concerned).
CASE_CREATED = "case_created"


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
class Milestone:
    """A clock that spans states and is measured against a national standard (A18, ADR-028): `working_days`
    from `from_event` to `until_event`. Met by the `until_event`; breached when it has not happened in time."""

    name: str
    from_event: str
    until_event: str
    working_days: int
    source: str


# Closure-evidence rules of a pathway (pack section `evidence`). The defaults are the bowel pathway's (A24, ADR-036), so a
# pack that says nothing behaves as it always did.
DEFAULT_PROCEDURE = "colonoscopy"
DEFAULT_CLOSABLE_COMPLETENESS = ("complete", "ct_colonography")
COMPLETENESS_VOCABULARY = ("complete", "incomplete", "ct_colonography")


@dataclass(frozen=True)
class EvidenceRule:
    """What may close a case of this pathway (pack section `evidence`).

    * `procedure`: the confirmatory procedure's plain-text label (`colonoscopy`, `colposcopy`); it words the refusal reasons
      and is the text a report must carry when `require_named_procedure` is true.
    * `closable_completeness`: the completeness values (the vocabulary of `app.evidence`) that may close a case.
    * `require_named_procedure`: a report whose text does not name `procedure` is refused, so another pathway's record of
      the same patient cannot close this case. False for the bowel pack (its rule predates the check and is unchanged).
    * `search_codes`: `[resource type, "system|code"]` pairs the evidence finder may search a patient's records by, besides
      the case identifier. Empty: the finder asks by case identifier only (no code is asserted without a source).
    """

    procedure: str = DEFAULT_PROCEDURE
    closable_completeness: tuple[str, ...] = DEFAULT_CLOSABLE_COMPLETENESS
    require_named_procedure: bool = False
    search_codes: tuple[tuple[str, str], ...] = (
        ("Procedure", "http://snomed.info/sct|73761001"), ("DiagnosticReport", "http://loinc.org|18746-8"))


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
    # SLA event -> clock start. Absent means CLOCK_STATE_ENTRY (A13). Optional: the shipped YAML has none since
    # the colonoscopy clock became a milestone (A18).
    sla_clock_start: dict[str, str] = field(default_factory=dict)
    # Milestone name -> definition, in YAML order (A18).
    milestones: dict[str, Milestone] = field(default_factory=dict)
    # Informed refusal (A33, ADR-042): the side state a recorded refusal waits in, its reason taxonomy and the re-offer
    # interval in calendar months. None / empty when the pathway has no refusal path.
    refusal_state: str | None = None
    refusal_reasons: tuple[str, ...] = ()
    re_offer_months: int = 12
    appointment_cancel_reasons: tuple[str, ...] = ()
    # Multi-pathway fields. Not in repr: the timing fingerprints hash `repr(pathway)`, and none of these is timing.
    evidence: EvidenceRule = field(default_factory=EvidenceRule, repr=False)
    # Patient messages: the pathway's `{next_step}` per language (the generic template files are shared; E14: an appointment,
    # never a finding). Empty: the app's DEFAULT_NEXT_STEP.
    next_steps: dict[str, str] = field(default_factory=dict, repr=False)
    # Shown by the web next to the case: {en, ar} short labels, and the screening the pathway belongs to.
    labels: dict[str, str] = field(default_factory=dict, repr=False)
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

    def next_step(self, language: str, default: dict[str, str]) -> str:
        """The `{next_step}` text for `language`: this pathway's own if the pack names one, else `default`'s."""
        return self.next_steps.get(language) or default[language]

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

    def milestone(self, name: str) -> Milestone | None:
        return self.milestones.get(name)

    def milestone_awaiting(self, state: str) -> tuple[Milestone, ...]:
        """Milestones whose `until_event` leaves `state` (the state that awaits them): the fallback start for a
        milestone whose `from_event` never happened is entry into one of these."""
        return tuple(m for m in self.milestones.values() if self.transitions_for(state, m.until_event))

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


MAX_EXTENDS_DEPTH = 3


def read_pack(path: str | Path, _depth: int = 0) -> dict:
    """The pack's YAML as a mapping, with `extends: <file>` resolved. A pack may name another pack in the same directory;
    every top-level key the pack itself sets REPLACES the parent's (no deep merge, so a section is read in one place), and
    every other key is the parent's. A pack that extends another reuses its states and transitions by not restating them:
    the closure guard, the side states and the annotation lists are then literally the parent's, and `_validate` checks the
    merged result like any other pack."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise PathwayError(f"{path.name}: a pathway pack must be a mapping")
    parent = raw.get("extends")
    if parent is None:
        return raw
    if _depth >= MAX_EXTENDS_DEPTH:
        raise PathwayError(f"{path.name}: `extends` nests deeper than {MAX_EXTENDS_DEPTH}")
    if not isinstance(parent, str) or "/" in parent or not parent.endswith(".yaml"):
        raise PathwayError(f"{path.name}: `extends` must name a .yaml file in the same directory")
    merged = read_pack(path.with_name(parent), _depth + 1)
    merged.pop("extends", None)
    merged.update({k: v for k, v in raw.items() if k != "extends"})
    merged["extended_from"] = parent
    return merged


def load_pathway(path: str | Path) -> Pathway:
    raw = read_pack(path)
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
            milestones=_load_milestones(raw.get("milestones")),
            refusal_state=(raw.get("informed_refusal") or {}).get("state"),
            refusal_reasons=tuple((raw.get("informed_refusal") or {}).get("reasons", [])),
            re_offer_months=(raw.get("informed_refusal") or {}).get("re_offer_months", 12),
            appointment_cancel_reasons=tuple(raw.get("appointment_cancel_reasons", [])),
            evidence=_load_evidence(raw.get("evidence")),
            next_steps=_load_str_map(raw.get("patient_messages"), "next_step", "patient_messages.next_step"),
            labels=_load_str_map(raw, "labels", "labels"),
            raw=raw,
        )
    except KeyError as exc:
        raise PathwayError(f"pathway config missing required key: {exc}") from exc
    _validate(pw)
    return pw


_LANGUAGES = ("en", "ar")


def _load_str_map(container: object, key: str, where: str) -> dict[str, str]:
    """`{en: ..., ar: ...}` under `container[key]`: non-empty strings, languages en/ar only. Missing: empty."""
    value = container.get(key) if isinstance(container, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict) or not set(value) <= set(_LANGUAGES):
        raise PathwayError(f"{where} must be a mapping with the languages {list(_LANGUAGES)}")
    for lang, text in value.items():
        if not isinstance(text, str) or not text.strip() or len(text) > 40:
            raise PathwayError(f"{where}.{lang} must be a non-empty string of at most 40 characters (the message field limit)")
    return {k: v.strip() for k, v in value.items()}


def _load_evidence(raw: object) -> EvidenceRule:
    if raw is None:
        return EvidenceRule()
    if not isinstance(raw, dict):
        raise PathwayError("evidence must be a mapping")
    unknown = set(raw) - {"procedure", "closable_completeness", "require_named_procedure", "search_codes"}
    if unknown:
        raise PathwayError(f"evidence has unknown keys {sorted(unknown)}")
    procedure = raw.get("procedure", DEFAULT_PROCEDURE)
    if not isinstance(procedure, str) or not re.fullmatch(r"[a-z][a-z ]{2,40}", procedure):
        raise PathwayError("evidence.procedure must be a lower-case plain-text label")
    closable = tuple(raw.get("closable_completeness", DEFAULT_CLOSABLE_COMPLETENESS))
    if not closable or not set(closable) <= set(COMPLETENESS_VOCABULARY) or "incomplete" in closable:
        raise PathwayError(f"evidence.closable_completeness must be a non-empty subset of {list(COMPLETENESS_VOCABULARY)} without 'incomplete'")
    named = raw.get("require_named_procedure", False)
    if not isinstance(named, bool):
        raise PathwayError("evidence.require_named_procedure must be true or false")
    codes_raw = raw.get("search_codes", None)
    if codes_raw is None:
        codes = EvidenceRule().search_codes
    else:
        if not isinstance(codes_raw, list):
            raise PathwayError("evidence.search_codes must be a list of [resource type, 'system|code']")
        codes = []
        for item in codes_raw:
            if (not isinstance(item, list) or len(item) != 2 or item[0] not in ("Procedure", "DiagnosticReport")
                    or not isinstance(item[1], str) or "|" not in item[1]):
                raise PathwayError("evidence.search_codes entries must be ['Procedure'|'DiagnosticReport', 'system|code']")
            codes.append((item[0], item[1]))
        codes = tuple(codes)
    return EvidenceRule(procedure, closable, named, codes)


def _load_milestones(raw: object) -> dict[str, Milestone]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PathwayError("milestones must be a mapping of name to definition")
    out: dict[str, Milestone] = {}
    for name, d in raw.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise PathwayError(f"milestone name {name!r} must be a lower-case code")
        if not isinstance(d, dict):
            raise PathwayError(f"milestone {name!r} must be a mapping")
        unknown = set(d) - {"from_event", "until_event", "working_days", "source"}
        if unknown:
            raise PathwayError(f"milestone {name!r} has unknown keys {sorted(unknown)}")
        missing = {"from_event", "until_event", "working_days", "source"} - set(d)
        if missing:
            raise PathwayError(f"milestone {name!r} is missing {sorted(missing)}")
        days = d["working_days"]
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise PathwayError(f"milestone {name!r}: working_days must be an integer >= 1")
        if not isinstance(d["source"], str) or not d["source"].strip():
            raise PathwayError(f"milestone {name!r}: source must say where the standard comes from")
        out[name] = Milestone(name, str(d["from_event"]), str(d["until_event"]), days, d["source"].strip())
    return out


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

    known_events = set(events) | set(pw.annotations)
    for m in pw.milestones.values():
        if m.from_event != CASE_CREATED and m.from_event not in known_events:
            raise PathwayError(f"milestone {m.name!r}: from_event {m.from_event!r} is not {CASE_CREATED!r} or a pathway event")
        if m.until_event not in known_events:
            raise PathwayError(f"milestone {m.name!r}: until_event {m.until_event!r} is not a pathway event")
        if m.until_event == m.from_event:
            raise PathwayError(f"milestone {m.name!r}: from_event and until_event must differ")

    # Closure guard at config level: only the main-path state before the terminal may close,
    # and the row must demand both evidence and a sign-off. No side state reaches `closed`.
    terminal = pw.terminal_state
    penultimate = pw.states[-2] if len(pw.states) > 1 else None
    for t in pw.transitions:
        if t.to_state == terminal:
            if t.from_state not in (penultimate, pw.refusal_state):
                raise PathwayError(
                    f"{t.event!r}: only {penultimate!r} (or the informed-refusal state, A33) may transition to "
                    f"{terminal!r}, not {t.from_state!r}"
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

    if pw.refusal_state is not None:
        if pw.refusal_state not in pw.side_states:
            raise PathwayError("informed_refusal.state must be one of the side_states")
        r = pw.refusal_reasons
        if not r or len(r) != len(set(r)) or not all(isinstance(x, str) and x for x in r):
            raise PathwayError("informed_refusal.reasons must be unique non-empty strings")
        if isinstance(pw.re_offer_months, bool) or not isinstance(pw.re_offer_months, int) or not 1 <= pw.re_offer_months <= 120:
            raise PathwayError("informed_refusal.re_offer_months must be an integer from 1 to 120")
        for t in pw.transitions:
            if t.from_state == pw.refusal_state and t.to_state == terminal and ("outcome", "declined") not in t.when:
                raise PathwayError("the informed-refusal state may close only with `when: {outcome: declined}`")
    cr = pw.appointment_cancel_reasons
    if len(cr) != len(set(cr)) or not all(isinstance(x, str) and x for x in cr):
        raise PathwayError("appointment_cancel_reasons must be unique non-empty strings")

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


class PathwayRegistry:
    """The pathways one engine runs, in load order; the first is the default (the one a case created without naming a
    pathway gets, and the one the aggregate routes that take a single pathway report). One engine, many packs: every
    pack goes through `load_pathway` and `_validate`, and every case is stepped by the pack named in its own row."""

    def __init__(self, pathways) -> None:
        pathways = tuple(pathways)
        if not pathways:
            raise PathwayError("a registry needs at least one pathway")
        names = [p.name for p in pathways]
        if len(set(names)) != len(names):
            raise PathwayError(f"pathway names must be unique, got {names}")
        if len({p.terminal_state for p in pathways}) != 1:
            # Cross-pathway reads ("every open case") filter on one terminal state; the closure guard is per pack.
            raise PathwayError("every pathway of one engine must end in the same terminal state")
        self._by_name = {p.name: p for p in pathways}
        self.default = pathways[0]

    def __iter__(self):
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name)

    def get(self, name: str | None) -> Pathway | None:
        """The pathway called `name` (the default for None), or None if no such pack is loaded."""
        return self.default if name is None else self._by_name.get(name)

    def require(self, name: str) -> Pathway:
        pw = self._by_name.get(name)
        if pw is None:
            raise PathwayError(f"pathway {name!r} is not loaded (loaded: {list(self._by_name)})")
        return pw


def load_registry(paths) -> PathwayRegistry:
    return PathwayRegistry([load_pathway(p) for p in paths])


def merge_counts(parts: list[dict], and_keys: tuple[str, ...] = ()) -> dict:
    """One answer from the per-pathway answers of a scan that ran once per pathway: whole numbers add, lists join, a
    boolean is true if any part says so (`and_keys`: only if all do), anything else keeps the first part's value. One part
    is returned as it is, so an engine with one pathway answers exactly as it always did."""
    if len(parts) == 1:
        return parts[0]
    out: dict = {}
    for part in parts:
        for key, value in part.items():
            if key not in out:
                out[key] = list(value) if isinstance(value, list) else value
            elif isinstance(value, bool):
                out[key] = (out[key] and value) if key in and_keys else (out[key] or value)
            elif isinstance(value, int) and isinstance(out[key], int):
                out[key] += value
            elif isinstance(value, float) and isinstance(out[key], (int, float)):
                out[key] += value
            elif isinstance(value, list) and isinstance(out[key], list):
                out[key] += value
    return out
