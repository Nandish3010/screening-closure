"""A29: the committed realm against a real Keycloak, end to end through `app.oidc` (marker `oidc`; `make test-oidc`).

Starts only the `keycloak` service of the `oidc` compose profile in its own compose project on a spare port, with
throw-away passwords generated here. Skips itself when Docker, the image or the network is not available, so it never
fails an offline run. Not part of `make test`.
"""

import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.oidc

ROOT = Path(__file__).resolve().parents[2]
PORT = 18180
BASE = f"http://localhost:{PORT}"
ISSUER = f"{BASE}/realms/screening-closure"
PROJECT = "sc-oidc-it"
USERS = {  # username -> (role, env var)
    "maryam.alsuwaidi": ("coordinator", "KC_PW_COORDINATOR"),
    "layla.haddad": ("primary_care_owner", "KC_PW_OWNER"),
    "karim.nasser": ("specialist_owner", "KC_PW_SPECIALIST"),
    "huda.rahman": ("primary_care_owner", "KC_PW_CLINICIAN"),
    "noura.alhammadi": ("supervisor", "KC_PW_SUPERVISOR"),
    "lab.intake": ("lab", "KC_PW_LAB"),
    "demo.patient": ("patient", "KC_PW_PATIENT"),
}


def _post(url, form, token=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


@pytest.fixture(scope="module")
def keycloak():
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker is not available")
    env = {**os.environ, "KEYCLOAK_PORT": str(PORT), "KEYCLOAK_ADMIN_PASSWORD": secrets.token_hex(12)}
    passwords = {name: secrets.token_hex(12) for _, name in USERS.values()}
    env.update(passwords)
    compose = ["docker", "compose", "-p", PROJECT, "--profile", "oidc"]
    up = subprocess.run([*compose, "up", "-d", "--no-deps", "keycloak"], cwd=ROOT, env=env, capture_output=True, text=True)
    if up.returncode != 0:
        pytest.skip(f"keycloak could not be started: {up.stderr[-300:]}")
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen(f"{ISSUER}/.well-known/openid-configuration", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        else:
            pytest.skip("keycloak did not become ready in 3 minutes")
        admin = _post(f"{BASE}/realms/master/protocol/openid-connect/token",
                      {"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": env["KEYCLOAK_ADMIN_PASSWORD"]})["access_token"]
        # The shipped client has no password grant (browser users only). The test turns it on for itself via the admin API.
        hdr = {"Authorization": f"Bearer {admin}", "Content-Type": "application/json"}
        listing = urllib.request.Request(f"{BASE}/admin/realms/screening-closure/clients?clientId=screening-closure-web", headers=hdr)
        client = json.loads(urllib.request.urlopen(listing, timeout=20).read())[0]
        client["directAccessGrantsEnabled"] = True
        put = urllib.request.Request(f"{BASE}/admin/realms/screening-closure/clients/{client['id']}", data=json.dumps(client).encode(), headers=hdr, method="PUT")
        urllib.request.urlopen(put, timeout=20)
        yield passwords
    finally:
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], cwd=ROOT, env=env, capture_output=True)


def login(username, password):
    return _post(f"{ISSUER}/protocol/openid-connect/token",
                 {"grant_type": "password", "client_id": "screening-closure-web", "username": username, "password": password, "scope": "openid"})["access_token"]


@pytest.mark.parametrize("username", list(USERS))
def test_each_demo_user_gets_a_token_the_api_accepts_with_the_right_role(keycloak, monkeypatch, username):
    from app import auth, oidc
    from app.config import settings

    role, env_name = USERS[username]
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", "screening-closure")
    monkeypatch.setattr(oidc, "jwks", oidc.JwksCache())
    ident = auth.authenticate(login(username, keycloak[env_name]))
    assert ident.role == role and ident.actor == f"{role}:{auth.opaque_user_id(role, ident.subject)}"
    if username == "demo.patient":
        assert ident.patient_ref == "Patient/demo-patient"
    if username == "huda.rahman":
        assert ident.provider == "Marina Digestive Health"
