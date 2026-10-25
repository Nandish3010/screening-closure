"""A35: the demo-cache recorder and the red-team generator, end to end against a fake Anthropic client (no network).

Records into a temporary cache, then replays offline (network off, replay mode) through the real drafting route and the real
agency run on the 22-case cohort, and checks the replay used the recording.
"""

from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agency_world as world
from app import evidence
from app.agency import planner, runner
from app.ai import cache, claude
from app.clock import clock
from app.routers import cases, drafts

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "claude"))
import record_demo  # noqa: E402
import redteam_generate  # noqa: E402


def _usage():
    return SimpleNamespace(input_tokens=200, output_tokens=60, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeAnthropic:
    """Answers drafting prompts with a plain valid message and planning prompts with the reversed order."""

    def __init__(self, **_):
        self.messages = SimpleNamespace(create=self._create)
        self.calls = []

    def _create(self, **kw):
        self.calls.append(kw)
        user = kw["messages"][0]["content"]
        if kw["model"] == "claude-sonnet-5-5" and "output_config" in kw and "format" in kw["output_config"]:
            if user.startswith("Attack class"):
                text = json.dumps({"variants": [{"text": redteam_generate.GOOD["en"] + " You have cancer."},
                                                {"text": redteam_generate.GOOD["en"] + " Thank you."}]})
            else:
                items = json.loads(user)["items"]
                text = json.dumps({"order": list(reversed(range(len(items)))), "rationale": "Newest items first."})
        else:
            owner = re.search(r"Care owner name to include: (.+)", user).group(1)
            step = re.search(r"Next step to include: (.+)", user).group(1)
            date = re.search(r"Appointment date to include: (.+)", user)
            if "Arabic" in user:
                text = f"نتيجة فحصك جاهزة. سيتواصل معك فريق رعايتك لمناقشة {step}. المسؤول عن رعايتك: {owner}."
                text += f" الموعد: {date.group(1)}." if date else ""
            else:
                text = (f"Your screening result is ready. The care team needs to talk with you about the next step, "
                        f"{step}. {owner} is your care owner and will contact you.")
                text += f" Your appointment is on {date.group(1)}." if date else ""
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=_usage())


@pytest.fixture
def env(monkeypatch, tmp_path):
    for module, attr in ((cases, "store"), (drafts, "store"), (evidence, "verifier")):
        monkeypatch.setattr(module, attr, getattr(module, attr))
    from app import notify, ops, users
    for module, attr in ((notify, "log"), (ops, "store"), (users, "directory")):
        monkeypatch.setattr(module, attr, getattr(module, attr))
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "usage.sqlite"))
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENCY_PLANNER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "5000000")
    import anthropic
    fake = FakeAnthropic()
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: fake)
    yield fake
    clock.reset()


def test_record_then_replay_the_whole_demo_offline(env, monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_NETWORK_ENABLED", "true")
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    counts = record_demo.record_drafts(record_demo.PERSONAS[:2])
    assert counts["drafts"] == 2 * 2 * 6 * 3  # personas x languages x purposes x channels
    agency = record_demo.record_agency()
    assert agency["status"] == "done" and agency["planned_by_claude"] >= 1
    recorded = len(list((tmp_path / "cache" / "anthropic").glob("*.json")))
    planned = len(list((tmp_path / "cache" / "agency" / "anthropic").glob("*.json")))
    assert recorded >= counts["drafts"] - counts["abstained"] and planned >= 1
    spent = sum(r["input_tokens"] + r["output_tokens"] for r in claude.UsageLedger().rows())
    assert spent > 0

    # the demo configuration: replay, network off, a socket would fail the test
    def refuse(*a, **k):
        raise AssertionError("outbound connection attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setenv("LLM_NETWORK_ENABLED", "false")
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    calls_before = len(env.calls)
    world.fresh_world()
    world.build_cohort()
    summary = runner.run_agency(trigger="manual")
    assert summary["status"] == "done" and len(env.calls) == calls_before
    heads = runner.ledger().query(agent_type="department_head")
    assert any(not t.fallback_used and t.provider == "anthropic" for t in heads)
    assert all(t.output.get("abstention_reason") in (None, "cache_miss") for t in heads if t.fallback_used)


def test_a_cache_miss_in_replay_is_the_template_not_an_error(env, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    world.fresh_world()
    world.build_cohort()
    summary = runner.run_agency(trigger="manual")
    assert summary["status"] == "done"
    heads = runner.ledger().query(agent_type="department_head")
    assert all(t.fallback_used and t.output["abstention_reason"] == "cache_miss" for t in heads)
    assert env.calls == []


def test_redteam_generator_writes_deterministic_cases_and_counts_slips(env, tmp_path):
    caller = claude._ClaudeCaller()
    import os
    os.environ["LLM_NETWORK_ENABLED"] = "true"
    try:
        totals = redteam_generate.generate(caller, "claude-sonnet-5-5", [1, 6], ["en"], 2, tmp_path / "gen")
        again = redteam_generate.generate(caller, "claude-sonnet-5-5", [1, 6], ["en"], 2, tmp_path / "gen")
    finally:
        del os.environ["LLM_NETWORK_ENABLED"]
    assert totals["added"] == 4 and totals["slipped"] == 2 and again["added"] == 0  # a rerun keeps ids and adds nothing
    data = json.loads((tmp_path / "gen" / "diagnosis_hint_en.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in data["cases"]] == ["RT-G-01E.1", "RT-G-01E.2"]
    assert data["cases"][0]["validator_at_generation"].startswith("rejected")
    assert data["cases"][1]["validator_at_generation"] == "accepted"
