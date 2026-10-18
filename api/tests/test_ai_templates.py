"""D2: message templates (T-D14)."""

import re

import pytest

from app.ai import templates
from app.ai.provider import (
    CHANNEL_MAX_CHARS,
    CHANNELS,
    LANGUAGES,
    MAX_FIELD_CHARS,
    PURPOSES,
    DraftRequest,
    TemplateProvider,
    prompt_hash,
)
from app.ai.templates import SAMPLE_VALUES, TemplateError, iter_templates, load_templates, render

PLACEHOLDER_LEFTOVER = re.compile(r"[{}]")


def test_full_matrix_present():
    ts = load_templates()
    for lang in LANGUAGES:
        for purpose in PURPOSES:
            for channel in CHANNELS:
                assert ts.has(lang, purpose, channel, False), (lang, purpose, channel)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("channel", CHANNELS)
def test_every_template_renders_and_fits(language, purpose, channel):
    s = SAMPLE_VALUES[language]
    text = render(purpose, channel, language, owner_name=s["owner_name"], next_step=s["next_step"],
                  appointment_date=s["appointment_date"])
    assert not PLACEHOLDER_LEFTOVER.search(text)
    assert s["owner_name"] in text and s["next_step"] in text
    assert 0 < len(text) <= CHANNEL_MAX_CHARS[channel]
    # Without a date: only the confirmation needs one.
    if purpose != "appointment_confirmation":
        plain = render(purpose, channel, language, owner_name=s["owner_name"], next_step=s["next_step"])
        assert not PLACEHOLDER_LEFTOVER.search(plain)
        assert s["appointment_date"] not in plain


def test_every_entry_including_dated_variants_fits_worst_case():
    worst = "x" * MAX_FIELD_CHARS
    n = 0
    for e in iter_templates():
        text = templates._fill(e.text, {p: worst for p in templates.PLACEHOLDERS})
        assert len(text) <= CHANNEL_MAX_CHARS[e.channel], (e.language, e.purpose, e.key)
        assert not PLACEHOLDER_LEFTOVER.search(text)
        n += 1
    assert n >= len(LANGUAGES) * len(PURPOSES) * len(CHANNELS)


def test_date_variant_used_when_date_given():
    s = SAMPLE_VALUES["en"]
    with_date = render("reminder", "sms", "en", owner_name=s["owner_name"], next_step=s["next_step"],
                       appointment_date=s["appointment_date"])
    assert s["appointment_date"] in with_date


def test_confirmation_without_date_is_an_error():
    with pytest.raises(ValueError):
        render("appointment_confirmation", "sms", "en", owner_name="A", next_step="b")


def test_no_forbidden_style_words_in_templates():
    banned = re.compile(r"cancer|tumou?r|malignan|diagnos|you have|worry|medicat|polyp|https?://|www\.", re.I)
    banned_ar = re.compile("سرطان|ورم|خبيث|تشخيص|تقلق|القلق|دواء|مصاب")
    for e in iter_templates():
        assert not banned.search(e.text), (e.language, e.purpose, e.key)
        assert not banned_ar.search(e.text), (e.language, e.purpose, e.key)


def test_english_reads_at_plain_level():
    """Rough proxy for grade-6: short sentences and short words."""
    for e in iter_templates():
        if e.language != "en":
            continue
        text = re.sub(r"\{[a-z_]+\}", "word", e.text)
        sentences = [s for s in re.split(r"[.!?\n]+", text) if s.strip()]
        lengths = [len(s.split()) for s in sentences]
        assert max(lengths) <= 22, (e.purpose, e.key, max(lengths))
        assert sum(lengths) / len(lengths) <= 12, (e.purpose, e.key)


def test_arabic_is_marked_for_review():
    raw = (templates.TEMPLATE_DIR / "ar.yaml").read_text(encoding="utf-8")
    assert "# TODO native-speaker review (H2)" in raw
    # Every Arabic purpose block carries the marker.
    assert raw.count("# TODO native-speaker review (H2)") >= 1 + len(PURPOSES)


def test_arabic_templates_are_arabic_script():
    for e in iter_templates():
        if e.language == "ar":
            letters = re.findall(r"[^\W\d_{}]", re.sub(r"\{[a-z_]+\}", "", e.text))
            arabic = [c for c in letters if "؀" <= c <= "ۿ"]
            assert len(arabic) / len(letters) > 0.95, (e.purpose, e.key)


def test_template_provider_matches_render_and_is_deterministic():
    req = DraftRequest("result_notification", "sms", "en", "Dr Sara Khan", "primary_care_owner",
                       "a colonoscopy", "colorectal_fit")
    p = TemplateProvider()
    r1, r2 = p.draft(req), p.draft(req)
    assert r1 == r2
    assert r1.text == render("result_notification", "sms", "en", owner_name="Dr Sara Khan",
                             next_step="a colonoscopy")
    assert (r1.provider, r1.language, r1.fallback_used, r1.abstention_reason) == ("template", "en", False, None)
    assert r1.model_id == f"template-v{load_templates().version}"
    assert r1.prompt_hash == prompt_hash(req)


@pytest.mark.parametrize(
    "old,new",
    [
        ("    sms: >-", "    smsx: >-"),  # missing channel
        ("Your care owner is {owner_name}", "Your care owner is {patient_name}"),  # unknown placeholder
        ("The next step is {next_step}.", "The next step is soon."),  # required placeholder dropped
        ("Your care owner is {owner_name}", "Your care owner is {owner_name} " + "word " * 80),  # too long for sms (first use is sms)
    ],
    ids=["missing_channel", "unknown_placeholder", "dropped_next_step", "over_length"],
)
def test_loader_rejects_incomplete_or_bad_templates(tmp_path, monkeypatch, old, new):
    src = templates.TEMPLATE_DIR
    (tmp_path / "ar.yaml").write_text((src / "ar.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    en = (src / "en.yaml").read_text(encoding="utf-8")
    assert old in en
    (tmp_path / "en.yaml").write_text(en.replace(old, new, 1), encoding="utf-8")
    monkeypatch.setattr(templates, "TEMPLATE_DIR", tmp_path)
    templates.load_templates.cache_clear()
    try:
        with pytest.raises(TemplateError):
            templates.load_templates()
    finally:
        monkeypatch.undo()
        templates.load_templates.cache_clear()


def test_reviewer_pack_covers_every_string():
    md = templates.reviewer_pack_markdown()
    assert md.startswith("# Message template reviewer pack")
    assert "Arabic draft, unreviewed" in md and "H2" in md
    for e in iter_templates():
        for line in e.text.splitlines():
            if line.strip():
                assert line.strip() in md, (e.language, e.purpose, e.key, line)
        assert f"**{e.key}**" in md
    assert md.count("characters with sample values") == len(list(iter_templates()))


def test_result_notification_names_no_finding_and_says_who_will_call():
    """E14: the first message says the result is ready, that the care team needs to talk, and who will call."""
    sms = render("result_notification", "sms", "en", owner_name="Dr Sara Khan", next_step="a camera test")
    assert sms.startswith(
        "Your screening result is ready and your care team needs to talk with you about the next step.")
    assert "Dr Sara Khan will call you, or you can reply to book a time." in sms
    assert not re.search(r"blood|positive|abnormal|cancer|found", sms, re.I)
    for channel in ("whatsapp", "letter"):
        text = render("result_notification", channel, "en", owner_name="Dr Sara Khan", next_step="a camera test")
        assert "Your care team will explain your result in person or by phone." in text


def test_appointment_confirmations_name_the_appointment_not_a_disease():
    s = SAMPLE_VALUES["en"]
    for channel in ("sms", "whatsapp", "letter"):
        text = render("appointment_confirmation", channel, "en", owner_name=s["owner_name"],
                      next_step=s["next_step"], appointment_date=s["appointment_date"])
        assert f"appointment for {s['next_step']}" in text
        assert not re.search(r"blood|positive|abnormal|cancer|colonoscop", text, re.I)
