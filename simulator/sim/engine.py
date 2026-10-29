"""Discrete-event engine for the positive-FIT -> colonoscopy -> closure pathway.

One global event heap and a simulated clock (float days since the start date). Two arms share the
same patients, the same pre-drawn random numbers (see behaviour.py) and the same messy-case plans:

  baseline      result issued, letter/SMS sent once, no named owner, no timers, no escalation, no
                bounce-back, no consent ledger; patients may or may not act; referrals may be lost.
  orchestrated  named owner, the API's own sweep (working-day SLA and milestone breaches, the escalation
                ladder with its coalescing, the bounce of an unanswered referral) on the API's own read model,
                handoff accept/reject with the re-route through `referral_rejected`, consent-aware outreach,
                an idle-case watchdog, and human-gated closure.

Programme reach (`orchestration.programme_reach`, 1.0 in params.yaml): in the orchestrated arm only this share of
cases (a per-patient latent, so the same patients in every scenario) gets the orchestrated process; the others follow
the baseline rules inside the same engine. The literature-anchored scenario lowers it to hold the average effect to the
published navigation risk ratio. At 1.0 every case is reached and the event log is identical to the engine without it.

Pathway events are validated through `app.state_machine.apply` against the `app.pathway.Pathway`
loaded from the YAML (the same code the API uses). For a case the orchestrated process reaches, the engine also keeps the
API's full read model and runs the API's sweep on it (`sim.machine`, M7-1): the simulator has no timers, ladder or bounce
of its own, and tests/test_sweep_conformance.py replays every such case through a real event store and the real
`app.sweep.run_sweep` and finds the same log, event for event. The overlay events the metrics read (`overdue`,
`escalated`, `reminder_sent`) are logged at the instant the sweep plans the breach or the rung.

The referral follows the API's path: a provider's rejection or the sweep's bounce of an unanswered request moves the case
to `referral_rejected` (its 2-working-day re-route SLA and ladder), a re-request accepted by a provider returns it to
`owner_accepted`, and the case then goes through contact, acknowledgement and booking again, as in the API. A missed
appointment that is rebooked is `appointment_cancelled` (back to `patient_acknowledged`). A reopen moves a closed case to
`reopened`, where it waits for a clinician's review and a fresh close: the pathway's only way out of `closed`.

Time: the clock counts days from 00:00 local time of the start date in the API's calendar zone (Asia/Dubai), and every
working-day count is the API's calendar (`sim.calendar`).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from heapq import heappop, heappush

from dataclasses import replace as _replace
from uuid import UUID

from app.events import CASE_CREATED, Event
from app.pathway import Pathway
from app.state_machine import CaseModel, InvalidTransition, apply, step
from app.sweep import next_due_at, plan_case

from .actors import as_machine_event, machine_actor
from .behaviour import (
    A_POOL, H_POOL, HUMAN_KINDS, M_POOL, N_RUNGS, STAFF_STEPS, T_POOL, W_POOL, Behaviour, Latents, draw_latents,
    lognormal,
)
from .calendar import WorkingCalendar
from .capacity import COORDINATOR, CapacityRun, SlotBook, StaffQueue
from .machine import MachineCase, machine_event
from .messy import MessyPlan, draw_messy
from .metrics import CaseRecord, build_record
from .params import ModelParams, ParamError
from .patients import Patient

# A33 (ADR-042): informed refusal in the orchestrated arm. A patient who truly refuses is, once the process notices (the ladder is
# exhausted, or the appointment is missed), counselled by the owner and the refusal recorded; a physician other than the
# counsellor signs a declined closure `close_sla` working days later (the pathway's `close` SLA). The counselling delay is an
# ASSUMPTION (5 working days from the moment the case is flagged), not a fitted parameter. The baseline arm has no such path.
COUNSELLING_DELAY_WD = 5
REFUSAL_STATES = ("owner_accepted", "patient_acknowledged", "confirmatory_booked")  # where the pathway allows it

ORCHESTRATED = "orchestrated"
BASELINE = "baseline"
ARMS = (BASELINE, ORCHESTRATED)

# What kind of actor completes each awaited pathway event. Event names come from the pathway YAML.
STEP_KIND = {
    "acknowledge": "staff",
    "accept_ownership": "staff",
    "contact_patient": "staff",
    "patient_acknowledge": "patient",
    "book_confirmatory": "staff",
    "complete_confirmatory": "appointment",
    "clinician_review": "staff",
    "close": "staff",
}
STAFF_INDEX = {s: i for i, s in enumerate(STAFF_STEPS)}
# `referral_rejected` (M7-1): the engine's step there is "reroute", the owner re-routing the rejected or bounced referral (a
# new request): a staff step with the booking step's draws. The pathway's awaited event there is `reassign` (its SLA).
REROUTE = "reroute"
STEP_KINDS = {**STEP_KIND, REROUTE: "staff"}
STAFF_INDEX[REROUTE] = STAFF_INDEX["book_confirmatory"]
REROUTE_STATE = "referral_rejected"
SWEEP_EPS = 1.0 / 86400  # the sweep checks a case one second after the instant `next_due_at` names (it needs now > due)
# B21 timing mixture: the staff steps between the result review and the referral that a fast-lane case does without delay.
FAST_LANE_STEPS = frozenset(("accept_ownership", "contact_patient", "book_confirmatory"))
# How each escalation-ladder role reaches a patient who has not responded.
ROLE_KIND = {"owner": "reminder", "owner_supervisor": "human_call", "programme_coordinator": "coordinator_route"}

OVERLAY_EVENTS = (
    "overdue", "escalated", "reminder_sent", "message_sent", "message_blocked", "delivery_failed",
    "contact_recovered", "handoff_requested", "handoff_accepted", "handoff_rejected", "consent_updated",
    "consent_review_completed", "watchdog_flagged", "lost_contact", "reopened", "appointment_missed",
    "appointment_rebooked", "provider_switched", "duplicate_result_received", "duplicate_result_merged",
    "conflicting_ack_rejected", "conflicting_ack_unresolved",
)


class Case:
    __slots__ = (
        "p", "lat", "msy", "t0", "end", "state", "token", "step", "log", "seq", "owner", "contact_ok",
        "consent_ok", "withdrawn", "declined_care", "dropped_out", "lapses", "resp_idx", "rec_idx", "h_idx",
        "h_cur", "hstate", "appt_idx", "appt_t", "pending_appt", "reminders", "awaiting_patient", "sub",
        "timer_n", "watch_n", "watch_armed", "last_act", "extra_delay", "abandoned", "reopened", "round",
        "bchannel", "lost_reasons", "booked_once", "last_flag", "orch", "ms_timer_n",
        "slot_prov", "refuses", "refusal", "fast", "mach", "accepted_h",
    )

    def __init__(self, p: Patient, lat: Latents, msy: MessyPlan, end: float, initial_state: str, bchannel: str,
                 refuses: bool = False, orch: bool = False):
        self.p, self.lat, self.msy = p, lat, msy
        self.orch = orch  # True: this case gets the orchestrated process; False: today's process (baseline rules)
        self.t0 = p.arrival_t
        self.end = end
        self.state = initial_state
        self.token = 0
        self.step: str | None = None
        self.log: list[Event] = []
        self.seq = 0
        self.owner: str | None = None
        self.contact_ok = p.contact_valid
        self.consent_ok = True  # what the SYSTEM believes (orchestrated consent ledger)
        self.withdrawn = False  # what is TRUE about the patient
        self.declined_care = refuses  # truth: this patient will not act (refusal or consent withdrawal)
        self.refuses = refuses  # truth: an outright refusal (not a consent withdrawal); the orchestrated arm counsels these (A33)
        self.refusal: dict | None = None  # the informed refusal on record: {counselling_ref, counselled_by}
        self.dropped_out = False
        self.lapses = 0
        self.resp_idx = 0
        self.rec_idx = 0
        self.h_idx = 0
        self.h_cur = -1
        self.hstate = "staff"  # staff | pending | bounced (book_confirmatory sub-state)
        self.appt_idx = 0
        self.appt_t: float | None = None
        self.pending_appt: float | None = None
        self.reminders = 0
        self.awaiting_patient = False
        self.sub: str | None = None
        self.timer_n = 0
        self.watch_n = 0
        self.watch_armed = False
        self.last_act = self.t0
        self.extra_delay = 0.0
        self.abandoned = False
        self.reopened = False
        self.round = 0
        self.bchannel = bchannel
        self.lost_reasons: list[str] = []
        self.booked_once = False
        self.last_flag = -1.0
        self.ms_timer_n = 0
        self.mach: MachineCase | None = None  # the API's read model and sweep, for a case the orchestrated process reaches
        self.accepted_h: int | None = None  # the referral attempt a provider accepted (a re-route then books on it)
        self.slot_prov: int | None = None  # the colonoscopy provider this case is booked with (capacity model only)
        self.fast = False  # B21: fast booking lane (set by the engine from the lane latent and programme management)


@dataclass
class RunResult:
    arm: str
    seed: int
    cases: list[Case]
    records: list[CaseRecord]
    event_hash: str
    n_events: int
    end_day: float
    counters: dict = field(default_factory=dict)
    capacity: CapacityRun | None = None  # set only when the parameters define `capacity` (B19)


DEADLINE_ORDER = 1 << 60  # heap tie-break offset of a deadline check: after every ordinary event of the same instant


def _mask() -> int:
    return ~0xFFFFF & ((1 << 128) - 1)


_MASK = _mask()


class Engine:
    def __init__(
        self,
        arm: str,
        mp: ModelParams,
        pathway: Pathway,
        cal: WorkingCalendar,
        follow_up_days: float,
        seed: int = 0,
    ):
        if arm not in ARMS:
            raise ValueError(f"arm must be one of {ARMS}")
        self.arm = arm
        self.orch = arm == ORCHESTRATED  # arm default; a case follows it only if it is within the programme reach (see Case.orch)
        # The sweep reads the bounce window from the pathway; the simulator's parameter for it (`handoff.response_sla_working_days`,
        # moved by the sensitivity sweep) is written there, so the real planner runs with it. Equal by default.
        if mp.handoff_sla_working_days != pathway.handoff_response_working_days:
            pathway = _replace(pathway, handoff_response_working_days=mp.handoff_sla_working_days)
        self.mp, self.pw, self.cal = mp, pathway, cal
        self.acal = cal.api  # the API's calendar (app.calendar), which the sweep counts on
        self.beh = Behaviour(mp)
        self.days = float(follow_up_days)
        self.seed = seed
        self.start_dt = cal.start_dt  # 00:00 local (Asia/Dubai) on the start date
        self.terminal = pathway.terminal_state
        self.states = pathway.states
        self.idx_booked = self.states.index("confirmatory_booked")
        self.next_event: dict[str, str] = {}
        # The forward chain, plus the one way back from a reopen (`reopened` waits for a clinician's review). Other side
        # states (referral_rejected, consent_withdrawn, declined_pending_signoff) are the engine's own sub-flows.
        for t in pathway.main_transitions:
            self.next_event.setdefault(t.from_state, t.event)
        reopen = [t for t in pathway.transitions if t.event == "reopen"]
        if len(reopen) != 1:
            raise ParamError("engine expects exactly one `reopen` transition in the pathway")
        self.reopen_state = reopen[0].to_state
        if mp.reopen_resume_state != self.reopen_state:
            raise ParamError(f"reopen.resume_state is {mp.reopen_resume_state!r}; the pathway reopens a case to "
                             f"{self.reopen_state!r}, its only way out of `closed`")
        after_reopen = [t for t in pathway.transitions if t.from_state == self.reopen_state and t.to_state in self.states]
        if len(after_reopen) != 1:
            raise ParamError(f"engine expects one forward step out of {self.reopen_state!r}")
        self.next_event[self.reopen_state] = after_reopen[0].event
        unknown = [e for e in self.next_event.values() if e not in STEP_KIND]
        if unknown:
            raise ParamError(f"engine has no actor model for pathway events {unknown}")
        self.slas = dict(pathway.slas_working_days)
        self.slas.setdefault("close", mp.signoff_sla_working_days)
        if len(pathway.milestones) > M_POOL:
            raise ParamError(f"engine draws {M_POOL} milestone timers per case, the pathway has {len(pathway.milestones)}")
        self.ladder = pathway.escalation_ladder
        for r in self.ladder:
            for table, name in ((mp.escalation_effect_prob, "orchestration.escalation_effect_prob"),
                                (mp.contact_recovery_prob, "orchestration.contact_recovery_prob")):
                if r.notify not in table:
                    raise ParamError(f"{name} has no entry for escalation role {r.notify!r}")
        self.heap: list = []
        self.counter = 0
        self.hash = hashlib.sha256()
        self.n_events = 0
        self.handlers = {
            "start": self.on_start, "act": self.on_act, "p_ack": self.on_p_ack, "self_present": self.on_self_present,
            "appt": self.on_appt, "appt_reminder": self.on_appt_reminder, "sweep": self.on_sweep,
            "ladder_end": self.on_ladder_end, "h_resp": self.on_h_resp,
            "h_retry": self.on_h_retry, "deliv_fail": self.on_deliv_fail,
            "recover": self.on_recover, "watch": self.on_watch, "force": self.on_force, "dup": self.on_dup,
            "switch": self.on_switch, "switch_done": self.on_switch_done, "consent": self.on_consent,
            "consent_review": self.on_consent_review, "reopen": self.on_reopen, "self_rebook": self.on_self_rebook,
            "rung_q": self.on_rung_q, "force_q": self.on_force_q, "refusal": self.on_refusal, "signoff": self.on_signoff,
        }
        self.cap: SlotBook | None = None  # finite capacity (B19); built in run() once the cohort size is known
        self.staff: StaffQueue | None = None
        self.counters = {"blocked_messages": 0, "timers_missed": 0, "watchdog_flags": 0, "escalations_coalesced": 0}

    # ------------------------------------------------------------------------------------------
    # run loop
    # ------------------------------------------------------------------------------------------
    def run(self, patients: list[Patient]) -> RunResult:
        cases: list[Case] = []
        for p in patients:
            lat = draw_latents(self.seed, p.key)
            msy = draw_messy(self.seed, p, self.mp)
            # Programme reach: in the orchestrated arm only this share of cases actually receives the orchestrated
            # process; the rest follow today's process (baseline rules). 1.0 (upper range) = every case.
            reached = self.orch and lat.u_reach < self.mp.programme_reach
            c = Case(p, lat, msy, p.arrival_t + self.days, self.pw.initial_state, self.beh.baseline_channel(lat),
                     lat.u_refuse < self.mp.refusal_prob, reached)
            # B21: a case is programme-managed when the orchestrated process reaches it, or when the whole setting is a
            # managed programme (baseline.programme_managed); managed cases are booked promptly more often.
            c.fast = self.beh.fast_lane(lat, reached or self.mp.programme_managed)
            cases.append(c)
            self.schedule(c.t0, "start", c)
        if self.mp.capacity is not None:
            wpw = 7 - len(self.mp.weekend_weekdays)
            self.cap = SlotBook(self.mp.capacity, self.cal, len(patients), wpw)
            self.staff = StaffQueue(self.mp.capacity, self.cal, len(patients))
        heap = self.heap
        handlers = self.handlers
        while heap:
            t, _, kind, c, token, data = heappop(heap)
            if t > c.end:
                continue
            handlers[kind](c, t, token, data)
        records = [
            build_record(c.p.idx, c.p.language, c.p.channel, c.msy.tags(), self.days, c.t0, c.log, self.start_dt,
                         in_reach=c.lat.u_reach < self.mp.programme_reach)
            for c in cases
        ]
        return RunResult(
            self.arm, self.seed, cases, records, self.hash.hexdigest(), self.n_events,
            max((c.end for c in cases), default=0.0), dict(self.counters),
            CapacityRun(self.cap, self.staff, len(patients)) if self.cap is not None else None,
        )

    def schedule(self, t: float, kind: str, c: Case, token: int | None = None, data=None) -> None:
        self.counter += 1
        heappush(self.heap, (t, self.counter, kind, c, token, data))

    def schedule_deadline(self, t: float, kind: str, c: Case, token: int | None = None, data=None) -> None:
        """A sweep check at `t`, run AFTER everything else due at the same instant: the API's sweep plans from the log as it
        stands, so whatever happens at the same instant is in it."""
        self.counter += 1
        heappush(self.heap, (t, DEADLINE_ORDER + self.counter, kind, c, token, data))

    # ------------------------------------------------------------------------------------------
    # event construction / logging
    # ------------------------------------------------------------------------------------------
    def _mk(self, c: Case, t: float, etype: str, actor: str, payload: dict | None) -> Event:
        c.seq += 1
        return Event(
            case_id=c.p.case_id,
            type=etype,
            at=self.cal.at(t),
            actor=actor,
            payload=payload if payload is not None else {},
            event_id=uuid.UUID(int=(c.p.case_id.int & _MASK) | c.seq),
            seq=c.seq,
        )

    def _log(self, c: Case, ev: Event, t: float) -> None:
        c.log.append(ev)
        self.n_events += 1
        self.hash.update(
            f"{c.p.idx}|{ev.seq}|{ev.type}|{t:.6f}|{ev.actor}|{json.dumps(ev.payload, sort_keys=True, separators=(',', ':'))}\n".encode()
        )
        if ev.type != "watchdog_flagged":
            c.last_act = t
            if c.orch and not c.watch_armed:
                c.watch_armed = True
                self.schedule(t + self.mp.watchdog_idle_days, "watch", c)

    def emit(self, c: Case, t: float, etype: str, actor: str, payload: dict | None = None,
             m: tuple | None = None, to_state: str | None = None) -> Event:
        """Log an event. `m` = (type, payload[, actor]): its machine form, folded into the API's read model of a reached
        case; `to_state`: the simulator's state after it (a side-state move the machine makes too)."""
        if to_state is not None:  # the log says so, so a replay of the log alone follows the move (sim.invariants)
            payload = {**(payload or {}), "to_state": to_state}
        ev = self._mk(c, t, etype, actor, payload)
        self._log(c, ev, t)
        if to_state is not None:
            c.state = to_state
        if m is not None and c.mach is not None:
            self._fold(c, t, machine_event(ev, *m))
        return ev

    def advance(self, c: Case, t: float, etype: str, actor: str, payload: dict | None = None) -> None:
        """Apply a pathway event through the shared state machine, then enter the next step."""
        ev = self._mk(c, t, etype, actor, payload)
        c.state = apply(c.state, as_machine_event(ev), self.pw)  # raises InvalidTransition on a bug in the engine
        self._log(c, ev, t)
        self._fold_pathway(c, t, ev)
        if c.state == self.terminal:
            c.step = None
            c.token += 1
            if not c.reopened and c.lat.u_reopen < self.mp.reopen_prob:
                lag = lognormal(self.mp.reopen_lag_median_days, self.mp.reopen_lag_sigma, c.lat.z_reopen)
                self.schedule(t + lag, "reopen", c)
        else:
            self.enter_step(c, t)

    # ------------------------------------------------------------------------------------------
    # the API's read model and sweep (M7-1)
    # ------------------------------------------------------------------------------------------
    def _machine_payload(self, c: Case, ev: Event) -> dict:
        """The payload of a pathway event as the API's strict schema has it (app.payloads)."""
        p, i = ev.payload, c.p.idx
        signer = machine_actor(ev.actor)
        if ev.type == "book_confirmatory":
            return {"appointment_date": p["appointment_on"]}
        if ev.type == "complete_confirmatory":
            return {"result_ref": f"Procedure/sim-{i}-{p['appointment']}"}
        if ev.type == "close" and p.get("outcome") == "declined":
            return {"outcome": "declined", "evidence_ref": self._counselling_ref(c), "signed_by": signer}
        if ev.type == "close":
            return {"evidence_ref": f"DiagnosticReport/sim-{i}-colonoscopy", "signed_by": signer}
        if ev.type == "record_informed_refusal":
            return {"counselling_ref": self._counselling_ref(c), "counselled_by": machine_actor(p["counselled_by"]),
                    "reason": p["reason"]}
        return {}  # acknowledge, accept_ownership, contact_patient, patient_acknowledge, clinician_review

    @staticmethod
    def _counselling_ref(c: Case) -> str:
        return f"DocumentReference/counsel-sim-{c.p.idx}"

    def _fold(self, c: Case, t: float, mev: Event, resched: bool = True) -> None:
        """Fold a machine event into the case's API read model (held while consent is withdrawn), then re-plan the sweep."""
        c.mach.apply(mev, self.pw)  # raises InvalidTransition on an engine bug
        self._draw_timers(c)
        if resched:
            self._resched(c, t)

    def _draw_timers(self, c: Case) -> None:
        """The scheduler silently misses an obligation with probability `timer_miss_prob`: drawn once per state SLA clock
        and once per milestone, when it starts (the simulator's only fault model of the sweep; `MachineCase.view`)."""
        mc, m = c.mach, c.mach.model
        if m.state in self.pw.open_states and m.clock_started_event_id not in mc.seen_clocks:
            mc.seen_clocks.add(m.clock_started_event_id)
            if self.pw.sla_for_state(m.state) is not None:
                k = c.timer_n
                c.timer_n += 1
                if k < T_POOL and c.lat.u_timer[k] < self.mp.timer_miss_prob:
                    self.counters["timers_missed"] += 1
                    mc.missed.add(("state", m.clock_started_event_id))
        for name, ms in m.milestones.items():
            if ms.started_at is not None and name not in mc.seen_ms:
                mc.seen_ms.add(name)
                k = c.ms_timer_n
                c.ms_timer_n += 1
                if k < M_POOL and c.lat.u_mtimer[k] < self.mp.timer_miss_prob:
                    self.counters["timers_missed"] += 1
                    mc.missed.add(("ms", name))

    def _resched(self, c: Case, t: float) -> None:
        """Schedule the sweep's next look at the case: one second after `app.sweep.next_due_at` (the planner needs
        now > due), never before now. Whatever event changes the model re-plans it."""
        mc = c.mach
        if not mc.frozen and not mc.queue and c.state != mc.model.state:
            raise AssertionError(f"case {c.p.idx}: simulator state {c.state!r} but the API's model is {mc.model.state!r}")
        due = next_due_at(mc.view(), self.pw, self.acal)
        if due is None:
            if mc.sweep_at is not None:
                mc.sweep_v += 1
                mc.sweep_at = None
            return
        at = max(self.cal.t_of(due), t) + SWEEP_EPS
        if mc.sweep_at is not None and abs(mc.sweep_at - at) < 1e-12:
            return
        mc.sweep_v += 1
        mc.sweep_at = at
        self.schedule_deadline(at, "sweep", c, mc.sweep_v)

    def on_sweep(self, c: Case, t: float, version, data) -> None:
        """The API's sweep looks at the case: `app.sweep.plan_case` at this instant, its events folded in and acted on. A
        bounce is planned alone and the case re-planned straight after, as `app.sweep.run_sweep` does."""
        mc = c.mach
        if mc is None or version != mc.sweep_v:
            return
        mc.sweep_at = None
        now = self.cal.at(t)
        mc.checks.append((now, len(mc.log)))
        for attempt in range(2):
            plan = plan_case(c.p.case_id, mc.view(), self.pw, self.acal, now)
            if not plan:
                if attempt == 0:
                    raise AssertionError(f"case {c.p.idx}: the sweep had nothing to plan at the instant next_due_at named")
                break
            bounced = False
            for ev in plan:
                self._fold(c, t, ev, resched=False)
                if ev.type == "mark_overdue":
                    self._on_breach(c, t, ev)
                elif ev.type == "escalate":
                    self._on_escalate(c, t, ev)
                else:
                    self._on_bounce(c, t, ev)
                    bounced = True
            if not bounced:
                break
        self._resched(c, t)

    def _on_breach(self, c: Case, t: float, ev: Event) -> None:
        payload = {"step": c.step, "sla_working_days": ev.payload["sla_working_days"]}
        if "milestone" in ev.payload:
            payload["milestone"] = ev.payload["milestone"]
        self.emit(c, t, "overdue", "scheduler", payload)

    def _ladder_entry(self, c: Case, x: dict) -> dict:
        return {"milestone": x["milestone"]} if "milestone" in x else {"step": c.step}

    def _on_escalate(self, c: Case, t: float, ev: Event) -> None:
        """A rung the sweep planned. It is logged as `escalated` / `reminder_sent` with the ladders it also stands for
        (`also_for`, A19). A rung `attached_to` an escalation of the same working day notifies nobody: it is recorded on
        that escalation's logged event. Then the person notified reacts (behaviour)."""
        mc, p = c.mach, ev.payload
        levels = [p["level"], *(x["level"] for x in p.get("also_for", ()))]
        if max(levels) == len(self.ladder):
            last = self.cal.add_working_days_t(t, self.mp.ladder_exhausted_wait_working_days)
            self.schedule(last, "ladder_end", c, c.token)
        if "attached_to" in p:
            holder = mc.esc.get(UUID(p["attached_to"]))
            mc.esc[ev.event_id] = holder
            if holder is not None:
                entry = self._ladder_entry(c, p)
                holder.payload.setdefault("also_for", []).append(entry)
                self.hash.update(f"{c.p.idx}|{holder.seq}|also_for|{t:.6f}|"
                                 f"{json.dumps(entry, sort_keys=True, separators=(',', ':'))}\n".encode())
            self.counters["escalations_coalesced"] += 1
            return
        i = p["level"] - 1
        rung = self.ladder[i]
        payload = {"rung": i, "notify": rung.notify, "step": c.step}
        if "milestone" in p:
            payload["milestone"] = p["milestone"]
        also = [self._ladder_entry(c, x) for x in p.get("also_for", ())]
        if also:
            payload["also_for"] = also
            self.counters["escalations_coalesced"] += len(also)
        logged = self.emit(c, t, "escalated" if rung.action == "escalate" else "reminder_sent", "scheduler", payload)
        mc.esc[ev.event_id] = logged
        if self.staff is not None and rung.action == "escalate":
            job = self.staff.request(rung.notify, t, self.staff.job_minutes(c.awaiting_patient))
            if job.start_t > t + 1e-9:  # finite capacity: the person who must work it is busy; it waits for them
                self.schedule(job.start_t, "rung_q", c, c.token, (i, job))
                return
            job.state = "acted"
        self._rung_effects(c, t, i)

    def on_rung_q(self, c: Case, t: float, token, data) -> None:
        """A rung that waited for a person is served."""
        i, job = data
        if token != c.token or c.abandoned:
            job.state = "stale"  # the case moved on while it waited; the person still spent the minutes
            return
        job.state = "acted"
        self._rung_effects(c, t, i)

    def _rung_effects(self, c: Case, t: float, i: int) -> None:
        """What the person a rung notifies does: contact the patient the case is waiting for, or (with the probability the
        role's escalation is acted on) do the overdue staff step. A request waiting for a provider is left to the sweep's
        bounce."""
        rung = self.ladder[i]
        if c.awaiting_patient:
            kind = ROLE_KIND.get(rung.notify, "reminder" if i == 0 else "human_call")
            self.send_outreach(c, t, kind, rung.notify)
        elif c.hstate == "pending":
            return
        elif c.step is not None and STEP_KINDS[c.step] == "staff":
            sd = c.lat.staff[c.round][STAFF_INDEX[c.step]]
            j = min(i, N_RUNGS - 1)
            if sd.u_eff[j] < self.mp.escalation_effect_prob[rung.notify]:
                self.schedule(self.cal.next_working_t(t + self.beh.react_delay(sd.z_react[j])), "act", c, c.token)

    def _on_bounce(self, c: Case, t: float, ev: Event) -> None:
        """The sweep bounced an unanswered referral request (A5). In the booking step the API moves the case to
        `referral_rejected`; a re-request in `referral_rejected` that goes unanswered bounces and stays there."""
        c.hstate = "bounced"
        moved = c.mach.model.state == REROUTE_STATE and c.state != REROUTE_STATE
        self.emit(c, t, "overdue", "scheduler", {"reason": "handoff_unanswered", "attempt": c.h_cur, "via": "timeout"},
                  to_state=REROUTE_STATE if moved else None)
        if moved:
            self._enter_reroute(c, t, "timeout")
        else:
            h = max(c.h_cur, 0)
            self.schedule(self.cal.next_working_t(t + self.beh.reroute_delay(c.lat.z_reroute[h])), "h_retry", c, c.token)

    def _enter_reroute(self, c: Case, t: float, reason: str | None) -> None:
        """The case is in `referral_rejected` (a provider's rejection, or the sweep's bounce): the owner re-routes it with a
        new request after the re-route delay; a patient who declined is re-engaged or lost."""
        c.token += 1
        c.step, c.awaiting_patient, c.sub, c.hstate = "reroute", False, None, "bounced"
        h = max(c.h_cur, 0)
        lat = c.lat
        if reason == "patient_declined" and not (lat.u_declined[h] < self.mp.declined_reengage_prob and c.consent_ok):
            self.flag_lost(c, t, "patient_declined")
            return
        self.schedule(self.cal.next_working_t(t + self.beh.reroute_delay(lat.z_reroute[h])), "h_retry", c, c.token)

    # ------------------------------------------------------------------------------------------
    # steps
    # ------------------------------------------------------------------------------------------
    def enter_step(self, c: Case, t: float) -> None:
        ev = self.next_event.get(c.state)
        c.token += 1
        c.step = ev
        c.awaiting_patient = False
        c.hstate = "staff"
        c.sub = None
        if ev is None:
            return
        kind = STEP_KIND[ev]
        if kind == "staff":
            self.sched_staff(c, t, ev)
        elif kind == "patient":
            c.awaiting_patient = True  # the outreach was sent by the contact_patient action
        else:
            self.book_appointment(c, t)

    def sched_staff(self, c: Case, t: float, step: str) -> None:
        sd = c.lat.staff[c.round][STAFF_INDEX[step]]
        extra, c.extra_delay = c.extra_delay, 0.0
        if self.beh.staff_forgets(sd.u_forget):
            return  # never done unless a reminder or escalation prompts it (orchestrated only)
        # B21: in the fast lane the referral is made when the result is reviewed, so the ownership, contact and booking steps
        # carry no delay of their own (they still happen on a working day and can still be forgotten).
        delay = 0.0 if (c.fast and step in FAST_LANE_STEPS) else self.beh.staff_delay(step, sd.z_delay)
        self.schedule(self.cal.next_working_t(t + delay + extra), "act", c, c.token)

    # ------------------------------------------------------------------------------------------
    # staff actions
    # ------------------------------------------------------------------------------------------
    def on_start(self, c: Case, t: float, token, data) -> None:
        created = self.emit(c, t, CASE_CREATED, "system:intake", {"pathway": self.pw.name})
        m = c.msy
        if m.duplicate_day is not None:
            self.schedule(t + m.duplicate_day, "dup", c)
        if m.switch_day is not None:
            self.schedule(t + m.switch_day, "switch", c)
        if c.lat.u_consent < self.mp.consent_withdrawal_prob:
            self.schedule(t + c.lat.u_consent_when * self.mp.consent_withdrawal_window_days, "consent", c, None, "generic")
        if c.orch:
            c.mach = MachineCase(self.pw, created)
            self._draw_timers(c)
            self._resched(c, t)
        self.enter_step(c, t)

    def on_act(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.abandoned:
            return
        step = c.step
        i = c.p.idx
        if step == "acknowledge":
            self.advance(c, t, "acknowledge", f"lab:staff-{i % 3}")
        elif step == "accept_ownership":
            self.accept_ownership(c, t)
        elif step == "contact_patient":
            self.advance(c, t, "contact_patient", f"staff:outreach-{i % 4}")
            self.send_outreach(c, t, "initial")
            d = self.beh.self_presentation_delay(c.lat)
            if d is not None:
                self.schedule(t + d, "self_present", c, c.token)
        elif step == "book_confirmatory":
            if c.sub == "rebook":
                return  # waiting for the patient to agree a new date
            if c.accepted_h is not None:
                self._book(c, t, c.accepted_h)  # a provider already accepted the referral (a re-route): book on it
            else:
                self.start_handoff(c, t)
        elif step == "reroute":
            self.start_handoff(c, t)
        elif step == "clinician_review":
            self.advance(c, t, "clinician_review", f"clinician:cl-{i % 5}")
        elif step == "close":
            who = f"clinician:cl-{i % 5}"
            self.advance(c, t, "close", who, {"evidence_ref": f"review-{c.p.key}", "signed_by": who})

    def accept_ownership(self, c: Case, t: float) -> None:
        i = c.p.idx
        m = c.msy
        if c.orch:
            owner = f"owner:pcp-{i % 7}"
            c.owner = owner
            self.advance(c, t, "accept_ownership", owner, {"owner": owner})
            if m.conflicting_ack:
                other = f"owner:other-{i % 5}"
                ev2 = self._mk(c, t, "accept_ownership", other, {"owner": other})
                c.seq -= 1  # a rejected claim is not part of the case log
                try:
                    apply(c.state, as_machine_event(ev2), self.pw)
                except InvalidTransition as exc:
                    self.emit(c, t, "conflicting_ack_rejected", "system:state-machine",
                              {"claimant": other, "owner": owner, "reason": str(exc)})
                else:  # pragma: no cover - would mean two owners were accepted
                    raise AssertionError("second ownership claim was accepted")
        else:
            if m.conflicting_ack:
                c.extra_delay += self.mp.baseline_conflict_delay_days  # both assume the other will act
            self.advance(c, t, "accept_ownership", f"staff:intake-{i % 4}", {"owner": None, "implicit": True})
            if m.conflicting_ack:
                self.emit(c, t, "conflicting_ack_unresolved", f"staff:intake-{i % 4}", {"claimants": 2})

    # ------------------------------------------------------------------------------------------
    # ladder end, refusal, watchdog (orchestrated)
    # ------------------------------------------------------------------------------------------
    def on_ladder_end(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.abandoned:
            return
        if c.awaiting_patient:
            self.flag_lost(c, t, "ladder_exhausted")

    def flag_lost(self, c: Case, t: float, reason: str) -> None:
        if reason in c.lost_reasons:
            return
        if (c.orch and c.refuses and c.refusal is None and reason in ("ladder_exhausted", "declined_care")
                and c.lat.u_reach < self.mp.refusal_path_share  # off unless orchestration.refusal_path_share is set (A33)
                and c.state in (*REFUSAL_STATES, "patient_contacted")):
            # A33: a refuser is not "lost": the owner counsels them and records the refusal (a physician signs the closure).
            # Scheduled once; if the case is not in a state that allows it by then, it is flagged lost as before.
            if "refusal_scheduled" not in c.lost_reasons:
                c.lost_reasons.append("refusal_scheduled")
                self.schedule(self.cal.add_working_days_t(t, COUNSELLING_DELAY_WD), "refusal", c)
            return
        c.lost_reasons.append(reason)
        self.emit(c, t, "lost_contact", "scheduler" if c.orch else "system:intake", {"reason": reason})

    def on_refusal(self, c: Case, t: float, token, data) -> None:
        """The owner counsels the refuser and records the informed refusal (A33); the case waits for a physician's sign-off."""
        if c.abandoned or c.state == self.terminal:
            return
        if c.state == "patient_contacted":  # the counselling conversation is also the patient's acknowledgement
            ack = self._log_ev(c, t, "patient_acknowledge", f"staff:outreach-{c.p.idx % 4}", {"via": "counselling"})
            c.state = apply(c.state, as_machine_event(ack), self.pw)
            self._fold_pathway(c, t, ack)
        if c.state not in REFUSAL_STATES:
            c.lost_reasons.remove("refusal_scheduled")
            self.flag_lost(c, t, "declined_care")  # cannot be recorded from here: lost, as before
            return
        counsellor = f"owner:pcp-{c.p.idx % 7}"
        payload = {"counselling_ref": f"DocumentReference/counsel-{c.p.key}", "counselled_by": counsellor,
                   "reason": "personal_choice"}
        ev = self._log_ev(c, t, "record_informed_refusal", counsellor, payload)
        c.state = apply(c.state, as_machine_event(ev), self.pw)
        c.refusal = {"counselling_ref": payload["counselling_ref"], "counselled_by": counsellor}
        c.token += 1  # nothing else is awaited: pending step events lapse
        c.step, c.awaiting_patient, c.hstate = None, False, "staff"
        self._fold_pathway(c, t, ev)
        self.schedule(self.cal.add_working_days_t(t, self.slas["close"]), "signoff", c)

    def on_signoff(self, c: Case, t: float, token, data) -> None:
        """A physician who is not the counsellor signs the declined closure (A33). No reopen is modelled for it."""
        if c.abandoned or c.state != self.pw.refusal_state or c.refusal is None:
            return
        signer = f"clinician:cl-{c.p.idx % 5}"  # a different person from the counsellor (`owner:pcp-N`)
        ev = self._log_ev(c, t, "close", signer, {"outcome": "declined", "evidence_ref": c.refusal["counselling_ref"],
                                                   "signed_by": signer})
        c.state = step(CaseModel(state=c.state, refusal=c.refusal), as_machine_event(ev), self.pw).state
        c.step = None
        c.token += 1
        self._fold_pathway(c, t, ev)

    def _fold_pathway(self, c: Case, t: float, ev: Event) -> None:
        if c.mach is not None:
            self._fold(c, t, machine_event(ev, ev.type, self._machine_payload(c, ev)))

    def _log_ev(self, c: Case, t: float, etype: str, actor: str, payload: dict) -> Event:
        """Build and log a pathway event whose state change the caller applies (the machine runs on the caller's model)."""
        ev = self._mk(c, t, etype, actor, payload)
        self._log(c, ev, t)
        return ev

    def on_watch(self, c: Case, t: float, token, data) -> None:
        c.watch_armed = False
        if c.abandoned or c.state == self.terminal:
            return
        idle = self.mp.watchdog_idle_days
        if t - c.last_act < idle - 1e-9:
            c.watch_armed = True
            self.schedule(c.last_act + idle, "watch", c)
            return
        c.watch_armed = True
        self.schedule(t + idle, "watch", c)
        if c.appt_t is not None and c.appt_t > t:
            return  # a confirmed future appointment is a scheduled wait, not silence
        if c.last_act <= c.last_flag:
            return  # already flagged for this stretch of silence; flag again only after new activity
        c.last_flag = t
        self.counters["watchdog_flags"] += 1
        self.emit(c, t, "watchdog_flagged", "watchdog", {"idle_days": round(t - c.last_act, 2), "step": c.step})
        k = c.watch_n
        c.watch_n += 1
        if k < W_POOL and c.lat.u_watch[k] < self.mp.watchdog_effect_prob:
            d = lognormal(self.mp.watchdog_reaction_delay_days, self.mp.reaction_delay_sigma, c.lat.z_watch[k])
            self.schedule(self.cal.next_working_t(t + d), "force", c, c.token)

    def on_force(self, c: Case, t: float, token, data) -> None:
        """The programme coordinator acts on a watchdog flag."""
        if token != c.token or c.abandoned or c.step is None:
            return
        if self.staff is not None:
            job = self.staff.request(COORDINATOR, t, self.staff.job_minutes(c.awaiting_patient), "force")
            if job.start_t > t + 1e-9:
                self.schedule(job.start_t, "force_q", c, c.token, job)
                return
            job.state = "acted"
        self._force_effects(c, t)

    def on_force_q(self, c: Case, t: float, token, job) -> None:
        if token != c.token or c.abandoned or c.step is None:
            job.state = "stale"
            return
        job.state = "acted"
        self._force_effects(c, t)

    def _force_effects(self, c: Case, t: float) -> None:
        if c.awaiting_patient:
            self.send_outreach(c, t, "coordinator_route", "programme_coordinator")
        elif c.hstate == "pending":
            return  # a request is with a provider: the sweep bounces it if it stays unanswered
        elif STEP_KINDS[c.step] == "staff":
            self.schedule(t, "act", c, c.token)

    # ------------------------------------------------------------------------------------------
    # outreach and patient response
    # ------------------------------------------------------------------------------------------
    def lapse(self, c: Case) -> None:
        k = c.lapses
        c.lapses += 1
        if self.beh.drops_out(c.lat, k):
            c.dropped_out = True

    def send_outreach(self, c: Case, t: float, kind: str, role: str | None = None) -> None:
        mp = self.mp
        if c.orch:
            if not c.consent_ok:
                self.counters["blocked_messages"] += 1
                self.emit(c, t, "message_blocked", "system:messaging", {"kind": kind, "reason": "consent_withdrawn"})
                return
            channel = "phone" if kind in HUMAN_KINDS else c.p.channel
            lang = c.p.language
            actor = f"owner:pcp-{c.p.idx % 7}" if kind in HUMAN_KINDS else "system:messaging"
        else:
            channel, lang, actor = c.bchannel, mp.baseline_default_language, f"staff:outreach-{c.p.idx % 4}"
        self.emit(c, t, "message_sent", actor, {"kind": kind, "channel": channel, "language": lang, "draft_source": "template"})
        if not c.contact_ok:
            self.lapse(c)
            if c.orch:
                if kind in HUMAN_KINDS:
                    self.try_recovery(c, t, role or "owner_supervisor")
                else:
                    self.schedule(t + mp.delivery_failure_detect_days[channel], "deliv_fail", c, c.token)
            return
        r = c.resp_idx
        c.resp_idx += 1
        delay = None
        if not (c.withdrawn or c.declined_care):
            delay = self.beh.response_delay(c.p, c.lat, r, channel, lang, kind, c.dropped_out)
        if delay is None:
            self.lapse(c)
        else:
            self.schedule(t + delay, "p_ack", c, c.token)

    def on_deliv_fail(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.contact_ok or not c.awaiting_patient:
            return
        self.emit(c, t, "delivery_failed", "system:messaging", {"channel": c.p.channel})
        self.try_recovery(c, t, "on_failure")

    def try_recovery(self, c: Case, t: float, role: str) -> None:
        k = c.rec_idx
        c.rec_idx += 1
        if k >= 4 or c.lat.u_rec[k] >= self.mp.contact_recovery_prob[role]:
            return
        d = lognormal(self.mp.contact_recovery_delay_median_days, self.mp.reaction_delay_sigma, c.lat.z_rec[k])
        self.schedule(self.cal.next_working_t(t + d), "recover", c)

    def on_recover(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.contact_ok:
            return
        c.contact_ok = True
        self.emit(c, t, "contact_recovered", f"owner:pcp-{c.p.idx % 7}", {"channel": c.p.channel})
        if c.awaiting_patient and c.consent_ok:
            self.send_outreach(c, t, "reminder", "owner")

    def on_p_ack(self, c: Case, t: float, token, data) -> None:
        if token != c.token or not c.awaiting_patient or c.declined_care or c.abandoned:
            return
        if c.step == "patient_acknowledge":
            self.advance(c, t, "patient_acknowledge", f"patient:{c.p.key}", {"via": "reply"})
        elif c.sub == "rebook":
            self.rebook(c, t, "outreach")

    def on_self_present(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.step != "patient_acknowledge" or c.declined_care or c.abandoned:
            return
        self.advance(c, t, "patient_acknowledge", f"patient:{c.p.key}", {"via": "self_presented"})

    # ------------------------------------------------------------------------------------------
    # referral handoff
    # ------------------------------------------------------------------------------------------
    def start_handoff(self, c: Case, t: float) -> None:
        if c.hstate == "pending":
            return  # a request is already with a provider (an escalation prompted the owner while it waits)
        h = c.h_idx
        if h >= min(H_POOL, self.mp.handoff_max_attempts):
            c.hstate = "bounced"
            self.flag_lost(c, t, "referral_attempts_exhausted")
            return
        c.h_idx += 1
        c.h_cur = h
        c.hstate = "pending"
        who = f"owner:pcp-{c.p.idx % 7}" if c.orch else f"staff:referrals-{c.p.idx % 3}"
        to = f"endoscopy-{h}"
        self.emit(c, t, "handoff_requested", who, {"attempt": h, "to": to}, m=("handoff_requested", {"to_provider": to}))
        outcome = self.beh.handoff_outcome(c.lat.u_h[h])
        if outcome != "none":
            reason = self.beh.rejection_reason(c.lat.u_reason[h]) if outcome == "reject" else None
            at = self.cal.next_working_t(t + self.beh.handoff_delay(c.lat.z_h[h]))
            self.schedule(at, "h_resp", c, c.token, (h, outcome, reason))
        # an orchestrated request that stays unanswered is bounced by the API's sweep (`_on_bounce`)

    def on_h_resp(self, c: Case, t: float, token, data) -> None:
        h, outcome, reason = data
        if token != c.token or c.hstate != "pending" or c.h_cur != h or c.abandoned:
            return
        if outcome == "accept":
            c.hstate = "staff"
            c.accepted_h = h
            if c.mach is not None and c.state == REROUTE_STATE:
                # the API's path: an accepted re-route returns the case to owner_accepted, and the owner contacts the
                # patient, the patient acknowledges and the booking is made on the accepted referral
                self.emit(c, t, "handoff_accepted", f"endoscopy:unit-{h}", {"attempt": h}, m=("handoff_accepted", {}),
                          to_state="owner_accepted")
                self.enter_step(c, t)
                return
            self.emit(c, t, "handoff_accepted", f"endoscopy:unit-{h}", {"attempt": h}, m=("handoff_accepted", {}))
            self._book(c, t, h)
            return
        c.hstate = "bounced"
        rejects = c.mach is not None and c.state in ("patient_acknowledged", "confirmatory_booked")
        self.emit(c, t, "handoff_rejected", f"endoscopy:unit-{h}", {"attempt": h, "reason": reason},
                  m=("handoff_rejected", {"reason": reason}), to_state=REROUTE_STATE if rejects else None)
        lat = c.lat
        if c.orch:
            if c.state == REROUTE_STATE and c.step != "reroute":
                self._enter_reroute(c, t, reason)
            elif reason == "patient_declined" and not (lat.u_declined[h] < self.mp.declined_reengage_prob and c.consent_ok):
                self.flag_lost(c, t, "patient_declined")
            else:  # capacity / wrong_service: re-route to another service; missing_info: complete and resubmit
                self.schedule(self.cal.next_working_t(t + self.beh.reroute_delay(lat.z_reroute[h])), "h_retry", c, c.token)
        elif lat.u_notice[h] < self.mp.baseline_rejection_noticed_prob:
            d = lognormal(self.mp.baseline_rejection_notice_median_days, self.mp.reaction_delay_sigma, lat.z_notice[h])
            self.schedule(t + d, "h_retry", c, c.token)
        # else: the rejection is never noticed and the referral is silently lost

    def _book(self, c: Case, t: float, h: int) -> None:
        """Book the colonoscopy on the referral that provider `h` accepted."""
        lead = self.beh.lead_days(c.lat, c.appt_idx, False, c.fast)
        c.pending_appt = self._reserve(c, t, self.cal.next_working_t(t + lead), h)
        who = f"owner:pcp-{c.p.idx % 7}" if c.orch else f"staff:referrals-{c.p.idx % 3}"
        self.advance(c, t, "book_confirmatory", who, {"appointment_on": self.cal.date_of(c.pending_appt).isoformat()})
        if c.msy.consent_after_booking_lag is not None and not c.booked_once:
            self.schedule(t + c.msy.consent_after_booking_lag, "consent", c, None, "after_booking")
        c.booked_once = True

    def on_h_retry(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.abandoned or c.step not in ("book_confirmatory", "reroute"):
            return
        self.start_handoff(c, t)

    # ------------------------------------------------------------------------------------------
    # appointment
    # ------------------------------------------------------------------------------------------
    def book_appointment(self, c: Case, t: float) -> None:
        a = c.appt_idx
        appt_t = c.pending_appt
        if appt_t is None:
            appt_t = self._reserve(c, t, self.cal.next_working_t(t + self.beh.lead_days(c.lat, a, False, c.fast)))
        c.pending_appt = None
        c.appt_t = appt_t
        c.reminders = 0
        self.schedule(appt_t, "appt", c, c.token, a)
        if c.orch:
            for d in self.mp.reminder_days_before:
                tr = appt_t - d
                if tr > t:
                    self.schedule(tr, "appt_reminder", c, c.token)

    def _reserve(self, c: Case, t: float, earliest: float, attempt: int | None = None) -> float:
        """The appointment time: `earliest` (the lead-time date) in the unconstrained model; with finite capacity the earliest
        free slot at the case's provider on or after it (B19)."""
        if self.cap is None:
            return earliest
        n = self.mp.capacity.providers
        if attempt is not None:
            c.slot_prov = (c.p.idx + attempt) % n
        elif c.slot_prov is None:
            c.slot_prov = c.p.idx % n
        return self.cap.book(c.p.idx, c.slot_prov, earliest, t)

    def on_appt_reminder(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.abandoned:
            return
        if not c.consent_ok:
            self.counters["blocked_messages"] += 1
            self.emit(c, t, "message_blocked", "system:messaging", {"kind": "appointment_reminder", "reason": "consent_withdrawn"})
            return
        self.emit(c, t, "message_sent", "system:messaging",
                  {"kind": "appointment_reminder", "channel": c.p.channel, "language": c.p.language, "draft_source": "template"})
        if c.contact_ok:
            c.reminders += 1

    def on_appt(self, c: Case, t: float, token, a: int) -> None:
        if token != c.token or c.abandoned:
            return
        c.appt_idx = a + 1
        c.appt_t = None
        channel = c.p.channel if c.orch else c.bchannel
        attends = not c.declined_care and not self.beh.no_show(c.p, c.lat, a, channel, c.reminders, c.orch)
        if self.cap is not None:
            if attends:
                self.cap.resolve(c.p.idx, "attended")
            else:  # the unit refills a no-show's slot from outside the cohort with probability dna_backfill_prob
                filled = c.lat.u_backfill[a] < self.mp.capacity.dna_backfill_prob
                self.cap.resolve(c.p.idx, "dna_backfilled" if filled else "dna_empty")
        if attends:
            self.advance(c, t, "complete_confirmatory", "endoscopy:unit",
                         {"appointment": a, "evidence_ref": f"procedure-{c.p.key}-{a}"})
            return
        more = (a + 1) < min(A_POOL, self.mp.appointment_max_attempts) and not c.declined_care
        if c.orch and more:
            # the API's path (A34): the missed appointment is cancelled, the case goes back to patient_acknowledged, and
            # its booking SLA (book_confirmatory) and ladder run while the owner agrees a new date with the patient
            self.emit(c, t, "appointment_missed", "endoscopy:unit", {"appointment": a},
                      m=("appointment_cancelled", {"reason": "other", "by_patient": True}), to_state="patient_acknowledged")
        else:
            self.emit(c, t, "appointment_missed", "endoscopy:unit", {"appointment": a})
        self.lapse(c)
        if c.orch:
            self.emit(c, t, "overdue", "scheduler", {"reason": "missed_appointment", "step": c.step})
            if more:
                c.token += 1  # a new sub-flow: waiting for the patient to agree a new date
                c.step, c.hstate = "book_confirmatory", "staff"
                c.awaiting_patient = True
                c.sub = "rebook"
                self.send_outreach(c, t, "reminder", "owner")  # the owner follows the missed appointment up at once
            else:
                self.flag_lost(c, t, "declined_care" if c.declined_care else "repeated_no_show")
        elif more and c.lat.u_selfrebook[a] < self.mp.noshow_self_rebook_prob:
            d = lognormal(self.mp.noshow_self_rebook_median_days, self.mp.response_delay_sigma, c.lat.z_selfrebook[a])
            c.sub = "self_rebook"
            self.schedule(t + d, "self_rebook", c, c.token)
        # else baseline: nobody notices; the patient does not come back

    def on_self_rebook(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.sub != "self_rebook" or c.declined_care or c.abandoned:
            return
        self.rebook(c, t, "self")

    def rebook(self, c: Case, t: float, via: str) -> None:
        a = c.appt_idx
        who = f"patient:{c.p.key}" if via == "self" else f"owner:pcp-{c.p.idx % 7}"
        self.emit(c, t, "appointment_rebooked", who, {"attempt": a, "via": via})
        c.token += 1
        c.awaiting_patient = False
        c.sub = None
        c.pending_appt = self._reserve(c, t, self.cal.next_working_t(t + self.beh.lead_days(c.lat, a, True, c.fast)))
        if c.mach is not None:  # back in patient_acknowledged (appointment_cancelled): the new date is a booking
            self.advance(c, t, "book_confirmatory", who, {"appointment_on": self.cal.date_of(c.pending_appt).isoformat()})
            return
        self.book_appointment(c, t)

    # ------------------------------------------------------------------------------------------
    # messy cases, consent, reopen
    # ------------------------------------------------------------------------------------------
    def on_dup(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.state == self.terminal:
            return
        if c.orch:
            self.emit(c, t, "duplicate_result_merged", "system:dedup", {"matched": True})
        else:
            self.emit(c, t, "duplicate_result_received", "lab:second", {"new_work_item": True})

    def _pre_booking(self, c: Case) -> bool:
        if c.state not in self.states:  # a recorded refusal waiting for its sign-off (A33): nothing to switch or transfer
            return False
        return self.states.index(c.state) < self.idx_booked

    def _reenter_staff_step(self, c: Case) -> bool:
        return (c.step is not None and c.step != REROUTE and STEP_KIND[c.step] == "staff" and c.sub is None
                and (c.step != "book_confirmatory" or c.hstate == "staff"))

    def on_switch(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.state == self.terminal:
            return
        if not self._pre_booking(c):
            self.emit(c, t, "provider_switched", "patient:" + c.p.key, {"applies": False})
            return
        lat = c.lat
        if c.orch:
            self.emit(c, t, "provider_switched", "patient:" + c.p.key, {"applies": True})
            who = f"owner:pcp-{c.p.idx % 7}"
            self.emit(c, t, "handoff_requested", who, {"kind": "transfer", "to": "provider-new"})
            rejected = lat.u_switch_rej < self.mp.referral_rejection_rate
            delay = self.beh.handoff_delay(lat.z_switch) + (self.beh.reroute_delay(lat.z_switch) if rejected else 0.0)
            reason = self.beh.rejection_reason(lat.u_switch_reason) if rejected else None
            self.schedule(self.cal.next_working_t(t + delay), "switch_done", c, None, reason)
        elif lat.u_switch_loss < self.mp.baseline_switch_loss_prob:
            c.abandoned = True
            c.token += 1
            self.emit(c, t, "provider_switched", "patient:" + c.p.key, {"applies": True, "outcome": "lost"})
        else:
            self.emit(c, t, "provider_switched", "patient:" + c.p.key, {"applies": True, "outcome": "continued"})
            if self._reenter_staff_step(c):
                c.extra_delay += self.mp.baseline_switch_delay_days
                self.enter_step(c, t)

    def on_switch_done(self, c: Case, t: float, token, reason) -> None:
        if c.abandoned or c.state == self.terminal:
            return
        if reason is not None:
            self.emit(c, t, "handoff_rejected", "provider:new", {"kind": "transfer", "reason": reason})
            self.emit(c, t, "handoff_requested", f"owner:pcp-{c.p.idx % 7}", {"kind": "transfer", "to": "provider-new-2"})
        new_owner = f"owner:new-{c.p.idx % 7}"
        self.emit(c, t, "handoff_accepted", new_owner, {"kind": "transfer", "owner": new_owner})
        c.owner = new_owner
        if not self._pre_booking(c):
            return
        if c.awaiting_patient:
            self.send_outreach(c, t, "reminder", "owner")  # the new owner re-establishes contact
        elif self._reenter_staff_step(c):
            c.extra_delay += self.mp.transfer_reestablish_days
            self.enter_step(c, t)

    def on_consent(self, c: Case, t: float, token, source: str) -> None:
        if c.abandoned or c.withdrawn or c.state == self.terminal:
            return
        c.withdrawn = True
        c.declined_care = c.declined_care or c.lat.u_consent_declines < self.mp.consent_withdrawal_declines_care_prob
        if self.cap is not None and c.declined_care:
            held = self.cap.held_day(c.p.idx)
            if held is not None and held > math.floor(t):  # a patient who declines care cancels before the day: the slot is free again
                self.cap.cancel(c.p.idx)
        if c.orch:
            c.consent_ok = False
            self.emit(c, t, "consent_updated", f"patient:{c.p.key}",
                      {"status": "withdrawn", "scope": "outreach", "flagged_for_review": True, "source": source},
                      m=("consent_updated", {"withdrawn": True}))
            d = lognormal(self.mp.consent_review_delay_median_days, self.mp.reaction_delay_sigma, c.lat.z_consent_review)
            self.schedule(self.cal.next_working_t(t + d), "consent_review", c)
        else:
            self.emit(c, t, "consent_updated", f"patient:{c.p.key}",
                      {"status": "withdrawn", "scope": "outreach", "recorded": False, "source": source})

    def on_consent_review(self, c: Case, t: float, token, data) -> None:
        if c.abandoned:
            return
        outcome = "declined_care" if c.declined_care else "continues_without_outreach"
        # a review that lets the case continue restores consent in the API (outreach stays off in the simulator); one that
        # finds the patient declines care leaves the case in consent_withdrawn, frozen, as the API would
        restore = ("consent_updated", {"withdrawn": False}) if not c.declined_care and c.withdrawn else None
        self.emit(c, t, "consent_review_completed", f"owner:pcp-{c.p.idx % 7}", {"outcome": outcome}, m=restore)
        if c.declined_care and c.state != self.terminal:
            self.flag_lost(c, t, "declined_care")

    def on_reopen(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.state != self.terminal:
            return
        resume = self.reopen_state  # `reopened`: a clinician reviews again and a fresh close is needed
        self.emit(c, t, "reopened", "clinician:late-finding", {"resume_state": resume}, m=("reopen", {"reason": "new_finding"}),
                  to_state=resume)
        c.reopened = True
        c.round = 1
        c.lost_reasons = []
        self.enter_step(c, t)


def make_engine(arm: str, mp: ModelParams, pathway: Pathway, cal: WorkingCalendar, days: float, seed: int) -> Engine:
    return Engine(arm, mp, pathway, cal, days, seed)
