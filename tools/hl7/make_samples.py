#!/usr/bin/env python3
"""Regenerate tools/hl7/samples/*.hl7: one synthetic message per supported type, for patients of the seeded cohort.

    python tools/hl7/make_samples.py

Patient ids come from the committed tools/hl7/cohort_ids.json (the seed 20261017 cohort), so the samples are the same on every
machine and the tests that compare them do not depend on a local `make demo-seed`. `--from-manifest` writes them for YOUR cohort
(data/results/manifest.json, gitignored) instead, for a live demo against the local stack; do not commit that output. Every value is synthetic: the name field carries the HAPI
patient id, the telephone number is not a real one, there is no date of birth. Segments are written one per line for
reading; `send.py` and the parser accept either line ends or the HL7 carriage return.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLES = HERE / "samples"
COHORT_FIXTURE = HERE / "cohort_ids.json"
SYSTEM = "SCREENING-CLOSURE"
GOOD_PHONE = "^PRN^CP^^971^50^0000000"  # a plausible, non-existent mobile number
BAD_PHONE = "^PRN^PH^^^^123"  # too short to be a number


def committed_ids() -> list[str]:
    """The ids the committed samples carry (tools/hl7/cohort_ids.json)."""
    return list(json.loads(COHORT_FIXTURE.read_text())["ids"])


def cohort_ids() -> list[str]:
    """The ids of THIS machine's cohort (the positive FIT results `make demo-seed` created), else the committed ones."""
    path = ROOT / "data" / "results" / "manifest.json"
    if path.exists():
        return [r["patient_id"] for r in json.loads(path.read_text())["results"]]
    return committed_ids()


def msh(kind: str, control: str, when: str = "20260106083000") -> str:
    return f"MSH|^~\\&|LABSYS|SYNTH-LAB|SCREENING|SCREENING-CLOSURE|{when}||{kind}|{control}|P|2.5.1"


def pid(pid_: str, phone: str = "") -> str:
    """PID-3 the patient id, PID-5 a synthetic name, PID-13 the telephone number if one is given."""
    return f"PID|1||{pid_}^^^{SYSTEM}^MR||SYNTHETIC^{pid_}||||||||{phone}"


def build(ids: list[str] | None = None) -> dict[str, str]:
    ids = committed_ids() if ids is None else ids
    first, second, third = ids[0], ids[1], ids[2]
    new_positive, other = ids[-1], ids[-2]
    colon = lambda ctrl, completeness: "\n".join([  # noqa: E731
        msh("ORU^R01^ORU_R01", ctrl), pid(second),
        "OBR|1||COLON-0001|18746-8^Colonoscopy study^LN|||20260106080000||||||||||||||||||F",
        f"OBX|1|ST|COLON-COMPLETENESS^Colonoscopy completeness^L||{completeness}||||||F"])
    siu = lambda kind, ctrl, start: "\n".join([  # noqa: E731
        msh(f"SIU^{kind}^SIU_S12", ctrl), "SCH|APPT-1001|APPT-1001|||||||||" + f"^^^{start}",
        pid(third), "RGS|1|A", f"AIS|1|A|COLONOSCOPY^Colonoscopy^L|{start}"])
    adt = lambda kind, ctrl, pid_line: "\n".join([msh(f"ADT^{kind}^ADT_A01", ctrl), "EVN||20260106083000", pid_line])  # noqa: E731
    return {
        "oru_fit_positive.hl7": "\n".join([
            msh("ORU^R01^ORU_R01", "FIT-POS-0001"), pid(new_positive, GOOD_PHONE),
            "OBR|1||FIT-0001|29771-3^Hemoglobin.gastrointestinal [Presence] in Stool by Immunologic method^LN|||20260105100000||||||||||||||||||F",
            "OBX|1|ST|29771-3^FIT^LN||positive|||A|||F|||20260105100000"]),
        "oru_fit_negative.hl7": "\n".join([
            msh("ORU^R01^ORU_R01", "FIT-NEG-0001"), pid(other),
            "OBR|1||FIT-0002|29771-3^Hemoglobin.gastrointestinal [Presence] in Stool by Immunologic method^LN|||20260105100000||||||||||||||||||F",
            "OBX|1|ST|29771-3^FIT^LN||negative|||N|||F|||20260105100000"]),
        "oru_colonoscopy_complete.hl7": colon("COLON-0001", "complete"),
        "oru_colonoscopy_incomplete.hl7": colon("COLON-0002", "incomplete"),
        "siu_s12_book.hl7": siu("S12", "SIU-0001", "20260112093000"),
        "siu_s14_reschedule.hl7": siu("S14", "SIU-0002", "20260113093000"),
        "siu_s15_cancel.hl7": siu("S15", "SIU-0003", "20260112093000"),
        "adt_a08_update_patient.hl7": adt("A08", "ADT-0001", pid(first, GOOD_PHONE)),
        "adt_a31_update_person.hl7": adt("A31", "ADT-0002", pid(first, BAD_PHONE)),
        "malformed_no_msh.hl7": "PID|1||12345\nOBX|1|ST|29771-3||positive",
    }


def main(argv: list[str] | None = None) -> None:
    import sys

    SAMPLES.mkdir(exist_ok=True)
    local = "--from-manifest" in (sys.argv[1:] if argv is None else argv)
    for name, text in build(cohort_ids() if local else None).items():
        (SAMPLES / name).write_text(text + "\n", encoding="utf-8")
        print("wrote", (SAMPLES / name).relative_to(ROOT))


if __name__ == "__main__":
    main()
