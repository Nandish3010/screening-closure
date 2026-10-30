"""Which language group a reply belongs to, detected at run time, and the stricter rule for the groups the model reads
worst (M7-1, after the M7-5 fairness audit, docs/results/fairness.md).

The audit found the model's auto-handled precision below the 95% target for two groups: Arabizi (Arabic written in Latin
letters, 81%) and Gulf-dialect Arabic (91%). Both are now detected here, deterministically and before anything acts:

* `arabizi`: no Arabic letters, some Latin ones, and either an Arabic numeral-letter inside a word (2 hamza, 3 ain,
  5 kha, 6 ta, 7 ha, 8 qaf, 9 sad: "ma3", "a2dar", "7aja", "9abl") or a word of the Arabizi lexicon below.
* `ar_gulf`: Arabic letters and at least one word of a short, conservative list of Gulf-dialect markers (words that
  Modern Standard Arabic does not use in this form: "ابي", "ابغى", "وايد", "الحين", "سويت", "مب", ...). Mixed Arabic and
  Latin replies with a Gulf marker count as Gulf.

The rule (`apply_group_rule`), on top of every other reason a reply goes to a person:

* Arabizi: auto-handled only when the confidence clears `GROUP_THRESHOLDS["arabizi"]` (0.918, fitted on the validation
  split of the detected group for 97% precision, the same way the global threshold was fitted; tools/rai/fairness.py
  re-fits it and fails its check if the value here is not the fitted one).
* Gulf dialect: always to a person. No threshold reaches the target on this group (the audit), so none is used.

A reply the detector misses keeps the global rules; one it flags wrongly costs a person's time, never safety.
"""

from __future__ import annotations

import re

ARABIZI = "arabizi"
AR_GULF = "ar_gulf"
# group -> the confidence an auto-handled reply of the group must reach; None: every reply of the group to a person
GROUP_THRESHOLDS: dict[str, float | None] = {ARABIZI: 0.918, AR_GULF: None}

_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-z]")
# a numeral-letter inside a word: a digit 2/3/5/6/7/8/9 touching a Latin letter ("ma3", "3ala", "a2dar", "7aja"), in a
# word that is not an English ordinal, time or unit ("25th", "4pm", "10am", "2nd")
_NUMERAL_LETTER = re.compile(r"(?:[A-Za-z][235-9]|[235-9][A-Za-z])")
_ENGLISH_NUMBER = re.compile(r"^\d+(?:st|nd|rd|th|am|pm|h|hrs?|min|mins|s|k|x)?$", re.I)
_ARABIZI_WORDS = frozenset("""
momken mumkin momkin mumken yimkin el il fil bil bel wil ma mish msh mesh mub mob mashi wallah walla wallahi yalla
shukran shokran mashkoor mashkoora mashkooreen mashkurin khalas khallast khalast khallas khalasna ana enta inta enti inti
shino shinu aywa feeni fini hatha hadha hathi shi sawwait sawwayt sawwaytah sawet ajee aji abi abgha abgh bokra bukra
baachir bacher lazem lazim law samaht asif aasif aasef tersil tersiloha tersiloon terslioha nghayyer ghayyer el7een alhin
wayed wayid zain zein shlon shlonak shu shoo esh leish laish leh 3arabi englizi englezi maw3id maw3ed fahs 3iyada
""".split())
_WORD = re.compile(r"[A-Za-z0-9']+")
_GULF_MARKERS = (
    "ابي", "ابغى", "ابغي", "أبغى", "نبي", "تبون", "وايد", "شلون", "الحين", "هالحين", "مب", "سويت", "سويته", "سويتها", "سوينا",
    "الجاي", "اجي", "باجر", "باچر", "تسلمون", "ترسلونها", "الحرمة", "يبا", "چ", "عيل", "زين",
    "اقدر", "مشكورين", "ليش", "خلاص", "رسايل", "الرسايل", "انسوى", "بجي", "هذي", "هلا",
    "اسوي", "نسوي", "يسوي", "شو", "اللي", "خلني", "خلنا", "اجيب", "اجيبها", "فيني", "بس",
)
# colloquial blessings in Gulf word order (verb after "الله"; Modern Standard Arabic writes "وفقكم الله")
_GULF_PHRASES = ("الله يوفقك", "الله يحفظك", "الله يعطيك", "يعطيك العافية", "الله يخليك")
_GULF_WORDS = frozenset(m for m in _GULF_MARKERS if len(m) > 1)
_ARABIC_WORD = re.compile(r"[؀-ۿ]+")


def detect(text: str) -> str | None:
    """`arabizi`, `ar_gulf`, or None (English, Modern Standard Arabic, or not recognised)."""
    if not isinstance(text, str) or not text.strip():
        return None
    has_arabic = bool(_ARABIC.search(text))
    if not has_arabic:
        if not _LATIN.search(text):
            return None
        tokens = _WORD.findall(text)
        if any(_NUMERAL_LETTER.search(t) and not _ENGLISH_NUMBER.match(t) for t in tokens):
            return ARABIZI
        words = {t.lower().strip("'") for t in tokens}
        return ARABIZI if words & _ARABIZI_WORDS else None
    if "چ" in text or any(phrase in text for phrase in _GULF_PHRASES):
        return AR_GULF
    words = set(_ARABIC_WORD.findall(text))
    # a marker on its own, or with a leading conjunction or particle ("وابي", "ما ابي" is two words already)
    for w in words:
        if w in _GULF_WORDS or (len(w) > 2 and w[0] in "وف" and w[1:] in _GULF_WORDS):
            return AR_GULF
    return None


def group_reason(result: dict, text: str) -> str | None:
    """Why a reply is with a person because of its language group, for display only: the group (`ar_gulf`, `arabizi`) when the
    group rule applies to this reply and it is with a person, else None. Never changes a result; `apply_group_rule` decides.
    An intent that always goes to a person (opt-out, question, other) has its own reason, so no group is named for it."""
    from .labels import ALWAYS_HUMAN
    group = detect(text)
    if group is None or result.get("needs_human") is not True or result.get("intent") in ALWAYS_HUMAN:
        return None
    tau = GROUP_THRESHOLDS[group]
    conf = result.get("confidence")
    if tau is None or not isinstance(conf, (int, float)) or conf < tau:
        return group
    return None


def apply_group_rule(result: dict, text: str) -> dict:
    """The result with `needs_human` forced for a detected group whose confidence does not clear the group's threshold.
    Same keys as the classifier's contract. Never makes a reply that needed a person not need one."""
    group = detect(text)
    if group is None or result.get("needs_human") is True:
        return result
    tau = GROUP_THRESHOLDS[group]
    if tau is None or not isinstance(result.get("confidence"), (int, float)) or result["confidence"] < tau:
        return {**result, "needs_human": True}
    return result
