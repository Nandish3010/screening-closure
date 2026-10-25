"""Adapter configuration: the code sets and identifier system the mappings use. Read from the environment on every call
(cheap, and a test or an operator can change it without a restart). Every default here is an assumption to confirm with
the exchange operator (docs/governance/hl7-adapter.md, "What Malaffi must confirm")."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _set(name: str, default: str) -> frozenset[str]:
    return frozenset(x.strip().upper() for x in os.environ.get(name, default).split(",") if x.strip())


DEMO_SENDER = "LABSYS|SYNTH-LAB"  # the sender of tools/hl7/samples and of the tests


def _senders(name: str, default: str) -> frozenset[str]:
    """Exact "APP|FACILITY" entries (case-sensitive, as Message.sender returns them). Unset means the default (the demo
    sender only); a set-but-empty value means nobody is allowed (fail closed), never "allow all"."""
    return frozenset(x.strip() for x in os.environ.get(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Hl7Config:
    # PID-3 repetitions are matched on the assigning authority (CX-4: namespace id or universal id). The matching
    # identifier becomes `Patient/<id>`. For the synthetic cohort the id is the HAPI Patient id.
    patient_id_system: str
    fit_codes: frozenset[str]  # OBX-3.1 / OBR-4.1 values that mean "FIT" (LOINC 29771-3 and a local code)
    colonoscopy_codes: frozenset[str]  # OBR-4.1 values that mean "colonoscopy report"
    ct_colonography_codes: frozenset[str]  # OBR-4.1 values that mean "CT colonography report"
    completeness_code: str  # OBX-3.1 of the observation that says how the colonoscopy ended
    allowed_senders: frozenset[str]  # HL7_ALLOWED_SENDERS: "APP|FACILITY" (MSH-3|MSH-4) entries accepted; default the demo sender
    utc_offset: str  # the offset assumed for a timestamp that carries none, +HHMM (the UAE is +0400)


def current() -> Hl7Config:
    return Hl7Config(
        patient_id_system=os.environ.get("HL7_PATIENT_ID_SYSTEM", "SCREENING-CLOSURE").strip(),
        fit_codes=_set("HL7_FIT_CODES", "29771-3,FIT"),
        colonoscopy_codes=_set("HL7_COLONOSCOPY_CODES", "18746-8,73761001,45378,45380,45381,45384,45385,COLONOSCOPY"),
        ct_colonography_codes=_set("HL7_CT_COLONOGRAPHY_CODES", "74261,74262,74263,CTCOLON"),
        completeness_code=os.environ.get("HL7_COMPLETENESS_CODE", "COLON-COMPLETENESS").strip().upper(),
        allowed_senders=_senders("HL7_ALLOWED_SENDERS", DEMO_SENDER),
        utc_offset=os.environ.get("HL7_UTC_OFFSET", "+0400").strip(),
    )
