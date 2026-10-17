"""What the risk score reads from a case, and how it is encoded (D8).

One feature set serves two callers that must never drift apart:

* the simulator's exporter (`simulator/sim/export_training.py`), which builds `CaseFeatures` from a
  simulated event log cut at a snapshot day, and
* the API (`app.risk.score.score_case`), which builds them from a case's read model and events.

Both go through `summarise_events`, which understands the event names of both worlds (the API
logs `escalate`, the simulator logs `escalated`; see the tuples below). A parity test
(`api/tests/test_risk_features.py`) feeds the same story in both vocabularies and requires the same
features.

Features are the ones observable while the case is open and before a colonoscopy is done:
language, preferred channel, age band, contact flag, pathway stage, days since the result,
prior escalations, prior handoff bounces, consent withdrawal, provider change. Missing values
(the API has no registry of patient attributes yet) are imputed with the training mean, which
adds exactly zero to the logit relative to an average patient (see `score.py`).

Pure standard library: no pydantic, no numpy, nothing that reads a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

LANGUAGES = ("ar", "en", "other")
CHANNELS = ("sms", "whatsapp", "phone", "letter")
AGE_BANDS = ("40-49", "50-59", "60-69", "70-75")
# The stages at which a colonoscopy has not yet happened. Later states are not at risk of this loss.
STAGES = (
    "result_received", "acknowledged", "owner_accepted", "patient_contacted", "patient_acknowledged",
    "confirmatory_booked",
)
DAYS_CAP = 90.0   # days since the result beyond which the feature stops growing (training saw up to this)
COUNT_CAP = 3     # prior escalations and bounces are capped here

# Side states of the API pathway mapped to the stage the case is really at. The simulator never
# leaves the main chain (its side events are overlays), so these map to what it would have logged.
SIDE_STATE_STAGE = {"referral_rejected": "patient_acknowledged", "lost_contact": "patient_contacted"}

# Event vocabularies: API name, simulator name.
ESCALATION_EVENTS = ("escalate", "escalated")
HANDOFF_REJECTED = "handoff_rejected"
HANDOFF_ACCEPTED = "handoff_accepted"
BOUNCE_OVERDUE_REASON = "handoff_unanswered"  # simulator: a timed-out referral is logged as `overdue` with this reason
REASSIGN = "reassign"

# The columns of the design matrix, in order. (feature, kind, level)
COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    *(("language", "onehot", v) for v in LANGUAGES),
    *(("channel", "onehot", v) for v in CHANNELS),
    *(("age_band", "onehot", v) for v in AGE_BANDS),
    ("contact_valid", "bool", None),
    *(("stage", "onehot", v) for v in STAGES),
    ("days_since_result", "numeric", None),
    ("prior_escalations", "numeric", None),
    ("prior_bounces", "numeric", None),
    ("consent_withdrawn", "bool", None),
    ("provider_changed", "bool", None),
)
FEATURES = tuple(dict.fromkeys(f for f, _k, _l in COLUMNS))


def column_name(feature: str, kind: str, level: str | None) -> str:
    return f"{feature}={level}" if kind == "onehot" else feature


COLUMN_NAMES = tuple(column_name(*c) for c in COLUMNS)


@dataclass(frozen=True, slots=True)
class CaseFeatures:
    """One case at one moment. `None` = not known: the encoder imputes the training mean."""

    stage: str
    days_since_result: float
    prior_escalations: int = 0
    prior_bounces: int = 0
    consent_withdrawn: bool = False
    provider_changed: bool = False
    contact_valid: bool | None = None
    language: str | None = None
    channel: str | None = None
    age_band: str | None = None

    def value_of(self, feature: str):
        return getattr(self, feature)


def cap_days(days: float) -> float:
    return min(max(float(days), 0.0), DAYS_CAP)


def cap_count(n: int) -> int:
    return min(max(int(n), 0), COUNT_CAP)


def age_band_of(age: int) -> str | None:
    """The band of an age in years, or None outside the screening range."""
    for band in AGE_BANDS:
        lo, hi = band.split("-")
        if int(lo) <= age <= int(hi):
            return band
    return None


def stage_of(state: str, resume_state: str | None = None) -> str | None:
    """The at-risk stage a pathway state stands for, or None when the colonoscopy is done or
    the case is closed (nothing left to lose at this step)."""
    if state == "consent_withdrawn":
        state = resume_state or ""
    state = SIDE_STATE_STAGE.get(state, state)
    return state if state in STAGES else None


def _contact_valid(raw) -> bool | None:
    """The genesis payload's `contact_valid`: a bool, or a per-channel map (any False = a known
    problem), or absent."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, dict) and raw:
        return all(v is not False for v in raw.values())
    return None


def contact_valid_of(genesis_payload: dict | None) -> bool | None:
    return _contact_valid((genesis_payload or {}).get("contact_valid"))


@dataclass(frozen=True, slots=True)
class EventSummary:
    prior_escalations: int
    prior_bounces: int
    consent_withdrawn: bool
    provider_changed: bool
    language_hint: str | None
    channel_hint: str | None


def summarise_events(events) -> EventSummary:
    """Counts and flags from an event list (objects with `.type` and `.payload`), in either the
    API's or the simulator's vocabulary. Only what the list contains is counted: cut the list at a
    snapshot to get the features as of then."""
    escalations = bounces = accepted = reassigned = 0
    transfer = False
    withdrawn = False
    language = channel = None
    for ev in events:
        t, p = ev.type, ev.payload or {}
        if t in ESCALATION_EVENTS:
            escalations += 1
        elif t == HANDOFF_REJECTED:
            bounces += 1
        elif t == "overdue" and p.get("reason") == BOUNCE_OVERDUE_REASON:
            bounces += 1
        elif t == HANDOFF_ACCEPTED:
            accepted += 1
            transfer = transfer or p.get("kind") == "transfer"
        elif t == REASSIGN:
            reassigned += 1
        elif t == "consent_updated":
            if p.get("withdrawn") is True or (p.get("status") == "withdrawn" and p.get("recorded") is not False):
                withdrawn = True
            elif p.get("withdrawn") is False:
                withdrawn = False
        elif t == "patient_acknowledge" and p.get("lang") in ("ar", "en"):
            language = p["lang"]
        elif t == "contact_patient" and p.get("channel") in CHANNELS:
            channel = p["channel"]
    return EventSummary(
        prior_escalations=cap_count(escalations),
        prior_bounces=cap_count(bounces),
        consent_withdrawn=withdrawn,
        provider_changed=transfer or reassigned > 0 or accepted >= 2,
        language_hint=language,
        channel_hint=channel,
    )


def features_from_case(
    model, events, created_at: datetime, now: datetime, attributes: dict | None = None
) -> CaseFeatures | None:
    """Features of an API case. `model` is the case's read model (`app.state_machine.CaseModel`),
    `events` its log, `created_at` the genesis time (result receipt), `now` the simulation clock.

    `attributes` optionally carries registry values for `language`, `channel`, `age_band`
    (and `contact_valid` to override the genesis flag); without them language and channel fall back to
    what the log shows, and the rest stay unknown.

    Returns None when the colonoscopy is already done or the case closed: no loss left to predict.
    """
    stage = stage_of(model.state, model.resume_state)
    if stage is None:
        return None
    attrs = attributes or {}
    s = summarise_events(events)
    genesis = events[0].payload if events else {}
    return CaseFeatures(
        stage=stage,
        days_since_result=cap_days((now - created_at).total_seconds() / 86400.0),
        prior_escalations=s.prior_escalations,
        prior_bounces=s.prior_bounces,
        consent_withdrawn=s.consent_withdrawn,
        provider_changed=s.provider_changed,
        contact_valid=attrs["contact_valid"] if "contact_valid" in attrs else contact_valid_of(genesis),
        language=attrs.get("language") or s.language_hint,
        channel=attrs.get("channel") or s.channel_hint,
        age_band=attrs.get("age_band"),
    )
