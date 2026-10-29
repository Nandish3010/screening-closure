"""A35 (ADR-043): Claude as a provider, with a mocked Anthropic client. No network anywhere in this file."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agency import planner
from app.ai import cache, claude
from app.ai.adapters import ProviderResponseError
from app.ai.cache import CachingProvider, read_entry
from app.ai.provider import (
    SYSTEM_PROMPT,
    BudgetExhausted,
    CacheMiss,
    DraftRequest,
    ProviderUnavailable,
    build_prompt,
    get_provider,
    prompt_hash,
)
from app.ai.validate import AbstentionLog, draft_with_fallback

GOOD = "Your screening result is ready. Dr Sara Khan will arrange a camera test and the care team will contact you."


def make_req(**over) -> DraftRequest:
    base = dict(purpose="result_notification", channel="sms", language="en", owner_name="Dr Sara Khan",
                owner_role="primary_care_owner", next_step="a camera test", pathway_name="colorectal_fit")
    base.update(over)
    return DraftRequest(**base)


def reply(text=GOOD, stop="end_turn", inp=120, out=40, cache_read=0, cache_write=0):
    usage = SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read,
                            cache_creation_input_tokens=cache_write)
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


class FakeClient:
    """Stands in for `anthropic.Anthropic`: records the kwargs of each `messages.create`."""

    def __init__(self, *responses, error=None):
        self.responses, self.error, self.calls = list(responses), error, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.responses[min(len(self.calls), len(self.responses)) - 1]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for name in ("LLM_CACHE_MODE", "LLM_NETWORK_ENABLED", "LLM_DAILY_TOKEN_BUDGET", "LLM_MAX_TOKENS_PER_CALL",
                 "LLM_MAX_INPUT_TOKENS_PER_CALL", "ANTHROPIC_DRAFT_MODEL", "ANTHROPIC_PLAN_MODEL", "AGENCY_PLANNER",
                 "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "usage.sqlite"))

    def refuse(*a, **k):
        raise AssertionError("outbound connection attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setenv("LLM_NETWORK_ENABLED", "true")


def provider(client) -> claude.ClaudeProvider:
    return claude.ClaudeProvider(client=client)


# ---------------------------------------------------------------- provider contract


def test_registered_and_wrapped_by_the_cache():
    p = get_provider("anthropic")
    assert isinstance(p, CachingProvider) and p.name == "anthropic" and p.model_id == "claude-haiku-5-5"


def test_models_default_to_haiku_for_drafting_and_sonnet_for_planning(monkeypatch):
    assert claude.draft_model() == "claude-haiku-5-5" and claude.plan_model() == "claude-sonnet-5-5"
    monkeypatch.setenv("ANTHROPIC_DRAFT_MODEL", "claude-haiku-x")
    assert claude.draft_model() == "claude-haiku-x"


def test_draft_sends_the_existing_prompt_and_returns_a_result_with_token_counts(online):
    fake = FakeClient(reply(cache_read=7))
    req = make_req()
    result = provider(fake).draft(req)
    assert result.text == GOOD and result.provider == "anthropic" and result.model_id == "claude-haiku-5-5"
    assert result.fallback_used is False and result.prompt_hash == prompt_hash(req)
    assert result.usage == {"input_tokens": 120, "output_tokens": 40, "cache_read_input_tokens": 7,
                            "cache_creation_input_tokens": 0}
    (call,) = fake.calls
    assert call["model"] == "claude-haiku-5-5" and call["messages"] == [{"role": "user", "content": build_prompt(req)}]
    assert call["system"][0]["text"] == SYSTEM_PROMPT
    assert call["output_config"] == {"effort": "low"} and call["thinking"] == {"type": "disabled"}
    assert not {"temperature", "top_p", "top_k", "tools", "tool_choice"} & set(call)
    assert call["max_tokens"] == claude.DEFAULT_CALL_OUTPUT_CAP


def test_no_patient_data_in_the_request(online):
    fake = FakeClient(reply())
    provider(fake).draft(make_req())
    sent = json.dumps(fake.calls[0], ensure_ascii=False)
    assert "patient_ref" not in sent and "@" not in sent and "http" not in sent


def test_network_off_never_reaches_the_client():
    fake = FakeClient(reply())
    with pytest.raises(ProviderUnavailable, match="LLM_NETWORK_ENABLED"):
        provider(fake).draft(make_req())
    assert fake.calls == []


def test_without_a_key_the_provider_is_unavailable_and_never_imports_the_sdk(online):
    with pytest.raises(ProviderUnavailable, match="ANTHROPIC_API_KEY"):
        claude.ClaudeProvider().draft(make_req())


# ---------------------------------------------------------------- fallback paths (always the template, with a reason)


@pytest.mark.parametrize("client, reason", [
    (FakeClient(error=TimeoutError("secret-ish detail")), "provider_unavailable"),
    (FakeClient(reply(stop="refusal", text=None)), "provider_error:ProviderResponseError"),
    (FakeClient(reply(stop="max_tokens", text="Your screening")), "provider_error:ProviderResponseError"),
    (FakeClient(reply(text=None)), "provider_error:ProviderResponseError"),
    (FakeClient(reply(text="You have cancer. Dr Sara Khan, a camera test.")), "validation_failed"),
    (FakeClient(reply(text="Hello, nothing else.")), "validation_failed"),
])
def test_every_failure_falls_back_to_the_template_with_an_abstention_reason(online, client, reason):
    log = AbstentionLog()
    req = make_req()
    out = draft_with_fallback(req, provider(client), log)
    assert out.fallback_used and out.provider == "template" and out.abstention_reason.startswith(reason)
    assert out.text and "cancer" not in out.text
    assert log.count == 1


def test_an_sdk_error_message_never_reaches_the_reason(online):
    out = draft_with_fallback(make_req(), provider(FakeClient(error=RuntimeError("sk-ant-SECRET"))), AbstentionLog())
    assert "SECRET" not in repr(out)


def test_valid_output_is_used_after_validation(online):
    out = draft_with_fallback(make_req(), provider(FakeClient(reply())), AbstentionLog())
    assert out.text == GOOD and not out.fallback_used and out.provider == "anthropic"


# ---------------------------------------------------------------- budget


def test_budget_exhausted_reason_and_no_call(online, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "100")
    fake = FakeClient(reply())
    log = AbstentionLog()
    out = draft_with_fallback(make_req(), provider(fake), log)
    assert out.fallback_used and out.abstention_reason == "budget_exhausted" and fake.calls == []
    assert log.by_category()["budget_exhausted"] == 1


def test_spend_accumulates_across_calls_and_provider_instances(online, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS_PER_CALL", "300")
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "1700")
    fake = FakeClient(reply(inp=400, out=200))  # 600 per call
    results = [draft_with_fallback(make_req(), claude.ClaudeProvider(client=fake), AbstentionLog()) for _ in range(4)]
    # two real calls fit (600 + 600 = 1200, the third needs the prompt estimate + 300, more than the 500 left)
    assert [r.fallback_used for r in results] == [False, False, True, True]
    assert results[2].abstention_reason == "budget_exhausted"
    assert claude.UsageLedger().spent() == 1200


def test_the_counter_is_persistent_and_the_day_rolls_over(tmp_path):
    ledger = claude.UsageLedger(tmp_path / "u.sqlite")
    now = datetime(2026, 11, 3, 23, 0, tzinfo=UTC)
    row = ledger.reserve("draft:x", "m", "h" * 64, 10, 100, now=now)
    ledger.settle(row, {"input_tokens": 10, "output_tokens": 50})
    assert claude.UsageLedger(tmp_path / "u.sqlite").spent(now) == 60  # a new object, same file
    assert ledger.spent(now + timedelta(hours=2)) == 0  # next UTC day
    rows = ledger.rows()
    assert rows[0]["input_tokens"] == 10 and rows[0]["status"] == "ok"


def test_the_ledger_stores_counts_only(online, tmp_path):
    provider(FakeClient(reply())).draft(make_req())
    (row,) = claude.UsageLedger().rows()
    assert set(row) == {"id", "day", "ts", "purpose", "model", "prompt_hash", "reserved", "input_tokens",
                        "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "status"}
    assert GOOD not in json.dumps(row)


def test_per_call_caps_refuse(online, monkeypatch):
    monkeypatch.setenv("LLM_MAX_INPUT_TOKENS_PER_CALL", "10")
    with pytest.raises(BudgetExhausted):
        provider(FakeClient(reply())).draft(make_req())
    monkeypatch.delenv("LLM_MAX_INPUT_TOKENS_PER_CALL")
    ledger = claude.UsageLedger()
    with pytest.raises(BudgetExhausted):
        ledger.reserve("p", "m", "h", 10, claude.max_tokens_per_call() + 1)
    monkeypatch.setenv("LLM_MAX_TOKENS_PER_CALL", "64")
    fake = FakeClient(reply())
    provider(fake).draft(make_req())
    assert fake.calls[0]["max_tokens"] == 64


def test_a_failed_call_releases_its_hold(online):
    p = provider(FakeClient(error=TimeoutError()))
    with pytest.raises(ProviderUnavailable):
        p.draft(make_req())
    assert claude.UsageLedger().spent() == 0 and claude.UsageLedger().rows()[0]["status"] == "error"


def test_a_zero_budget_refuses_everything(online, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "0")
    with pytest.raises(BudgetExhausted):
        provider(FakeClient(reply())).draft(make_req())


# ---------------------------------------------------------------- cache record and replay


def test_record_then_replay_offline(online, monkeypatch):
    fake = FakeClient(reply())
    rec = CachingProvider(provider(fake), mode="record")
    req = make_req()
    recorded = rec.draft(req)
    entry = read_entry("anthropic", prompt_hash(req))
    assert entry.text == GOOD and entry.model_id == "claude-haiku-5-5" and entry.usage["output_tokens"] == 40
    assert "sk-" not in json.dumps(entry.__dict__)

    monkeypatch.delenv("LLM_NETWORK_ENABLED")  # the demo configuration: replay, network off
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    replayed = get_provider("anthropic").draft(req)
    assert replayed.text == recorded.text and replayed.usage is None and len(fake.calls) == 1
    with pytest.raises(CacheMiss):
        get_provider("anthropic").draft(make_req(purpose="reminder"))
    out = draft_with_fallback(make_req(purpose="reminder"), get_provider("anthropic"), AbstentionLog())
    assert out.fallback_used and out.abstention_reason == "cache_miss"


def test_replay_needs_neither_the_sdk_nor_a_key(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)  # an import of the SDK would now fail
    out = draft_with_fallback(make_req(), get_provider("anthropic"), AbstentionLog())
    assert out.abstention_reason == "cache_miss"


# ---------------------------------------------------------------- the planner


def _plan() -> planner.DepartmentPlan:
    return planner.DepartmentPlan(orders=[
        planner.TaskOrder(agent_type="draft_message", case_id=str(uuid4()), reason="awaiting_contact",
                          inputs={"state": "owner_accepted"}),
        planner.TaskOrder(agent_type="followup_call_prep", case_id=str(uuid4()), reason="lost_contact",
                          inputs={"state": "lost_contact"}),
    ])


def test_claude_planner_reorders_with_a_json_schema_and_sonnet(online, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    fake = FakeClient(reply(text=json.dumps({"order": [1, 0], "rationale": "Lost contact first."})))
    pl = planner.ClaudePlanner(claude.ClaudePlanAdapter(client=fake))
    out, prov = pl.plan("outreach", _plan())
    assert [o.agent_type for o in out.orders] == ["followup_call_prep", "draft_message"]
    assert out.rationale == "Lost contact first." and not prov.fallback_used
    assert prov.provider == "anthropic" and prov.model_id == "claude-sonnet-5-5"
    (call,) = fake.calls
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": claude.PLAN_SCHEMA}
    assert "tools" not in call and "tool_choice" not in call and "temperature" not in call and "thinking" not in call


@pytest.mark.parametrize("text, reason", [
    ("not json", "invalid_result"),
    (json.dumps({"order": [9], "rationale": "x"}), "invalid_result"),
    (json.dumps({"order": [0, 0], "rationale": "x"}), "invalid_result"),
    (json.dumps({"order": [0], "rationale": "see https://evil.example"}), "invalid_result"),
    (json.dumps({"order": [0], "rationale": "ok", "close": True}), "invalid_result"),
    (json.dumps({"order": ["0"], "rationale": "ok"}), "invalid_result"),
])
def test_claude_planner_schema_violations_give_the_rule_plan(online, monkeypatch, text, reason):
    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    plan = _plan()
    out, prov = planner.ClaudePlanner(claude.ClaudePlanAdapter(client=FakeClient(reply(text=text)))).plan("outreach", plan)
    assert prov.fallback_used and prov.abstention_reason == reason and prov.provider == "template"
    assert out.orders == plan.orders


def test_claude_planner_cannot_add_or_drop_work(online, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    plan = _plan()
    pl = planner.ClaudePlanner(claude.ClaudePlanAdapter(
        client=FakeClient(reply(text=json.dumps({"order": [1], "rationale": "Only one."})))))
    out, _ = planner.contained(pl, "outreach", plan)
    assert sorted(o.agent_type for o in out.orders) == sorted(o.agent_type for o in plan.orders)
    assert [o.agent_type for o in out.orders][0] == "followup_call_prep"


def test_claude_planner_budget_exhaustion_and_errors_fall_back(online, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "10")
    fake = FakeClient(reply())
    out, prov = planner.ClaudePlanner(claude.ClaudePlanAdapter(client=fake)).plan("outreach", _plan())
    assert prov.fallback_used and prov.abstention_reason == "budget_exhausted" and fake.calls == []
    monkeypatch.delenv("LLM_DAILY_TOKEN_BUDGET")
    _, prov = planner.ClaudePlanner(claude.ClaudePlanAdapter(client=FakeClient(error=OSError()))).plan("outreach", _plan())
    assert prov.fallback_used and prov.abstention_reason == "provider_error:ProviderUnavailable"


def test_claude_planner_record_then_replay(online, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    plan = _plan()
    fake = FakeClient(reply(text=json.dumps({"order": [1, 0], "rationale": "Lost contact first."})))
    planner.ClaudePlanner(claude.ClaudePlanAdapter(client=fake)).plan("outreach", plan)
    digest = planner.prompt_hash(planner.PLAN_SYSTEM_PROMPT, planner.plan_prompt("outreach", plan))
    entry = cache.read_entry("anthropic", digest, cache.cache_dir() / "agency")
    assert entry.model_id == "claude-sonnet-5-5" and entry.usage["input_tokens"] == 120

    monkeypatch.delenv("LLM_NETWORK_ENABLED")
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    out, prov = planner.ClaudePlanner().plan("outreach", plan)  # no client, no network: the file answers
    assert [o.agent_type for o in out.orders] == ["followup_call_prep", "draft_message"] and not prov.fallback_used
    other = planner.DepartmentPlan(orders=plan.orders[:1])
    _, miss = planner.ClaudePlanner().plan("outreach", other)
    assert miss.fallback_used and miss.abstention_reason == "cache_miss"


def test_agency_planner_setting(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(planner.get_planner(), planner.TemplatePlanner)  # a provider alone does not opt the planner in
    monkeypatch.setenv("AGENCY_PLANNER", "claude")
    assert isinstance(planner.get_planner(), planner.ClaudePlanner)
    monkeypatch.setenv("LLM_PROVIDER", "template")
    assert isinstance(planner.get_planner(), planner.ClaudePlanner)  # AGENCY_PLANNER wins
    monkeypatch.setenv("AGENCY_PLANNER", "template")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(planner.get_planner(), planner.TemplatePlanner)


def test_provider_response_error_is_the_adapters_class():
    assert issubclass(ProviderResponseError, Exception)


# ---- placeholders: the model copies {owner_name}, {next_step} and {appointment_date}; the system fills them in (A35) ----

AR_WITH_TOKENS = "نتيجة فحصك جاهزة. الخطوة التالية هي {next_step}. سيتواصل معك فريق رعايتك والمسؤول عنه هو {owner_name}."


def test_the_prompt_carries_placeholders_not_values_and_steers_away_from_you_have():
    req = make_req(appointment_date="Tuesday 3 November 2026", purpose="appointment_confirmation")
    prompt = build_prompt(req)
    for value in (req.owner_name, req.next_step, req.appointment_date):
        assert value not in prompt and value not in SYSTEM_PROMPT
    for token in ("{owner_name}", "{next_step}", "{appointment_date}"):
        assert token in prompt
    assert "you have" in (SYSTEM_PROMPT + prompt).lower()  # named as a phrase to avoid
    assert "{appointment_date}" not in build_prompt(make_req())  # only when there is a date


def test_two_owners_with_the_same_role_get_the_same_prompt_so_no_name_is_ever_sent():
    a, b = make_req(owner_name="Dr Sara Khan"), make_req(owner_name="Dr. Layla Haddad")
    assert prompt_hash(a) == prompt_hash(b)


def test_placeholders_are_filled_after_the_model_answers_and_the_validator_still_checks_the_result(online):
    req = make_req(language="ar", owner_name="Dr Sara Khan", next_step="فحص بالكاميرا")
    result = draft_with_fallback(req, provider(FakeClient(reply(text=AR_WITH_TOKENS))), AbstentionLog())
    assert result.fallback_used is False
    assert "Dr Sara Khan" in result.text and "فحص بالكاميرا" in result.text and "{" not in result.text


def test_a_model_that_drops_the_owner_placeholder_is_still_rejected(online):
    req = make_req(language="ar", next_step="فحص بالكاميرا")
    dropped = "نتيجة فحصك جاهزة. الخطوة التالية هي {next_step}. سيتواصل معك فريق رعايتك."
    log = AbstentionLog()
    result = draft_with_fallback(req, provider(FakeClient(reply(text=dropped))), log)
    assert result.fallback_used is True and "missing_owner_name" in result.abstention_reason


def test_an_unknown_placeholder_is_not_filled_and_is_rejected(online):
    text = GOOD.replace("Dr Sara Khan", "{owner_name}") + " Ref {patient_name}."
    result = draft_with_fallback(make_req(), provider(FakeClient(reply(text=text))), AbstentionLog())
    assert result.fallback_used is True and "unfilled_placeholder" in result.abstention_reason


def test_a_provider_that_does_not_use_placeholders_gets_no_substitution():
    class Plain:
        name, model_id = "plain", "m"

        def draft(self, req):
            from app.ai.provider import DraftResult
            return DraftResult(GOOD.replace("Dr Sara Khan", "{owner_name}"), req.language, "plain", "m", False, None, "h")

    result = draft_with_fallback(make_req(), Plain(), AbstentionLog())
    assert result.fallback_used is True and "unfilled_placeholder" in result.abstention_reason


def test_the_cache_provider_passes_the_flag_through_and_stores_the_raw_text(online, monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    req = make_req(language="ar", next_step="فحص بالكاميرا")
    cp = CachingProvider(provider(FakeClient(reply(text=AR_WITH_TOKENS))), mode="record")
    result = draft_with_fallback(req, cp, AbstentionLog())
    assert result.fallback_used is False and "Dr Sara Khan" in result.text
    assert read_entry("anthropic", prompt_hash(req)).text == AR_WITH_TOKENS  # the recording holds the tokens, no name
    replayed = draft_with_fallback(req, CachingProvider(provider(FakeClient()), mode="replay"), AbstentionLog())
    assert replayed.text == result.text


def test_the_english_prompt_also_rules_out_if_you_have_questions_and_the_arabic_prompt_is_unchanged_by_it():
    en, ar = build_prompt(make_req(language="en")), build_prompt(make_req(language="ar"))
    assert "if you have questions" in en.lower() and "closing" in en.lower()
    assert "if you have questions" not in ar.lower()
