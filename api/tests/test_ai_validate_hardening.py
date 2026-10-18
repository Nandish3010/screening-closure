"""SEC-1 fix 5: the bypasses an independent review found in the output validator, one test each."""

import pytest
from test_ai_validate import GOOD, make_req

from app.ai import validate as v
from app.ai.provider import DraftRequest
from app.ai.validate import forbidden_hits, skeleton, validate

EN = make_req("en")
AR = make_req("ar")


def reasons(text, req=EN):
    return validate(text, req).reasons


# ---- invisible characters ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char",
    [
        "​",  # zero-width space
        "‌", "‍",  # zero-width non-joiner / joiner
        "⁠",  # word joiner
        "﻿",  # BOM / zero-width no-break space
        "­",  # soft hyphen
        "‎", "‏", "‮", "⁦",  # bidi marks and overrides
        "\U000e0041",  # tag character
        "", "\U000f0000",  # private use
        "\x00", "\x07", "\x1b", "\x7f", "\t", "\r", "\x85",  # controls (not the line feed)
        " ", " ",  # line and paragraph separators
        "\ud800",  # a lone surrogate
    ],
)
def test_an_invisible_character_anywhere_is_rejected(char):
    assert v.INVISIBLE_CHARACTER in reasons(GOOD["en"] + char)
    assert v.INVISIBLE_CHARACTER in reasons(char + GOOD["en"])
    assert v.INVISIBLE_CHARACTER in reasons(GOOD["en"].replace("result", "res" + char + "ult"))


@pytest.mark.parametrize("zw", ["​", "‌", "‍", "⁠", "﻿", "­", "‮"])
def test_zero_width_inside_cancer_is_rejected_and_the_word_is_still_found(zw):
    res = reasons(GOOD["en"] + f" You have can{zw}cer.")
    assert v.INVISIBLE_CHARACTER in res
    assert "forbidden_phrase:cancer" in res  # the skeleton drops the character, so the phrase matches as well


def test_zero_width_in_a_multiword_phrase_and_across_the_space():
    res = reasons(GOOD["en"] + " Do not​ worry.")
    assert v.INVISIBLE_CHARACTER in res and any(r.startswith("forbidden_phrase:") for r in res)


def test_arabic_text_with_a_hidden_character_is_rejected():
    assert v.INVISIBLE_CHARACTER in reasons(GOOD["ar"] + " لديك سر‍طان", AR)
    assert v.INVISIBLE_CHARACTER in reasons(GOOD["ar"] + "‏", AR)


def test_a_plain_line_feed_is_still_allowed_because_the_templates_use_it():
    assert validate(GOOD["en"] + "\n\nThank you.", make_req("en", channel="letter")).ok
    assert validate(GOOD["ar"] + "\n\nشكراً لك.", make_req("ar", channel="letter")).ok


# ---- homoglyphs and scripts --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word",
    [
        "cаncer",  # Cyrillic a
        "саnсеr",  # Cyrillic с а с е
        "cαncer",  # Greek alpha
        "cancеr",  # Cyrillic e
        "сancer",  # Cyrillic с
        "CANCER".replace("C", "С").replace("A", "А"),  # capitals: Cyrillic С А
        "cancer".replace("c", "ϲ"),  # Greek lunate sigma, a c
    ],
)
def test_cyrillic_and_greek_cancer_is_rejected_twice_over(word):
    res = reasons(GOOD["en"] + f" You have {word}.")
    assert "forbidden_phrase:cancer" in res  # the skeleton match
    assert v.WRONG_LANGUAGE in res  # and a letter outside the Latin script
    assert word not in "cancer"


def test_a_single_foreign_letter_fails_the_script_rule_with_no_ratio():
    long_text = GOOD["en"] + " Your care team is here to help." * 4  # many Latin letters, one wrong one below
    assert v.WRONG_LANGUAGE not in reasons(long_text, make_req("en", channel="letter"))
    for odd in ("а", "α", "א", "中", "ا"):  # Cyrillic, Greek, Hebrew, CJK, Arabic
        assert v.WRONG_LANGUAGE in reasons(long_text + f" Ok{odd}", make_req("en", channel="letter")), odd


def test_a_single_latin_letter_in_an_arabic_message_fails_too():
    assert validate(GOOD["ar"], AR).ok
    assert v.WRONG_LANGUAGE in reasons(GOOD["ar"] + " x", AR)
    assert v.WRONG_LANGUAGE in reasons(GOOD["ar"] + " а", AR)


def test_request_values_in_another_script_are_still_allowed_when_used_whole():
    req = DraftRequest("result_notification", "sms", "ar", "Dr Sara Khan", "primary_care_owner", "تنظير القولون", "colorectal_fit", None)
    text = "نتيجة فحصك جاهزة. الخطوة التالية هي تنظير القولون. المسؤول عن رعايتك هو Dr Sara Khan."
    assert validate(text, req).ok


@pytest.mark.parametrize("entry", [e for e in v.load_forbidden_phrases() if e.language == "en"], ids=lambda e: e.id)
def test_every_english_forbidden_entry_is_found_through_cyrillic_lookalikes(entry):
    swap = str.maketrans({"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
                          "y": "у", "i": "і", "s": "ѕ", "j": "ј", "h": "һ"})
    spoof = entry.example.translate(swap)
    if spoof == entry.example:
        pytest.skip("no letter of this example has a Cyrillic look-alike in the table")
    assert entry.id in {h.id for h in forbidden_hits(spoof)}, spoof


def test_skeleton_folds_the_homoglyphs_and_nothing_else():
    assert skeleton("CАNCЕR") == "cancer"
    assert skeleton("c​an­cer") == "cancer"
    assert skeleton("Plain text, 123.") == "plain text, 123."
    assert skeleton("naïve") == "naive"


def test_ordinary_latin_text_is_not_flagged_by_the_skeleton():
    for text in (GOOD["en"], "The clinic is open. Please keep this message secure and close by.", "Café visit"):
        assert not [h for h in forbidden_hits(text)], text


# ---- digits ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "050/123/4567", "050,123,4567", "050.123.4567", "050 123 4567", "050-123-4567", "(050) 123/4567",
        "0 5 0 / 1 2 3 / 4 5 6 7", "+971/50/123/4567",
        "٠٥٠/١٢٣/٤٥٦٧",  # Arabic-Indic
        "०५०१२३४५६७",  # Devanagari
        "０５０１２３４５６７",  # fullwidth
        "০৫০১২৩৪৫৬৭",  # Bengali
    ],
)
def test_digit_runs_with_any_separator_are_personal_data(number):
    assert v.PII_DIGITS in reasons(GOOD["en"] + f" Call {number} now.")


@pytest.mark.parametrize("fine", ["floor 3", "3/4 of the time", "room 12", "1,5 hours", "call in 7 days", "10:30", "at 3.5 km"])
def test_short_numbers_are_still_fine(fine):
    assert v.PII_DIGITS not in reasons(GOOD["en"] + f" See {fine}.")


# ---- masking request values ------------------------------------------------------------------------------------------------


def om_request(language="en", **kw):
    return DraftRequest("result_notification", "sms", language, "Om", "primary_care_owner", "a colonoscopy", "colorectal_fit", None, **kw)


BASE = "Your screening result is ready. The next step is a colonoscopy. Your care owner is Om."


def test_the_owner_om_does_not_hide_evil_dot_com():
    req = om_request()
    assert validate(BASE, req).ok
    assert v.URL in validate(BASE + " Visit evil.com", req).reasons
    assert v.URL in validate(BASE + " Visit evil.Com", req).reasons
    assert v.URL in validate(BASE + " Visit EVIL.COM", req).reasons
    assert v.URL in validate(BASE + " Visit om.com", req).reasons  # the name at the start of a dotted token


@pytest.mark.parametrize("owner,link", [("Co", "shop.co"), ("Me", "bit.me"), ("Ly", "x.ly"), ("Io", "evil.io"), ("Ai", "evil.ai"),
                                       ("App", "evil.app"), ("Org", "evil.org"), ("Net", "evil.net")])
def test_an_owner_named_like_a_tld_does_not_hide_a_link(owner, link):
    req = DraftRequest("result_notification", "sms", "en", owner, "primary_care_owner", "a colonoscopy", "colorectal_fit", None)
    text = f"Your screening result is ready. The next step is a colonoscopy. Your care owner is {owner}. See {link}"
    assert v.URL in validate(text, req).reasons


def test_masking_works_only_at_word_boundaries():
    from app.ai.validate import _strip_values

    req = om_request()
    mask = lambda text: " ".join(_strip_values(text, req).split())  # noqa: E731
    assert mask("Contact Om. Thanks") == "Contact . Thanks"  # a name at the end of a sentence is masked
    assert mask("Om and OM and om") == "and and"  # case-insensitive, whole tokens
    assert mask("Hello Omar, from Rome") == "Hello Omar, from Rome"  # inside other words: kept
    assert mask("evil.com") == "evil.com" and mask("om.com") == "om.com" and mask("evil.Om") == "evil.Om"
    assert mask("a/Om/b") == "a/Om/b" and mask("x@Om") == "x@Om" and mask("Om-x") == "Om-x"
    assert mask("a colonoscopy5551234567") == "a colonoscopy5551234567"  # glued to digits: not the request value
    assert mask("The next step is a colonoscopy.") == "The next step is ."


def test_a_digit_run_cannot_hide_inside_a_masked_value():
    req = DraftRequest("result_notification", "sms", "en", "Dr Sara Khan", "primary_care_owner", "a colonoscopy", "colorectal_fit", None)
    text = "Your screening result is ready. The next step is a colonoscopy5551234567. Dr Sara Khan will contact you."
    assert not validate(text, req).ok  # "a colonoscopy" glued to digits is not the request value, so the glued text stays visible
    assert v.PII_IDENTIFIER in validate(text, req).reasons
