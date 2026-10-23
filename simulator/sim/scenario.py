"""A hand-built five-case scenario with known outcomes. Tests feed it through the same
`build_record` / `compute_metrics` path the engine uses and compare with `EXPECTED`, which was
worked out by hand (the arithmetic is in the comments next to each value).

Day 0 is Monday 2026-01-05 and every case starts on day 0, follow-up is 180 days.

  C1  ar/sms       owner d1, contact d3, colonoscopy d20, closed d25
  C2  en/letter    owner d2, contact d5, escalation d10 (patient acks d12), colonoscopy d40, closed d50,
                   reopened d95, closed again d120.  Provider switch handled by a transfer.
  C3  ar/phone     owner d1, contact d2, escalation d15 (nothing happens until d30), colonoscopy d60, closed d85.
                   Duplicate result merged.
  C4  other/wa     owner d4, contact d10, wrong contact number, lost_contact flag d60, never completes.
  C5  en/sms       owner d1, contact d4, colonoscopy d100, closed d110. Conflicting ownership claim rejected.
  C1 also has consent withdrawn after booking (reviewed by a human).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.events import CASE_CREATED, Event

from .calendar import WorkingCalendar
from .metrics import CaseRecord, build_record, compute_metrics

START = date(2026, 1, 5)
START_DT = datetime(2026, 1, 5, tzinfo=timezone.utc)
FOLLOW_UP = 180.0

H = "clinician:cl-1"
CLOSE = {"evidence_ref": "rep-1", "signed_by": H}
OWNER = {"owner": "owner:pcp-1"}
REOPEN = {"resume_state": "owner_accepted"}


def _chain(book_pa: float, book: float, complete: float, review: float, close: float | None, pa: float):
    return [
        (pa, "patient_acknowledge", "patient:p", {}),
        (book, "book_confirmatory", "owner:pcp-1", {}),
        (complete, "complete_confirmatory", "endoscopy:unit", {}),
        (review, "clinician_review", H, {}),
    ] + ([(close, "close", H, CLOSE)] if close is not None else [])


# (language, channel, static messy tags, [(day, type, actor, payload), ...])
CASES = [
    ("ar", "sms", (), [
        (0.5, "acknowledge", "lab:1", {}), (1, "accept_ownership", "owner:pcp-1", OWNER), (3, "contact_patient", "staff:1", {}),
        (6, "patient_acknowledge", "patient:p", {}), (10, "book_confirmatory", "owner:pcp-1", {}),
        (11, "consent_updated", "patient:p", {"status": "withdrawn", "scope": "outreach", "flagged_for_review": True, "source": "after_booking"}),
        (13, "consent_review_completed", "owner:pcp-1", {"outcome": "continues_without_outreach"}),
        (20, "complete_confirmatory", "endoscopy:unit", {}), (22, "clinician_review", H, {}), (25, "close", H, CLOSE),
    ]),
    ("en", "letter", (), [
        (1, "acknowledge", "lab:1", {}), (2, "accept_ownership", "owner:pcp-1", OWNER), (5, "contact_patient", "staff:1", {}),
        (6, "provider_switched", "patient:p", {"applies": True}),
        (8, "handoff_accepted", "owner:new-1", {"kind": "transfer", "owner": "owner:new-1"}),
        (10, "escalated", "scheduler", {"rung": 1}),
        (12, "patient_acknowledge", "patient:p", {}), (20, "book_confirmatory", "owner:pcp-1", {}),
        (40, "complete_confirmatory", "endoscopy:unit", {}), (45, "clinician_review", H, {}), (50, "close", H, CLOSE),
        (95, "reopened", "clinician:late-finding", REOPEN),
        (97, "contact_patient", "staff:1", {}), (100, "patient_acknowledge", "patient:p", {}),
        (103, "book_confirmatory", "owner:pcp-1", {}), (108, "complete_confirmatory", "endoscopy:unit", {}),
        (112, "clinician_review", H, {}), (120, "close", H, CLOSE),
    ]),
    ("ar", "phone", (), [
        (0.5, "acknowledge", "lab:1", {}), (1, "accept_ownership", "owner:pcp-1", OWNER), (2, "contact_patient", "staff:1", {}),
        (4, "duplicate_result_merged", "system:dedup", {"matched": True}),
        (15, "escalated", "scheduler", {"rung": 1}),
        (30, "patient_acknowledge", "patient:p", {}), (40, "book_confirmatory", "owner:pcp-1", {}),
        (60, "complete_confirmatory", "endoscopy:unit", {}), (80, "clinician_review", H, {}), (85, "close", H, CLOSE),
    ]),
    ("other", "whatsapp", ("wrong_contact",), [
        (1, "acknowledge", "lab:1", {}), (4, "accept_ownership", "owner:pcp-1", OWNER), (10, "contact_patient", "staff:1", {}),
        (60, "lost_contact", "scheduler", {"reason": "ladder_exhausted"}),
    ]),
    ("en", "sms", (), [
        (0.5, "acknowledge", "lab:1", {}), (1, "accept_ownership", "owner:pcp-1", OWNER),
        (1, "conflicting_ack_rejected", "system:state-machine", {"claimant": "owner:other-1"}),
        (4, "contact_patient", "staff:1", {}), (8, "patient_acknowledge", "patient:p", {}),
        (15, "book_confirmatory", "owner:pcp-1", {}), (100, "complete_confirmatory", "endoscopy:unit", {}),
        (105, "clinician_review", H, {}), (110, "close", H, CLOSE),
    ]),
]


def scenario_logs() -> list[list[Event]]:
    logs = []
    for i, (_, _, _, rows) in enumerate(CASES):
        cid = uuid.UUID(int=i + 1)
        evs = [Event(cid, CASE_CREATED, START_DT, "system:intake", {}, uuid.UUID(int=(i + 1) << 16 | 0), 1)]
        for n, (day, etype, actor, payload) in enumerate(sorted(rows, key=lambda r: r[0]), start=2):
            evs.append(Event(cid, etype, START_DT + timedelta(days=day), actor, dict(payload),
                             uuid.UUID(int=(i + 1) << 16 | n), n))
        logs.append(evs)
    return logs


def scenario_records() -> list[CaseRecord]:
    return [
        build_record(i, lang, chan, tags, FOLLOW_UP, 0.0, log, START_DT)
        for i, ((lang, chan, tags, _), log) in enumerate(zip(CASES, scenario_logs()))
    ]


def scenario_metrics(min_group_size: int = 1) -> dict:
    return compute_metrics(
        scenario_records(), cal=WorkingCalendar(START), ltfu_idle_days=30, escalation_window_wd=5,
        min_group_size=min_group_size, delay_harm_days=270,
    )


# Hand-computed. p-quantiles use linear interpolation: value at position q*(n-1).
EXPECTED = {
    "n_cases": 5.0,
    "owned_share": 1.0,
    "time_to_ownership_median": 1.0,       # owners at [1, 2, 1, 4, 1] -> sorted [1,1,1,2,4], middle value 1
    "time_to_ownership_p90": 3.2,          # position 0.9*4 = 3.6 -> 2 + 0.6*(4-2)
    "time_to_first_contact_median": 4.0,   # [3, 5, 2, 10, 4] -> sorted [2,3,4,5,10]
    "time_to_first_contact_p90": 8.0,      # position 3.6 -> 5 + 0.6*(10-5)
    "closed_share": 0.8,                   # C1, C2, C3, C5 closed; C4 never
    "time_to_closure_median": 67.5,        # first closures [25, 50, 85, 110] -> (50 + 85) / 2
    "time_to_closure_km_median": 85.0,     # S after closes at 25, 50, 85: 0.8, 0.6, 0.4 (C4 censored at 180); first S <= 0.5 is at 85
    "time_to_closure_rmst": 90.0,          # area under S to 180: 25*1 + 25*0.8 + 35*0.6 + 25*0.4 + 70*0.2 (= mean of 25, 50, 85, 110, 180)
    "time_to_colonoscopy_km_median": 60.0, # completions at 20, 40, 60, 100: S 0.8, 0.6, 0.4 -> 60
    "time_to_colonoscopy_rmst": 80.0,      # mean of min(t, 180) = (20 + 40 + 60 + 100 + 180) / 5
    "in_reach_share": 1.0,                 # programme_reach is not applied to the hand-built cases
    "silent_loss_rate_in_reach": 0.0,
    "consent_breach_rate_in_reach": 0.0,
    "time_to_closure_p90": 102.5,          # position 2.7 -> 85 + 0.7*(110-85)
    "open_30d": 0.8,                       # only C1 closed by day 30
    "open_60d": 0.6,                       # C1 (25) and C2 (50, reopened only at 95) closed
    "open_90d": 0.4,                       # C1, C2 (reopen at 95 is later), C3 (85) closed; C4, C5 open
    "time_to_colonoscopy_median": 50.0,    # [20, 40, 60, 100] -> (40 + 60) / 2
    "time_to_colonoscopy_p25": 35.0,       # position 0.75 -> 20 + 0.75*20
    "time_to_colonoscopy_p75": 70.0,       # position 2.25 -> 60 + 0.25*40
    "completion_90d": 0.6,                 # 20, 40, 60 are within 90; 100 is not
    "completion_180d": 0.8,
    "completion_360d": None,               # follow-up (180) shorter than 360
    "completion_final": 0.8,
    "delay_harm_share": None,              # follow-up shorter than the 270-day threshold
    "declined_closed_share": 0.0,          # no informed refusal in the hand-built logs (A33)
    "reopen_rate": 0.25,                   # 1 reopened (C2) of 4 ever closed
    "escalations_per_100_cases": 40.0,     # 2 escalations / 5 cases
    "escalation_acted_on_rate": 0.5,         # C2 d10: patient acks d12, inside 5 working days (to d17); C3 d15: next action d30, outside (d22)
    "automation_abstention_rate": None,
    "lost_to_follow_up_rate": 0.2,         # C4 only (no colonoscopy, lost_contact)
    "silent_loss_rate": 0.0,               # C4 carries a lost_contact flag, so it was surfaced
    "consent_breach_rate": 0.0,
    "fairness.language.completion_gap_max": 1.0,            # ar 1.0, en 1.0, other 0.0
    "fairness.language.closure_median_gap_max_days": 25.0,  # ar [25,85] -> 55; en [50,110] -> 80; other none
    "fairness.channel.completion_gap_max": 1.0,             # sms/letter/phone 1.0, whatsapp 0.0
    "fairness.channel.closure_median_gap_max_days": 35.0,   # sms [25,110] -> 67.5; letter 50; phone 85
    "messy.wrong_contact.n": 1.0,
    "messy.wrong_contact.handled_rate": 0.0,                # no contact_recovered in C4
    "messy.wrong_contact.completion_final": 0.0,
    "messy.wrong_contact.completion_final_unaffected": 1.0,
    "messy.provider_switch.n": 1.0,
    "messy.provider_switch.handled_rate": 1.0,
    "messy.provider_switch.completion_final_unaffected": 0.75,  # C1, C3, C5 completed; C4 not
    "messy.duplicate_result.handled_rate": 1.0,
    "messy.conflicting_ack.handled_rate": 1.0,
    "messy.consent_after_booking.handled_rate": 1.0,
}
