"""Parsers of the held-out validation targets (ticket B20) on fixtures built here; no external file is needed."""
from __future__ import annotations

import json

import pytest

import validation_targets as VT
import xlsxread as X
from test_calibration import write_xlsx

HTML = """<html><head><style>.x{}</style><script>var a=1;</script></head><body><p>In 2024, 73,724 people received a positive screening
result (5.8%), meaning blood was detected. However, only 14.7% (2023: 13.9%) were recorded as having received a colonoscopy within the
recommended 30-day timeframe. Participants waited between 132 days (WA) and 202 days (NT), depending on where they lived.</p></body></html>"""


def test_aihw_summary_parser_extracts_the_figures():
    r = VT.parse_aihw_summary_html(HTML)
    assert r == {"year": 2024, "positives": 73724, "positivity_pct": 5.8, "colonoscopy_within_30d_pct": 14.7,
                 "colonoscopy_within_30d_pct_prev_year": 13.9, "wait_days_state_min": 132, "wait_days_state_min_state": "WA",
                 "wait_days_state_max": 202, "wait_days_state_max_state": "NT"}


def test_aihw_summary_parser_fails_loudly_when_the_page_changes():
    with pytest.raises(ValueError):
        VT.parse_aihw_summary_html("<p>nothing useful</p>")
    with pytest.raises(ValueError):
        VT.parse_aihw_summary_html(HTML.replace("14.7%", "about fifteen percent"))


def _table12():
    h1 = ["Region Code", "Region", "Hub", "Screening Provider Code", "Screening Provider", "Attendance", "Definitive FOBt+ Patients"]
    rows = [["title"], h1,
            ["Y61", "East", "H", "B1", "Alpha", 70.0, 100.0],
            ["Y61", "East", "H", "B2", "Beta", 80.0, 100.0],
            ["Y56", "London", "H", "B3", "Gamma", 60.0, 100.0],
            [],
            [None, None, None, "Region Code", "Region", "Attendance", "Definitive FOBt+ Patients"],
            [None, None, None, "Y61", "East", 150.0, 200.0],
            [None, None, None, "Y56", "London", 60.0, 100.0],
            [None, None, None, None, "England", 210.0, 300.0]]
    return rows


def test_bcsp_provider_parser_on_a_fixture(tmp_path):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, {"Table 12": _table12()})
    r = VT.parse_bcsp_providers(X.read_xlsx(p))
    assert r["providers"] == 3
    assert r["provider_uptake_min"] == 0.6 and r["provider_uptake_max"] == 0.8
    assert r["region_uptake"] == {"East": 0.75, "London": 0.6}
    assert (r["sum_attendance"], r["sum_definitive_positive"]) == (210.0, 300.0)      # the England row, not the sum of the second table


def test_bcsp_provider_parser_rejects_an_unknown_layout(tmp_path):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, {"Table 12": [["nothing"]]})
    with pytest.raises(StopIteration):
        VT.parse_bcsp_providers(X.read_xlsx(p))


def test_phs_parser_on_extract_dict():
    ex = {"kpi4": {"boards": ["A", "B", "Scotland"], "bands": {"0 to 4 weeks": [10.0, 50.0, 28.8], ">4 to 8 weeks": [40.0, 20.0, 41.6], "median (weeks)": [5.0, 2.0, 5.7]}},
          "a6": {"boards": ["A", "B", "Scotland"], "bands": {"median days": [37.0, 20.0, 40.0]}},
          "a4_counts": {"Scotland": {"Invites": 1.0, "Positive tests": 1000.0, "Colonoscopy performed": 750.0}}}
    ex["a6"]["bands"] = {"median": [37.0, 20.0, 40.0]}
    ex["kpi4"]["bands"] = {"0 to 4": [10.0, 50.0, 28.8], ">4 to 8": [40.0, 20.0, 41.6], "median": [5.0, 2.0, 5.7]}
    r = VT.parse_phs_validation(ex)
    assert r["share_within_4w"] == 0.288 and r["share_within_8w"] == 0.704
    assert r["reported_median_days_kpi4"] == pytest.approx(39.9)
    assert r["board_median_days_min"] == 14.0 and r["board_median_days_max"] == 35.0 and r["boards"] == 2
    assert (r["positive_tests"], r["colonoscopies_performed"]) == (1000.0, 750.0)


def test_transcribed_targets_are_internally_consistent():
    cohorts, trials = VT._lit()
    ids = [t["id"] for t in cohorts + trials]
    assert len(ids) == len(set(ids))
    sm = next(t for t in cohorts if t["id"] == "seamar-fqhc")
    for c, pub in zip(sm["observed"]["completion"], (0.229, 0.371, 0.428)):
        assert c["k"] / c["n"] == pytest.approx(pub, abs=0.001)          # counts reproduce the published percentages
    az = next(t for t in trials if t["id"] == "azulay-sms")
    assert az["treated"]["k"] / az["treated"]["n"] == pytest.approx(0.703, abs=0.001)
    assert az["control"]["k"] / az["control"]["n"] == pytest.approx(0.471, abs=0.001)
    kp = next(t for t in trials if t["id"] == "kpnw-navigation")
    assert kp["treated"]["k"] / kp["treated"]["n"] == pytest.approx(0.755, abs=0.001) and kp["control"]["k"] / kp["control"]["n"] == pytest.approx(0.649, abs=0.003)
    an = next(t for t in trials if t["id"] == "annals-navigation-2025")
    assert an["treated"]["k"] / an["treated"]["n"] == pytest.approx(0.551, abs=0.001) and an["control"]["k"] / an["control"]["n"] == pytest.approx(0.421, abs=0.002)


def test_committed_targets_are_valid_json_with_sources():
    d = json.loads(VT.OUT.read_text())
    assert {t["id"] for t in d["cohort_targets"]} >= {"phs-scotland", "nhs-england-bcsp", "aihw-nbcsp-2026-secondary"}
    assert d["attempted_not_obtained"]
