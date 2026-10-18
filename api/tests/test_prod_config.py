"""SEC-1 fix 1: the identity configuration fails closed outside APP_ENV=dev (ADR-029).

Unit tests only: they change `settings` with monkeypatch and never start the app.
"""

import json

import pytest
from api_helpers import make_client

from app import auth
from app.config import DEV_AUTH_SECRET_DEFAULT, SCHEDULER_TOKEN_DEFAULT, WATCHDOG_TOKEN_DEFAULT, settings

GOOD = {"x": "a" * 32, "y": "b" * 32, "z": "c" * 32}


def configure(monkeypatch, *, env, login, secret, sched=GOOD["y"], dog=GOOD["z"]):
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "dev_login_enabled", login)
    monkeypatch.setattr(settings, "dev_auth_secret", secret)
    monkeypatch.setattr(settings, "scheduler_service_token", sched)
    monkeypatch.setattr(settings, "watchdog_service_token", dog)


def forge(claims, secret):
    body = auth._b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{auth._sign(body, secret)}"


SECRETS = {
    "default": DEV_AUTH_SECRET_DEFAULT,
    "empty": "",
    "31 bytes": "s" * 31,
    "32 bytes": "s" * 32,
    "multibyte 32 bytes": "é" * 16,  # 16 characters, 32 bytes
    "multibyte 30 bytes": "é" * 15,
}


@pytest.mark.parametrize("env", ["dev", "prod", "production", "staging", "test", "", "DEV", "Dev"])
@pytest.mark.parametrize("login", [True, False])
@pytest.mark.parametrize("secret_name", list(SECRETS))
def test_secret_and_login_matrix(monkeypatch, env, login, secret_name):
    secret = SECRETS[secret_name]
    configure(monkeypatch, env=env, login=login, secret=secret)
    secret_ok = len(secret.encode()) >= 32 and secret != DEV_AUTH_SECRET_DEFAULT
    should_start = env == "dev" or (not login and secret_ok)  # only the exact string "dev" is dev
    if should_start:
        auth.assert_secure_config()
        return
    with pytest.raises(RuntimeError) as exc:
        auth.assert_secure_config()
    message = str(exc.value)
    if env != "dev" and login:
        assert "DEV_LOGIN_ENABLED" in message
    if env != "dev" and not secret_ok:
        assert "DEV_AUTH_SECRET" in message


@pytest.mark.parametrize(
    ("sched", "dog", "needle"),
    [
        (SCHEDULER_TOKEN_DEFAULT, GOOD["z"], "SCHEDULER_SERVICE_TOKEN is the built-in"),
        (GOOD["y"], WATCHDOG_TOKEN_DEFAULT, "WATCHDOG_SERVICE_TOKEN is the built-in"),
        ("", GOOD["z"], "SCHEDULER_SERVICE_TOKEN is empty"),
        (GOOD["y"], "", "WATCHDOG_SERVICE_TOKEN is empty"),
        ("s" * 31, GOOD["z"], "SCHEDULER_SERVICE_TOKEN is shorter than 32"),
        (GOOD["y"], "w" * 31, "WATCHDOG_SERVICE_TOKEN is shorter than 32"),
        ("same" * 10, "same" * 10, "are the same value"),
    ],
)
def test_service_tokens_are_checked(monkeypatch, sched, dog, needle):
    configure(monkeypatch, env="prod", login=False, secret=GOOD["x"], sched=sched, dog=dog)
    with pytest.raises(RuntimeError, match=needle):
        auth.assert_secure_config()
    configure(monkeypatch, env="dev", login=True, secret=DEV_AUTH_SECRET_DEFAULT, sched=sched, dog=dog)
    auth.assert_secure_config()  # dev accepts anything


def test_a_good_production_configuration_starts(monkeypatch):
    configure(monkeypatch, env="prod", login=False, secret=GOOD["x"])
    auth.assert_secure_config()
    assert auth.secure_config_problems() == []


def test_every_problem_is_reported_at_once(monkeypatch):
    configure(monkeypatch, env="prod", login=True, secret=DEV_AUTH_SECRET_DEFAULT,
              sched=SCHEDULER_TOKEN_DEFAULT, dog="")
    with pytest.raises(RuntimeError) as exc:
        auth.assert_secure_config()
    for needle in ("DEV_LOGIN_ENABLED", "DEV_AUTH_SECRET", "SCHEDULER_SERVICE_TOKEN", "WATCHDOG_SERVICE_TOKEN"):
        assert needle in str(exc.value)


@pytest.mark.parametrize("env", ["prod", "staging", "test", "", "Dev"])
def test_token_forged_with_the_default_secret_is_401_outside_dev(monkeypatch, env):
    client = make_client()
    forged = forge({"v": 1, "role": "supervisor", "name": "mallory"}, DEV_AUTH_SECRET_DEFAULT)
    # The same forgery works in dev (that is what dev is), which proves the test forges correctly.
    configure(monkeypatch, env="dev", login=True, secret=DEV_AUTH_SECRET_DEFAULT)
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 200
    # Outside dev: refused with the default secret still configured ...
    configure(monkeypatch, env=env, login=False, secret=DEV_AUTH_SECRET_DEFAULT)
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    # ... and refused when a real secret is configured (the forged signature simply fails) ...
    configure(monkeypatch, env=env, login=False, secret=GOOD["x"])
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    # ... while a token signed with the real secret works.
    genuine = forge({"v": 1, "role": "supervisor", "name": "carol"}, GOOD["x"])
    assert client.get("/me", headers={"Authorization": f"Bearer {genuine}"}).status_code == 200


def test_verify_token_refuses_an_explicit_default_secret_outside_dev(monkeypatch):
    token = forge({"v": 1, "role": "lab", "name": "x"}, DEV_AUTH_SECRET_DEFAULT)
    configure(monkeypatch, env="dev", login=True, secret=DEV_AUTH_SECRET_DEFAULT)
    assert auth.verify_token(token, secret=DEV_AUTH_SECRET_DEFAULT).role == "lab"
    configure(monkeypatch, env="prod", login=False, secret=GOOD["x"])
    with pytest.raises(auth.AuthError):
        auth.verify_token(token, secret=DEV_AUTH_SECRET_DEFAULT)
    with pytest.raises(auth.AuthError):
        auth.verify_token(token)


def test_tokens_cannot_be_minted_with_the_default_secret_outside_dev(monkeypatch):
    configure(monkeypatch, env="prod", login=False, secret=DEV_AUTH_SECRET_DEFAULT)
    with pytest.raises(auth.AuthError):
        auth.issue_token("lab", "x")


@pytest.mark.parametrize("env", ["prod", "staging", ""])
def test_dev_login_is_404_outside_dev_even_if_the_flag_is_on(monkeypatch, env):
    configure(monkeypatch, env=env, login=True, secret=GOOD["x"])
    r = make_client().post("/auth/dev-login", json={"role": "lab", "name": "x"})
    assert r.status_code == 404


def test_an_empty_service_token_never_authenticates(monkeypatch):
    configure(monkeypatch, env="dev", login=True, secret=DEV_AUTH_SECRET_DEFAULT, sched="", dog="")
    assert auth.service_identity("") is None
    r = make_client().get("/me", headers={"Authorization": "Bearer x"})
    assert r.status_code == 401


def test_the_default_service_tokens_still_work_in_dev(monkeypatch):
    configure(monkeypatch, env="dev", login=True, secret=DEV_AUTH_SECRET_DEFAULT,
              sched=SCHEDULER_TOKEN_DEFAULT, dog=WATCHDOG_TOKEN_DEFAULT)
    assert auth.service_identity(SCHEDULER_TOKEN_DEFAULT).role == "scheduler"
    assert auth.service_identity(WATCHDOG_TOKEN_DEFAULT).role == "watchdog"

