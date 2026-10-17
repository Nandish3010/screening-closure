"""Shared test helpers. Importable as `from api_helpers import login_headers` (pytest puts
the tests directory on sys.path)."""

from fastapi.testclient import TestClient

from app.main import app

ROLES = ["lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor"]


def make_client() -> TestClient:
    return TestClient(app)  # no lifespan: cases.store stays the in-memory default


def login_headers(client: TestClient, role: str, name: str | None = None, patient_ref=None) -> dict:
    body = {"role": role, "name": name or f"test {role}"}
    if patient_ref:
        body["patient_ref"] = patient_ref
    resp = client.post("/auth/dev-login", json=body)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
