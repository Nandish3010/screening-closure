"""MOCKS of the Malaffi and Sahatna message shapes, for the Integrations page (ADR-057).

Everything here is a MOCK. The server builds a synthetic message of the shape our own adapter reads and feeds it to the
adapter that is already there, in this process:

    Malaffi-style inbound result   an HL7 v2 ORU^R01 (a positive FIT) -> `app.hl7.handler.handle` -> the same
                                   `routers.cases.create_case` as POST /cases. Redelivery of the same message finds the case.
    Sahatna-style booking callback an invented JSON callback ("the patient confirmed a slot") -> translated into an HL7 SIU^S12
                                   -> `app.hl7.handler.handle` -> the existing `book_confirmatory` event as `system:hl7`.

What this is NOT: a connection to Malaffi or Sahatna, or a test against either. No specification of either was seen. The
message shapes are our reading of the HL7 v2 standard (the sample messages in tools/hl7/samples) and, for Sahatna, an
invented callback with no counterpart in any real interface. The callback translator exists to show the engine's side of an
inbound booking; it proves nothing about Sahatna.

Nothing is opened to the network and no new way to change a case exists: both mocks go through the HL7 handler's own checks
(sender allow-list, exact identifier match, state rules, idempotency on MSH-10), and the events are written as `system:hl7`,
so the case's log says where they came from. The routes exist only on a development or declared demo deployment
(`enabled`), and only coordinators and supervisors may call them (`policy.ROUTE_POLICY`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app import integration_feed
from app.clock import clock
from app.config import settings
from app.events import replay
from app.hl7 import handler
from app.hl7.config import DEMO_SENDER, current
from app.hl7.parser import parse

MALAFFI_LABEL = "MOCK: a Malaffi-style inbound result replayed by this server. Not a connection to Malaffi."
SAHATNA_LABEL = "MOCK: a Sahatna-style booking callback made up for this demo. Not a connection to Sahatna."
NO_ACCESS_NOTE = "No Malaffi or Sahatna specification was available to us. These are the shapes of our own adapter's messages, not theirs."

MAX_REPLAYS = 3  # distinct Malaffi mock messages (and so cases); `make demo-reset` brings the count back to zero
MOCK_PATIENT_ID = "174074"  # the id of tools/hl7/samples/oru_fit_positive.hl7: a synthetic id, not a person
SLOT_DAYS_AHEAD = 14
LOCAL_OFFSET = timezone(timedelta(hours=4))  # the UAE; the HL7 config's default offset for a timestamp with none
BOOKABLE_STATES = ("patient_acknowledged", "confirmatory_booked")
ELIGIBLE_LIMIT = 5


class MockError(Exception):
    """A refusal before any message is built. `status` is the HTTP status; `detail` is a fixed phrase."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def enabled() -> bool:
    """Development, or the declared hosted demo. Never a production deployment that has not declared itself a demo."""
    return settings.app_env == "dev" or settings.demo_deployment


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _ack_summary(ack_text: str) -> dict:
    msa = parse(ack_text).first("MSA")
    return {"code": msa.get(1), "reason": msa.get(3) or None}


def _outcome(ack: dict, first: str) -> str:
    if ack["code"] != "AA":
        return "rejected"
    if ack["reason"] == handler.D_DUPLICATE:
        return "duplicate"
    if ack["reason"] == handler.D_ALREADY_BOOKED:
        return "unchanged"
    return first


def _state_of(case_id: UUID) -> str | None:
    from app.routers import cases

    events = cases.store.events_for(case_id)
    if not events:
        return None
    return replay(events, cases.pathway_of(case_id))


# ---- Malaffi: an inbound result ---------------------------------------------------------------------------------


def oru_message(control_id: str, when: datetime) -> str:
    """A positive FIT as an HL7 v2.5.1 ORU^R01, the shape of tools/hl7/samples/oru_fit_positive.hl7. Synthetic patient."""
    system = current().patient_id_system
    sender_app, sender_fac = DEMO_SENDER.split("|")
    stamp = _stamp(when)
    return "\r".join([
        f"MSH|^~\\&|{sender_app}|{sender_fac}|SCREENING|SCREENING-CLOSURE|{stamp}||ORU^R01^ORU_R01|{control_id}|P|2.5.1",
        f"PID|1||{MOCK_PATIENT_ID}^^^{system}^MR||SYNTHETIC^MOCK||||||||^PRN^CP^^971^50^0000000",
        f"OBR|1||FIT-{control_id}|29771-3^Hemoglobin.gastrointestinal [Presence] in Stool by Immunologic method^LN|||{stamp}||||||||||||||||||F",
        f"OBX|1|ST|29771-3^FIT^LN||positive|||A|||F|||{stamp}",
    ]) + "\r"


def _malaffi_control_ids() -> list[str]:
    return [f"MOCK-MALAFFI-ORU-{n}" for n in range(1, MAX_REPLAYS + 1)]


def _malaffi_used() -> list[str]:
    """The mock messages already applied, in order: those whose case exists."""
    from app.routers import cases

    out = []
    for ctrl in _malaffi_control_ids():
        probe = parse(oru_message(ctrl, clock.now()))
        if cases.store.events_for(handler.case_id_for(probe)):
            out.append(ctrl)
    return out


def malaffi_replay(redeliver: bool = False) -> dict:
    """Send the next mock ORU^R01 to the HL7 adapter in this process; with `redeliver`, the last one again (the adapter
    answers AA 'duplicate' and no second case appears)."""
    used = _malaffi_used()
    if redeliver:
        if not used:
            raise MockError(409, "nothing to redeliver yet: replay a result first")
        control = used[-1]
    else:
        remaining = [c for c in _malaffi_control_ids() if c not in used]
        if not remaining:
            raise MockError(409, f"the mock has replayed its {MAX_REPLAYS} messages: reset the demo to start again")
        control = remaining[0]
    raw = oru_message(control, clock.now())
    case_id = handler.case_id_for(parse(raw))
    ack_text = handler.handle(raw)
    ack = _ack_summary(ack_text)
    integration_feed.record("hl7", "in", "MOCK Malaffi result replay (ORU^R01)", f"ACK {ack['code']}", handler._cid(parse(raw)))
    return {
        "mock": True, "label": MALAFFI_LABEL, "note": NO_ACCESS_NOTE,
        "message_type": "ORU^R01", "message": raw.replace("\r", "\n").rstrip("\n"), "control_id": control,
        "ack": ack, "outcome": _outcome(ack, "case_created"),
        "case_id": str(case_id) if ack["code"] == "AA" else None,
        "state": _state_of(case_id) if ack["code"] == "AA" else None,
        "replays_used": len(_malaffi_used()), "replays_max": MAX_REPLAYS,
    }


# ---- Sahatna: a booking callback --------------------------------------------------------------------------------


def eligible_cases(limit: int = ELIGIBLE_LIMIT) -> list[dict]:
    """Cases a booking callback can apply to: the patient has exactly one case that is acknowledged by the patient or already
    booked (the adapter refuses none or several). Bounded: one page per pathway and state."""
    from app.routers import cases

    found: list[tuple[str, str, str, str]] = []  # (patient_ref, case_id, state, pathway)
    for pw in cases.all_pathways():
        for state in BOOKABLE_STATES:
            page, _ = cases.store.load_cases_page(pw, state, None, 100)
            found += [(lc.patient_ref, str(lc.case_id), state, pw.name) for lc in page]
    per_patient: dict[str, int] = {}
    for ref, *_ in found:
        per_patient[ref] = per_patient.get(ref, 0) + 1
    rows = [{"case_id": cid, "state": state, "pathway": pw} for ref, cid, state, pw in found if per_patient[ref] == 1]
    return sorted(rows, key=lambda r: (r["state"] != "patient_acknowledged", r["case_id"]))[:limit]


def default_slot(now: datetime) -> datetime:
    """09:30 local, 14 days after the simulation clock, moved to the next weekday (the UAE weekend is Saturday and Sunday)."""
    local = (now.astimezone(LOCAL_OFFSET) + timedelta(days=SLOT_DAYS_AHEAD)).replace(hour=9, minute=30, second=0, microsecond=0)
    while local.weekday() >= 5:
        local += timedelta(days=1)
    return local


def siu_message(control_id: str, patient_id: str, appointment_ref: str, slot_local: datetime, when: datetime) -> str:
    system = current().patient_id_system
    sender_app, sender_fac = DEMO_SENDER.split("|")
    start = slot_local.strftime("%Y%m%d%H%M%S")
    return "\r".join([
        f"MSH|^~\\&|{sender_app}|{sender_fac}|SCREENING|SCREENING-CLOSURE|{_stamp(when)}||SIU^S12^SIU_S12|{control_id}|P|2.5.1",
        f"SCH|{appointment_ref}|{appointment_ref}|||||||||^^^{start}",
        f"PID|1||{patient_id}^^^{system}^MR||SYNTHETIC^MOCK||||||||",
        "RGS|1|A",
        f"AIS|1|A|COLONOSCOPY^Colonoscopy^L|{start}",
    ]) + "\r"


def sahatna_booking_callback(case_id: UUID, slot_start: datetime | None = None) -> dict:
    """An invented 'booking confirmed' callback for `case_id`, translated into an HL7 SIU^S12 and applied by the HL7 adapter:
    the case moves to `confirmatory_booked` through the event the adapter already writes (`book_confirmatory`). Calling it
    again with the same slot is a duplicate; a different slot for a booked case is a reschedule (cancel and rebook)."""
    from app.routers import cases

    events = cases.store.events_for(case_id)
    if not events:
        raise MockError(404, "case not found")
    ref = str(events[0].payload.get("patient_ref") or "")
    patient_id = ref.split("/", 1)[1] if ref.startswith("Patient/") else ""
    if not patient_id:
        raise MockError(409, "the case has no patient reference to book for")
    now = clock.now()
    if slot_start is None:
        slot = default_slot(now)
    else:
        slot = (slot_start if slot_start.tzinfo else slot_start.replace(tzinfo=LOCAL_OFFSET)).astimezone(LOCAL_OFFSET)
    control = f"MOCK-SAHATNA-{case_id.hex[:12]}-{slot.strftime('%Y%m%d%H%M')}"
    appt_ref = f"SAH-{case_id.hex[:8].upper()}"
    raw = siu_message(control, patient_id, appt_ref, slot, now)
    before = _state_of(case_id)
    ack = _ack_summary(handler.handle(raw))
    integration_feed.record("hl7", "in", "MOCK Sahatna booking callback (as SIU^S12)", f"ACK {ack['code']}", handler._cid(parse(raw)))
    return {
        "mock": True, "label": SAHATNA_LABEL, "note": NO_ACCESS_NOTE,
        "callback": {"callback_id": control, "status": "confirmed", "case_id": str(case_id),
                     "appointment_ref": appt_ref, "slot_start": slot.isoformat()},
        "translated_to": "HL7 SIU^S12", "message": raw.replace("\r", "\n").rstrip("\n"),
        "ack": ack, "outcome": _outcome(ack, "booked"),
        "event": "book_confirmatory" if ack["code"] == "AA" and ack["reason"] is None else None,
        "case_id": str(case_id), "state_before": before, "state": _state_of(case_id),
    }


def status() -> dict:
    return {
        "mock": True, "enabled": True, "note": NO_ACCESS_NOTE,
        "malaffi": {"label": MALAFFI_LABEL, "replays_used": len(_malaffi_used()), "replays_max": MAX_REPLAYS},
        "sahatna": {"label": SAHATNA_LABEL, "eligible_cases": eligible_cases()},
    }


__all__ = ["MockError", "enabled", "malaffi_replay", "sahatna_booking_callback", "status"]
