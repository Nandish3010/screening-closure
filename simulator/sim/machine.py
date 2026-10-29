"""A simulated case as the API sees it: the real read model, the real sweep (M7-1, engineering review F6).

The simulator used to have its own SLA timers, escalation ladder, ladder coalescing and referral bounce. They are gone.
For every case the orchestrated process reaches, the engine keeps a `MachineCase`:

* `model`: the API's own read model (`app.state_machine.CaseModel`), folded with the API's own `step` from the machine
  form of every simulated event that the API's sweep reads (pathway steps, referral requests and answers, consent
  withdrawal and its review, a cancelled appointment, a reopen, an informed refusal). `log` is that machine log, in
  order, ready to be appended to a real event store (the conformance test does exactly that).
* the sweep: the engine asks `app.sweep.next_due_at` when the sweep could next have something to do for the case,
  checks the case at that instant (after every other event of the same instant) and appends what `app.sweep.plan_case`
  plans: breaches, escalation rungs with the API's coalescing (A19), and the bounce of an unanswered referral. Nothing
  about timing, rung order or coalescing is computed by the simulator.

What the simulator still adds is behaviour: who answers, how fast, what a rung prompts a person to do, and one fault the
API cannot have by construction, a scheduler that silently misses an obligation (`orchestration.timer_miss_prob`): such an
obligation (a state's SLA clock, or a milestone) is masked out of the model the planner sees, so nothing is ever planned
for it (`view`).

Consent (A6, A36): a withdrawal moves the machine to `consent_withdrawn`, where the API refuses every pathway step and the
sweep plans nothing. The simulated people keep acting (the colonoscopy can still happen); their events wait in `queue`
and are folded in, in order and with their own times, when a person's review restores consent. A case whose consent is
never restored stays frozen in the machine, as it would in the API.

Not machine events: messages, deliveries and recoveries, watchdog flags, lost contact, duplicate results, a refused
second ownership claim, and a change of primary-care owner (`provider_switched` and its transfer handoff): none of them
is read by the sweep. A transfer is an ownership change in the simulator, not a referral request; it starts and meets no
milestone.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace

from app.events import Event
from app.pathway import Pathway
from app.state_machine import CONSENT_WITHDRAWN, CaseModel, genesis, initial_model, step

from .actors import machine_actor


class MachineCase:
    __slots__ = ("model", "log", "queue", "missed", "seen_clocks", "seen_ms", "sweep_v", "sweep_at", "checks", "esc")

    def __init__(self, pathway: Pathway, created: Event):
        machine = dataclasses.replace(created, payload={"patient_ref": f"Patient/sim-{created.case_id.hex[:12]}"})
        self.model: CaseModel = genesis(initial_model(pathway), machine, pathway)
        self.log: list[Event] = [machine]
        self.queue: list[Event] = []  # events held while consent is withdrawn
        self.missed: set[tuple] = set()  # ("state", clock event id) / ("ms", milestone): the scheduler missed it
        self.seen_clocks: set = set()
        self.seen_ms: set[str] = set()
        self.sweep_v = 0  # version of the scheduled sweep check (a stale one is ignored)
        self.sweep_at: float | None = None
        self.checks: list = []  # the instants the sweep checked this case (the conformance test replays them)
        self.esc: dict = {}  # escalate event id -> the simulator's logged rung event, for `attached_to` (A19)

    @property
    def frozen(self) -> bool:
        return self.model.state == CONSENT_WITHDRAWN

    def apply(self, event: Event, pathway: Pathway) -> list[Event]:
        """Fold one machine event (raises InvalidTransition on an engine bug). While consent is withdrawn every event but a
        consent change is held; a consent change that restores it folds the held ones in after it. Returns what was folded."""
        if self.frozen and event.type != "consent_updated":
            self.queue.append(event)
            return []
        done = [event]
        self.model = step(self.model, event, pathway)
        self.log.append(event)
        if not self.frozen and self.queue:
            held, self.queue = self.queue, []
            for e in held:
                done += self.apply(e, pathway)
        return done

    def view(self) -> CaseModel:
        """The model the planner sees: obligations the scheduler missed look already breached and accepted, so the planner
        plans nothing for them (and `next_due_at` has no trigger for them)."""
        m = self.model
        if not self.missed:
            return m
        if ("state", m.clock_started_event_id) in self.missed:
            m = replace(m, overdue=True, escalation_accepted=True)
        masked = {n: replace(s, overdue=True, escalation_accepted=True)
                  for n, s in m.milestones.items() if ("ms", n) in self.missed}
        return replace(m, milestones={**m.milestones, **masked}) if masked else m


def machine_event(ev: Event, etype: str, payload: dict, actor: str | None = None) -> Event:
    """The machine form of a logged simulator event: the API's event type, a payload the API's strict schema accepts, the
    actor the API's actor table knows (`sim.actors`), the same time and event id."""
    return Event(ev.case_id, etype, ev.at, machine_actor(actor or ev.actor), payload, ev.event_id)
