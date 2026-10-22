"""Contact validity from ADT (A08, A31): a registry of booleans, never of numbers.

The adapter learns from a patient-update message whether the patient has a usable telephone number. It keeps only that
verdict, keyed by the patient reference, and applies it to a case created later for the same patient
(`contact_valid` on the case's genesis event, which is what the notification mock already honours). The number
itself is read, judged and dropped: it is not stored, not logged, not returned. The registry lives in process memory
(it is a hint for the next case, and it is lost on a restart: documented limit).

An existing case is not touched. `contact_updated` is a person's decision (state machine: human-only), and the actor row
of `system:hl7` does not hold it.
"""

from __future__ import annotations

import re
import threading

from app.hl7.parser import Segment

MAX_ENTRIES = 200_000
_lock = threading.Lock()
_registry: dict[str, bool] = {}


def phones_valid(pid: Segment) -> bool | None:
    """True when PID-13 or PID-14 holds a plausible telephone number, False when they hold none (or only implausible
    ones), None when the PID has neither field at all (the message says nothing about contact details)."""
    seen = False
    for n in (13, 14):
        for i in range(pid.reps(n)):
            seen = True
            equipment = pid.get(n, 3, rep=i).upper()
            if equipment in ("INTERNET", "X.400", "FX", "BP"):
                continue  # an e-mail address, a fax or a pager is not a number we can call or text
            raw = pid.get(n, 12, rep=i) or pid.get(n, 1, rep=i) or (
                pid.get(n, 5, rep=i) + pid.get(n, 6, rep=i) + pid.get(n, 7, rep=i))
            digits = re.sub(r"\D", "", raw)
            if 7 <= len(digits) <= 15:
                return True
    return False if seen else None


def record(patient_ref: str, valid: bool) -> None:
    with _lock:
        if patient_ref not in _registry and len(_registry) >= MAX_ENTRIES:
            _registry.pop(next(iter(_registry)))  # drop the oldest entry: bounded memory
        _registry[patient_ref] = valid


def lookup(patient_ref: str) -> bool | None:
    with _lock:
        return _registry.get(patient_ref)


def reset() -> None:
    with _lock:
        _registry.clear()
