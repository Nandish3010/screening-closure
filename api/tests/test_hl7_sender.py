"""F11: the HL7 sender (MSH-3|MSH-4) is self-asserted, so only senders on HL7_ALLOWED_SENDERS are accepted. The default is
the demo sender alone; a message from anyone else is an AR with a fixed reason and nothing is applied."""

from __future__ import annotations

import pytest

from app import confirmatory
from app.clock import clock
from app.events import InMemoryEventStore
from app.hl7 import contacts, handler
from app.hl7 import parser as p
from app.routers import cases

DEMO = "LABSYS|SYNTH-LAB"
OTHER = "EVIL|ELSEWHERE"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    from app.main import pathway

    monkeypatch.delenv("HL7_ALLOWED_SENDERS", raising=False)
    store = InMemoryEventStore()
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()
    clock.reset()
    yield store
    contacts.reset()
    clock.reset()


def fit(sender: str, ctrl: str = "F1") -> str:
    app_, fac = sender.split("|")
    return "\r".join([
        f"MSH|^~\\&|{app_}|{fac}|SCREENING|SC|20260106083000||ORU^R01^ORU_R01|{ctrl}|P|2.5.1",
        "PID|1||10242^^^SCREENING-CLOSURE^MR||SYNTHETIC^10242||||||||^PRN^CP^^971^50^0000000",
        "OBR|1||FIT-1|29771-3^FIT^LN|||20260105100000||||||||||||||||||F",
        "OBX|1|ST|29771-3^FIT^LN||positive|||A|||F"]) + "\r"


def ack(text: str) -> tuple[str, str]:
    a = p.parse(handler.handle(text))
    msa = a.first("MSA")
    return msa.get(1, 1), msa.get(3, 1)


def n_cases() -> int:
    return len(cases.store.load_cases(cases.pathway))


def test_an_unknown_sender_gets_ar_with_the_fixed_reason_and_nothing_is_written():
    assert ack(fit(OTHER)) == ("AR", handler.R_SENDER)
    assert handler.R_SENDER == "sending application or facility not permitted"
    assert n_cases() == 0


def test_the_demo_sender_is_accepted_by_default():
    assert ack(fit(DEMO))[0] == "AA"
    assert n_cases() == 1


def test_a_configured_list_accepts_its_sender_and_refuses_the_demo_one(monkeypatch):
    monkeypatch.setenv("HL7_ALLOWED_SENDERS", f"{OTHER}, LIS2|FAC2")
    assert ack(fit(OTHER))[0] == "AA"
    assert ack(fit(DEMO, "F2")) == ("AR", handler.R_SENDER)
    assert n_cases() == 1


def test_an_empty_setting_allows_nobody(monkeypatch):
    monkeypatch.setenv("HL7_ALLOWED_SENDERS", "")
    assert ack(fit(DEMO)) == ("AR", handler.R_SENDER)
    assert n_cases() == 0
