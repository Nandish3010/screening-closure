"""create_cases.py logs in as a dev coordinator and sends the token on every request (A8)."""

import json

import httpx

import create_cases


def test_dev_login_then_cases_carry_the_bearer_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/auth/dev-login":
            body = json.loads(request.content)
            assert body["role"] == "coordinator"
            return httpx.Response(200, json={"token": "tok123"})
        if request.headers.get("authorization") != "Bearer tok123":
            return httpx.Response(401, json={"detail": "authentication required"})
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        create_cases.dev_login(client, "http://api")
        entry = {"report_id": "r1", "patient_id": "p1"}
        assert create_cases.create_case(client, "http://api", entry) == "created"
    assert seen[0][:2] == ("POST", "/auth/dev-login") and seen[0][2] is None
    assert seen[1] == ("POST", "/cases", "Bearer tok123")


def test_the_cohort_pipeline_registers_each_patient_deterministically_with_a_configured_provider():
    """A21 (ADR-032): the case body carries `registered_provider`, picked from config/providers.yaml by the Synthea patient
    id alone, the same arithmetic as the API's `app.providers.provider_for_patient`."""
    from app.providers import get_providers, provider_for_patient

    providers = create_cases.registration_providers()
    assert providers and providers == sorted(get_providers().registration_providers)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for patient in ("p1", "p2", "p3", "p1"):
            create_cases.create_case(client, "http://api", {"report_id": f"r-{patient}", "patient_id": patient}, providers)
    assert all(b["registered_provider"] in providers for b in bodies)
    assert bodies[0]["registered_provider"] == bodies[3]["registered_provider"]  # the same patient, the same provider
    for b in bodies:
        assert b["registered_provider"] == provider_for_patient(b["patient_ref"].split("/", 1)[1], providers)
    assert len({provider_for_patient(f"patient-{i}", providers) for i in range(50)}) == len(providers)


def test_without_a_provider_registry_the_case_is_created_without_a_registration():
    assert create_cases.registered_provider_for("p1", []) is None
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        create_cases.create_case(client, "http://api", {"report_id": "r", "patient_id": "p"}, [])
    assert "registered_provider" not in seen[0]
