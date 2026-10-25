"""D6: output cache, record and replay keyed by prompt hash (T-D7, unit half)."""

import json
import socket

import httpx
import pytest

from app.ai import cache
from app.ai.adapters import AzureOpenAIProvider
from app.ai.cache import CacheEntry, CachingProvider, read_entry, write_entry
from app.ai.provider import (
    CacheMiss,
    DraftRequest,
    DraftResult,
    ProviderUnavailable,
    TemplateProvider,
    full_prompt,
    prompt_hash,
)
from app.ai.validate import AbstentionLog, draft_with_fallback

GOOD = "Your screening result is ready. Dr Sara Khan will arrange a colonoscopy and the care team will contact you."


def make_req(**overrides) -> DraftRequest:
    base = dict(purpose="result_notification", channel="sms", language="en", owner_name="Dr Sara Khan",
                owner_role="primary_care_owner", next_step="a colonoscopy", pathway_name="colorectal_fit")
    base.update(overrides)
    return DraftRequest(**base)


class Counting:
    name = "azure_openai"
    model_id = "dep-1"

    def __init__(self, text=GOOD):
        self.calls, self.text = 0, text

    def draft(self, req):
        self.calls += 1
        return DraftResult(self.text, req.language, self.name, self.model_id, False, None, prompt_hash(req))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LLM_CACHE_MODE", raising=False)
    monkeypatch.delenv("LLM_NETWORK_ENABLED", raising=False)


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a socket fails the test (T-D7: zero outbound connections)."""
    def refuse(*a, **k):
        raise AssertionError("outbound connection attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_default_mode_is_replay_and_default_dir_is_inside_the_package(monkeypatch):
    assert cache.cache_mode() == "replay"
    monkeypatch.delenv("LLM_CACHE_DIR")
    assert cache.cache_dir() == cache.DEFAULT_CACHE_DIR
    assert cache.DEFAULT_CACHE_DIR.parts[-4:] == ("api", "app", "ai", "cache")


def test_bad_mode_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "sometimes")
    with pytest.raises(ValueError, match="LLM_CACHE_MODE"):
        CachingProvider(Counting())


def test_replay_miss_falls_back_to_the_template_as_cache_miss(no_network):
    inner = Counting()
    log = AbstentionLog()
    res = draft_with_fallback(make_req(), CachingProvider(inner), log)
    assert inner.calls == 0  # replay never calls the provider
    assert res.fallback_used and res.provider == "template" and res.abstention_reason == "cache_miss"
    assert res.text == TemplateProvider().draft(make_req()).text
    assert log.records()[0].category == "cache_miss"


def test_cache_miss_is_a_provider_unavailable_but_distinguishable():
    assert issubclass(CacheMiss, ProviderUnavailable)
    with pytest.raises(CacheMiss):
        CachingProvider(Counting()).draft(make_req())


def test_record_then_replay_gives_identical_drafts_without_the_network(monkeypatch, no_network):
    inner = Counting()
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    recorded = CachingProvider(inner).draft(make_req())
    assert inner.calls == 1 and recorded.text == GOOD

    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    replayed = draft_with_fallback(make_req(), CachingProvider(inner), AbstentionLog())
    assert inner.calls == 1  # still one: replay did not call it
    assert replayed.text == GOOD and not replayed.fallback_used and replayed.abstention_reason is None
    assert replayed.provider == "azure_openai" and replayed.model_id == "dep-1"
    assert replayed.prompt_hash == recorded.prompt_hash == prompt_hash(make_req())


def test_replay_is_keyed_by_the_prompt(monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    CachingProvider(Counting()).draft(make_req())
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    cp = CachingProvider(Counting())
    cp.draft(make_req())  # hit
    for other in (make_req(language="ar"), make_req(owner_name="Dr Omar"), make_req(channel="letter")):
        with pytest.raises(CacheMiss):
            cp.draft(other)


def test_entries_are_per_provider():
    entry = CacheEntry(prompt_hash(make_req()), "azure_openai", "m", "en", GOOD, full_prompt(make_req()))
    write_entry(entry)
    assert read_entry("azure_openai", entry.prompt_hash) == entry
    assert read_entry("jais", entry.prompt_hash) is None


def test_off_mode_calls_the_provider_every_time_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    inner = Counting()
    cp = CachingProvider(inner)
    cp.draft(make_req())
    cp.draft(make_req())
    assert inner.calls == 2 and not (tmp_path / "cache").exists()


def test_record_mode_still_needs_the_network_switch(monkeypatch, tmp_path):
    """Record wraps the real adapter, which refuses while LLM_NETWORK_ENABLED is not true."""
    monkeypatch.setenv("LLM_CACHE_MODE", "record")

    def boom(request):  # pragma: no cover - must never run
        raise AssertionError("request sent with the network off")

    cp = CachingProvider(AzureOpenAIProvider(transport=httpx.MockTransport(boom)))
    with pytest.raises(ProviderUnavailable):
        cp.draft(make_req())
    assert not (tmp_path / "cache").exists()


def test_record_stores_even_a_rejected_output_so_replay_reproduces_the_abstention(monkeypatch):
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    bad = "You have cancer. Dr Sara Khan will arrange a colonoscopy."
    draft_with_fallback(make_req(), CachingProvider(Counting(bad)), AbstentionLog())
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    res = draft_with_fallback(make_req(), CachingProvider(Counting()), AbstentionLog())
    assert res.fallback_used and res.abstention_reason.startswith("validation_failed:")


def test_files_are_deterministic_json_with_the_exact_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_MODE", "record")
    cp = CachingProvider(Counting())
    cp.draft(make_req())
    path = tmp_path / "cache" / "azure_openai" / f"{prompt_hash(make_req())}.json"
    first = path.read_bytes()
    data = json.loads(first)
    assert data["prompt"] == full_prompt(make_req()) and data["text"] == GOOD
    assert set(data) == {"prompt_hash", "provider", "model_id", "language", "text", "prompt", "usage"}
    cp.draft(make_req())
    assert path.read_bytes() == first  # re-recording an unchanged output changes nothing
    assert list(path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("junk", ["not json", "[]", '{"text": "x"}', ""])
def test_unreadable_entries_are_misses(junk, tmp_path, monkeypatch):
    path = tmp_path / "cache" / "azure_openai"
    path.mkdir(parents=True)
    (path / f"{prompt_hash(make_req())}.json").write_text(junk)
    with pytest.raises(CacheMiss):
        CachingProvider(Counting()).draft(make_req())


def test_an_entry_under_the_wrong_key_is_a_miss(tmp_path):
    entry = CacheEntry(prompt_hash(make_req(language="ar")), "azure_openai", "m", "ar", GOOD, "p")
    d = tmp_path / "cache" / "azure_openai"
    d.mkdir(parents=True)
    (d / f"{prompt_hash(make_req())}.json").write_text(json.dumps(entry.__dict__))
    with pytest.raises(CacheMiss):
        CachingProvider(Counting()).draft(make_req())


@pytest.mark.parametrize("provider,key", [("../x", "a" * 64), ("azure_openai", "../../etc/passwd"), ("A", "a" * 64)])
def test_cache_keys_cannot_escape_the_directory(provider, key):
    with pytest.raises(ValueError):
        cache._path(provider, key)


def test_the_committed_cache_directory_exists():
    assert (cache.DEFAULT_CACHE_DIR / ".gitkeep").exists()


def test_template_provider_needs_no_cache(no_network):
    res = draft_with_fallback(make_req(), TemplateProvider(), AbstentionLog())
    assert not res.fallback_used and res.provider == "template"


def test_registry_wraps_model_providers_in_the_cache_and_keeps_names():
    from app.ai.provider import get_provider

    for name in ("azure_openai", "jais"):
        p = get_provider(name)
        assert isinstance(p, CachingProvider) and p.name == name
