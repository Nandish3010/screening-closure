"""D8/D9: the feature extraction that both the simulator's exporter and the API use. The same story in the
API's event vocabulary and the simulator's must give the same features (no train/serve skew)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.events import Event
from app.risk import features as F
from app.state_machine import CaseModel

T0 = datetime(2026, 1, 5, 8, tzinfo=UTC)
CID = uuid4()


def ev(type_, payload=None, day=0.0, actor="x:y"):
    return Event(CID, type_, T0 + timedelta(days=day), actor, payload or {})


# One story told twice: escalated twice; one referral bounced by timeout and one rejected by a human; a provider change; consent withdrawn.
API_STORY = [
    ev("case_created", {"patient_ref": "Patient/1", "contact_valid": False}),
    ev("acknowledge", day=1), ev("accept_ownership", day=2), ev("contact_patient", {"channel": "sms"}, day=3),
    ev("mark_overdue", {"state": "patient_contacted", "due_at": "2026-01-14T08:00:00+00:00"}, day=10),
    ev("escalate", {"rung": "reminder", "state": "patient_contacted"}, day=10),
    ev("escalate", {"rung": "supervisor", "state": "patient_contacted"}, day=13),
    ev("patient_acknowledge", {"lang": "ar"}, day=14),
    ev("handoff_requested", {"to_provider": "endo-1"}, day=15),
    ev("handoff_rejected", {"reason": "timeout", "bounced": True}, day=19),
    ev("handoff_requested", {"to_provider": "endo-2"}, day=20),
    ev("handoff_rejected", {"reason": "capacity"}, day=21),
    ev("handoff_requested", {"to_provider": "endo-3"}, day=22),
    ev("handoff_accepted", {"provider": "endo-3"}, day=23),
    ev("handoff_accepted", {"provider": "endo-4"}, day=24),   # the second acceptance: a different provider
    ev("consent_updated", {"withdrawn": True}, day=25),
]
SIM_STORY = [
    ev("case_created", {"pathway": "colorectal_fit"}),
    ev("acknowledge", day=1), ev("accept_ownership", {"owner": "owner:pcp-1"}, day=2), ev("contact_patient", day=3),
    ev("message_sent", {"kind": "initial", "channel": "sms", "language": "ar"}, day=3),
    ev("overdue", {"step": "patient_acknowledge", "sla_working_days": 7}, day=10),
    ev("reminder_sent", {"rung": 0}, day=10),
    ev("escalated", {"rung": 1, "notify": "owner_supervisor", "step": "patient_acknowledge"}, day=13),
    ev("escalated", {"rung": 2, "notify": "programme_coordinator", "step": "patient_acknowledge"}, day=17),
    ev("patient_acknowledge", {"via": "reply"}, day=14),
    ev("handoff_requested", {"attempt": 0, "to": "endoscopy-0"}, day=15),
    ev("overdue", {"reason": "handoff_unanswered", "attempt": 0, "via": "timeout"}, day=19),
    ev("handoff_rejected", {"attempt": 1, "reason": "capacity"}, day=21),
    ev("handoff_accepted", {"kind": "transfer", "owner": "owner:new-1"}, day=23),
    ev("consent_updated", {"status": "withdrawn", "scope": "outreach", "flagged_for_review": True}, day=25),
]


def test_the_same_story_in_both_vocabularies_gives_the_same_counts_and_flags():
    a, b = F.summarise_events(API_STORY), F.summarise_events(SIM_STORY)
    assert (a.prior_escalations, a.prior_bounces, a.consent_withdrawn, a.provider_changed) == (2, 2, True, True)
    assert (b.prior_escalations, b.prior_bounces, b.consent_withdrawn, b.provider_changed) == (2, 2, True, True)


def test_counts_are_capped_and_a_snapshot_cut_hides_the_future():
    many = [ev("escalate", {"rung": "r", "state": "s"}, day=i) for i in range(9)]
    assert F.summarise_events(many).prior_escalations == F.COUNT_CAP
    assert F.summarise_events(API_STORY[:5]).prior_escalations == 0  # cut before the first escalation


def test_consent_restored_clears_the_flag_and_unrecorded_withdrawal_is_not_visible():
    assert F.summarise_events([ev("consent_updated", {"withdrawn": True}), ev("consent_updated", {"withdrawn": False})]).consent_withdrawn is False
    # the simulator's baseline-rules cases never record a withdrawal: the system cannot know
    assert F.summarise_events([ev("consent_updated", {"status": "withdrawn", "recorded": False})]).consent_withdrawn is False
    assert F.summarise_events([ev("consent_updated", {"channel_sms": False})]).consent_withdrawn is False


def test_features_from_case_maps_states_and_reads_the_genesis_flag():
    now = T0 + timedelta(days=30)
    m = CaseModel(state="patient_contacted")
    f = F.features_from_case(m, API_STORY, T0, now)
    assert f.stage == "patient_contacted" and f.days_since_result == 30.0
    assert f.contact_valid is False and f.channel == "sms" and f.language == "ar"
    assert f.age_band is None  # no registry: unknown, not guessed
    assert F.features_from_case(m, API_STORY, T0, now, {"language": "en", "age_band": "50-59", "contact_valid": True}).language == "en"
    assert F.features_from_case(m, API_STORY, T0, now, {"contact_valid": True}).contact_valid is True


@pytest.mark.parametrize("state,resume,stage", [
    ("result_received", None, "result_received"), ("confirmatory_booked", None, "confirmatory_booked"),
    ("referral_rejected", None, "patient_acknowledged"), ("lost_contact", None, "patient_contacted"),
    ("consent_withdrawn", "owner_accepted", "owner_accepted"), ("consent_withdrawn", None, None),
    ("confirmatory_completed", None, None), ("clinician_reviewed", None, None), ("closed", None, None),
    ("reopened", None, None),
])
def test_stage_mapping(state, resume, stage):
    assert F.stage_of(state, resume) == stage
    assert (F.features_from_case(CaseModel(state=state, resume_state=resume), [ev("case_created")], T0, T0) is None) == (stage is None)


def test_days_are_capped_and_never_negative():
    m = CaseModel(state="acknowledged")
    assert F.features_from_case(m, [ev("case_created")], T0, T0 + timedelta(days=400)).days_since_result == F.DAYS_CAP
    assert F.features_from_case(m, [ev("case_created")], T0, T0 - timedelta(days=3)).days_since_result == 0.0


def test_contact_valid_forms():
    assert F.contact_valid_of({"contact_valid": True}) is True
    assert F.contact_valid_of({"contact_valid": {"sms": False, "phone": True}}) is False
    assert F.contact_valid_of({"contact_valid": {"sms": True}}) is True
    assert F.contact_valid_of({}) is None and F.contact_valid_of(None) is None


def test_age_bands_cover_the_screening_range_exactly():
    assert [F.age_band_of(a) for a in (40, 49, 50, 59, 60, 69, 70, 75)] == ["40-49", "40-49", "50-59", "50-59", "60-69", "60-69", "70-75", "70-75"]
    assert F.age_band_of(39) is None and F.age_band_of(76) is None
