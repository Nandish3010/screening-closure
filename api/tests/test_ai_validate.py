"""D3: output validation, template fallback and abstention accounting (T-D3, T-D8)."""

import re

import pytest

from app.ai import validate as v
from app.ai.provider import (
    CHANNEL_MAX_CHARS,
    CHANNELS,
    LANGUAGES,
    PURPOSES,
    DraftRequest,
    DraftResult,
    ProviderUnavailable,
    TemplateProvider,
    prompt_hash,
)
from app.ai.templates import SAMPLE_VALUES, render
from app.ai.validate import AbstentionLog, draft_with_fallback, validate


def make_req(language="en", purpose="result_notification", channel="sms", date=None) -> DraftRequest:
    s = SAMPLE_VALUES[language]
    if purpose == "appointment_confirmation":
        date = date or s["appointment_date"]
    return DraftRequest(purpose, channel, language, s["owner_name"], "primary_care_owner",
                        s["next_step"], "colorectal_fit", date)


GOOD = {
    "en": "Your screening result is ready. The next step is a camera test. "
          "Dr Sara Khan from your care team will contact you.",
    "ar": "نتيجة فحصك جاهزة. الخطوة التالية هي فحص بالكاميرا. "
          "سيتواصل معك فريق رعايتك. المسؤول عن رعايتك هو الدكتورة سارة خان.",
}


class FakeProvider:
    name = "fake_llm"
    model_id = "fake-1"

    def __init__(self, text=None, exc=None):
        self.text, self.exc = text, exc

    def draft(self, req):
        if self.exc:
            raise self.exc
        return DraftResult(self.text, req.language, self.name, self.model_id, False, None, prompt_hash(req))


# ------------------------------------------------------------------ T-D8: one rule, two fixtures
# rule code -> (request kwargs, failing text, passing text). Must cover v.RULE_CODES exactly.
RULE_FIXTURES = {
    v.EMPTY: (dict(), "   ", GOOD["en"]),
    v.UNFILLED_PLACEHOLDER: (dict(), GOOD["en"] + " Call {phone}.", GOOD["en"]),
    v.TOO_LONG: (dict(), GOOD["en"] + " Your care team is here to help." * 12, GOOD["en"]),
    v.WRONG_LANGUAGE: (dict(), GOOD["ar"], GOOD["en"]),
    v.MISSING_OWNER_NAME: (dict(), "Your screening result is ready. The next step is a camera test.", GOOD["en"]),
    v.MISSING_NEXT_STEP: (dict(), "Your screening result is ready. Dr Sara Khan will contact you.", GOOD["en"]),
    v.MISSING_APPOINTMENT_DATE: (
        dict(purpose="appointment_confirmation"),
        "Your appointment is booked. The next step is a camera test. Dr Sara Khan will contact you.",
        "Your appointment is on Tuesday 3 November 2026. The next step is a camera test. "
        "Dr Sara Khan will contact you.",
    ),
    v.FORBIDDEN_PHRASE: (dict(), GOOD["en"] + " Do not worry about it.", GOOD["en"]),
    v.PII_DIGITS: (dict(), GOOD["en"] + " Call 0501234567 if needed.", GOOD["en"] + " The clinic is on floor 3."),
    v.PII_EMAIL: (dict(), GOOD["en"] + " Write to clinic@example.org", GOOD["en"]),
    v.PII_IDENTIFIER: (dict(), GOOD["en"] + " Your MRN is AB123456.", GOOD["en"]),
    v.URL: (dict(), GOOD["en"] + " See https://example.org/info", GOOD["en"]),
}


def test_rule_table_matches_fixture_table():
    assert set(RULE_FIXTURES) == set(v.RULE_CODES)


@pytest.mark.parametrize("rule", v.RULE_CODES)
def test_each_rule_has_a_failing_and_a_passing_fixture(rule):
    kwargs, failing, passing = RULE_FIXTURES[rule]
    req = make_req(**kwargs)
    bad = validate(failing, req)
    assert not bad.ok and any(r == rule or r.startswith(rule + ":") for r in bad.reasons), bad
    good = validate(passing, req)
    assert good.ok, good.reasons


def test_good_outputs_pass_in_both_languages():
    for lang in LANGUAGES:
        assert validate(GOOD[lang], make_req(lang)).ok


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("channel", CHANNELS)
def test_every_template_passes_validation(language, purpose, channel):
    s = SAMPLE_VALUES[language]
    for date in ([None, s["appointment_date"]] if purpose != "appointment_confirmation" else [s["appointment_date"]]):
        req = DraftRequest(purpose, channel, language, s["owner_name"], "primary_care_owner",
                           s["next_step"], "colorectal_fit", date)
        text = render(purpose, channel, language, owner_name=s["owner_name"], next_step=s["next_step"],
                      appointment_date=date)
        result = validate(text, req)
        assert result.ok, (language, purpose, channel, date, result.reasons)


def test_length_limit_is_per_channel():
    text = GOOD["en"] + " " + "word " * 80  # about 540 chars
    assert not validate(text, make_req(channel="sms")).ok
    assert validate(text, make_req(channel="whatsapp")).ok
    assert not validate(GOOD["en"] + " " + "word " * 130, make_req(channel="whatsapp")).ok
    assert CHANNEL_MAX_CHARS == {"sms": 320, "whatsapp": 600, "letter": 1200}


# ------------------------------------------------------------------ forbidden phrases


def test_every_forbidden_entry_rejects_its_example():
    entries = v.load_forbidden_phrases()
    assert {e.language for e in entries} == {"en", "ar"}
    for e in entries:
        assert e.rationale and e.category and e.example
        assert e.id in {h.id for h in v.forbidden_hits(e.example)}, (e.language, e.id)


def test_forbidden_ids_unique_per_language():
    ids = [(e.language, e.id) for e in v.load_forbidden_phrases()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "text",
    [
        "You have cancer.",
        "There is a tumour.",
        "The result is malignant.",
        "Don't worry, it is probably nothing.",
        "Please do not worry.",
        "You should take aspirin.",
        "We recommend a change of diet.",
        "YOU HAVE a problem",
    ],
)
def test_english_diagnosis_advice_and_reassurance_rejected(text):
    res = validate(GOOD["en"] + " " + text, make_req("en"))
    assert not res.ok and any(r.startswith("forbidden_phrase:") for r in res.reasons)


@pytest.mark.parametrize(
    "text",
    ["لديك سرطان.", "هناك ورم.", "لا تقلق أبداً.", "الأَوْرَام خطيرة.", "خذ الدواء مساء."],
)
def test_arabic_diagnosis_advice_and_reassurance_rejected(text):
    res = validate(GOOD["ar"] + " " + text, make_req("ar"))
    assert not res.ok and any(r.startswith("forbidden_phrase:") for r in res.reasons)


def test_forbidden_phrases_checked_across_languages():
    # An English forbidden phrase inside an Arabic message is still caught.
    res = validate(GOOD["ar"] + " cancer", make_req("ar"))
    assert any(r == "forbidden_phrase:cancer" for r in res.reasons)


def test_forbidden_words_do_not_match_inside_other_words():
    # "dose" must not match "close", "cure" must not match "secure", "ورم" not "ورمضان".
    ok_en = GOOD["en"] + " Please keep this message secure and close by."
    assert validate(ok_en, make_req("en")).ok
    assert validate(GOOD["ar"] + " ورمضان", make_req("ar")).ok


# ------------------------------------------------------------------ personal data


@pytest.mark.parametrize(
    "extra,code",
    [
        (" Ring 05012345678.", v.PII_DIGITS),
        (" Ring +971 50 123 4567.", v.PII_DIGITS),
        (" ID 784-1985-1234567-1.", v.PII_DIGITS),
        (" Reference 1234567.", v.PII_DIGITS),
        (" Mail me: a.b@clinic.ae", v.PII_EMAIL),
        (" Your Emirates ID is on file.", v.PII_IDENTIFIER),
        (" Your file no is on the card.", v.PII_IDENTIFIER),
        (" Token a1b2c3d4e5.", v.PII_IDENTIFIER),
        (" Visit www.clinic.ae today.", v.URL),
        (" See clinic.com for more.", v.URL),
        (" Use wa.me/971500000000.", v.URL),
    ],
)
def test_personal_data_and_links_rejected(extra, code):
    res = validate(GOOD["en"] + extra, make_req("en"))
    assert code in res.reasons, res.reasons


def test_arabic_indic_digits_count_as_digits():
    res = validate(GOOD["ar"] + " اتصل على ٠٥٠١٢٣٤٥٦٧.", make_req("ar"))
    assert v.PII_DIGITS in res.reasons


def test_short_numbers_and_request_values_are_allowed():
    # Short numbers (floor, time) pass; the supplied appointment date passes even though it has digits.
    req = make_req("en", "appointment_confirmation", date="2026-11-03 10:30")
    text = ("Your appointment is on 2026-11-03 10:30. The next step is a camera test. "
            "Dr Sara Khan will contact you.")
    assert validate(text, req).ok
    assert not validate(text.replace("2026-11-03 10:30", "2026-11-04 10:30"), req).ok


# ------------------------------------------------------------------ fallback (T-D3)

BAD_OUTPUTS = {
    "diagnosis": ("en", "You have cancer. The next step is a camera test. Dr Sara Khan will contact you."),
    "over_length": ("en", GOOD["en"] + " Your care team is here to help." * 12),
    "wrong_language": ("en", GOOD["ar"]),
    "pii": ("en", GOOD["en"] + " Your number 0501234567 is on file."),
    "empty": ("en", ""),
    "unfilled": ("en", "Dear {patient}, the next step is a camera test. Dr Sara Khan will contact you."),
    "advice": ("en", GOOD["en"] + " You should stop taking your tablets."),
    "reassurance": ("en", GOOD["en"] + " Do not worry."),
    "missing_fields": ("en", "Your screening test found blood."),
    "url": ("en", GOOD["en"] + " Details at www.example.com"),
    "arabic_diagnosis": ("ar", "لديك سرطان. " + GOOD["ar"]),
    "arabic_wrong_language": ("ar", GOOD["en"]),
}


@pytest.mark.parametrize("name", list(BAD_OUTPUTS))
def test_bad_model_output_falls_back_to_template(name):
    language, text = BAD_OUTPUTS[name]
    req = make_req(language)
    log = AbstentionLog()
    res = draft_with_fallback(req, FakeProvider(text), log)
    template = TemplateProvider().draft(req)
    assert res.text == template.text
    assert res.fallback_used is True
    assert res.provider == "template" and res.abstention_reason.startswith("validation_failed:")
    assert res.prompt_hash == prompt_hash(req)
    assert log.count == 1 and log.attempts == 1
    rec = log.records()[0]
    assert (rec.provider, rec.category, rec.purpose, rec.channel, rec.language) == (
        "fake_llm", "validation_failed", req.purpose, req.channel, req.language)
    assert validate(res.text, req).ok  # the fallback itself is always valid


def test_good_output_passes_through_untouched():
    for lang in LANGUAGES:
        req, log = make_req(lang), AbstentionLog()
        res = draft_with_fallback(req, FakeProvider(GOOD[lang]), log)
        assert res.text == GOOD[lang] and res.fallback_used is False and res.abstention_reason is None
        assert res.provider == "fake_llm"
        assert log.count == 0 and log.attempts == 1 and log.rate() == 0.0


@pytest.mark.parametrize(
    "exc,reason",
    [
        (ProviderUnavailable("down"), "provider_unavailable"),
        (TimeoutError("slow"), "provider_error:TimeoutError"),
        (ConnectionRefusedError("refused"), "provider_error:ConnectionRefusedError"),
        (RuntimeError("boom"), "provider_error:RuntimeError"),
    ],
)
def test_provider_failure_falls_back_without_raising(exc, reason):
    req, log = make_req(), AbstentionLog()
    res = draft_with_fallback(req, FakeProvider(exc=exc), log)
    assert res.fallback_used and res.abstention_reason == reason
    assert res.text == TemplateProvider().draft(req).text
    assert log.records()[0].reason == reason


def test_model_providers_fall_back_by_default(monkeypatch, tmp_path):
    """Default config: replay mode with an empty cache is a cache miss; network off otherwise."""
    from app.ai.provider import get_provider

    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_NETWORK_ENABLED", raising=False)
    for mode, reason in (("replay", "cache_miss"), ("off", "provider_unavailable")):
        monkeypatch.setenv("LLM_CACHE_MODE", mode)
        for name in ("azure_openai", "jais"):
            log = AbstentionLog()
            res = draft_with_fallback(make_req(), get_provider(name), log)
            assert res.fallback_used and res.abstention_reason == reason
            assert log.records()[0].provider == name


def test_wrong_language_result_object_is_invalid():
    class Mismatch(FakeProvider):
        def draft(self, req):
            return DraftResult(GOOD["en"], "ar", self.name, self.model_id, False, None, "h")

    log = AbstentionLog()
    res = draft_with_fallback(make_req("en"), Mismatch(), log)
    assert res.fallback_used and res.abstention_reason == "invalid_result"


def test_template_provider_is_not_an_abstention():
    log = AbstentionLog()
    res = draft_with_fallback(make_req(), TemplateProvider(), log)
    assert not res.fallback_used and log.count == 0 and log.attempts == 1


# ------------------------------------------------------------------ abstention accounting (T-D8)


def test_abstention_count_and_rate():
    log = AbstentionLog()
    req = make_req()
    plan = [
        FakeProvider(GOOD["en"]),  # ok
        FakeProvider("You have cancer. a camera test Dr Sara Khan"),  # forbidden
        FakeProvider(exc=ProviderUnavailable("x")),  # unavailable
        FakeProvider(GOOD["en"]),  # ok
        FakeProvider(GOOD["en"] + " Visit www.example.com"),  # url
        FakeProvider(exc=TimeoutError()),  # error
    ]
    for p in plan:
        draft_with_fallback(req, p, log)
    assert log.attempts == 6 and log.count == 4
    assert log.rate() == pytest.approx(4 / 6)
    assert log.by_category() == {"validation_failed": 2, "provider_unavailable": 1, "provider_error": 1}
    rules = log.by_rule()
    assert rules["forbidden_phrase"] == 0 and rules["forbidden_phrase:cancer"] == 1
    assert rules["url"] == 1
    assert [r.seq for r in log.records()] == [1, 2, 3, 4]
    log.reset()
    assert (log.count, log.attempts, log.rate()) == (0, 0, 0.0)


def test_default_log_is_used_when_none_given():
    v.DEFAULT_ABSTENTION_LOG.reset()
    draft_with_fallback(make_req(), FakeProvider(exc=ProviderUnavailable("x")))
    assert v.DEFAULT_ABSTENTION_LOG.count == 1
    v.DEFAULT_ABSTENTION_LOG.reset()


def test_reviewer_pack_lists_forbidden_phrases():
    from app.ai.templates import reviewer_pack_markdown

    md = reviewer_pack_markdown()
    assert "## Forbidden phrases" in md
    for e in v.load_forbidden_phrases():
        assert f"`{e.id}`" in md


# ------------------------------------------------------------------ E14: no outbound text discloses the result
# IFHAS: abnormal results are communicated with the participant present.

DISCLOSING_EN = [
    "Your screening test found blood.",
    "The lab found some blood in the sample.",
    "There is blood in your stool.",
    "We saw blood in the sample.",
    "You have a positive result.",
    "Your test was positive.",
    "The result came back positive.",
    "This is an abnormal result.",
    "Your result shows a problem.",
    "Your test showed something.",
]
DISCLOSING_AR = [
    "وجد فحصك دماً.",
    "وجدنا دماً في العينة.",
    "أظهر فحصك وجود دم.",
    "هناك دم في البراز.",
    "نتيجتك موجبة.",
    "نتيجتك ايجابية.",
    "النتيجة غير طبيعية.",
    "أظهرت نتيجتك مشكلة.",
    "أظهر فحصك مشكلة.",
]


def _disclosure_ids(text):
    return {h.id for h in v.forbidden_hits(text) if h.category in ("result_disclosure", "positive_result_jargon")}


@pytest.mark.parametrize("text", DISCLOSING_EN)
def test_english_result_disclosure_is_rejected(text):
    assert _disclosure_ids(text), text
    draft = f"{text} The next step is a camera test. Dr Sara Khan will call you."
    res = validate(draft, make_req("en"))
    assert not res.ok and any(r.startswith("forbidden_phrase:") for r in res.reasons), res.reasons


@pytest.mark.parametrize("text", DISCLOSING_AR)
def test_arabic_result_disclosure_is_rejected(text):
    assert _disclosure_ids(text), text
    draft = f"{text} الخطوة التالية هي فحص بالكاميرا. ستتلقى اتصالاً من الدكتورة سارة خان."
    res = validate(draft, make_req("ar"))
    assert not res.ok and any(r.startswith("forbidden_phrase:") for r in res.reasons), res.reasons


def test_a_disclosing_model_draft_falls_back_to_the_template():
    req = make_req("en")
    log = AbstentionLog()
    out = draft_with_fallback(
        req, FakeProvider("Your screening test found blood. The next step is a camera test. Dr Sara Khan will call you."), log)
    assert out.fallback_used and out.abstention_reason.startswith("validation_failed:")
    assert "forbidden_phrase:found_blood" in out.abstention_reason
    assert "blood" not in out.text.lower()
    assert log.count == 1


def test_a_non_disclosing_model_draft_is_accepted():
    req = make_req("en")
    ok = ("Your screening result is ready and your care team needs to talk with you about the next step, "
          "a camera test. Dr Sara Khan will call you, or you can reply to book a time.")
    assert validate(ok, req).ok


def test_disclosure_entries_carry_the_ifhas_rationale_and_reject_their_own_example():
    entries = [fp for fp in v.load_forbidden_phrases() if fp.category == "result_disclosure"]
    assert {fp.language for fp in entries} == {"en", "ar"}
    for fp in entries:
        assert fp.rationale == "IFHAS: abnormal results are communicated with the participant present", fp.id
        assert fp.id in {h.id for h in v.forbidden_hits(fp.example) if h.language == fp.language}, fp.id
    # The phrases the ticket names, in both languages.
    en_ids = {fp.id for fp in entries if fp.language == "en"}
    ar_ids = {fp.id for fp in entries if fp.language == "ar"}
    assert {"found_blood", "blood_in_stool", "positive_result", "abnormal_result"} <= en_ids
    assert {"found_blood", "blood_in_stool", "positive_result", "abnormal_result"} <= ar_ids


def test_no_template_in_any_purpose_channel_or_language_discloses_the_result():
    from app.ai.templates import iter_templates
    n = 0
    for e in iter_templates():
        hits = [h.id for h in v.forbidden_hits(e.text)]
        assert not hits, (e.language, e.purpose, e.key, hits)
        assert not re.search(r"blood|positive|abnormal|cancer|colonoscop|دم|تنظير|سرطان", e.text, re.I), (e.language, e.purpose, e.key)
        n += 1
    assert n >= 30
