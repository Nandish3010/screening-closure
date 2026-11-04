"""ADR-054: the demo cohort carries a deterministic, synthetic mix of insurance schemes."""

import json

import httpx

import create_cases
import insurance_mix


def test_the_mix_names_exactly_the_apis_scheme_codes():
    from app import insurance

    assert tuple(s for s, _ in insurance_mix.MIX) == insurance.SCHEME_CODES


def test_a_patient_always_gets_the_same_scheme_and_a_cohort_gets_a_mix():
    picks = [insurance_mix.insurance_scheme_for(f"patient-{i}") for i in range(400)]
    assert picks == [insurance_mix.insurance_scheme_for(f"patient-{i}") for i in range(400)]
    assert set(picks) == {s for s, _ in insurance_mix.MIX}
    share = {s: picks.count(s) / len(picks) for s, _ in insurance_mix.MIX}
    assert share["thiqa"] > share["basic"] > share["unknown"] and 0.03 < share["unknown"] < 0.09


def test_the_bowel_pipeline_sends_the_scheme_with_the_case():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        create_cases.create_case(client, "http://api", {"report_id": "r1", "patient_id": "p1"}, [])
    assert seen[0]["insurance_scheme"] == insurance_mix.insurance_scheme_for("p1")
