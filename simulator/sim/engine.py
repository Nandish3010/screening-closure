"""Discrete-event engine for the positive-FIT -> colonoscopy -> closure pathway.

One global event heap and a simulated clock (float days since the start date). Two arms share the
same patients, the same pre-drawn random numbers (see behaviour.py) and the same messy-case plans:

  baseline      result issued, letter/SMS sent once, no named owner, no timers, no escalation, no
                bounce-back, no consent ledger; patients may or may not act; referrals may be lost.
  orchestrated  named owner, working-day SLA timers from the pathway YAML, the escalation ladder,
                handoff accept/reject/bounce, consent-aware outreach, an idle-case watchdog, and
                human-gated closure.

Pathway events are validated through `app.state_machine.apply` against the `app.pathway.Pathway`
loaded from the YAML (the same code the API uses). Side events (overdue, escalated, handoff_*,
consent_updated, watchdog_flagged, lost_contact, reopened and the other bookkeeping events) are
logged as typed overlay events and do not change the pathway state, because the pathway YAML
defines no transitions for them. `reopened` is the one exception: it moves a closed case back to
`reopen.resume_state` and is the only way out of `closed`.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush

from app.events import CASE_CREATED, Event
from app.pathway import Pathway
from app.state_machine import InvalidTransition, apply

from .behaviour import (
    A_POOL, H_POOL, HUMAN_KINDS, N_RUNGS, STAFF_STEPS, T_POOL, W_POOL, Behaviour, Latents, draw_latents,
    lognormal,
)
from .calendar import WorkingCalendar
from .messy import MessyPlan, draw_messy
from .metrics import CaseRecord, build_record
from .params import ModelParams, ParamError
from .patients import Patient

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
        "bchannel", "lost_reasons", "booked_once", "last_flag",
    )

    def __init__(self, p: Patient, lat: Latents, msy: MessyPlan, end: float, initial_state: str, bchannel: str, refuses: bool = False):
        self.p, self.lat, self.msy = p, lat, msy
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
        self.orch = arm == ORCHESTRATED
        self.mp, self.pw, self.cal = mp, pathway, cal
        self.beh = Behaviour(mp)
        self.days = float(follow_up_days)
        self.seed = seed
        self.start_dt = datetime(cal.start.year, cal.start.month, cal.start.day, tzinfo=timezone.utc)
        self.terminal = pathway.terminal_state
        self.states = pathway.states
        self.idx_booked = self.states.index("confirmatory_booked")
        self.next_event: dict[str, str] = {}
        # The forward chain only: side-state transitions (referral_rejected, consent_withdrawn,
        # lost_contact, reopened) are modelled by the engine's own sub-flows, not as awaited steps.
        for t in pathway.main_transitions:
            self.next_event.setdefault(t.from_state, t.event)
        unknown = [e for e in self.next_event.values() if e not in STEP_KIND]
        if unknown:
            raise ParamError(f"engine has no actor model for pathway events {unknown}")
        self.slas = dict(pathway.slas_working_days)
        self.slas.setdefault("close", mp.signoff_sla_working_days)
        # DoH KPI: colonoscopy within 31 working days of referral. Until the pathway YAML says so,
        # the simulator uses that value for complete_confirmatory (recorded in the results meta).
        self.sla_overrides: dict[str, dict] = {}
        if "complete_confirmatory" in self.slas and self.slas["complete_confirmatory"] != mp.followup_target_working_days:
            self.sla_overrides["complete_confirmatory"] = {
                "pathway_yaml": self.slas["complete_confirmatory"], "used": mp.followup_target_working_days,
            }
            self.slas["complete_confirmatory"] = mp.followup_target_working_days
        self.ladder = pathway.escalation_ladder
        for r in self.ladder:
            for table, name in ((mp.escalation_effect_prob, "orchestration.escalation_effect_prob"),
                                (mp.contact_recovery_prob, "orchestration.contact_recovery_prob")):
                if r.notify not in table:
                    raise ParamError(f"{name} has no entry for escalation role {r.notify!r}")
        if mp.reopen_resume_state not in self.states or mp.reopen_resume_state == self.terminal:
            raise ParamError(f"reopen.resume_state {mp.reopen_resume_state!r} is not a non-terminal pathway state")
        self.heap: list = []
        self.counter = 0
        self.hash = hashlib.sha256()
        self.n_events = 0
        self.handlers = {
            "start": self.on_start, "act": self.on_act, "p_ack": self.on_p_ack, "self_present": self.on_self_present,
            "appt": self.on_appt, "appt_reminder": self.on_appt_reminder, "sla_due": self.on_sla_due,
            "rung": self.on_rung, "ladder_end": self.on_ladder_end, "h_resp": self.on_h_resp,
            "h_timeout": self.on_h_timeout, "h_retry": self.on_h_retry, "deliv_fail": self.on_deliv_fail,
            "recover": self.on_recover, "watch": self.on_watch, "force": self.on_force, "dup": self.on_dup,
            "switch": self.on_switch, "switch_done": self.on_switch_done, "consent": self.on_consent,
            "consent_review": self.on_consent_review, "reopen": self.on_reopen, "self_rebook": self.on_self_rebook,
        }
        self.counters = {"blocked_messages": 0, "timers_missed": 0, "watchdog_flags": 0}

    # ------------------------------------------------------------------------------------------
    # run loop
    # ------------------------------------------------------------------------------------------
    def run(self, patients: list[Patient]) -> RunResult:
        cases: list[Case] = []
        for p in patients:
            lat = draw_latents(self.seed, p.key)
            msy = draw_messy(self.seed, p, self.mp)
            c = Case(p, lat, msy, p.arrival_t + self.days, self.pw.initial_state, self.beh.baseline_channel(lat),
                     lat.u_refuse < self.mp.refusal_prob)
            cases.append(c)
            self.schedule(c.t0, "start", c)
        heap = self.heap
        handlers = self.handlers
        while heap:
            t, _, kind, c, token, data = heappop(heap)
            if t > c.end:
                continue
            handlers[kind](c, t, token, data)
        records = [
            build_record(c.p.idx, c.p.language, c.p.channel, c.msy.tags(), self.days, c.t0, c.log, self.start_dt)
            for c in cases
        ]
        return RunResult(
            self.arm, self.seed, cases, records, self.hash.hexdigest(), self.n_events,
            max((c.end for c in cases), default=0.0), dict(self.counters),
        )

    def schedule(self, t: float, kind: str, c: Case, token: int | None = None, data=None) -> None:
        self.counter += 1
        heappush(self.heap, (t, self.counter, kind, c, token, data))

    # ------------------------------------------------------------------------------------------
    # event construction / logging
    # ------------------------------------------------------------------------------------------
    def _mk(self, c: Case, t: float, etype: str, actor: str, payload: dict | None) -> Event:
        c.seq += 1
        return Event(
            case_id=c.p.case_id,
            type=etype,
            at=self.start_dt + timedelta(days=t),
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
            if self.orch and not c.watch_armed:
                c.watch_armed = True
                self.schedule(t + self.mp.watchdog_idle_days, "watch", c)

    def emit(self, c: Case, t: float, etype: str, actor: str, payload: dict | None = None) -> Event:
        ev = self._mk(c, t, etype, actor, payload)
        self._log(c, ev, t)
        return ev

    def advance(self, c: Case, t: float, etype: str, actor: str, payload: dict | None = None) -> None:
        """Apply a pathway event through the shared state machine, then enter the next step."""
        ev = self._mk(c, t, etype, actor, payload)
        c.state = apply(c.state, ev, self.pw)  # raises InvalidTransition on a bug in the engine
        self._log(c, ev, t)
        if c.state == self.terminal:
            c.step = None
            c.token += 1
            if not c.reopened and c.lat.u_reopen < self.mp.reopen_prob:
                lag = lognormal(self.mp.reopen_lag_median_days, self.mp.reopen_lag_sigma, c.lat.z_reopen)
                self.schedule(t + lag, "reopen", c)
        else:
            self.enter_step(c, t)

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
        if self.orch:
            self.arm_sla(c, t, ev)
        kind = STEP_KIND[ev]
        if kind == "staff":
            self.sched_staff(c, t, ev)
        elif kind == "patient":
            c.awaiting_patient = True  # the outreach was sent by the contact_patient action
        else:
            self.book_appointment(c, t)

    def arm_sla(self, c: Case, t: float, ev: str) -> None:
        sla = self.slas.get(ev)
        if sla is None:
            return
        k = c.timer_n
        c.timer_n += 1
        if k < T_POOL and c.lat.u_timer[k] < self.mp.timer_miss_prob:
            self.counters["timers_missed"] += 1  # the scheduler silently failed to arm this timer
            return
        self.schedule(self.cal.add_working_days_t(t, sla), "sla_due", c, c.token, sla)

    def sched_staff(self, c: Case, t: float, step: str) -> None:
        sd = c.lat.staff[c.round][STAFF_INDEX[step]]
        extra, c.extra_delay = c.extra_delay, 0.0
        if self.beh.staff_forgets(sd.u_forget):
            return  # never done unless a reminder or escalation prompts it (orchestrated only)
        self.schedule(self.cal.next_working_t(t + self.beh.staff_delay(step, sd.z_delay) + extra), "act", c, c.token)

    # ------------------------------------------------------------------------------------------
    # staff actions
    # ------------------------------------------------------------------------------------------
    def on_start(self, c: Case, t: float, token, data) -> None:
        self.emit(c, t, CASE_CREATED, "system:intake", {"pathway": self.pw.name})
        m = c.msy
        if m.duplicate_day is not None:
            self.schedule(t + m.duplicate_day, "dup", c)
        if m.switch_day is not None:
            self.schedule(t + m.switch_day, "switch", c)
        if c.lat.u_consent < self.mp.consent_withdrawal_prob:
            self.schedule(t + c.lat.u_consent_when * self.mp.consent_withdrawal_window_days, "consent", c, None, "generic")
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
            self.start_handoff(c, t)
        elif step == "clinician_review":
            self.advance(c, t, "clinician_review", f"clinician:cl-{i % 5}")
        elif step == "close":
            who = f"clinician:cl-{i % 5}"
            self.advance(c, t, "close", who, {"evidence_ref": f"review-{c.p.key}", "signed_by": who})

    def accept_ownership(self, c: Case, t: float) -> None:
        i = c.p.idx
        m = c.msy
        if self.orch:
            owner = f"owner:pcp-{i % 7}"
            c.owner = owner
            self.advance(c, t, "accept_ownership", owner, {"owner": owner})
            if m.conflicting_ack:
                other = f"owner:other-{i % 5}"
                ev2 = self._mk(c, t, "accept_ownership", other, {"owner": other})
                c.seq -= 1  # a rejected claim is not part of the case log
                try:
                    apply(c.state, ev2, self.pw)
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
    # timers, ladder, watchdog (orchestrated)
    # ------------------------------------------------------------------------------------------
    def on_sla_due(self, c: Case, t: float, token, sla) -> None:
        if token != c.token or c.abandoned:
            return
        self.emit(c, t, "overdue", "scheduler", {"step": c.step, "sla_working_days": sla})
        self.start_ladder(c, t)

    def start_ladder(self, c: Case, t: float) -> None:
        last = t
        for i, rung in enumerate(self.ladder):
            last = self.cal.add_working_days_t(t, rung.after_breach_days)
            self.schedule(last, "rung", c, c.token, i)
        self.schedule(self.cal.add_working_days_t(last, self.mp.ladder_exhausted_wait_working_days), "ladder_end", c, c.token)

    def on_rung(self, c: Case, t: float, token, i: int) -> None:
        if token != c.token or c.abandoned:
            return
        rung = self.ladder[i]
        if rung.action == "escalate":
            self.emit(c, t, "escalated", "scheduler", {"rung": i, "notify": rung.notify, "step": c.step})
        else:
            self.emit(c, t, "reminder_sent", "scheduler", {"rung": i, "notify": rung.notify, "step": c.step})
        if c.awaiting_patient:
            kind = ROLE_KIND.get(rung.notify, "reminder" if i == 0 else "human_call")
            self.send_outreach(c, t, kind, rung.notify)
        elif c.step == "book_confirmatory" and c.hstate != "staff":
            if c.hstate == "pending" and i >= 1:
                self.handoff_bounce(c, t, "escalation")
        elif c.step is not None and STEP_KIND[c.step] == "staff":
            sd = c.lat.staff[c.round][STAFF_INDEX[c.step]]
            j = min(i, N_RUNGS - 1)
            if sd.u_eff[j] < self.mp.escalation_effect_prob[rung.notify]:
                self.schedule(self.cal.next_working_t(t + self.beh.react_delay(sd.z_react[j])), "act", c, c.token)

    def on_ladder_end(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.abandoned:
            return
        if c.awaiting_patient:
            self.flag_lost(c, t, "ladder_exhausted")

    def flag_lost(self, c: Case, t: float, reason: str) -> None:
        if reason in c.lost_reasons:
            return
        c.lost_reasons.append(reason)
        self.emit(c, t, "lost_contact", "scheduler" if self.orch else "system:intake", {"reason": reason})

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
        if c.awaiting_patient:
            self.send_outreach(c, t, "coordinator_route", "programme_coordinator")
        elif c.step == "book_confirmatory" and c.hstate != "staff":
            if c.hstate == "pending":
                self.handoff_bounce(c, t, "watchdog")
        elif STEP_KIND[c.step] == "staff":
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
        if self.orch:
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
            if self.orch:
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
        elif c.step == "complete_confirmatory" and c.sub == "rebook":
            self.rebook(c, t, "outreach")

    def on_self_present(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.step != "patient_acknowledge" or c.declined_care or c.abandoned:
            return
        self.advance(c, t, "patient_acknowledge", f"patient:{c.p.key}", {"via": "self_presented"})

    # ------------------------------------------------------------------------------------------
    # referral handoff
    # ------------------------------------------------------------------------------------------
    def start_handoff(self, c: Case, t: float) -> None:
        h = c.h_idx
        if h >= min(H_POOL, self.mp.handoff_max_attempts):
            c.hstate = "bounced"
            self.flag_lost(c, t, "referral_attempts_exhausted")
            return
        c.h_idx += 1
        c.h_cur = h
        c.hstate = "pending"
        who = f"owner:pcp-{c.p.idx % 7}" if self.orch else f"staff:referrals-{c.p.idx % 3}"
        self.emit(c, t, "handoff_requested", who, {"attempt": h, "to": f"endoscopy-{h}"})
        outcome = self.beh.handoff_outcome(c.lat.u_h[h])
        if outcome != "none":
            reason = self.beh.rejection_reason(c.lat.u_reason[h]) if outcome == "reject" else None
            at = self.cal.next_working_t(t + self.beh.handoff_delay(c.lat.z_h[h]))
            self.schedule(at, "h_resp", c, c.token, (h, outcome, reason))
        if self.orch:
            self.schedule(self.cal.add_working_days_t(t, self.mp.handoff_sla_working_days), "h_timeout", c, c.token, h)

    def on_h_resp(self, c: Case, t: float, token, data) -> None:
        h, outcome, reason = data
        if token != c.token or c.hstate != "pending" or c.h_cur != h or c.abandoned:
            return
        if outcome == "accept":
            self.emit(c, t, "handoff_accepted", f"endoscopy:unit-{h}", {"attempt": h})
            lead = self.beh.lead_days(c.lat, c.appt_idx, False)
            c.pending_appt = self.cal.next_working_t(t + lead)
            who = f"owner:pcp-{c.p.idx % 7}" if self.orch else f"staff:referrals-{c.p.idx % 3}"
            self.advance(c, t, "book_confirmatory", who, {"appointment_on": self.cal.date_of(c.pending_appt).isoformat()})
            if c.msy.consent_after_booking_lag is not None and not c.booked_once:
                self.schedule(t + c.msy.consent_after_booking_lag, "consent", c, None, "after_booking")
            c.booked_once = True
            return
        c.hstate = "bounced"
        self.emit(c, t, "handoff_rejected", f"endoscopy:unit-{h}", {"attempt": h, "reason": reason})
        lat = c.lat
        if self.orch:
            if reason == "patient_declined":
                if lat.u_declined[h] < self.mp.declined_reengage_prob and c.consent_ok:
                    self.schedule(self.cal.next_working_t(t + self.beh.reroute_delay(lat.z_reroute[h])), "h_retry", c, c.token)
                else:
                    self.flag_lost(c, t, "patient_declined")
            else:  # capacity / wrong_service: re-route to another service; missing_info: complete and resubmit
                self.schedule(self.cal.next_working_t(t + self.beh.reroute_delay(lat.z_reroute[h])), "h_retry", c, c.token)
        elif lat.u_notice[h] < self.mp.baseline_rejection_noticed_prob:
            d = lognormal(self.mp.baseline_rejection_notice_median_days, self.mp.reaction_delay_sigma, lat.z_notice[h])
            self.schedule(t + d, "h_retry", c, c.token)
        # else: the rejection is never noticed and the referral is silently lost

    def on_h_timeout(self, c: Case, t: float, token, h) -> None:
        if token != c.token or c.hstate != "pending" or c.h_cur != h or c.abandoned:
            return
        self.handoff_bounce(c, t, "timeout")

    def handoff_bounce(self, c: Case, t: float, via: str) -> None:
        c.hstate = "bounced"
        self.emit(c, t, "overdue", "scheduler", {"reason": "handoff_unanswered", "attempt": c.h_cur, "via": via})
        h = max(c.h_cur, 0)
        self.schedule(self.cal.next_working_t(t + self.beh.reroute_delay(c.lat.z_reroute[h])), "h_retry", c, c.token)

    def on_h_retry(self, c: Case, t: float, token, data) -> None:
        if token != c.token or c.abandoned or c.step != "book_confirmatory":
            return
        self.start_handoff(c, t)

    # ------------------------------------------------------------------------------------------
    # appointment
    # ------------------------------------------------------------------------------------------
    def book_appointment(self, c: Case, t: float) -> None:
        a = c.appt_idx
        appt_t = c.pending_appt
        if appt_t is None:
            appt_t = self.cal.next_working_t(t + self.beh.lead_days(c.lat, a, False))
        c.pending_appt = None
        c.appt_t = appt_t
        c.reminders = 0
        self.schedule(appt_t, "appt", c, c.token, a)
        if self.orch:
            for d in self.mp.reminder_days_before:
                tr = appt_t - d
                if tr > t:
                    self.schedule(tr, "appt_reminder", c, c.token)

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
        channel = c.p.channel if self.orch else c.bchannel
        attends = not c.declined_care and not self.beh.no_show(c.p, c.lat, a, channel, c.reminders, self.orch)
        if attends:
            self.advance(c, t, "complete_confirmatory", "endoscopy:unit",
                         {"appointment": a, "evidence_ref": f"procedure-{c.p.key}-{a}"})
            return
        self.emit(c, t, "appointment_missed", "endoscopy:unit", {"appointment": a})
        self.lapse(c)
        more = (a + 1) < min(A_POOL, self.mp.appointment_max_attempts) and not c.declined_care
        if self.orch:
            self.emit(c, t, "overdue", "scheduler", {"reason": "missed_appointment", "step": c.step})
            if more:
                c.token += 1  # a new sub-flow: the old SLA timer and rungs no longer apply
                c.awaiting_patient = True
                c.sub = "rebook"
                self.start_ladder(c, t)
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
        c.pending_appt = self.cal.next_working_t(t + self.beh.lead_days(c.lat, a, True))
        self.book_appointment(c, t)

    # ------------------------------------------------------------------------------------------
    # messy cases, consent, reopen
    # ------------------------------------------------------------------------------------------
    def on_dup(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.state == self.terminal:
            return
        if self.orch:
            self.emit(c, t, "duplicate_result_merged", "system:dedup", {"matched": True})
        else:
            self.emit(c, t, "duplicate_result_received", "lab:second", {"new_work_item": True})

    def _pre_booking(self, c: Case) -> bool:
        return self.states.index(c.state) < self.idx_booked

    def _reenter_staff_step(self, c: Case) -> bool:
        return c.step is not None and STEP_KIND[c.step] == "staff" and (c.step != "book_confirmatory" or c.hstate == "staff")

    def on_switch(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.state == self.terminal:
            return
        if not self._pre_booking(c):
            self.emit(c, t, "provider_switched", "patient:" + c.p.key, {"applies": False})
            return
        lat = c.lat
        if self.orch:
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
        if self.orch:
            c.consent_ok = False
            self.emit(c, t, "consent_updated", f"patient:{c.p.key}",
                      {"status": "withdrawn", "scope": "outreach", "flagged_for_review": True, "source": source})
            d = lognormal(self.mp.consent_review_delay_median_days, self.mp.reaction_delay_sigma, c.lat.z_consent_review)
            self.schedule(self.cal.next_working_t(t + d), "consent_review", c)
        else:
            self.emit(c, t, "consent_updated", f"patient:{c.p.key}",
                      {"status": "withdrawn", "scope": "outreach", "recorded": False, "source": source})

    def on_consent_review(self, c: Case, t: float, token, data) -> None:
        if c.abandoned:
            return
        outcome = "declined_care" if c.declined_care else "continues_without_outreach"
        self.emit(c, t, "consent_review_completed", f"owner:pcp-{c.p.idx % 7}", {"outcome": outcome})
        if c.declined_care and c.state != self.terminal:
            self.flag_lost(c, t, "declined_care")

    def on_reopen(self, c: Case, t: float, token, data) -> None:
        if c.abandoned or c.state != self.terminal:
            return
        resume = self.mp.reopen_resume_state
        self.emit(c, t, "reopened", "clinician:late-finding", {"resume_state": resume})
        c.state = resume
        c.reopened = True
        c.round = 1
        c.lost_reasons = []
        self.enter_step(c, t)


def make_engine(arm: str, mp: ModelParams, pathway: Pathway, cal: WorkingCalendar, days: float, seed: int) -> Engine:
    return Engine(arm, mp, pathway, cal, days, seed)
