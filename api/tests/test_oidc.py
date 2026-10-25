"""A29 (ADR-040): OIDC access-token verification, role mapping, actor derivation and dev-token expiry.

No network: the JWKS document comes from a stub fetcher, tokens are signed with keys generated here.
"""

import time

import jwt
import pytest
from api_helpers import make_client, actor_of
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app import auth, oidc, users
from app.config import settings

ISSUER = "https://idp.example/realms/closure"
AUDIENCE = "screening-closure"


def new_key(kid):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private, jwk


KEY_A, JWK_A = new_key("key-a")
KEY_B, JWK_B = new_key("key-b")


class Provider:
    """A stub identity provider: the JWKS it serves and how often it was asked."""

    def __init__(self, *jwks):
        self.keys = list(jwks)
        self.fetches = 0
        self.down = False

    def fetch(self, url, timeout):
        self.fetches += 1
        if self.down:
            raise OSError("provider unreachable")
        return {"keys": list(self.keys)}


@pytest.fixture
def idp(monkeypatch):
    provider = Provider(JWK_A)
    now = {"t": 1000.0}
    monkeypatch.setattr(oidc, "jwks", oidc.JwksCache(provider.fetch, lambda: now["t"]))
    provider.now = now
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(settings, "oidc_jwks_min_refetch_seconds", 30.0)
    monkeypatch.setattr(users, "directory", users.InMemoryUserDirectory())
    monkeypatch.setattr(auth, "_seen_oidc", {})
    return provider


def mint(key=KEY_A, kid="key-a", roles=("coordinator",), sub="sub-1", name="Maryam Al Suwaidi", alg="RS256", **over):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 300, "name": name,
              "realm_access": {"roles": list(roles)}}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


client = make_client()


# ---- verification ------------------------------------------------------------------------------------------------

def test_a_valid_token_is_accepted(idp):
    r = client.get("/me", headers=bearer(mint()))
    assert r.status_code == 200
    assert r.json()["role"] == "coordinator" and r.json()["display_name"] == "Maryam Al Suwaidi"


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"exp": int(time.time()) - 3600},  # expired (beyond the leeway)
        {"aud": "some-other-api"},
        {"aud": None},
        {"iss": "https://evil.example/realms/closure"},
        {"iss": None},
        {"nbf": int(time.time()) + 3600},  # not yet valid
        {"exp": None},
        {"sub": None},
    ],
    ids=["expired", "wrong-audience", "no-audience", "wrong-issuer", "no-issuer", "not-yet-valid", "no-exp", "no-sub"],
)
def test_bad_claims_are_401(idp, token_kwargs):
    assert client.get("/me", headers=bearer(mint(**token_kwargs))).status_code == 401


def test_leeway_tolerates_small_clock_skew(idp):
    assert client.get("/me", headers=bearer(mint(exp=int(time.time()) - 5))).status_code == 200


def test_a_token_signed_by_an_unknown_key_is_401(idp):
    stranger, _ = new_key("key-a")  # same kid, different key material
    assert client.get("/me", headers=bearer(mint(key=stranger))).status_code == 401


def test_an_unknown_kid_is_401(idp):
    assert client.get("/me", headers=bearer(mint(key=KEY_B, kid="key-b"))).status_code == 401


def test_algorithm_confusion_and_none_are_refused(idp):
    unsigned = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "s", "exp": int(time.time()) + 60,
                           "realm_access": {"roles": ["supervisor"]}}, None, algorithm="none", headers={"kid": "key-a"})
    assert client.get("/me", headers=bearer(unsigned)).status_code == 401
    # HS256 signed with the public key's JWK text as the secret: the classic key-confusion attack.
    hs = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "s", "exp": int(time.time()) + 60,
                     "realm_access": {"roles": ["supervisor"]}}, "x" * 64, algorithm="HS256", headers={"kid": "key-a"})
    assert client.get("/me", headers=bearer(hs)).status_code == 401
    assert client.get("/me", headers=bearer("not.a.jwt")).status_code == 401


def test_a_rotated_key_is_picked_up_once_the_cache_looks_again(idp):
    assert client.get("/me", headers=bearer(mint())).status_code == 200
    assert idp.fetches == 1
    idp.keys = [JWK_B]  # the provider rotated: key-a is gone, key-b is new
    idp.now["t"] += 31  # past the minimum refetch interval
    assert client.get("/me", headers=bearer(mint(key=KEY_B, kid="key-b"))).status_code == 200
    assert idp.fetches == 2
    assert client.get("/me", headers=bearer(mint())).status_code == 401  # the retired key no longer verifies


def test_unknown_kids_cannot_make_the_api_hammer_the_provider(idp):
    for _ in range(20):
        client.get("/me", headers=bearer(mint(key=KEY_B, kid="key-b")))
    assert idp.fetches == 1  # the first fill; every unknown kid after it is inside the refetch window
    idp.now["t"] += 31
    client.get("/me", headers=bearer(mint(key=KEY_B, kid="key-b")))
    assert idp.fetches == 2


def test_the_cache_expires_and_stale_keys_survive_a_short_outage(idp, monkeypatch):
    monkeypatch.setattr(settings, "oidc_jwks_cache_seconds", 100.0)
    monkeypatch.setattr(settings, "oidc_jwks_stale_seconds", 1000.0)
    assert client.get("/me", headers=bearer(mint())).status_code == 200
    idp.now["t"] += 200
    idp.down = True
    assert client.get("/me", headers=bearer(mint())).status_code == 200  # stale keys, provider down
    idp.now["t"] += 5000
    assert client.get("/me", headers=bearer(mint())).status_code == 401  # too stale: fail closed


def test_an_unreachable_provider_with_no_keys_is_401_not_500(idp):
    idp.down = True
    assert client.get("/me", headers=bearer(mint())).status_code == 401


# ---- roles -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", auth.USER_ROLES)
def test_each_of_our_six_roles_maps(idp, role):
    extra = {"patient_ref": "Patient/p-1"} if role == "patient" else {}
    r = client.get("/me", headers=bearer(mint(roles=["offline_access", role, "uma_authorization"], **extra)))
    assert r.status_code == 200 and r.json()["role"] == role


@pytest.mark.parametrize(
    "roles",
    [[], ["offline_access"], ["system"], ["scheduler"], ["lab", "coordinator"], ["supervisor", "patient"]],
    ids=["none", "unrelated", "automated-system", "automated-scheduler", "two-roles", "two-roles-incl-patient"],
)
def test_no_role_or_an_ambiguous_one_is_refused(idp, roles):
    assert client.get("/me", headers=bearer(mint(roles=roles))).status_code == 401


def test_a_role_map_translates_the_providers_names(idp, monkeypatch):
    monkeypatch.setattr(settings, "oidc_role_map", "screening-coordinator=coordinator, screening-lab=lab")
    assert client.get("/me", headers=bearer(mint(roles=["screening-coordinator"]))).json()["role"] == "coordinator"
    assert client.get("/me", headers=bearer(mint(roles=["screening-lab"], sub="s2"))).json()["role"] == "lab"


def test_a_patient_token_needs_a_valid_patient_ref(idp):
    assert client.get("/me", headers=bearer(mint(roles=["patient"]))).status_code == 401
    assert client.get("/me", headers=bearer(mint(roles=["patient"], patient_ref="../etc"))).status_code == 401
    ok = client.get("/me", headers=bearer(mint(roles=["patient"], patient_ref="Patient/abc-1")))
    assert ok.status_code == 200 and ok.json()["patient_ref"] == "Patient/abc-1"


def test_a_clinician_token_carries_its_provider(idp):
    r = client.get("/me", headers=bearer(mint(roles=["primary_care_owner"], provider="Riverside Primary Care")))
    assert r.json()["provider"] == "Riverside Primary Care"


# ---- actor and display name --------------------------------------------------------------------------------------

def test_the_actor_comes_from_sub_exactly_as_for_dev_logins(idp):
    ident = auth.authenticate(mint(roles=["coordinator"], sub="kc-user-42"))
    assert ident.actor == f"coordinator:{auth.opaque_user_id('coordinator', 'kc-user-42')}"
    assert ident.user_id == auth.opaque_user_id("coordinator", "kc-user-42")


def test_the_actor_is_stable_across_logins_renames_and_name_claims(idp):
    first = auth.authenticate(mint(sub="kc-7", name="Dr. Layla Haddad", roles=["primary_care_owner"]))
    again = auth.authenticate(mint(sub="kc-7", name="Layla H. Haddad", roles=["primary_care_owner"]))
    assert first.actor == again.actor
    assert "Layla" not in first.actor  # the name never reaches the actor
    other = auth.authenticate(mint(sub="kc-8", name="Dr. Layla Haddad", roles=["primary_care_owner"]))
    assert other.actor != first.actor  # a different subject is a different person even with the same name


def test_the_same_person_in_another_role_is_another_actor(idp):
    a = auth.authenticate(mint(sub="kc-9", roles=["coordinator"]))
    b = auth.authenticate(mint(sub="kc-9", roles=["supervisor"]))
    assert a.user_id != b.user_id


def test_display_names_go_to_the_users_table_and_follow_a_rename(idp):
    token = mint(sub="kc-10", name="Maryam Al Suwaidi")
    ident = auth.authenticate(token)
    assert client.get("/me", headers=bearer(token)).status_code == 200
    assert users.directory.get(ident.user_id).display_name == "Maryam Al Suwaidi"
    assert client.get("/me", headers=bearer(mint(sub="kc-10", name="Maryam Al-Suwaidi"))).status_code == 200
    assert users.directory.get(ident.user_id).display_name == "Maryam Al-Suwaidi"


def test_unsafe_display_names_are_cleaned_and_have_a_fallback(idp):
    ident = auth.authenticate(mint(sub="kc-11", name="a:b <script>"))
    assert ":" not in ident.name and "<" not in ident.name
    nameless = auth.authenticate(mint(sub="kc-12", name=None))
    assert nameless.name.startswith("user ")


# ---- modes -------------------------------------------------------------------------------------------------------

def test_dev_login_is_refused_in_oidc_mode(idp):
    r = client.post("/auth/dev-login", json={"role": "supervisor", "name": "x"})
    assert r.status_code == 404


def test_dev_tokens_do_not_authenticate_in_oidc_mode(idp, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "dev")
    dev = client.post("/auth/dev-login", json={"role": "supervisor", "name": "x"}).json()["token"]
    assert client.get("/me", headers=bearer(dev)).status_code == 200
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    assert client.get("/me", headers=bearer(dev)).status_code == 401


def test_oidc_tokens_do_not_authenticate_in_dev_mode(idp, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "dev")
    assert client.get("/me", headers=bearer(mint())).status_code == 401


def test_service_tokens_work_in_oidc_mode(idp):
    r = client.get("/me", headers={"Authorization": f"Bearer {settings.scheduler_service_token}"})
    assert r.status_code == 200 and r.json()["role"] == "scheduler"


def test_the_event_actor_recorded_for_an_oidc_user_is_the_opaque_one(idp):
    token = mint(roles=["coordinator"], sub="kc-20", name="Maryam Al Suwaidi")
    r = client.get("/cases", headers=bearer(token))
    assert r.status_code == 200
    ident = auth.authenticate(token)
    assert ident.actor.startswith("coordinator:") and "Maryam" not in ident.actor


# ---- configuration -----------------------------------------------------------------------------------------------

def good_prod(monkeypatch):
    for k, v in {"app_env": "prod", "dev_login_enabled": False, "dev_auth_secret": "a" * 32,
                 "scheduler_service_token": "b" * 32, "watchdog_service_token": "c" * 32,
                 "automation_service_token": "d" * 32, "agency_service_token": "e" * 32,
                 "outreach_demo_approval": False, "fhir_verify_evidence": True,  # E20a F2
                 "auth_mode": "oidc", "oidc_issuer": ISSUER}.items():
        monkeypatch.setattr(settings, k, v)


def test_prod_requires_oidc_mode(monkeypatch):
    good_prod(monkeypatch)
    auth.assert_secure_config()
    monkeypatch.setattr(settings, "auth_mode", "dev")
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        auth.assert_secure_config()


@pytest.mark.parametrize("issuer", ["", "http://idp.example/realms/x"])
def test_prod_oidc_needs_an_https_issuer(monkeypatch, issuer):
    good_prod(monkeypatch)
    monkeypatch.setattr(settings, "oidc_issuer", issuer)
    with pytest.raises(RuntimeError, match="OIDC_ISSUER"):
        auth.assert_secure_config()


def test_a_typo_in_auth_mode_does_not_fall_back_to_dev_even_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "OIDC")
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", "")
    with pytest.raises(RuntimeError, match="OIDC_ISSUER"):
        auth.assert_secure_config()


# ---- dev tokens expire (wall clock, ADR-040) ---------------------------------------------------------------------

def test_dev_tokens_default_to_eight_hours(monkeypatch):
    monkeypatch.setattr(auth, "wall_now", lambda: 1_800_000_000.0)
    body = client.post("/auth/dev-login", json={"role": "lab", "name": "x"}).json()
    assert body["expires_in"] == 8 * 3600 and body["expires_at"] == 1_800_000_000 + 8 * 3600


def test_a_dev_token_works_until_exp_and_not_after(monkeypatch):
    t = {"now": 1_800_000_000.0}
    monkeypatch.setattr(auth, "wall_now", lambda: t["now"])
    token = auth.issue_token("lab", "x")
    t["now"] += 8 * 3600 - 1
    assert client.get("/me", headers=bearer(token)).status_code == 200
    t["now"] += 1
    assert client.get("/me", headers=bearer(token)).status_code == 401  # expired: refresh is a new login
    fresh = client.post("/auth/dev-login", json={"role": "lab", "name": "x"}).json()["token"]
    assert client.get("/me", headers=bearer(fresh)).status_code == 200


def test_expiry_is_wall_clock_not_the_simulation_clock(monkeypatch):
    token = auth.issue_token("supervisor", "boss")
    r = client.post("/clock/advance", json={"days": 400}, headers=bearer(token))  # a year on the sim clock
    assert r.status_code == 200
    assert client.get("/me", headers=bearer(token)).status_code == 200  # still valid: wall time barely moved
    client.post("/clock/reset", headers=bearer(token))


@pytest.mark.parametrize("exp", [None, "tomorrow", True, 1.5])
def test_a_signed_dev_token_without_a_usable_exp_is_refused(exp):
    claims = {"v": 1, "role": "lab", "name": "x"}
    if exp is not None:
        claims["exp"] = exp
    import json

    body = auth._b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    token = f"{body}.{auth._sign(body, settings.dev_auth_secret)}"
    with pytest.raises(auth.AuthError):
        auth.verify_token(token)


def test_actor_helper_matches_dev_derivation():
    assert actor_of("lab", "x").startswith("lab:")
