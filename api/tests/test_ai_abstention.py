"""D11: abstention accounting, GET /metrics/abstention and the simulator hook (T-D15, unit half)."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from api_helpers import login_headers, make_client
from app import metrics_abstention as ma
from app.ai import provider as prov
from app.ai.cache import CacheEntry, write_entry
from app.ai.draft_store import AttemptCount, InMemoryDraftStore
from app.ai.provider import DraftResult, full_prompt, prompt_hash
from app.routers import drafts

client = make_client()
GOOD = "Your screening result is ready. Dr Sara Khan will arrange a camera test and the care team will contact you."
BAD = "You have cancer. Dr Sara Khan will arrange a camera test."


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_CACHE_MODE", raising=False)
    monkeypatch.delenv("LLM_NETWORK_ENABLED", raising=False)


def H(role, name=None):
    return login_headers(client, role, name)


def metric(role="coordinator"):
    r = client.get("/metrics/abstention", headers=H(role))
    assert r.status_code == 200, r.text
    return r.json()


def owned_case():
    cid = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=H("coordinator")).json()["case_id"]
    for type_, role, name in (("acknowledge", "lab", None), ("accept_ownership", "primary_care_owner", "Dr Sara Khan")):
        assert client.post(f"/cases/{cid}/events", json={"type": type_}, headers=H(role, name)).status_code == 201
    return cid


def draft(cid, language="en", channel="sms"):
    r = client.post(f"/cases/{cid}/drafts", headers=H("coordinator"),
                    json={"purpose": "result_notification", "channel": channel, "language": language})
    assert r.status_code == 201, r.text
    return r.json()


class Scripted:
    """Model stand-in that returns the next scripted outcome: a text, or an exception."""

    name = "azure_openai"
    model_id = "scripted"

    def __init__(self, script):
        self.script = list(script)

    def draft(self, req):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return DraftResult(item, req.language, self.name, self.model_id, False, None, "x")


def use_model(monkeypatch, script):
    fake = Scripted(script)
    monkeypatch.setitem(prov._REGISTRY, "azure_openai", lambda: fake)
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")


# -- pure summary ------------------------------------------------------------------------------


def test_summarise_empty():
    assert ma.summarise([]) == {
        "attempts": 0, "abstentions": 0, "rate": 0.0, "by_reason": {}, "by_language": {}, "by_channel": {},
    }


def test_summarise_counts_rate_and_splits():
    out = ma.summarise([
        AttemptCount("en", "sms", None, 6),
        AttemptCount("en", "sms", "validation_failed:forbidden_phrase:cancer,url", 1),
        AttemptCount("ar", "sms", "validation_failed:wrong_language", 1),
        AttemptCount("ar", "letter", "provider_unavailable", 1),
        AttemptCount("ar", "letter", None, 1),
    ])
    assert (out["attempts"], out["abstentions"], out["rate"]) == (10, 3, 0.3)
    assert out["by_reason"] == {"provider_unavailable": 1, "validation_failed": 2}
    assert out["by_language"]["en"] == {"attempts": 7, "abstentions": 1, "rate": round(1 / 7, 6)}
    assert out["by_language"]["ar"] == {"attempts": 3, "abstentions": 2, "rate": round(2 / 3, 6)}
    assert out["by_channel"]["letter"]["abstentions"] == 1
    assert sum(out["by_reason"].values()) == out["abstentions"]


# -- route -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role,status", [
    ("coordinator", 200), ("supervisor", 200), ("primary_care_owner", 403), ("specialist_owner", 403),
    ("lab", 403), ("patient", 403),
])
def test_metrics_roles(role, status):
    assert client.get("/metrics/abstention", headers=H(role)).status_code == status


def test_metrics_needs_authentication():
    assert client.get("/metrics/abstention").status_code == 401


def test_template_only_counts_attempts_and_no_abstentions():
    cid = owned_case()
    for _ in range(3):
        draft(cid)
    out = metric()
    assert (out["attempts"], out["abstentions"], out["rate"], out["by_reason"]) == (3, 0, 0.0, {})


def test_every_fallback_is_a_stored_abstention_and_the_counts_agree(monkeypatch):
    cid = owned_case()
    use_model(monkeypatch, [GOOD, BAD, TimeoutError("slow"), GOOD, "", BAD])
    results = [draft(cid) for _ in range(6)]
    fell_back = [r for r in results if r["fallback_used"]]
    assert len(fell_back) == 4

    out = metric("supervisor")
    assert (out["attempts"], out["abstentions"]) == (6, 4)
    assert out["rate"] == round(4 / 6, 6)
    assert out["by_reason"] == {"provider_error": 1, "validation_failed": 3}

    # the stored rows say the same thing: count of fallbacks == recorded abstentions
    rows = drafts.store.list_for_case(UUID(cid))
    assert sum(1 for r in rows if r.fallback_used) == sum(1 for r in rows if r.abstention_reason) == 4
    assert all(r.abstained_at == r.created_at for r in rows if r.abstention_reason)
    assert all(r.abstained_at is None for r in rows if not r.abstention_reason)
    assert [r.abstention_reason for r in rows if r.abstention_reason] == [r["abstention_reason"] for r in fell_back]


def test_fairness_split_by_language_and_channel(monkeypatch):
    cid = owned_case()
    use_model(monkeypatch, [GOOD, GOOD, "هذا نص غير صالح", GOOD])
    draft(cid, "en", "sms")
    draft(cid, "en", "letter")
    draft(cid, "ar", "sms")  # model text fails validation for Arabic
    draft(cid, "en", "sms")
    out = metric()
    assert out["by_language"]["en"]["abstentions"] == 0 and out["by_language"]["ar"]["rate"] == 1.0
    assert out["by_channel"]["sms"] == {"attempts": 3, "abstentions": 1, "rate": round(1 / 3, 6)}


def test_cache_misses_are_counted_under_their_own_reason(monkeypatch):
    cid = owned_case()
    monkeypatch.setenv("LLM_PROVIDER", "jais")
    draft(cid)
    assert metric()["by_reason"] == {"cache_miss": 1}


# -- simulator hook ----------------------------------------------------------------------------


def recs(*pairs):
    return [SimpleNamespace(language=l, channel=c) for l, c in pairs]


def test_hook_is_none_for_no_records():
    assert ma.abstention_rate_hook([]) is None


def test_hook_is_zero_for_the_template_provider():
    assert ma.abstention_rate_hook(recs(("en", "sms"), ("ar", "whatsapp"), ("other", "phone"))) == 0.0


def test_hook_is_one_for_a_model_provider_with_an_empty_cache(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    assert ma.abstention_rate_hook(recs(("en", "sms"), ("ar", "letter"))) == 1.0


def test_hook_weights_by_the_language_channel_mix(monkeypatch):
    """Record an output for English SMS only: 3 of 4 cases are then abstentions."""
    from app.ai.provider import DraftRequest
    from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES

    req = DraftRequest("result_notification", "sms", "en", SAMPLE_VALUES["en"]["owner_name"],
                       "primary_care_owner", DEFAULT_NEXT_STEP["en"], "colorectal_fit")
    text = f"Your screening result is ready. {req.owner_name} will arrange {req.next_step} and the care team will contact you."
    write_entry(CacheEntry(prompt_hash(req), "azure_openai", "m", "en", text, full_prompt(req)))
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    mix = recs(("en", "sms"), ("other", "sms"), ("en", "phone"), ("ar", "sms"))
    # "other" -> en and "phone" -> sms, so three of the four cases are English SMS and hit the cache
    assert ma.abstention_rate_hook(mix) == 0.25


def test_hook_does_not_store_drafts_or_touch_the_global_log(monkeypatch):
    from app.ai.validate import DEFAULT_ABSTENTION_LOG

    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    before = (DEFAULT_ABSTENTION_LOG.attempts, DEFAULT_ABSTENTION_LOG.count)
    ma.abstention_rate_hook(recs(("en", "sms")))
    assert (DEFAULT_ABSTENTION_LOG.attempts, DEFAULT_ABSTENTION_LOG.count) == before
    assert drafts.store.attempt_counts() == []


def test_hook_matches_the_simulators_hook_signature():
    """AbstentionHook = Callable[[Sequence[CaseRecord]], float | None]."""
    import inspect

    params = inspect.signature(ma.abstention_rate_hook).parameters
    assert len(params) == 1
