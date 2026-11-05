"""A deterministic, SYNTHETIC mix of insurance schemes for the demo cohort (ADR-056).

Like the registration provider, the scheme is picked from the Synthea patient id (sha256, then a bucket), so a rerun gives
the same patient the same scheme. The mix is an invented demo mix, not a population estimate: 40% thiqa, 30% enhanced,
25% basic, 5% unknown. It is the cohort pipelines' only source of `insurance_scheme`; the API accepts the field from any
caller and never requires it.
"""

from __future__ import annotations

import hashlib

# (scheme, share out of 20). The schemes are the API's codes (api/app/insurance.py); a test keeps the two lists equal.
MIX = (("thiqa", 8), ("enhanced", 6), ("basic", 5), ("unknown", 1))


def insurance_scheme_for(patient_id: str) -> str:
    bucket = int(hashlib.sha256(f"insurance|{patient_id}".encode()).hexdigest(), 16) % sum(n for _, n in MIX)
    for scheme, n in MIX:
        if bucket < n:
            return scheme
        bucket -= n
    raise AssertionError("unreachable")
