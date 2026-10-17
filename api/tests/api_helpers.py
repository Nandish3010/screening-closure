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


def actor_of(role: str, name: str | None = None) -> str:
    """The actor string the API records for `role` logging in as `name` (A14: `role:opaque_id`)."""
    from app.auth import opaque_user_id

    name = name or f"test {role}"
    return f"{role}:{opaque_user_id(role, name)}"


def user_id_of(role: str, name: str | None = None) -> str:
    return actor_of(role, name).split(":", 1)[1]


# Payloads that satisfy the strict per-type schemas (app.payloads, A14) for events whose
# payload is otherwise irrelevant to the test.
DRAFT_ID = "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11"
DRAFTED = {"draft_id": DRAFT_ID, "provider": "template", "fallback_used": False,
           "prompt_hash": "0" * 64, "language": "en"}
APPROVED = {"draft_id": DRAFT_ID}
