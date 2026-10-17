"""Offline tests for the pure selection logic in generate_results (no HAPI, no network)."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import generate_results as gr

SAMPLE = Path(__file__).resolve().parent.parent / "sample" / "synthea_patient_bundle.json"
CLOCK = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)


def sample_patient() -> dict:
    bundle = json.loads(SAMPLE.read_text())
    return next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")


def variant(pid: str, birth: str, **extra) -> dict:
    p = copy.deepcopy(sample_patient())
    p["id"] = pid
    p["identifier"] = [{"system": gr.SYNTHEA_ID_SYSTEM, "value": f"synthea-{pid}"}]
    p["birthDate"] = birth
    p.update(extra)
    return p


def cohort(n: int = 400) -> list[dict]:
    # ages 45..75 at 2026-01-05 (born 1950-07-01 .. 1980-07-01), so everyone is eligible (40-75)
    return [variant(str(i), f"{1951 + i % 30}-07-01") for i in range(n)]


def test_sample_is_a_real_small_synthea_bundle():
    assert SAMPLE.stat().st_size < 50_000
    p = sample_patient()
    assert p["resourceType"] == "Patient" and p["birthDate"]
    assert gr.is_eligible(p, CLOCK)  # born 1973 -> 52 on the clock date


def test_age_filter_lower_bound():
    ok = lambda birth: gr.is_eligible(variant("x", birth), CLOCK)  # noqa: E731
    assert ok("1986-01-05") is True    # exactly 40 on 2026-01-05
    assert ok("1986-01-06") is False   # 39, turns 40 tomorrow


def test_age_filter_upper_bound():
    ok = lambda birth: gr.is_eligible(variant("x", birth), CLOCK)  # noqa: E731
    assert ok("1950-01-06") is True    # 75 until tomorrow
    assert ok("1950-01-05") is False   # 76 today
    assert ok("2000-01-01") is False   # 26
    assert ok("1930-01-01") is False   # 96


def test_deceased_and_missing_birthdate_excluded():
    assert not gr.is_eligible(variant("d", "1970-01-01", deceasedDateTime="2024-03-01T00:00:00Z"), CLOCK)
    assert not gr.is_eligible(variant("d", "1970-01-01", deceasedBoolean=True), CLOCK)
    # died after the clock date: alive on the clock date
    assert gr.is_eligible(variant("d", "1970-01-01", deceasedDateTime="2026-06-01T00:00:00Z"), CLOCK)
    p = variant("n", "1970-01-01")
    del p["birthDate"]
    assert not gr.is_eligible(p, CLOCK)


def test_selection_is_deterministic_and_seed_sensitive():
    pts = cohort()
    a = gr.select_positives(pts, seed=20261017, rate=0.07, clock=CLOCK)
    b = gr.select_positives(copy.deepcopy(pts), seed=20261017, rate=0.07, clock=CLOCK)
    assert a == b and len(a) > 0
    other = gr.select_positives(pts, seed=1, rate=0.07, clock=CLOCK)
    assert {p.patient_key for p in a} != {p.patient_key for p in other}


def test_selection_independent_of_order_and_other_patients():
    pts = cohort(100)
    full = gr.select_positives(pts, 7, 0.2, CLOCK)
    assert gr.select_positives(list(reversed(pts)), 7, 0.2, CLOCK) == full
    first_half = {p["id"] for p in pts[:50]}
    sub = gr.select_positives(pts[:50], 7, 0.2, CLOCK)
    assert sub == [p for p in full if p.patient_id in first_half]


def test_positivity_rate_is_roughly_the_parameter_and_extremes_hold():
    pts = cohort(2000)
    n = len(gr.select_positives(pts, 20261017, 0.07, CLOCK))
    assert 0.04 * 2000 < n < 0.10 * 2000
    assert gr.select_positives(pts, 1, 0.0, CLOCK) == []
    assert len(gr.select_positives(pts, 1, 1.0, CLOCK)) == 2000


def test_ineligible_patients_never_selected_even_at_rate_one():
    pts = [variant("old", "1940-01-01"), variant("young", "2005-01-01"), variant("ok", "1970-01-01")]
    sel = gr.select_positives(pts, 1, 1.0, CLOCK)
    assert [p.patient_id for p in sel] == ["ok"]


def test_cap_dates_and_ids():
    pts = cohort(300)
    full = gr.select_positives(pts, 20261017, 0.5, CLOCK)
    capped = gr.select_positives(pts, 20261017, 0.5, CLOCK, cap=5)
    assert capped == full[:5]
    for pos in full:
        assert CLOCK - timedelta(days=14) < pos.effective <= CLOCK
    assert len({p.report_id for p in full}) == len(full) == len({p.observation_id for p in full})


def test_resources_have_the_expected_shape():
    pos = gr.select_positives([variant("p1", "1970-01-01")], 1, 1.0, CLOCK)[0]
    obs, rep = gr.build_resources(pos)
    assert obs["status"] == rep["status"] == "final"
    assert obs["code"]["coding"][0]["code"] == "29771-3" == rep["code"]["coding"][0]["code"]
    assert obs["valueCodeableConcept"] == {"text": "Positive"}
    assert obs["subject"] == rep["subject"] == {"reference": "Patient/p1"}
    assert rep["result"] == [{"reference": f"Observation/{obs['id']}"}]
    assert rep["conclusion"] == "Positive FIT. Confirmatory colonoscopy recommended."
    assert rep["effectiveDateTime"] == rep["issued"] == obs["effectiveDateTime"]
