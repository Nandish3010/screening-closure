"""Patient and staff behaviour.

All randomness is pre-drawn per patient into `Latents` in a FIXED layout (common random numbers):
the k-th staff delay, the k-th outreach response, the k-th appointment outcome ... are the same
numbers in every arm. The `Behaviour` methods map (latent draw, context, parameters) to an
outcome, so changing a parameter changes outcomes without re-drawing, and an arm that makes
fewer or more attempts simply consumes more or fewer entries of the same pools.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .params import ModelParams
from .patients import Patient
from .rng import stream

STAFF_STEPS = ("acknowledge", "accept_ownership", "contact_patient", "book_confirmatory", "clinician_review", "close")
N_ROUNDS = 2  # staff draws for the first pass and for the pass after a reopen
N_RUNGS = 3
R_POOL = 6  # outreach attempts per patient
K_POOL = 4  # contact-recovery attempts
A_POOL = 4  # appointments
H_POOL = 5  # referral attempts
D_POOL = 8  # lapses
T_POOL = 16  # scheduler timers
M_POOL = 4  # milestone timers (A18)
W_POOL = 8  # watchdog flags

REJECTION_REASONS = ("capacity", "wrong_service", "missing_info", "patient_declined")


@dataclass(slots=True)
class StaffDraw:
    z_delay: float
    u_forget: float
    u_eff: list
    z_react: list


class Latents:
    __slots__ = (
        "staff", "u_resp", "z_resp", "u_rec", "z_rec", "u_noshow", "z_lead", "u_selfrebook", "z_selfrebook",
        "u_h", "u_reason", "z_h", "u_notice", "z_notice", "u_declined", "z_reroute", "u_drop",
        "u_self", "z_self", "u_consent", "u_consent_when", "u_consent_declines", "z_consent_review",
        "u_reopen", "z_reopen", "u_timer", "u_watch", "z_watch", "u_bchannel",
        "u_switch_loss", "u_switch_rej", "z_switch", "u_switch_reason", "z_review", "u_refuse", "u_reach", "u_mtimer", "u_backfill",
        "u_lane",
    )


def draw_latents(seed: int, key: str) -> Latents:
    rng = stream(seed, key, "behaviour")
    r, g = rng.random, rng.gauss
    L = Latents()
    L.staff = [
        [StaffDraw(g(0, 1), r(), [r() for _ in range(N_RUNGS)], [g(0, 1) for _ in range(N_RUNGS)]) for _ in STAFF_STEPS]
        for _ in range(N_ROUNDS)
    ]
    L.u_resp = [r() for _ in range(R_POOL)]
    L.z_resp = [g(0, 1) for _ in range(R_POOL)]
    L.u_rec = [r() for _ in range(K_POOL)]
    L.z_rec = [g(0, 1) for _ in range(K_POOL)]
    L.u_noshow = [r() for _ in range(A_POOL)]
    L.z_lead = [g(0, 1) for _ in range(A_POOL)]
    L.u_selfrebook = [r() for _ in range(A_POOL)]
    L.z_selfrebook = [g(0, 1) for _ in range(A_POOL)]
    L.u_h = [r() for _ in range(H_POOL)]
    L.u_reason = [r() for _ in range(H_POOL)]
    L.z_h = [g(0, 1) for _ in range(H_POOL)]
    L.u_notice = [r() for _ in range(H_POOL)]
    L.z_notice = [g(0, 1) for _ in range(H_POOL)]
    L.u_declined = [r() for _ in range(H_POOL)]
    L.z_reroute = [g(0, 1) for _ in range(H_POOL)]
    L.u_drop = [r() for _ in range(D_POOL)]
    L.u_self, L.z_self = r(), g(0, 1)
    L.u_consent, L.u_consent_when, L.u_consent_declines, L.z_consent_review = r(), r(), r(), g(0, 1)
    L.u_reopen, L.z_reopen = r(), g(0, 1)
    L.u_timer = [r() for _ in range(T_POOL)]
    L.u_watch = [r() for _ in range(W_POOL)]
    L.z_watch = [g(0, 1) for _ in range(W_POOL)]
    L.u_bchannel = r()
    L.u_switch_loss, L.u_switch_rej, L.z_switch, L.u_switch_reason = r(), r(), g(0, 1), r()
    L.z_review = g(0, 1)
    L.u_refuse = r()
    L.u_reach = r()  # drawn last so every earlier draw (and every existing result) is unchanged
    L.u_mtimer = [r() for _ in range(M_POOL)]  # A18: after u_reach for the same reason
    L.u_backfill = [r() for _ in range(A_POOL)]  # B19: is the slot of a no-show refilled? Last, so every earlier draw is unchanged
    L.u_lane = r()  # B21: fast or slow booking lane (timing mixture). Last, so every earlier draw is unchanged
    return L


def shift_logit(p: float, x: float) -> float:
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-(math.log(p / (1.0 - p)) + x)))


def lognormal(median: float, sigma: float, z: float) -> float:
    return median * math.exp(sigma * z)


HUMAN_KINDS = ("human_call", "coordinator_route")


class Behaviour:
    def __init__(self, mp: ModelParams):
        self.mp = mp

    # ---- staff ----------------------------------------------------------------------------
    def staff_delay(self, step: str, z: float) -> float:
        return lognormal(self.mp.staff_delay_median[step], self.mp.staff_delay_sigma, z)

    def staff_forgets(self, u: float) -> bool:
        return u < self.mp.staff_forget_prob

    def react_delay(self, z: float) -> float:
        return lognormal(self.mp.reaction_delay_median_days, self.mp.reaction_delay_sigma, z)

    # ---- patient response to one outreach attempt ---------------------------------------------
    def response_delay(
        self, p: Patient, lat: Latents, idx: int, channel: str, language: str, kind: str, dropped: bool
    ) -> float | None:
        """Days until the patient acts on this attempt, or None if they never do."""
        if idx >= R_POOL:
            return None
        mp = self.mp
        if kind == "human_call":
            base = mp.staff_call_response_rate
        elif kind == "coordinator_route":
            base = mp.coordinator_route_response_rate
        else:
            base = mp.response_rate[channel]
        if language != p.language:
            base *= mp.language_mismatch_multiplier
        prob = shift_logit(base, mp.responsiveness_sd * p.responsiveness)
        if dropped:
            prob *= mp.human_dropout_residual_multiplier if kind in HUMAN_KINDS else mp.dropout_residual_multiplier
        if lat.u_resp[idx] >= prob:
            return None
        median = mp.response_delay_median["phone" if kind in HUMAN_KINDS else channel]
        return lognormal(median, mp.response_delay_sigma, lat.z_resp[idx])

    def self_presentation_delay(self, lat: Latents) -> float | None:
        if lat.u_self >= self.mp.self_presentation_prob:
            return None
        return lognormal(self.mp.self_presentation_median_days, self.mp.response_delay_sigma, lat.z_self)

    # ---- appointments ----------------------------------------------------------------------
    def no_show(self, p: Patient, lat: Latents, a: int, channel: str, reminders: int, managed: bool) -> bool:
        """Baseline: the published non-attendance for the channel used (no reminders). Managed
        (orchestrated): the no-reminder rate `dna_rate` reduced by `sms_reminder_effect` per reminder."""
        mp = self.mp
        if managed:
            base = mp.dna_rate * (1.0 - mp.sms_reminder_effect) ** reminders
        else:
            base = mp.no_show_rate[channel]
        prob = shift_logit(base, -mp.responsiveness_noshow_shift * p.responsiveness)
        return lat.u_noshow[a] < prob

    def lead_days(self, lat: Latents, a: int, rebook: bool, fast: bool) -> float:
        """Days from the booking decision to the appointment (B21 timing mixture). A first booking is drawn from the
        case's lane: the fast lane (booked promptly, the managed-programme wait) or the slow lane (unmanaged, heavy-tailed
        delay). A rebooking after a no-show uses `rebook_lead_median_days` with the fast-lane spread in both lanes."""
        mp = self.mp
        if rebook:
            return lognormal(mp.rebook_lead_median_days, mp.fast_lane_lead_sigma, lat.z_lead[a])
        if fast:
            return lognormal(mp.fast_lane_lead_median_days, mp.fast_lane_lead_sigma, lat.z_lead[a])
        return lognormal(mp.slow_lane_lead_median_days, mp.slow_lane_lead_sigma, lat.z_lead[a])

    def fast_lane(self, lat: Latents, managed: bool) -> bool:
        """Is this case in the fast booking lane? The share depends on whether the pathway is programme-managed (B21)."""
        mp = self.mp
        return lat.u_lane < (mp.fast_lane_share_managed if managed else mp.fast_lane_share_unmanaged)

    # ---- referral / handoff ----------------------------------------------------------------------
    def handoff_outcome(self, u: float) -> str:
        mp = self.mp
        if u < mp.handoff_no_response_prob:
            return "none"
        if u < mp.handoff_no_response_prob + mp.referral_rejection_rate:
            return "reject"
        return "accept"

    def rejection_reason(self, u: float) -> str:
        acc = 0.0
        for name, w in self.mp.rejection_reason_share:
            acc += w
            if u < acc:
                return name
        return self.mp.rejection_reason_share[-1][0]

    def handoff_delay(self, z: float) -> float:
        return lognormal(self.mp.handoff_delay_median_days, self.mp.handoff_delay_sigma, z)

    def reroute_delay(self, z: float) -> float:
        return lognormal(self.mp.reroute_delay_median_days, self.mp.reaction_delay_sigma, z)

    # ---- misc ----------------------------------------------------------------------------------
    def baseline_channel(self, lat: Latents) -> str:
        acc = 0.0
        for name, w in self.mp.baseline_channel_share:
            acc += w
            if lat.u_bchannel < acc:
                return name
        return self.mp.baseline_channel_share[-1][0]

    def drops_out(self, lat: Latents, lapse_index: int) -> bool:
        return lapse_index < D_POOL and lat.u_drop[lapse_index] < self.mp.dropout_prob_per_lapse
