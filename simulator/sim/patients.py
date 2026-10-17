"""Synthetic patient attributes that drive behaviour. Each patient is seeded from a uuid5 of
(base seed, patient key), so adding patients never changes the attributes of existing ones."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .calendar import WorkingCalendar
from .params import ModelParams
from .rng import derive_uuid, stream

LANGUAGES = ("ar", "en", "other")
CHANNELS = ("sms", "whatsapp", "phone", "letter")


@dataclass(frozen=True, slots=True)
class Patient:
    idx: int
    key: str
    case_id: uuid.UUID
    language: str
    channel: str  # preferred channel (used by the orchestrated arm)
    contact_valid: bool  # False = the number/address on file is wrong
    provider_switch: bool  # will move to another provider mid-pathway
    responsiveness: float  # latent standard-normal factor; scaled by patients.responsiveness_sd
    arrival_t: float  # simulation day on which the positive FIT result arrives


def _pick(u: float, shares: tuple[tuple[str, float], ...]) -> str:
    acc = 0.0
    for name, w in shares:
        acc += w
        if u < acc:
            return name
    return shares[-1][0]


def patient_key(idx: int) -> str:
    return f"P{idx:06d}"


def make_patient(idx: int, seed: int, mp: ModelParams, cal: WorkingCalendar) -> Patient:
    key = patient_key(idx)
    rng = stream(seed, key, "attributes")
    u_lang, u_chan, u_contact, u_switch, z_resp, u_arrival = (
        rng.random(), rng.random(), rng.random(), rng.random(), rng.gauss(0.0, 1.0), rng.random(),
    )
    day = int(u_arrival * mp.intake_window_days)
    return Patient(
        idx=idx,
        key=key,
        case_id=derive_uuid(seed, "case", key),
        language=_pick(u_lang, mp.language_share),
        channel=_pick(u_chan, mp.channel_share),
        contact_valid=u_contact >= mp.wrong_contact_rate,
        provider_switch=u_switch < mp.provider_switch_prob,
        responsiveness=z_resp,
        arrival_t=cal.next_working_t(day + 0.35),
    )


def make_cohort(n: int, seed: int, mp: ModelParams, cal: WorkingCalendar) -> list[Patient]:
    return [make_patient(i, seed, mp, cal) for i in range(n)]
