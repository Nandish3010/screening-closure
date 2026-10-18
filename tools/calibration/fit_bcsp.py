"""NHS England Bowel Cancer Screening Programme annual report tables (2024-25): positivity and diagnostic uptake (ticket B18, dataset 3).

Extracts the England row of Table 5 (BCSP-S02 uptake and positivity), Table 11 (waiting-time standards S06 to S08) and
Table 12 (S11 diagnostic procedure uptake). Positivity here is at the English FIT cut-off of 120 ug Hb/g faeces, so it is NOT
transferable to a programme with a lower cut-off (Abu Dhabi, 7% in params.yaml); it is a documented reference value.
"""
from __future__ import annotations

import math

import xlsxread as X


def wilson(k: float, n: float, z: float = 1.959964) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def _england_table(sheet) -> dict[str, float]:
    """Header names (second occurrences suffixed '#2') mapped to the England row's values, from the last header row above it."""
    rows = X.rows(sheet)
    eng_row = None
    for r, v in rows:
        if "England" in v[3:6] and any(isinstance(x, float) for x in v):
            eng_row = r
    if eng_row is None:
        raise ValueError("no England row")
    hdr_row = max(r for r, v in rows if r < eng_row and ("Region Code" in v[:4]))
    cells = dict(rows)
    hdr, vals = cells[hdr_row], cells[eng_row]
    # the totals block has three extra empty leading columns relative to the provider block
    off = 0
    if hdr[0] != "Region Code":
        off = list(hdr).index("Region Code")
    start = list(vals).index("England") - list(hdr).index("Region")
    out: dict[str, float] = {}
    for j, name in enumerate(hdr):
        if name is None or j >= len(vals) + 0:
            continue
        v = vals[j + start] if 0 <= j + start < len(vals) else None
        if isinstance(v, float):
            key = str(name).strip().replace("\n", " ")
            if key in out:
                key += "#2"
            out[key] = v
    return out


def extract_bcsp(path) -> dict:
    book = X.read_xlsx(path)
    return {"table5": _england_table(book["Table 5"]), "table11": _england_table(book["Table 11"]),
            "table12": _england_table(book["Table 12"])}


def fit_bcsp(ex: dict) -> dict:
    t5, t11, t12 = ex["table5"], ex["table11"], ex["table12"]
    out = {
        "cutoff_note": "England uses a FIT cut-off of 120 ug Hb/g faeces; positivity is cut-off dependent",
        "screened": t5["Screened"], "invited": t5["Invited"], "further_tests_needed": t5["Further Tests Needed"],
        "uptake_of_invited": t5["Screened"] / t5["Invited"],
        "positivity": t5["Further Tests Needed"] / t5["Screened"],
        "positivity_ci95": list(wilson(t5["Further Tests Needed"], t5["Screened"])),
        "ssp_appointment_offered_within_14d_of_referral": t11["Offer within 14 days"] / t11["Referred"],
        "diagnostic_test_offered_within_14d_of_ssp": t11["Offer within 14 days#2"] / t11["Referred#2"],
        "attended_diagnostic_within_14d_of_ssp": t11["Attendance within 14 days"] / t11["Participants"],
        "attended_within_14d_counts": [t11["Attendance within 14 days"], t11["Participants"]],
        "diagnostic_uptake_after_positive": t12["Attendance"] / t12["Definitive FOBt+ Patients"],
        "diagnostic_uptake_counts": [t12["Attendance"], t12["Definitive FOBt+ Patients"]],
    }
    out["diagnostic_uptake_ci95"] = list(wilson(t12["Attendance"], t12["Definitive FOBt+ Patients"]))
    return out
