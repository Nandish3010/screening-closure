"""A28 (ADR-039): no PHI and no free text in the adapter's log or in an ACK. Marker values (a patient id, a name, a
telephone number, free text in a note and in the result value, an appointment id) go through every message type and every
error path; none may appear in any log record or ACK. The log carries the message type, a kind, the ACK code, a fixed
reason and a short hash of the control id."""

from __future__ import annotations

import logging
import re

import pytest

from app import confirmatory
from app.clock import clock
from app.events import InMemoryEventStore
from app.hl7 import contacts, handler
from app.routers import cases
from test_hl7_handler import colonoscopy, fit, make_case, pid, siu

MARKERS = ["MARKERPATIENT", "MARKERNAME", "5559876543", "MARKERFREETEXT", "MARKERAPPT"]
PHONE = "^PRN^CP^^971^55^59876543"


@pytest.fixture(autouse=True)
def world(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()
    clock.reset()


def tainted(text: str) -> str:
    """Put the markers into a message from test_hl7_handler: name, telephone, free text."""
    return (text.replace("SYNTHETIC", "MARKERNAME").replace("APPT-1001", "MARKERAPPT")
            .replace("|20260106083000||", "|20260106083000||", 1))


def messages() -> list[str]:
    pat = "MARKERPATIENT"
    out = [
        fit("MARKERCTRL1", patient=pat, value="MARKERFREETEXT"),                         # unreadable result
        fit("MARKERCTRL2", patient=pat, pid_line=f"PID|1||{pat}^^^SCREENING-CLOSURE||MARKERNAME^MARKERNAME||||||||{PHONE}"),
        fit("MARKERCTRL2", patient=pat, pid_line=f"PID|1||{pat}^^^SCREENING-CLOSURE||MARKERNAME^MARKERNAME||||||||{PHONE}"),  # dup
        fit("MARKERCTRL3", flag="N", value="negative", patient=pat),
        fit("MARKERCTRL4", pid_line=f"PID|1||{pat}^^^WRONGSYSTEM||MARKERNAME||||||||{PHONE}"),
        colonoscopy("MARKERCTRL5", patient=pat), colonoscopy("MARKERCTRL6", "MARKERFREETEXT", patient=pat),
        siu("S12", "MARKERCTRL7", patient=pat, appt="MARKERAPPT"), siu("S15", "MARKERCTRL8", patient=pat),
        "\r".join(["MSH|^~\\&|A|B|C|D|2026||ADT^A08|MARKERCTRL9|P|2.5", f"PID|1||{pat}^^^SCREENING-CLOSURE||MARKERNAME||||||||{PHONE}",
                   "NTE|1||MARKERFREETEXT"]) + "\r",
        "MARKERFREETEXT MARKERNAME " + PHONE,                                            # not HL7 at all
        "MSH|^~\\&|A|B|C|D|2026||ZZZ^Z01|MARKERCTRL10|P|2.5.1\rNTE|1||MARKERFREETEXT\r",
    ]
    return out


def test_no_marker_reaches_the_log_or_an_ack(caplog, monkeypatch):
    make_case("MARKERPATIENT", upto="patient_acknowledged")
    acks = []
    with caplog.at_level(logging.DEBUG):
        for text in messages():
            acks.append(handler.handle(text))
        monkeypatch.setattr(cases, "create_case", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("MARKERNAME 5559876543")))
        acks.append(handler.handle(fit("MARKERCTRL11", patient="MARKERPATIENT")))
    lines = [r.getMessage() + " " + str(r.args) + " " + (r.exc_text or "") for r in caplog.records]
    assert lines, "the handler logs one line per message"
    blob = "\n".join(lines + acks)
    for marker in MARKERS:
        assert marker not in blob, marker
    assert "MARKERCTRL" not in "\n".join(lines), "the control id is hashed in the log (an ACK must echo it: MSA-2)"
    text = re.sub(r"cid=[0-9a-f]{12}|'[0-9a-f]{12}'", "", "\n".join(r.getMessage() for r in caplog.records))  # the hash is hex
    assert not re.search(r"\d{7,}", text), "no long digit run (a telephone number, an identifier) in the log"
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", blob)


def test_the_log_line_shape_is_fixed(caplog):
    with caplog.at_level(logging.INFO, logger="app.hl7"):
        handler.handle(fit("SHAPE1"))
    (record,) = [r for r in caplog.records if r.name == "app.hl7"]
    assert re.fullmatch(r"hl7 type=ORU\^R01 kind=fit_positive ack=AA reason=- cid=[0-9a-f]{12}", record.getMessage())
    assert "SHAPE1" not in record.getMessage()  # the control id is hashed


def test_the_event_payloads_the_adapter_writes_hold_codes_and_dates_only():
    case_id = make_case(upto="patient_acknowledged")
    handler.handle(siu("S12", "P1", appt="MARKERAPPT"))
    payload = cases.store.events_for(case_id)[-1].payload
    assert set(payload) <= {"appointment_ref", "appointment_date", "appointment_at"}
    handler.handle(tainted(fit("P2", pid_line=pid("12345", phone=PHONE))))
    genesis = [lc for lc in cases.store.load_cases(cases.pathway) if lc.patient_ref == "Patient/12345"][0].events[0].payload
    assert set(genesis) == {"patient_ref", "result_ref", "contact_valid"}
    assert "MARKERNAME" not in str(genesis) and "5559876543" not in str(genesis)
