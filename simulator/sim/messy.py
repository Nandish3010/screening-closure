"""Messy-case injection. A `MessyPlan` says which messy things happen to a case and when; the engine
executes them in both arms (same plan, common random numbers) and each arm handles them its own way.

  wrong_contact         the number/address on file is wrong (rate: wrong_contact_rate; a patient attribute)
  provider_switch       the patient moves to another provider mid-pathway (patients.provider_switch_prob)
  duplicate_result      a second lab reports the same FIT result
  conflicting_ack       a second party also claims ownership
  consent_after_booking consent for outreach is withdrawn shortly after the colonoscopy is booked
"""

from __future__ import annotations

from dataclasses import dataclass

from .params import ModelParams
from .patients import Patient
from .rng import stream

MESSY_TAGS = ("wrong_contact", "provider_switch", "duplicate_result", "conflicting_ack", "consent_after_booking")


@dataclass(frozen=True, slots=True)
class MessyPlan:
    wrong_contact: bool
    switch_day: float | None  # days after the result arrives
    duplicate_day: float | None
    conflicting_ack: bool
    consent_after_booking_lag: float | None  # days after booking

    def tags(self) -> tuple[str, ...]:
        out = []
        if self.wrong_contact:
            out.append("wrong_contact")
        if self.switch_day is not None:
            out.append("provider_switch")
        if self.duplicate_day is not None:
            out.append("duplicate_result")
        if self.conflicting_ack:
            out.append("conflicting_ack")
        if self.consent_after_booking_lag is not None:
            out.append("consent_after_booking")
        return tuple(out)


def draw_messy(seed: int, p: Patient, mp: ModelParams) -> MessyPlan:
    rng = stream(seed, p.key, "messy")
    u_dup, u_dup_day, u_conf, u_cab, u_cab_lag, u_sw_day = (rng.random() for _ in range(6))
    m = mp.messy_multiplier
    return MessyPlan(
        wrong_contact=not p.contact_valid,
        switch_day=(5.0 + u_sw_day * max(mp.switch_window_days - 5.0, 0.0)) if p.provider_switch else None,
        duplicate_day=(1.0 + u_dup_day * max(mp.duplicate_delay_max_days - 1.0, 0.0))
        if u_dup < mp.duplicate_result_rate * m
        else None,
        conflicting_ack=u_conf < mp.conflicting_ack_rate * m,
        consent_after_booking_lag=(1.0 + u_cab_lag * 9.0) if u_cab < mp.consent_after_booking_rate * m else None,
    )
