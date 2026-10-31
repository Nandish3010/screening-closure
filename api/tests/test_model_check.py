"""A27 (ADR-038): the explicit-state model checker of tools/verify/model_check.py, and the bug it found.

* The regression tests for the bug: an approved draft was sent while an opt-out or wrong-number hold was open (the hold
  stopped only the automation's messages). The state machine now refuses it until a person clears the hold.
* Seeded mutations, each put in with monkeypatch and each caught with a counterexample: automation may close, the
  evidence check removed, message_sent without approval, the hold bug put back, the consent freeze removed, and (A36) the
  signer check removed and the referral hold removed, and (E20a F1) the physician check removed.
* The checker's own soundness checks bite: a signature that forgets a field step reads is reported (ABSTRACTION), and an
  event that reads another slice's fields is reported (INTERFERENCE).

The full run (every slice, every check) is `make verify`; here each run is one slice, with smaller samples.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from app import state_machine as sm
from app.events import Event

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
import model_check as mc  # noqa: E402
from test_outbox import crashing_drain, world  # noqa: E402,F401  (fixtures for the end-to-end outbox test)

FAST = {"abstraction_samples": 60, "interference_samples": 20}


def _ev(type_, actor, payload):
    return Event(mc.CASE, type_, mc.T0, actor, payload)


def _model_after(*moves):
    """Genesis, then (type, actor, payload) moves through the real step."""
    pw = mc.load_pathway(mc.PATHWAY_PATH)
    m = sm.genesis(sm.initial_model(pw), Event(mc.CASE, sm.CASE_CREATED, mc.T0, "system:intake", {}), pw)
    for type_, actor, payload in moves:
        m = sm.step(m, _ev(type_, actor, payload), pw)
    return m, pw


SEND_D1 = ("message_sent", sm.NOTIFIER_ACTOR, {"draft_id": "d1", "channel": "sms", "language": "en",
                                               "purpose": "reminder", "notification_id": "n1"})


# ---- the bug: an approved draft went out past an open outreach hold ------------------------------------------------


@pytest.mark.parametrize("hold, clear", [
    ("opt_out", ("consent_updated", mc.IDENTITIES["patient"].actor, {"channel_sms": True})),
    ("wrong_number", ("contact_updated", mc.COORD, {"contact_valid": True})),
])
def test_an_approved_draft_is_not_sent_while_a_hold_is_open(hold, clear):
    """Found by the model checker (I3): approve a draft, the patient replies STOP (or it is the wrong number), the outbox
    drains: the message used to go out. Now the machine refuses it, and sends it once a person has cleared the hold."""
    m, pw = _model_after(
        ("message_approved", mc.PCO, {"draft_id": "d1"}),
        ("review_requested", sm.TRIAGE_ACTOR, {"reason": hold, "source_event_id": "e-1"}),
    )
    assert m.outreach_holds == (hold,) and m.approved_unsent == ("d1",)
    with pytest.raises(sm.InvalidTransition, match="held"):
        sm.step(m, _ev(*SEND_D1), pw)
    cleared = sm.step(m, _ev(*clear), pw)
    assert cleared.outreach_holds == ()
    sent = sm.step(cleared, _ev(*SEND_D1), pw)
    assert sent.approved_unsent == ()


def test_the_outbox_drain_blocks_a_draft_approved_before_the_opt_out(world, crashing_drain):
    """End to end: the draft is approved, the drain does not run (the process died after the commit), the patient's
    reply opens an opt-out hold, the drain runs: the row is blocked and no message_sent is written."""
    from uuid import UUID, uuid4

    from test_outbox import PATHWAY, approve, make_draft, owned_case, types

    from app import outbox
    from app.routers import cases, drafts

    cid, _ = owned_case()
    approve(make_draft(cid))
    cases.store.append(Event(UUID(cid), "review_requested", mc.T0, sm.TRIAGE_ACTOR,
                             {"reason": "opt_out", "source_event_id": str(uuid4())}), PATHWAY)
    crashing_drain()
    assert [(o.status, o.reason) for o in drafts.drain_outbox()] == [(outbox.BLOCKED, "case_state")]
    assert "message_sent" not in types(cid)
    assert drafts.drain_outbox() == []  # final: not retried


# ---- the unmutated machine passes ------------------------------------------------------------------------------------


@pytest.mark.parametrize("slice_name", ["messaging", "ownership"])
def test_the_shipped_machine_passes_every_invariant(slice_name):
    r = mc.check([slice_name], **FAST)
    assert r.ok, mc.render(r)
    s = r.slices[0]
    assert s.saturated and s.levels[-1] > 0 and s.states > 500  # the last level found states, the next found none
    assert {"I1 closures", "I2 human decisions", "I5 open states", "I6 replays", "I7 states", "I8 erasures"} <= set(s.checked)
    assert s.table_probes > 0 and s.abstraction_pairs > 0 and s.interference_pairs > 0


def test_a_bound_too_small_is_reported_as_not_saturated():
    r = mc.check(["messaging"], max_depth=5, **FAST)
    assert not r.ok and not r.slices[0].saturated and not r.violations


# ---- seeded mutations: each is caught, with a counterexample -------------------------------------------------------


def _only(r, invariant: str) -> mc.Violation:
    assert not r.ok, "the mutation was not caught"
    v = r.violations[0]
    assert v.invariant == invariant, v.render()
    assert v.trace, "a violation comes with a counterexample trace"
    return v


def test_mutation_automation_may_close_is_caught(monkeypatch):
    """The closure guard loses its automated-actor check (and the physician rule its refusal of the automation), and the
    automation's row gains `close`."""
    monkeypatch.setitem(sm.ACTOR_EVENTS, sm.AUTOMATION_ACTOR, sm.ACTOR_EVENTS[sm.AUTOMATION_ACTOR] | {"close"})
    monkeypatch.setattr(sm, "is_automated", lambda actor: actor.startswith(("scheduler", "watchdog", "model:"))
                        or (actor.startswith("system:") and actor != sm.AUTOMATION_ACTOR))
    original = sm._check_physician  # since E20a F1 the physician rule would refuse it too: let the automation through
    monkeypatch.setattr(sm, "_check_physician", lambda event: None if event.actor == sm.AUTOMATION_ACTOR else original(event))
    v = _only(mc.check(["ownership"], **FAST), "I1")
    last = v.trace[-1]
    assert last[0] == "close" and last[1] == sm.AUTOMATION_ACTOR
    # shortest: straight down the main path, or to a recorded refusal (with the physician rule open to it, the automation
    # may sign a declined closure too)
    assert [t for t, *_ in v.trace][-2] in ("clinician_review", "record_informed_refusal")


def test_mutation_evidence_check_removed_is_caught(monkeypatch):
    """`close` no longer needs evidence_ref and signed_by (the `requires` of the transition is ignored)."""
    original = sm._transition
    monkeypatch.setattr(sm, "_transition", lambda model, event, pw, t: original(
        model, event, pw, dataclasses.replace(t, requires=()) if t.to_state == pw.terminal_state else t))
    v = _only(mc.check(["ownership"], **FAST), "I1")
    assert v.trace[-1][0] == "close" and "without" in v.message
    assert not v.trace[-1][3].get("evidence_ref") or not v.trace[-1][3].get("signed_by")


def test_mutation_message_sent_without_approval_is_caught(monkeypatch):
    """The notifier's approval gate is gone: any draft id passes as approved."""
    original = sm._precheck

    def precheck(model, event, pw):
        if event.type == "message_sent" and event.actor == sm.NOTIFIER_ACTOR:
            model = dataclasses.replace(model, approved_unsent=(*model.approved_unsent, event.payload.get("draft_id")))
        return original(model, event, pw)

    monkeypatch.setattr(sm, "_precheck", precheck)
    v = _only(mc.check(["messaging"], **FAST), "I3")
    assert "without an unsent approval" in v.message
    assert len(v.trace) == 1 and v.trace[0][0] == "message_sent"  # minimal: the very first event


def test_mutation_the_hold_bug_put_back_is_caught(monkeypatch):
    """The bug this ticket fixed, reintroduced: the notifier's send ignores the outreach holds."""
    original = sm._precheck

    def precheck(model, event, pw):
        if event.type == "message_sent" and event.actor == sm.NOTIFIER_ACTOR:
            model = dataclasses.replace(model, outreach_holds=())
        return original(model, event, pw)

    monkeypatch.setattr(sm, "_precheck", precheck)
    v = _only(mc.check(["messaging"], **FAST), "I3")
    assert "hold" in v.message
    assert [t for t, *_ in v.trace] == ["message_approved", "review_requested", "message_sent"]


def test_mutation_consent_freeze_removed_is_caught(monkeypatch):
    monkeypatch.setattr(sm, "CONSENT_FROZEN_EVENTS", frozenset())
    v = _only(mc.check(["messaging"], **FAST), "I4")
    assert v.trace[-2][0] == "consent_updated" and v.trace[-1][0] in mc.OUTREACH


def test_mutation_signer_check_removed_is_caught(monkeypatch):
    """A36 (ADR-044): the machine's signer = closer rule is gone. Since A36 there is no route-gate exception for it, so I1
    reports the close signed in someone else's name with a counterexample."""
    monkeypatch.setattr(sm, "_check_signer", lambda event: None)
    v = _only(mc.check(["ownership"], **FAST), "I1")
    assert v.trace[-1][0] in ("close", "clinician_review") and "signer is not" in v.message
    assert v.trace[-1][3].get("signed_by") == mc.FOREIGN_SIGNER


def test_mutation_physician_check_removed_is_caught(monkeypatch):
    """E20a F1 (M8): the machine's physician rule for `close` and `clinician_review` is gone, so a coordinator or a
    supervisor (both in the cast) may review and close. I1 reports it with a counterexample."""
    monkeypatch.setattr(sm, "_check_physician", lambda event: None)
    v = _only(mc.check(["ownership"], **FAST), "I1")
    assert v.trace[-1][0] in ("close", "clinician_review") and "not a physician role" in v.message
    assert v.trace[-1][1].split(":", 1)[0] not in sm.PHYSICIAN_ROLES


def test_mutation_referral_hold_removed_is_caught(monkeypatch):
    """A36 (ADR-044): a held referral may be accepted again (the hold reads as none). I4 reports it."""
    monkeypatch.setattr(sm, "referral_hold", lambda model: None)
    v = _only(mc.check(["ownership"], **FAST), "I4")
    assert "held" in v.message
    assert "consent_updated" in [t for t, *_ in v.trace] and v.trace[-1][0] in (*mc.REFERRAL, "handoff_rejected")


def test_a_signature_without_the_sharing_flag_is_reported(monkeypatch):
    """A36: the referral hold reads `share_with_specialist`; a signature that drops it merges models step tells apart."""
    original = mc.signature

    def coarse(m):
        core, msg, own, lad = original(m)
        return (*core[:5], True, *core[6:]), msg, own, lad

    monkeypatch.setattr(mc, "signature", coarse)
    _only(mc.check(["ownership"], abstraction_samples=5000, interference_samples=0), "ABSTRACTION")


def test_mutation_erased_case_may_be_reopened_is_caught(monkeypatch):
    """A32 (I8): an erased case accepts `reopen` again (the erased flag is ignored for it)."""
    original = sm.step

    def step(model, event, pw, *a, **kw):
        if model.erased and event.type == "reopen":
            model = dataclasses.replace(model, erased=False)
        return original(model, event, pw, *a, **kw)

    monkeypatch.setattr(sm, "step", step)
    v = _only(mc.check(["ownership"], **FAST), "I8")
    assert v.trace[-2][0] == sm.ERASURE_EVENT and v.trace[-1][0] == "reopen"


# ---- the checker's own soundness checks bite ------------------------------------------------------------------------


def test_a_signature_that_forgets_what_step_reads_is_reported(monkeypatch):
    """Drop the outreach holds from the signature: two models step treats differently now look the same."""
    original = mc.signature

    def coarse(m):
        core, msg, own, lad = original(m)
        return core, (msg[0], (), msg[2]), own, lad

    monkeypatch.setattr(mc, "signature", coarse)
    _only(mc.check(["messaging"], abstraction_samples=5000, interference_samples=0), "ABSTRACTION")


def test_an_event_that_reads_another_slices_fields_is_reported(monkeypatch):
    """Make `close` read the messaging fields (refused while a hold is open): the ownership slice never varies them,
    so only the non-interference check can see it."""
    original = sm._precheck

    def precheck(model, event, pw):
        if event.type == "close" and model.outreach_holds:
            raise sm.InvalidTransition("mutation: close waits for the holds")
        return original(model, event, pw)

    monkeypatch.setattr(sm, "_precheck", precheck)
    v = _only(mc.check(["ownership"], abstraction_samples=0, interference_samples=2000), "INTERFERENCE")
    assert "messaging" in v.message and "close" in v.message


# ---- the second pathway is explored by the same checker ---------------------------------------------------------------


CERVICAL_PACK = ROOT / "config/pathways/cervical_hpv.yaml"


@pytest.mark.parametrize("slice_name", ["messaging", "ownership", "ladders"])
def test_the_cervical_pathway_passes_every_invariant_on_every_slice(slice_name):
    """The second pathway on the same machine: I1 to I8 hold, the search saturates, and its state counts are reported
    on their own. The pack extends the bowel pack, so the count equals the bowel pack's for the same slice: a difference
    would mean the pack changed a state, a transition or a timing the model reads."""
    cervical = mc.load_pathway(CERVICAL_PACK)
    bowel = mc.load_pathway(mc.PATHWAY_PATH)
    r = mc.check([slice_name], cervical, **FAST)
    assert r.ok and r.pathway == "cervical_hpv", mc.render(r)
    s = r.slices[0]
    assert s.saturated and s.levels[-1] > 0 and s.states > 500
    assert {"I1 closures", "I2 human decisions", "I5 open states", "I6 replays", "I7 states", "I8 erasures"} <= set(s.checked)
    assert s.checked["I1 closures"] > 0, "the cervical closure guard was exercised"
    reference = mc.check([slice_name], bowel, **FAST).slices[0]
    assert s.states == reference.states and s.states_by_name == reference.states_by_name


def test_the_closure_guard_mutation_is_caught_on_the_cervical_pathway_too(monkeypatch):
    """Remove the evidence requirement from `close` in the machine: caught on the cervical pack exactly as on the bowel pack."""
    original = sm._transition

    def lax(model, event, pathway, t):
        if event.type == "close":
            t = dataclasses.replace(t, requires=tuple(r for r in t.requires if r != "evidence_ref"))
        return original(model, event, pathway, t)

    monkeypatch.setattr(sm, "_transition", lax)
    r = mc.check(["ownership"], mc.load_pathway(CERVICAL_PACK), **FAST)
    assert not r.ok and r.violations[0].invariant == "I1", mc.render(r)


def test_the_command_line_names_packs_and_reports_each_pathways_counts(capsys):
    assert [p.name for p in mc.pack_paths(None, True)] == ["colorectal_fit.yaml", "cervical_hpv.yaml"]
    assert mc.pack_paths(["cervical_hpv"], False) == [CERVICAL_PACK]
    with pytest.raises(SystemExit):
        mc.pack_paths(["no_such_pack"], False)
    code = mc.main(["--slice", "messaging", "--pathway", "colorectal_fit", "--pathway", "cervical_hpv",
                    "--abstraction-samples", "20", "--interference-samples", "10"])
    out = capsys.readouterr().out
    assert code == 0
    assert "== pathway colorectal_fit ==" in out and "== pathway cervical_hpv ==" in out
    assert "model-check summary: PASS" in out and "cervical_hpv:" in out and "colorectal_fit:" in out
