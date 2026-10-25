"""A28 (ADR-039): the HL7 v2 handler end to end on the in-memory store. Message types, ACK codes, idempotency on MSH-10,
negatives ignored, malformed messages rejected, the colonoscopy completeness rule, contact validity, and what the adapter
may never do."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from api_helpers import actor_of
from app import confirmatory, policy
from app.clock import clock
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.hl7 import contacts, handler
from app.hl7 import parser as p
from app.routers import cases

PATIENT = "10242"
SYSTEM = "SCREENING-CLOSURE"
GOOD_PHONE = "^PRN^CP^^971^50^0000000"


@pytest.fixture(autouse=True)
def recorder(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    rec = confirmatory.StaticRecorder()
    monkeypatch.setattr(confirmatory, "recorder", rec)
    contacts.reset()
    clock.reset()
    yield rec
    contacts.reset()
    clock.reset()


def msh(kind: str, ctrl: str, sender: str = "LABSYS|SYNTH-LAB", version: str = "2.5.1") -> str:
    app_, fac = sender.split("|")
    return f"MSH|^~\\&|{app_}|{fac}|SCREENING|SC|20260106083000||{kind}|{ctrl}|P|{version}"


def pid(patient: str = PATIENT, system: str = SYSTEM, phone: str = "") -> str:
    return f"PID|1||{patient}^^^{system}^MR||SYNTHETIC^{patient}||||||||{phone}"


def fit(ctrl="F1", flag="A", value="positive", patient=PATIENT, status="F", code="29771-3^FIT^LN", pid_line=None,
        sender="LABSYS|SYNTH-LAB") -> str:
    return "\r".join([
        msh("ORU^R01^ORU_R01", ctrl, sender), pid_line if pid_line is not None else pid(patient, phone=GOOD_PHONE),
        f"OBR|1||FIT-1|{code}|||20260105100000||||||||||||||||||F",
        f"OBX|1|ST|{code}||{value}|||{flag}|||{status}"]) + "\r"


def colonoscopy(ctrl="C1", completeness="complete", patient=PATIENT, obr_code="18746-8^Colonoscopy study^LN", status="F",
                obx_status="F", observed="20260106080000") -> str:
    lines = [msh("ORU^R01^ORU_R01", ctrl), pid(patient),
             f"OBR|1||COL-1|{obr_code}|||{observed}||||||||||||||||||{status}"]
    if completeness is not None:
        lines.append(f"OBX|1|ST|COLON-COMPLETENESS^Completeness^L||{completeness}||||||{obx_status}")
    return "\r".join(lines) + "\r"


def siu(kind="S12", ctrl="S1", start="20260112093000", patient=PATIENT, appt="APPT-1001", with_pid=True) -> str:
    lines = [msh(f"SIU^{kind}^SIU_S12", ctrl), f"SCH|{appt}|{appt}|||||||||^^^{start}"]
    if with_pid:
        lines.append(pid(patient))
    lines.append(f"AIS|1|A|COLONOSCOPY^Colonoscopy^L|{start}")
    return "\r".join(lines) + "\r"


def adt(kind="A08", ctrl="A1", patient=PATIENT, phone=GOOD_PHONE) -> str:
    return "\r".join([msh(f"ADT^{kind}^ADT_A01", ctrl), "EVN||20260106083000", pid(patient, phone=phone)]) + "\r"


def ack_of(text: str) -> tuple[str, str, str]:
    ack = p.parse(handler.handle(text))
    msa = ack.first("MSA")
    return msa.get(1), msa.get(2), msa.get(3)


def all_cases():
    return cases.store.load_cases(cases.pathway)


def make_case(patient=PATIENT, upto="result_received") -> uuid.UUID:
    case_id = uuid.uuid4()
    cases.store.create_case(case_id, cases.pathway.name,
                            Event(case_id, CASE_CREATED, clock.now(), "system:intake", {"patient_ref": f"Patient/{patient}"}),
                            cases.pathway.initial_state)
    steps = [("acknowledge", actor_of("lab"), {}), ("accept_ownership", actor_of("primary_care_owner"), {}),
             ("contact_patient", actor_of("primary_care_owner"), {"channel": "sms"}),
             ("patient_acknowledge", actor_of("coordinator"), {}),
             ("book_confirmatory", actor_of("coordinator"), {"appointment_date": "2026-02-01"})]
    order = ["result_received", "acknowledged", "owner_accepted", "patient_contacted", "patient_acknowledged", "confirmatory_booked"]
    for (etype, actor, payload), target in zip(steps, order[1:]):
        if order.index(upto) < order.index(target):
            break
        cases.store.append(Event(case_id, etype, clock.now(), actor, payload), cases.pathway)
    return case_id


# ---- ORU^R01: the FIT result ----------------------------------------------------------------------------------------


def test_a_positive_fit_creates_one_case_by_the_post_cases_path():
    assert ack_of(fit("F1")) == ("AA", "F1", "")
    (lc,) = all_cases()
    genesis = lc.events[0]
    assert genesis.type == CASE_CREATED and genesis.actor == "system:intake"  # the POST /cases path, not a new one
    assert genesis.payload["patient_ref"] == f"Patient/{PATIENT}"
    assert genesis.payload["result_ref"].startswith("DiagnosticReport/hl7-")
    assert genesis.payload["contact_valid"] is True
    assert lc.case_id == handler.case_id_for(p.parse(fit("F1")))
    assert lc.model.state == "result_received"


def test_redelivery_with_the_same_control_id_changes_nothing_and_is_acked_aa():
    assert ack_of(fit("F1"))[0] == "AA"
    again = ack_of(fit("F1"))
    assert again[0] == "AA" and again[2] == handler.D_DUPLICATE
    assert len(all_cases()) == 1 and len(all_cases()[0].events) == 1
    assert ack_of(fit("F2"))[0] == "AA" and len(all_cases()) == 2  # another control id is another result


def test_the_same_control_id_from_another_sender_is_another_message(monkeypatch):
    monkeypatch.setenv("HL7_ALLOWED_SENDERS", "LABSYS|SYNTH-LAB,LABSYS|OTHER-LAB")
    assert ack_of(fit("F1"))[0] == "AA"
    assert ack_of(fit("F1", sender="LABSYS|OTHER-LAB"))[0] == "AA" and len(all_cases()) == 2


@pytest.mark.parametrize("flag,value", [("N", "negative"), ("NEG", "x"), ("", "negative"), ("", "Not detected"), ("L", "")])
def test_a_negative_fit_is_acked_and_ignored(flag, value):
    assert ack_of(fit("N1", flag=flag, value=value)) == ("AA", "N1", handler.D_NEGATIVE)
    assert all_cases() == []


@pytest.mark.parametrize("flag,value", [("A", ""), ("H", "21"), ("AA", "x"), ("POS", ""), ("", "Positive"), ("", "detected"), ("N~A", "?")])
def test_every_positive_form_creates_a_case(flag, value):
    assert ack_of(fit("P1", flag=flag, value=value))[0] == "AA"
    assert len(all_cases()) == 1


@pytest.mark.parametrize("flag,value", [("", "42"), ("", ""), ("", "maybe"), ("Q", "5")])
def test_a_result_that_cannot_be_read_is_never_silently_dropped(flag, value):
    code, _, reason = ack_of(fit("U1", flag=flag, value=value))
    assert code == "AE" and reason == handler.R_UNREADABLE_RESULT
    assert all_cases() == []


@pytest.mark.parametrize("status", ["P", "I", "X", "", "R"])
def test_a_result_that_is_not_final_is_an_ae(status):
    assert ack_of(fit("S1", status=status)) == ("AE", "S1", handler.R_NOT_FINAL)
    assert all_cases() == []


def test_a_corrected_result_is_final():
    assert ack_of(fit("C1", status="C"))[0] == "AA"


def test_fit_by_a_local_code_is_recognised_and_other_observations_are_not(monkeypatch):
    assert ack_of(fit("L1", code="FIT^Faecal immunochemical^L"))[0] == "AA"
    assert ack_of(fit("L2", code="2345-7^Glucose^LN")) == ("AE", "L2", handler.R_NO_OBSERVATION)
    monkeypatch.setenv("HL7_FIT_CODES", "ZZ-FIT")
    assert ack_of(fit("L3", code="29771-3^FIT^LN"))[1:] == ("L3", handler.R_NO_OBSERVATION)
    assert ack_of(fit("L4", code="ZZ-FIT^x^L"))[0] == "AA"


def test_patient_identification_is_exact_or_a_rejection():
    # the configured system selects the identifier, whatever its position among the repetitions
    multi = "PID|1||99999^^^OTHER^NI~10242^^^SCREENING-CLOSURE^MR||X"
    assert ack_of(fit("I1", pid_line=multi))[0] == "AA"
    assert all_cases()[0].patient_ref == "Patient/10242"
    for ctrl, line in [("I2", pid(system="OTHER")), ("I3", "PID|1||"), ("I4", pid(patient="bad id!")),
                       ("I5", "PID|1||1^^^SCREENING-CLOSURE~2^^^SCREENING-CLOSURE"),
                       ("I6", pid(patient="../etc"))]:
        assert ack_of(fit(ctrl, pid_line=line))[0] == "AE", ctrl
    no_pid = "\r".join(fit("I7").split("\r")[:1] + fit("I7").split("\r")[2:])
    assert ack_of(no_pid)[::2] == ("AE", handler.R_NO_PID)
    assert len(all_cases()) == 1
    # a negative result needs no patient: it is acknowledged and ignored whoever it is for
    assert ack_of(fit("I8", flag="N", value="negative", pid_line="PID|1||"))[0] == "AA"


def test_the_configured_identifier_system_is_used(monkeypatch):
    monkeypatch.setenv("HL7_PATIENT_ID_SYSTEM", "EMIRATES-ID")
    assert ack_of(fit("Y1"))[0] == "AE"                                                   # the default system is no longer the one
    assert ack_of(fit("Y2", pid_line=pid("784199012345671", system="EMIRATES-ID")))[0] == "AA"
    assert all_cases()[0].patient_ref == "Patient/784199012345671"


# ---- malformed and unsupported messages -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "garbage", "PID|1||x", "MSH|^~\\&|A", "\x00\x01\x02", "MSH|^~\\&|A|B|C|D|E||ORU^R01|", "MSH|||"])
def test_a_malformed_message_is_an_ar_and_creates_nothing(text):
    ack = p.parse(handler.handle(text))
    assert ack.first("MSA").get(1) == "AR" and ack.first("ERR").get(4) == "E"
    assert all_cases() == []


def test_unsupported_types_events_and_versions():
    assert ack_of("\r".join([msh("ORM^O01", "O1"), pid()]) + "\r")[0] == "AR"                       # a type we do not handle
    assert ack_of(msh("ORU^R03", "O2") + "\r")[0] == "AE"                                          # a trigger we do not handle
    assert ack_of(msh("ADT^A01", "O3") + "\r")[0] == "AE" and ack_of(msh("SIU^S13", "O4") + "\r")[0] == "AE"
    assert ack_of(msh("ORU^R01", "O5", version="3.0") + "\r")[0] == "AR"
    assert ack_of(msh("ORU^R01", "O6", version="") + "\r")[0] == "AR"
    err = p.parse(handler.handle(msh("ORU^R01", "O5", version="3.0") + "\r")).first("ERR")
    assert err.get(3, 1) == "203"


def test_an_internal_failure_is_an_ae_with_a_fixed_reason(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("SECRET-PATIENT-NAME")

    monkeypatch.setattr(cases, "create_case", boom)
    ack_text = handler.handle(fit("E1"))
    ack = p.parse(ack_text)
    assert ack.first("MSA").get(1) == "AE" and ack.first("MSA").get(3) == handler.R_INTERNAL
    assert "SECRET" not in ack_text


# ---- ORU^R01: the colonoscopy report --------------------------------------------------------------------------------


def test_a_complete_colonoscopy_is_recorded_through_the_confirmatory_recorder(recorder):
    case_id = make_case(upto="confirmatory_booked")
    assert ack_of(colonoscopy("C1", "complete"))[0] == "AA"
    assert recorder.completeness == ["complete"]
    assert recorder.calls[0][0] == case_id and recorder.calls[0][1] == f"Patient/{PATIENT}"
    assert len(cases.store.events_for(case_id)) == 6  # it appended nothing: closing is still a person's act


def test_an_incomplete_colonoscopy_is_recorded_as_incomplete_never_as_complete(recorder):
    make_case(upto="confirmatory_booked")
    assert ack_of(colonoscopy("C2", "incomplete"))[0] == "AA"
    assert recorder.completeness == ["incomplete"]


def test_a_ct_colonography_is_recorded_as_such(recorder):
    make_case(upto="confirmatory_booked")
    assert ack_of(colonoscopy("C3", "ct_colonography", obr_code="74263^CT colonography^C4"))[0] == "AA"
    assert ack_of(colonoscopy("C4", "complete", obr_code="CTCOLON^CT colonography^L"))[0] == "AA"
    assert recorder.completeness == ["ct_colonography", "ct_colonography"]
    # a colonoscopy report cannot claim to be a CT colonography
    assert ack_of(colonoscopy("C5", "ct_colonography"))[2] == handler.R_BAD_COMPLETENESS


def test_completeness_is_never_assumed(recorder):
    make_case(upto="confirmatory_booked")
    assert ack_of(colonoscopy("C6", None)) == ("AE", "C6", handler.R_NO_COMPLETENESS)
    assert ack_of(colonoscopy("C7", "mostly"))[2] == handler.R_BAD_COMPLETENESS
    assert ack_of(colonoscopy("C8", "complete", status="P"))[2] == handler.R_NOT_FINAL
    assert ack_of(colonoscopy("C9", "complete", obx_status="P"))[2] == handler.R_NOT_FINAL
    assert recorder.calls == []


def test_a_colonoscopy_needs_exactly_one_case_awaiting_it(recorder):
    assert ack_of(colonoscopy("D1"))[2] == handler.R_NO_CASE                  # no case at all
    make_case(upto="patient_acknowledged")
    assert ack_of(colonoscopy("D2"))[2] == handler.R_NO_CASE                  # not booked yet: no colonoscopy to record
    make_case(upto="confirmatory_booked")
    assert ack_of(colonoscopy("D3"))[0] == "AA"
    make_case(upto="confirmatory_booked")
    assert ack_of(colonoscopy("D4"))[2] == handler.R_AMBIGUOUS                # two candidates: never attach by guessing
    assert len(recorder.calls) == 1


def test_a_recorder_failure_is_an_ae_the_sender_can_retry(monkeypatch):
    make_case(upto="confirmatory_booked")
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder(fail="HAPI down"))
    assert ack_of(colonoscopy("R1")) == ("AE", "R1", handler.R_RECORDER)


def test_the_performed_time_is_the_report_time_but_never_after_the_sim_clock(recorder):
    make_case(upto="confirmatory_booked")
    ack_of(colonoscopy("T1", observed="20260104080000"))
    ack_of(colonoscopy("T2", observed="20991231080000"))
    assert recorder.calls[0][2] == datetime(2026, 1, 4, 4, 0, tzinfo=UTC)  # 08:00 in the configured +0400
    assert recorder.calls[1][2] == clock.now()


# ---- SIU^S12 / S14 / S15 --------------------------------------------------------------------------------------------


def test_s12_books_the_confirmatory_appointment_as_system_hl7():
    case_id = make_case(upto="patient_acknowledged")
    assert ack_of(siu("S12", "S1")) == ("AA", "S1", "")
    booked = cases.store.events_for(case_id)[-1]
    assert booked.type == "book_confirmatory" and booked.actor == "system:hl7"
    assert booked.payload == {"appointment_date": "2026-01-12", "appointment_at": "2026-01-12T05:30:00Z",
                              "appointment_ref": "APPT-1001"}
    assert cases.store.current_state(case_id) == "confirmatory_booked"


def test_s12_redelivery_is_a_duplicate_even_after_the_case_moved_on():
    case_id = make_case(upto="patient_acknowledged")
    assert ack_of(siu("S12", "S1"))[0] == "AA"
    again = ack_of(siu("S12", "S1"))
    assert again[0] == "AA" and again[2] == handler.D_DUPLICATE
    cases.store.append(Event(case_id, "complete_confirmatory", clock.now(), actor_of("specialist_owner"), {}), cases.pathway)
    assert ack_of(siu("S12", "S1"))[2] == handler.D_DUPLICATE
    assert len([e for e in cases.store.events_for(case_id) if e.type == "book_confirmatory"]) == 1


def test_a_date_only_appointment_carries_only_the_date():
    case_id = make_case(upto="patient_acknowledged")
    assert ack_of(siu("S12", "S2", start="20260112"))[0] == "AA"
    payload = cases.store.events_for(case_id)[-1].payload
    assert payload["appointment_date"] == "2026-01-12" and "appointment_at" not in payload


def test_s14_before_any_booking_books_and_for_a_booked_case_with_a_new_time_cancels_and_rebooks():
    case_id = make_case(upto="patient_acknowledged")
    assert ack_of(siu("S14", "S3"))[0] == "AA"                                             # nothing was booked: it books
    assert ack_of(siu("S14", "S5"))[2] == handler.D_ALREADY_BOOKED                          # same time: nothing to change
    assert ack_of(siu("S14", "S4", start="20260113093000"))[0] == "AA"                     # A34: a new time cancels and rebooks
    kinds = [(e.type, e.actor) for e in cases.store.events_for(case_id) if e.type in ("book_confirmatory", "appointment_cancelled")]
    assert kinds == [("book_confirmatory", "system:hl7"), ("appointment_cancelled", "system:hl7"), ("book_confirmatory", "system:hl7")]
    assert cases.store.current_state(case_id) == "confirmatory_booked"
    assert cases.store.events_for(case_id)[-1].payload["appointment_date"] == "2026-01-13"
    # redelivery of the reschedule changes nothing
    assert ack_of(siu("S14", "S4", start="20260113093000"))[0] == "AA"
    assert len([e for e in cases.store.events_for(case_id) if e.type == "appointment_cancelled"]) == 1
    # a second S12 with another time for a booked case is a rebooking too
    assert ack_of(siu("S12", "S6", start="20260114093000"))[0] == "AA"
    assert len([e for e in cases.store.events_for(case_id) if e.type == "appointment_cancelled"]) == 2


def test_s15_cancels_a_booked_appointment_as_system_hl7_and_the_case_returns_to_patient_acknowledged():
    case_id = make_case(upto="confirmatory_booked")
    assert ack_of(siu("S15", "S7"))[0] == "AA"                                              # A34: ACK AA, not AE
    cancelled = cases.store.events_for(case_id)[-1]
    assert (cancelled.type, cancelled.actor) == ("appointment_cancelled", "system:hl7")
    assert cancelled.payload == {"reason": "other", "by_patient": False}                  # the sender does not say who or why
    assert cases.store.current_state(case_id) == "patient_acknowledged"
    assert ack_of(siu("S15", "S7"))[2] == handler.D_DUPLICATE                               # redelivery
    assert ack_of(siu("S15", "S8"))[0] == "AE" and ack_of(siu("S15", "S8"))[2] == handler.R_NOT_BOOKED  # nothing left to cancel
    assert len([e for e in cases.store.events_for(case_id) if e.type == "appointment_cancelled"]) == 1
    # and the booking can be made again from a later SIU
    assert ack_of(siu("S12", "S9", start="20260120093000"))[0] == "AA"
    assert cases.store.current_state(case_id) == "confirmatory_booked"


def test_s15_for_a_patient_with_no_case_is_an_ae():
    assert ack_of(siu("S15", "S10", patient="99999"))[0] == "AE"


@pytest.mark.parametrize("kind", ["S13", "S16", "S26", "S17"])
def test_other_siu_variants_are_an_ae(kind):
    make_case(upto="patient_acknowledged")
    code, _, reason = ack_of(siu(kind, "SV"))
    assert code == "AE" and reason == handler.R_UNSUPPORTED_EVENT


def test_siu_refusals_are_acked_with_reasons():
    assert ack_of(siu("S12", "X1"))[2] == handler.R_NO_CASE
    make_case(upto="patient_acknowledged")
    assert ack_of(siu("S12", "X2", with_pid=False))[2] == handler.R_NO_PID
    assert ack_of(siu("S12", "X3", start=""))[2] == handler.R_NO_START
    assert ack_of(siu("S12", "X4", start="soon"))[2] == handler.R_NO_START
    assert ack_of(siu("S12", "X5", patient="55555"))[2] == handler.R_NO_CASE                # another patient: nothing attached
    make_case(upto="patient_acknowledged")
    assert ack_of(siu("S12", "X6"))[2] == handler.R_AMBIGUOUS


def test_a_case_that_is_not_ready_for_a_booking_is_not_a_candidate():
    make_case(upto="owner_accepted")
    assert ack_of(siu("S12", "X7"))[2] == handler.R_NO_CASE


# ---- ADT^A08 / A31 --------------------------------------------------------------------------------------------------


def test_adt_records_contact_validity_and_a_later_case_carries_it():
    assert ack_of(adt("A31", "A2", phone="^PRN^PH^^^^123"))[::2] == ("AA", handler.D_CONTACT)
    assert contacts.lookup(f"Patient/{PATIENT}") is False
    assert ack_of(fit("F9", pid_line=pid()))[0] == "AA"                  # the ORU's PID carries no telephone: the registry speaks
    assert all_cases()[0].events[0].payload["contact_valid"] is False
    assert ack_of(adt("A08", "A3"))[0] == "AA" and contacts.lookup(f"Patient/{PATIENT}") is True


def test_adt_changes_no_existing_case():
    case_id = make_case(upto="owner_accepted")
    before = cases.store.events_for(case_id)
    assert ack_of(adt("A08", "A4", phone="^PRN^PH^^^^1"))[0] == "AA"
    assert cases.store.events_for(case_id) == before


def test_adt_without_telephone_fields_says_nothing_and_unsupported_adt_is_an_ae():
    assert ack_of(adt("A08", "A5", phone=""))[2] == handler.D_NO_CONTACT
    assert contacts.lookup(f"Patient/{PATIENT}") is None
    assert ack_of(adt("A01", "A6"))[0] == "AE" and ack_of(adt("A04", "A7"))[0] == "AE"
    assert ack_of(adt("A08", "A8", patient="x y"))[0] == "AE"


@pytest.mark.parametrize("phone,expected", [
    ("^PRN^CP^^971^50^0000000", True), ("^^^^^^050 000 0000", True), ("0500000000^PRN^PH", True),
    ("^NET^Internet^a@b.c", False), ("^PRN^FX^^^^0500000000", False), ("^PRN^PH^^^^12345", False), ("^PRN^PH^^^^1234567890123456", False),
])
def test_phone_plausibility(phone, expected):
    seg = p.parse(adt(phone=phone)).first("PID")
    assert contacts.phones_valid(seg) is expected


def test_the_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(contacts, "MAX_ENTRIES", 3)
    for i in range(5):
        contacts.record(f"Patient/{i}", True)
    assert contacts.lookup("Patient/0") is None and contacts.lookup("Patient/4") is True


# ---- what the adapter may never do ----------------------------------------------------------------------------------


def test_the_adapter_has_exactly_four_actions_and_two_events():
    assert policy.HL7_ACTIONS == {"create_case", "book_confirmatory", "cancel_appointment", "record_confirmatory_result"}
    assert policy.HL7_EVENTS == {"book_confirmatory", "appointment_cancelled"}
    for action in ("close", "clinician_review", "reassign", "accept_ownership", "contact_updated", "consent_updated", "delete"):
        with pytest.raises(Exception) as exc:
            policy.authorize_hl7(action)
        assert getattr(exc.value, "status_code", None) == 403


def test_a_denied_action_is_an_ae_not_a_crash(monkeypatch):
    monkeypatch.setattr(policy, "HL7_ACTIONS", frozenset())
    assert ack_of(fit("Z1"))[::2] == ("AE", handler.R_DENIED)
    make_case(upto="patient_acknowledged")
    assert ack_of(siu("S12", "Z2"))[2] == handler.R_DENIED
    make_case("20202", upto="confirmatory_booked")
    assert ack_of(colonoscopy("Z3", patient="20202"))[2] == handler.R_DENIED
