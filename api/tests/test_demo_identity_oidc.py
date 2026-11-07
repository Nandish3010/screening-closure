"""The hosted demo's one-click logins ARE the people the seed made case owners (regression for "Physician sees no cases").

On the hosted demo the seed runs on the dev login, so a case owner's opaque id is uuid5(role | display name). A one-click login is an
OIDC token whose id used to be uuid5(role | sub), and Keycloak's `sub` is a random UUID per realm import: "Dr. Huda Rahman" the login
was a stranger to "Dr. Huda Rahman" the owner (worklist 0 cases, 403 on every case). These tests run the OIDC claims path against owners
the seed produced, and keep the cast, the providers file and the realm in step.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID

import jwt
import pytest
from api_helpers import make_client
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app import auth, demo_signin, evidence, notify, oidc, providers, users
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.config import settings
from app.events import InMemoryEventStore
from app.routers import cases, drafts

ISSUER = "https://idp.example/realms/closure"
AUDIENCE = "screening-closure"
REPO = Path(__file__).resolve().parents[2]
client = make_client()

_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_jwk = {**RSAAlgorithm.to_jwk(_private.public_key(), as_dict=True), "kid": "k1", "use": "sig", "alg": "RS256"}


def token(username: str, role: str, sub: str, name: str, provider: str | None = None) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 300, "name": name,
              "preferred_username": username, "realm_access": {"roles": [role]}, "provider": provider}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, _private, algorithm="RS256", headers={"kid": "k1"})


def bearer(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def hosted(monkeypatch):
    """OIDC mode under the demo gate, in-memory stores, a Marina-registered case created by the seed path (dev login, then OIDC)."""
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    monkeypatch.setattr(users, "directory", users.InMemoryUserDirectory())
    monkeypatch.setattr(auth, "_seen_oidc", {})
    monkeypatch.setattr(settings, "automation_enabled", True)
    monkeypatch.setattr(evidence, "finder", evidence.StaticFinder())
    monkeypatch.setattr(evidence, "verifier", evidence.AcceptAllVerifier())
    clock.reset()
    # seed phase: dev login creates the cases (the owner is assigned by the automation from config/providers.yaml)
    coordinator = client.post("/auth/dev-login", json={"role": "coordinator", "name": "Maryam Al Suwaidi"}).json()["token"]
    ids = {}
    for provider in ("Marina Digestive Health", "Riverside Primary Care"):
        r = client.post("/cases", json={"patient_ref": "Patient/1", "registered_provider": provider}, headers=bearer(coordinator))
        assert r.status_code == 201, r.text
        ids[provider] = r.json()["case_id"]
    # production phase: OIDC, hosted demo gate
    monkeypatch.setattr(oidc, "jwks", oidc.JwksCache(lambda url, timeout: {"keys": [_jwk]}, time.monotonic))
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "demo_simulated_clock", True)
    monkeypatch.setattr(settings, "demo_deployment", True)
    monkeypatch.setattr(settings, "clock_mode", "sim")
    assert auth.demo_controls_active()
    yield ids
    clock.reset()


HUDA = ("huda.rahman", "primary_care_owner", "Dr. Huda Rahman", "Marina Digestive Health")
LAYLA = ("layla.haddad", "primary_care_owner", "Dr. Layla Haddad", "Riverside Primary Care")


def test_the_physician_one_click_login_sees_and_opens_her_seeded_cases(hosted):
    # a random Keycloak sub, as on the hosted realm: it must not matter
    huda = bearer(token(HUDA[0], HUDA[1], "f473e28e-f2b8-406e-a39e-67a9fa4ee542", HUDA[2], HUDA[3]))
    rows = client.get("/cases", headers=huda).json()
    assert [r["case_id"] for r in rows] == [hosted["Marina Digestive Health"]]
    assert rows[0]["owner"]["display_name"] == HUDA[2]
    assert client.get(f"/cases/{hosted['Marina Digestive Health']}", headers=huda).status_code == 200
    assert client.get("/me", headers=huda).json()["user_id"] == auth.opaque_user_id("primary_care_owner", HUDA[2])


def test_she_still_cannot_read_another_owners_case_and_the_other_owner_sees_hers(hosted):
    huda = bearer(token(HUDA[0], HUDA[1], "sub-h", HUDA[2], HUDA[3]))
    layla = bearer(token(LAYLA[0], LAYLA[1], "sub-l", LAYLA[2], LAYLA[3]))
    assert client.get(f"/cases/{hosted['Riverside Primary Care']}", headers=huda).status_code == 403
    assert client.get(f"/cases/{hosted['Marina Digestive Health']}", headers=layla).status_code == 403
    assert client.get(f"/cases/{hosted['Riverside Primary Care']}", headers=layla).status_code == 200


def test_the_owner_can_act_on_her_case_with_her_own_signature(hosted):
    cid = hosted["Marina Digestive Health"]
    t = token(*HUDA[:1], HUDA[1], "sub-h", HUDA[2], HUDA[3])
    r = client.post(f"/cases/{cid}/events", json={"type": "accept_ownership", "payload": {}}, headers=bearer(t))
    assert r.status_code == 201, r.text


def test_the_mapping_exists_only_under_the_demo_gate_and_only_for_the_cast_in_their_role(hosted, monkeypatch):
    sub_based = auth.opaque_user_id("primary_care_owner", "sub-h")
    assert auth.authenticate(token(HUDA[0], HUDA[1], "sub-h", HUDA[2], HUDA[3])).user_id == auth.opaque_user_id("primary_care_owner", HUDA[2])
    # not in the cast: still sub-based
    assert auth.authenticate(token("someone.else", HUDA[1], "sub-h", HUDA[2], HUDA[3])).user_id == sub_based
    # a cast username in another role: still sub-based
    other = auth.authenticate(token(HUDA[0], "coordinator", "sub-h", "Dr. Huda Rahman"))
    assert other.user_id == auth.opaque_user_id("coordinator", "sub-h")
    # outside the demo gate (a real deployment) ids come from sub only
    monkeypatch.setattr(settings, "demo_deployment", False)
    assert not auth.demo_controls_active()
    assert auth.authenticate(token(HUDA[0], HUDA[1], "sub-h", HUDA[2], HUDA[3])).user_id == sub_based


def test_the_cast_equals_the_providers_file_the_stager_and_the_realm():
    cfg = providers.get_providers()
    for seed in cfg.owners.values():
        entry = next(v for v in auth.DEMO_CAST.values() if v[1] == seed.name)
        assert entry[0] == seed.role
    realm = json.loads((REPO / "config/keycloak/screening-closure-realm.json").read_text())
    realm_roles = {u["username"]: u["realmRoles"] for u in realm["users"]}
    for username, (role, _name) in auth.DEMO_CAST.items():
        assert realm_roles[username] == [role], username
    for role, username in demo_signin.DEMO_USERS.items():
        if role != "patient":
            assert username in auth.DEMO_CAST and auth.DEMO_CAST[username][0] == ("primary_care_owner" if role == "clinician" else role)
