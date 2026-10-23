"""One HL7 v2 message in, one ACK out. The only module that calls the engine, and only through paths that exist.

    ORU^R01  a FIT result      positive -> a case, by the same function as POST /cases (`routers.cases.create_case`)
                               negative -> ACK AA, ignored. Neither is ever closed or signed by a message.
    ORU^R01  a colonoscopy     the report is recorded as FHIR evidence through `confirmatory.recorder`, the path of
                               POST /cases/{id}/confirmatory-result, and only with the completeness the report states
    SIU^S12  book              `book_confirmatory` with the appointment fields, as `system:hl7`
    SIU^S14  reschedule        accepted when the case has no booking yet; for a booked case the pathway has no event
    SIU^S15  cancel            for it: ACK AE with the reason (docs/governance/hl7-adapter.md, "Not built")
    ADT^A08 / A31              contact validity only (a boolean in a registry; no number kept, no event written)

Everything else is refused with a reason from the fixed list below: AR when the message cannot be read at all or is of a
type we do not handle, AE when it is readable but cannot be applied. A reason is a fixed phrase and never contains message
content, so an ACK and a log line cannot leak PHI. The log carries the message type, the ACK code, the reason and a short
hash of the control id: no identifier, name, number or free text (tests/test_hl7_logs.py scans for it).

Idempotency: a case is `uuid5(sender | MSH-10)`; a redelivered FIT result finds the case and is ACKed AA again. A booking
is the event `uuid5("siu" | sender | MSH-10)`; the colonoscopy record has deterministic ids. Redelivery changes nothing.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException

from app import confirmatory, payloads, policy
from app.clock import clock
from app.events import Event, EventContentConflict, EventIdConflict
from app.hl7 import contacts
from app.hl7.config import Hl7Config, current
from app.hl7.parser import Hl7ParseError, Message, Segment, build_ack, parse, parse_ts
from app.state_machine import HL7_ACTOR, InvalidTransition, PayloadError, validate_payload

log = logging.getLogger("app.hl7")

HL7_NAMESPACE = uuid.UUID("0fb4ca46-9372-4cf5-a0f4-a080718c43b0")  # never change: case and event ids derive from it

# Fixed reasons (MSA-3 and ERR-8). Nothing else is ever written there.
R_NOT_FINAL = "result status is not final"
R_UNREADABLE_RESULT = "result cannot be interpreted as positive or negative"
R_NO_OBSERVATION = "no recognised observation in the message"
R_NO_PATIENT_ID = "patient identifier missing or not from the configured system"
R_BAD_PATIENT_ID = "patient identifier is not usable"
R_NO_CASE = "no matching case for the patient"
R_AMBIGUOUS = "more than one matching case: not attached automatically"
R_NO_PID = "PID segment missing"
R_NO_START = "appointment start time missing or unreadable"
R_RESCHEDULE = "reschedule of a booked appointment is not supported by the pathway"
R_CANCEL = "cancellation is not supported by the pathway"
R_INVALID_STATE = "the event is not valid in the case's current state"
R_CONTENT_CONFLICT = "message control id reused with different content"
R_NO_COMPLETENESS = "colonoscopy completeness not stated"
R_BAD_COMPLETENESS = "colonoscopy completeness value not recognised"
R_RECORDER = "evidence store unavailable, retry"
R_INTERNAL = "internal error, retry"
R_DENIED = "action not permitted for this interface"
R_CASE_REFUSED = "case could not be created from this message"
R_UNSUPPORTED_TYPE = "message type not supported"
R_UNSUPPORTED_EVENT = "trigger event not supported"
R_VERSION = "HL7 version not supported"
R_TOO_LARGE = "message too large"
R_ENCODING = "message is not valid UTF-8"
D_DUPLICATE = "duplicate message already processed"
D_NEGATIVE = "negative result acknowledged, no case created"
D_ALREADY_BOOKED = "appointment already booked for this time"
D_NO_CONTACT = "no contact information in the message"
D_CONTACT = "contact validity recorded"

POSITIVE_FLAGS = frozenset({"A", "AA", "H", "HH", "POS", "DET", "ABN"})  # HL7 table 0078
NEGATIVE_FLAGS = frozenset({"N", "NEG", "ND", "L", "LL"})
POSITIVE_WORDS = frozenset({"positive", "pos", "detected", "abnormal", "reactive"})
NEGATIVE_WORDS = frozenset({"negative", "neg", "not detected", "normal", "non-reactive", "nonreactive"})
FINAL_STATUS = frozenset({"F", "C"})
_PATIENT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/._-]{0,99}$")


class Reject(Exception):
    """The message is readable and is not applied. `ack` is AE (the sender may fix and resend) or AR."""

    def __init__(self, ack: str, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.ack, self.error_code, self.reason = ack, error_code, reason


@dataclass(frozen=True)
class Outcome:
    ack: str  # AA, AE or AR
    error_code: str | None
    reason: str | None
    kind: str  # for the log and the tests: fit_positive, fit_negative, siu_book, colonoscopy, adt, duplicate, ...


def _cid(msg: Message | None) -> str:
    """A short hash of sender + control id for the log: enough to match a line to a message, not an identifier."""
    if msg is None:
        return "-"
    return hashlib.sha256(f"{msg.sender}|{msg.control_id}".encode()).hexdigest()[:12]


def _key(msg: Message) -> str:
    return f"{msg.sender}|{msg.control_id}"


def case_id_for(msg: Message) -> UUID:
    return uuid.uuid5(HL7_NAMESPACE, f"case|{_key(msg)}")


def booking_event_id(msg: Message) -> UUID:
    return uuid.uuid5(HL7_NAMESPACE, f"siu|{_key(msg)}")


# ---- entry point ------------------------------------------------------------------------------------------------


def handle(raw: str) -> str:
    """Process one message text and return the ACK text. Never raises."""
    msg: Message | None = None
    try:
        msg = parse(raw)
        outcome = _dispatch(msg)
    except Hl7ParseError as exc:
        outcome = Outcome("AR", exc.code, exc.reason, "unreadable")
    except Reject as exc:
        outcome = Outcome(exc.ack, exc.error_code, exc.reason, "rejected")
    except Exception as exc:  # noqa: BLE001 - the interface must always answer; the sender retries an AE
        log.error("hl7 internal failure: %s", type(exc).__name__)  # the class only: a message could carry the input
        outcome = Outcome("AE", "207", R_INTERNAL, "internal")
    mt = f"{msg.message_type[0]}^{msg.message_type[1]}" if msg is not None else "-"
    log.info("hl7 type=%s kind=%s ack=%s reason=%s cid=%s", mt[:12], outcome.kind, outcome.ack, outcome.reason or "-", _cid(msg))
    return build_ack(msg, outcome.ack, outcome.error_code, outcome.reason, clock.now())


def _dispatch(msg: Message) -> Outcome:
    version = msg.msh.get(12, 1)
    if not version.startswith("2."):
        raise Reject("AR", "203", R_VERSION)
    code, trigger = msg.message_type
    if code == "ORU":
        if trigger != "R01":
            raise Reject("AE", "201", R_UNSUPPORTED_EVENT)
        return _oru(msg)
    if code == "SIU":
        if trigger not in ("S12", "S14", "S15"):
            raise Reject("AE", "201", R_UNSUPPORTED_EVENT)
        return _siu(msg, trigger)
    if code == "ADT":
        if trigger not in ("A08", "A31"):
            raise Reject("AE", "201", R_UNSUPPORTED_EVENT)
        return _adt(msg)
    raise Reject("AR", "200", R_UNSUPPORTED_TYPE)


# ---- shared helpers ---------------------------------------------------------------------------------------------


def patient_ref_of(pid: Segment | None, cfg: Hl7Config) -> str:
    """`Patient/<id>` from the PID-3 repetition whose assigning authority is the configured system. Never a fuzzy match:
    no identifier from that system, or two different ones, is a rejection (a wrong-patient attachment is a hazard, E6)."""
    if pid is None:
        raise Reject("AE", "101", R_NO_PID)
    wanted = cfg.patient_id_system.upper()
    found: set[str] = set()
    for i in range(pid.reps(3)):
        authority = {pid.get(3, 4, 1, rep=i).upper(), pid.get(3, 4, 2, rep=i).upper(), pid.get(3, 4, rep=i).upper()}
        if wanted and wanted in authority and pid.get(3, 1, rep=i):
            found.add(pid.get(3, 1, rep=i))
    if not found:
        raise Reject("AE", "204", R_NO_PATIENT_ID)
    if len(found) > 1 or not _PATIENT_ID.match(next(iter(found))):
        raise Reject("AE", "102", R_BAD_PATIENT_ID)
    return f"Patient/{next(iter(found))}"


def _cases_of(patient_ref: str, states) -> list:
    from app.routers import cases

    out = []
    for state in states:
        out += [lc for lc in cases.store.load_cases(cases.pathway, state) if lc.patient_ref == patient_ref]
    return out


def _one_case(patient_ref: str, states):
    found = _cases_of(patient_ref, states)
    if not found:
        raise Reject("AE", "204", R_NO_CASE)
    if len(found) > 1:
        raise Reject("AE", "204", R_AMBIGUOUS)
    return found[0]


def _timestamp(value: str, cfg: Hl7Config) -> tuple[datetime, str] | None:
    """(UTC datetime, precision) of an HL7 timestamp; a value with no offset is read in the configured offset."""
    when, precision = parse_ts(value)
    if when is None:
        return None
    if when.tzinfo is None:
        sign = -1 if cfg.utc_offset.startswith("-") else 1
        digits = cfg.utc_offset.lstrip("+-")
        when = when.replace(tzinfo=timezone(sign * timedelta(hours=int(digits[:2] or 0), minutes=int(digits[2:4] or 0))))
    return when, precision or "datetime"


# ---- ORU^R01 ----------------------------------------------------------------------------------------------------


def _code_matches(seg: Segment, field: int, codes: frozenset[str], rep: int = 0) -> bool:
    return any(seg.get(field, c, rep=rep).upper() in codes for c in (1, 4))


def _oru(msg: Message) -> Outcome:
    cfg = current()
    for obr in msg.find("OBR"):
        if _code_matches(obr, 4, cfg.colonoscopy_codes) or _code_matches(obr, 4, cfg.ct_colonography_codes):
            return _colonoscopy(msg, obr, cfg)
    return _fit(msg, cfg)


def _fit_verdict(obx: Segment) -> bool | None:
    """True positive, False negative, None cannot tell. The abnormal flag (OBX-8) decides; the value only when it is empty."""
    flags = {f.upper() for f in obx.all(8)}
    if flags & POSITIVE_FLAGS:
        return True
    if flags & NEGATIVE_FLAGS:
        return False
    value = " ".join(obx.get(5, 1).lower().split())
    if value in POSITIVE_WORDS:
        return True
    if value in NEGATIVE_WORDS:
        return False
    return None


def _fit(msg: Message, cfg: Hl7Config) -> Outcome:
    fits = [o for o in msg.find("OBX") if _code_matches(o, 3, cfg.fit_codes)]
    if not fits:
        raise Reject("AE", "103", R_NO_OBSERVATION)
    if any(o.get(11).upper() not in FINAL_STATUS for o in fits):
        raise Reject("AE", "103", R_NOT_FINAL)
    verdicts = [_fit_verdict(o) for o in fits]
    if any(v is None for v in verdicts):
        raise Reject("AE", "102", R_UNREADABLE_RESULT)  # never drop a result we cannot read: the sender sees the AE
    if not any(verdicts):
        return Outcome("AA", None, D_NEGATIVE, "fit_negative")
    pid = msg.first("PID")
    patient_ref = patient_ref_of(pid, cfg)
    contact = contacts.phones_valid(pid) if pid is not None else None
    if contact is None:
        contact = contacts.lookup(patient_ref)
    return _create_case(msg, patient_ref, contact)


def _create_case(msg: Message, patient_ref: str, contact_valid: bool | None) -> Outcome:
    from app.routers import cases

    try:
        policy.authorize_hl7("create_case")
        case_id = case_id_for(msg)
        cases.create_case(cases.CreateCase(
            case_id=case_id, patient_ref=patient_ref, result_ref=f"DiagnosticReport/hl7-{case_id}",
            contact_valid=contact_valid))
    except HTTPException as exc:
        if exc.status_code == 409:
            genesis = cases.store.events_for(case_id)
            if genesis and genesis[0].payload.get("patient_ref") != patient_ref:  # the control id was reused for another patient
                raise Reject("AE", "205", R_CONTENT_CONFLICT) from None
            return Outcome("AA", None, D_DUPLICATE, "duplicate")
        if exc.status_code == 403:
            raise Reject("AE", "207", R_DENIED) from None
        raise Reject("AE", "102", R_CASE_REFUSED) from None  # the detail may echo the input: never copied out
    return Outcome("AA", None, None, "fit_positive")


def _colonoscopy(msg: Message, obr: Segment, cfg: Hl7Config) -> Outcome:
    """A colonoscopy report: recorded only when it is final and says how the colonoscopy ended. The recorder's resources
    carry that completeness, and `close` accepts only a complete colonoscopy or a CT colonography (A24, ADR-036)."""
    from app.routers import cases

    if obr.get(25).upper() not in FINAL_STATUS:
        raise Reject("AE", "103", R_NOT_FINAL)
    stated = None
    for obx in msg.find("OBX"):
        if obx.get(3, 1).upper() == cfg.completeness_code:
            if obx.get(11).upper() not in FINAL_STATUS:
                raise Reject("AE", "103", R_NOT_FINAL)
            stated = obx.get(5, 1).strip().lower()
    is_ct = _code_matches(obr, 4, cfg.ct_colonography_codes)
    if stated is None:
        raise Reject("AE", "101", R_NO_COMPLETENESS)  # never assume "complete": it is the one fact closure turns on
    if stated not in ("complete", "incomplete", "ct_colonography"):
        raise Reject("AE", "103", R_BAD_COMPLETENESS)
    if is_ct and stated == "complete":
        stated = "ct_colonography"
    if not is_ct and stated == "ct_colonography":
        raise Reject("AE", "103", R_BAD_COMPLETENESS)  # a colonoscopy report cannot say it was a CT colonography
    patient_ref = patient_ref_of(msg.first("PID"), cfg)
    lc = _one_case(patient_ref, sorted(cases.RESULT_STATES))
    performed = _timestamp(obr.get(7, 1) or obr.get(8, 1), cfg)
    now = clock.now()
    at = performed[0].astimezone(UTC) if performed and performed[0].astimezone(UTC) <= now else now
    try:
        policy.authorize_hl7("record_confirmatory_result")
        confirmatory.recorder.record(lc.case_id, patient_ref, at, stated)
    except confirmatory.RecorderUnavailable:
        raise Reject("AE", "207", R_RECORDER) from None
    except HTTPException:
        raise Reject("AE", "207", R_DENIED) from None
    return Outcome("AA", None, None, "colonoscopy")


# ---- SIU^S12 / S14 / S15 ----------------------------------------------------------------------------------------


def _start_time(msg: Message) -> str:
    sch = msg.first("SCH")
    tq1 = msg.first("TQ1")
    ais = msg.first("AIS")
    return (tq1.get(7, 1) if tq1 else "") or (sch.get(11, 4) if sch else "") or (ais.get(4, 1) if ais else "")


def _siu(msg: Message, trigger: str) -> Outcome:
    cfg = current()
    if trigger == "S15":
        raise Reject("AE", "201", R_CANCEL)
    sch = msg.first("SCH")
    if sch is None:
        raise Reject("AE", "101", R_NO_START)
    patient_ref = patient_ref_of(msg.first("PID"), cfg)
    start = _timestamp(_start_time(msg), cfg)
    if start is None:
        raise Reject("AE", "101", R_NO_START)
    when, precision = start
    payload: dict = {"appointment_date": when.date().isoformat()}
    if precision == "datetime":
        payload["appointment_at"] = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ref = sch.get(2, 1) or sch.get(1, 1)
    if ref and _REF.match(ref) and not any(set(seg) == {"."} for seg in ref.split("/")):
        payload["appointment_ref"] = ref

    from app.routers import cases

    event_id = booking_event_id(msg)
    open_cases = _cases_of(patient_ref, ("patient_acknowledged", "confirmatory_booked"))
    if not open_cases:  # a redelivery after the case moved on is still a duplicate, not an error
        prior = next((e for lc in _cases_of(patient_ref, (None,)) for e in lc.events if e.event_id == event_id), None)
        if prior is not None:
            return _duplicate_of(prior, payload)
    lc = _one_case(patient_ref, ("patient_acknowledged", "confirmatory_booked"))
    prior = next((e for e in lc.events if e.event_id == event_id), None)
    if prior is not None:
        return _duplicate_of(prior, payload)
    if lc.model is not None and lc.model.state == "confirmatory_booked":
        booked = next((e for e in reversed(lc.events) if e.type == "book_confirmatory"), None)
        same = booked is not None and booked.payload.get("appointment_at") == payload.get("appointment_at") \
            and booked.payload.get("appointment_date") == payload["appointment_date"]
        if same:
            return Outcome("AA", None, D_ALREADY_BOOKED, "siu_unchanged")
        raise Reject("AE", "201", R_RESCHEDULE)
    return _book(cases, lc.case_id, payload, event_id)


def _duplicate_of(prior: Event, payload: dict) -> Outcome:
    """A redelivery is a duplicate only if it says what the first message said; the same control id with another time
    or reference is a conflict the sender must hear about (AE), and the first booking stands."""
    if prior.payload != payload:
        raise Reject("AE", "205", R_CONTENT_CONFLICT)
    return Outcome("AA", None, D_DUPLICATE, "duplicate")


def _book(cases, case_id: UUID, payload: dict, event_id: UUID) -> Outcome:
    try:
        policy.authorize_hl7("book_confirmatory")
        event = Event(case_id, "book_confirmatory", clock.now(), HL7_ACTOR, payload, event_id)
        payloads.validate("book_confirmatory", payload)
        validate_payload(event, cases.pathway)
        cases.store.append(event, cases.pathway)
    except HTTPException:
        raise Reject("AE", "207", R_DENIED) from None
    except (InvalidTransition, PayloadError, payloads.PayloadSchemaError):
        raise Reject("AE", "100", R_INVALID_STATE) from None
    except EventContentConflict:
        raise Reject("AE", "205", R_CONTENT_CONFLICT) from None
    except EventIdConflict:
        raise Reject("AE", "205", R_CONTENT_CONFLICT) from None
    return Outcome("AA", None, None, "siu_book")


# ---- ADT^A08 / A31 ----------------------------------------------------------------------------------------------


def _adt(msg: Message) -> Outcome:
    cfg = current()
    pid = msg.first("PID")
    patient_ref = patient_ref_of(pid, cfg)
    valid = contacts.phones_valid(pid)
    if valid is None:
        return Outcome("AA", None, D_NO_CONTACT, "adt")
    contacts.record(patient_ref, valid)
    return Outcome("AA", None, D_CONTACT, "adt")
