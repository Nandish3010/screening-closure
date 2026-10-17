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
