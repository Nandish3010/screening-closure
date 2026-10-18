"""T-A72 and T-A73 (A26, ADR-037): the outreach policy is unsigned by default, and an unsigned policy sends nothing.

The shipped `config/outreach_policy.yaml` says `approved_by: null`. While that is so the append-time guard refuses every
automatic `message_sent` and the automation scan only counts and logs what it held back (`outreach_held`, reason
`policy_unsigned`, once per case, no event). `OUTREACH_DEMO_APPROVAL=1` (compose) stands in for the signature under the
text "DEMO PLACEHOLDER (clinical sign-off pending, H1)", which `/health` reports; a real signature in the file wins; and the
API refuses to start outside dev with the switch on (test_prod_config.py)."""

from __future__ import annotations

import dataclasses
import json
import logging
from uuid import UUID

import pytest

from app import automation, notify, outreach_policy
from app.config import settings as app_settings
from app.state_machine import InvalidTransition
from test_automation import (  # noqa: F401  (`fresh` is a fixture)
    PATHWAY, accepted_case_store, append_auto, auto_msg, client, clock, fresh, log, model, owner_accepted_case, scan,
    sent, types, world,
)

DEMO = outreach_policy.DEMO_APPROVER


@pytest.fixture
def unsigned(monkeypatch):
    """The shipped state: the policy is unsigned and nobody has switched the demo placeholder on."""
    monkeypatch.setattr(app_settings, "outreach_demo_approval", False)
    automation.outreach_held_logged.clear()
    yield
    automation.outreach_held_logged.clear()


def signed_policy(monkeypatch, name="Dr. Clinical Lead"):
    pol = outreach_policy.get_policy()
    monkeypatch.setattr(outreach_policy, "get_policy", lambda path=None: dataclasses.replace(pol, approved_by=name))


# ---- T-A72: what "signed" means --------------------------------------------------------------------------------------


def test_t_a72_the_shipped_policy_is_unsigned_and_the_demo_text_is_the_agreed_one(unsigned):
    pol = outreach_policy.get_policy()
    assert pol.approved_by is None and not pol.signed
    assert DEMO == "DEMO PLACEHOLDER (clinical sign-off pending, H1)"
    assert outreach_policy.signed_by(pol) is None


def test_t_a72_the_demo_switch_stands_in_and_a_real_signature_wins(unsigned, monkeypatch):
    pol = outreach_policy.get_policy()
    monkeypatch.setattr(app_settings, "outreach_demo_approval", True)
    assert outreach_policy.signed_by(pol) == DEMO
    signed = dataclasses.replace(pol, approved_by="Dr. Clinical Lead")
    assert outreach_policy.signed_by(signed) == "Dr. Clinical Lead"  # the demo text never overwrites a signature
    monkeypatch.setattr(app_settings, "outreach_demo_approval", False)
    assert outreach_policy.signed_by(signed) == "Dr. Clinical Lead"


def test_t_a72_the_loader_refuses_a_blank_approver_the_demo_text_and_a_per_template_one():
    import yaml

    raw = yaml.safe_load(open(app_settings.outreach_policy_path))
    assert raw["approved_by"] is None
    for edit, needle in ((lambda r: r.update(approved_by="  "), "approved_by"), (lambda r: r.update(approved_by=7), "approved_by"),
                         (lambda r: r.update(approved_by=DEMO), "demo placeholder"),
                         (lambda r: r["templates"][0].update(approved_by="x"), "policy's")):
        bad = json.loads(json.dumps(raw))
        edit(bad)
        with pytest.raises(outreach_policy.OutreachPolicyError, match=needle):
            outreach_policy.parse_policy(bad)
    ok = json.loads(json.dumps(raw))
    ok["approved_by"] = "  Dr. Clinical Lead "
    assert outreach_policy.parse_policy(ok).approved_by == "Dr. Clinical Lead"


def test_t_a72_health_reports_the_state_of_the_approval(unsigned, monkeypatch):
    off = client.get("/health").json()["outreach"]
    assert off == {"usable": True, "signed": False, "demo_approval": False, "approved_by": None, "automatic_sending": False}
    monkeypatch.setattr(app_settings, "outreach_demo_approval", True)
    demo = client.get("/health").json()["outreach"]
    assert demo == {"usable": True, "signed": False, "demo_approval": True, "approved_by": DEMO, "automatic_sending": True}
    signed_policy(monkeypatch)
    real = client.get("/health").json()["outreach"]
    assert real == {"usable": True, "signed": True, "demo_approval": False, "approved_by": "Dr. Clinical Lead",
                    "automatic_sending": True}
    body = client.get("/health").json()
    assert body["status"] == "ok" and "sim_time" in body  # the old fields are unchanged


def test_t_a72_a_policy_that_does_not_load_is_not_usable(unsigned, monkeypatch, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a policy\n")
    monkeypatch.setattr(app_settings, "outreach_policy_path", str(bad))
    outreach_policy._cached.cache_clear()
    try:
        assert outreach_policy.public_status() == {"usable": False, "signed": False, "demo_approval": False,
                                                   "approved_by": None, "automatic_sending": False}
    finally:
        outreach_policy._cached.cache_clear()


# ---- T-A73: an unsigned policy sends nothing -------------------------------------------------------------------------


def test_t_a73_the_append_guard_refuses_every_automatic_message_while_unsigned(unsigned):
    with world():
        store, cid = accepted_case_store()
        with pytest.raises(InvalidTransition, match="outreach policy is not signed"):
            append_auto(store, auto_msg(cid))
        assert "message_sent" not in [e.type for e in store.events_for(cid)]


def test_t_a73_the_guard_accepts_under_the_demo_placeholder_and_under_a_real_signature(unsigned, monkeypatch):
    with world():
        store, cid = accepted_case_store()
        monkeypatch.setattr(app_settings, "outreach_demo_approval", True)
        append_auto(store, auto_msg(cid))
        monkeypatch.setattr(app_settings, "outreach_demo_approval", False)
        signed_policy(monkeypatch)
        append_auto(store, auto_msg(cid, template_id="reminder.sms", purpose="reminder"))
        assert [e.type for e in store.events_for(cid)].count("message_sent") == 2


def test_t_a73_notify_send_cannot_send_automatically_while_unsigned(fresh, unsigned):
    cid = owner_accepted_case()
    with pytest.raises(InvalidTransition, match="not signed"):
        from app.routers import cases

        notify.send(UUID(cid), "sms", "en", "result_notification", "hello", at=clock.now(), store=cases.store,
                    pathway=PATHWAY, auto={"template_id": "result_notification.sms", "template_version": 1})
    assert not notify.log.for_case(UUID(cid)) and not sent(cid)


def test_t_a73_the_scan_sends_nothing_counts_it_and_logs_once_per_case(fresh, unsigned, caplog):
    cid = owner_accepted_case()
    before = types(cid)
    with caplog.at_level(logging.WARNING, logger="app.automation"):
        out = scan()
        again = scan()
    assert out.get("outreach_sent") is None and out["outreach_held"] >= 1 and out["outreach_held_policy_unsigned"] == 1
    assert again["outreach_held_policy_unsigned"] == 1  # still counted on every scan: it is still due
    held = [r for r in caplog.records if r.getMessage().startswith("outreach_held ")]
    assert len(held) == 1  # once per case, not once per scan
    payload = json.loads(held[0].getMessage().removeprefix("outreach_held "))
    assert payload["reason"] == "policy_unsigned" and payload["case_id"] == cid
    assert payload["rules"] == ["result_notice_on_owner_accepted"]
    assert types(cid) == before and not sent(cid) and not notify.log.for_case(UUID(cid))  # no event, no notification row


def test_t_a73_each_case_is_logged_once(fresh, unsigned, caplog):
    first, second = owner_accepted_case(), owner_accepted_case()
    with caplog.at_level(logging.WARNING, logger="app.automation"):
        scan()
        scan()
    cases_logged = [json.loads(r.getMessage().removeprefix("outreach_held "))["case_id"]
                    for r in caplog.records if r.getMessage().startswith("outreach_held ")]
    assert sorted(cases_logged) == sorted([first, second])


def test_t_a73_signing_the_policy_releases_the_messages_that_were_held(fresh, unsigned, monkeypatch):
    cid = owner_accepted_case()
    assert scan().get("outreach_sent") is None and not sent(cid)
    monkeypatch.setattr(app_settings, "outreach_demo_approval", True)  # the demo placeholder
    assert scan()["outreach_sent"] == 1 and len(sent(cid)) == 1  # still due: sent now, once


def test_t_a73_a_real_signature_sends_without_the_demo_switch(fresh, unsigned, monkeypatch):
    signed_policy(monkeypatch)
    cid = owner_accepted_case()
    assert scan()["outreach_sent"] == 1 and len(sent(cid)) == 1


def test_t_a73_the_rest_of_the_automation_does_not_depend_on_the_signature(fresh, unsigned):
    """Intake, re-routing and the evidence scan are not messaging: an unsigned policy leaves them alone."""
    cid = owner_accepted_case()
    assert model(cid).owner is not None  # acknowledged and assigned at ingest
    assert types(cid)[:3] == ["case_created", "acknowledge", "assign_ownership"]


def test_t_a73_replay_never_reads_the_signature(unsigned):
    """A log with automatic messages replays the same whether or not the policy is signed (the state machine does not read
    the file)."""
    from app.state_machine import replay_model

    with world():
        store, cid = accepted_case_store()
        app_settings.outreach_demo_approval = True
        try:
            append_auto(store, auto_msg(cid))
        finally:
            app_settings.outreach_demo_approval = False
        assert replay_model(store.events_for(cid), PATHWAY).auto_message_times
