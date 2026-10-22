"""A28 (ADR-039): tools/hl7: the committed samples are what the generator writes, every sample parses (the malformed one
fails to), each is handled with the ACK its name promises, `send.py` normalises line ends and `--fresh` changes only MSH-10."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "hl7"))

import make_samples  # noqa: E402
import send  # noqa: E402

from app import confirmatory  # noqa: E402
from app.events import InMemoryEventStore  # noqa: E402
from app.hl7 import contacts, handler  # noqa: E402
from app.hl7 import parser as p  # noqa: E402
from app.routers import cases  # noqa: E402

SAMPLES = ROOT / "tools" / "hl7" / "samples"


def test_the_committed_samples_equal_the_generator_output():
    built = make_samples.build()
    assert {f.name for f in SAMPLES.glob("*.hl7")} == set(built)
    for name, text in built.items():
        assert (SAMPLES / name).read_text(encoding="utf-8") == text + "\n", f"{name} is stale: run make hl7-samples"


def test_one_sample_per_supported_message_type():
    names = {f.name for f in SAMPLES.glob("*.hl7")}
    assert {"oru_fit_positive.hl7", "oru_fit_negative.hl7", "oru_colonoscopy_complete.hl7", "siu_s12_book.hl7", "siu_s14_reschedule.hl7",
            "siu_s15_cancel.hl7", "adt_a08_update_patient.hl7", "adt_a31_update_person.hl7", "malformed_no_msh.hl7"} <= names


@pytest.fixture
def world(monkeypatch):
    from app.main import pathway

    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(cases, "pathway", pathway)
    monkeypatch.setattr(confirmatory, "recorder", confirmatory.StaticRecorder())
    contacts.reset()


EXPECTED = {  # with no case on the store: the messages that need one are refused with a reason, the rest are applied
    "oru_fit_positive.hl7": "AA", "oru_fit_negative.hl7": "AA", "adt_a08_update_patient.hl7": "AA", "adt_a31_update_person.hl7": "AA",
    "oru_colonoscopy_complete.hl7": "AE", "oru_colonoscopy_incomplete.hl7": "AE", "siu_s12_book.hl7": "AE", "siu_s14_reschedule.hl7": "AE",
    "siu_s15_cancel.hl7": "AE", "malformed_no_msh.hl7": "AR",
}


@pytest.mark.parametrize("name,code", sorted(EXPECTED.items()))
def test_each_sample_gets_its_ack(world, name, code):
    ack = p.parse(handler.handle(send.load(SAMPLES / name)))
    assert ack.first("MSA").get(1) == code


def test_every_sample_but_the_malformed_one_parses_and_carries_a_cohort_patient():
    ids = set(make_samples.cohort_ids())
    for f in SAMPLES.glob("*.hl7"):
        if f.name.startswith("malformed"):
            with pytest.raises(p.Hl7ParseError):
                p.parse(send.load(f))
            continue
        msg = p.parse(send.load(f))
        assert msg.first("PID").get(3, 1) in ids, f.name


def test_the_positive_sample_creates_the_case_the_demo_looks_for(world):
    message = send.load(SAMPLES / "oru_fit_positive.hl7")
    assert p.parse(handler.handle(message)).first("MSA").get(1) == "AA"
    (lc,) = cases.store.load_cases(cases.pathway)
    assert lc.case_id == handler.case_id_for(p.parse(message))


def test_send_load_uses_carriage_returns_and_fresh_changes_only_the_control_id():
    plain = send.load(SAMPLES / "oru_fit_positive.hl7")
    assert "\n" not in plain and plain.endswith("\r") and plain.count("\r") == 4
    fresh = send.load(SAMPLES / "oru_fit_positive.hl7", fresh=True)
    a, b = p.parse(plain), p.parse(fresh)
    assert a.control_id == "FIT-POS-0001" and b.control_id != a.control_id and b.control_id.startswith("T")
    assert plain.replace(a.control_id, "X") == fresh.replace(b.control_id, "X")
    assert send.load(SAMPLES / "oru_fit_positive.hl7", fresh=True) != fresh  # a new id each time


def test_send_summary_prints_msa_and_err_only():
    ack = handler.handle("garbage")
    code, line = send.summary(ack)
    assert code == "AR" and line.startswith("MSA|AR|") and "ERR|" in line and "|screening-closure|" not in line
