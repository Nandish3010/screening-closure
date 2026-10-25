"""A deterministic opt-out gate that runs BEFORE the model (D15). Standard library only.

An opt-out is a consent decision and the costliest error to get wrong (docs/governance/hazard-log.md HZ-25), so it must not
depend on a model being loaded, reachable or right. A reply that says STOP in English, Arabic or Arabizi is answered here,
with `opt_out` and `needs_human` true, and the model is never asked. The gate is deliberately over-eager: a false positive
costs a person reading one reply; a miss is what the model (which still labels `opt_out`, always for a person) is for.
"""

from __future__ import annotations

import re

GATE_MODEL_ID = "rule:opt-out-gate-v1"

_ARABIC_MARKS = re.compile("[ؐ-ًؚ-ٰٟـ]")  # harakat, superscript alef, tatweel
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ئ": "ي", "ؤ": "و"})
# digits used as Arabizi letters stay as they are; Arabic-Indic digits are mapped to ASCII
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalise(text: str) -> str:
    t = _ARABIC_MARKS.sub("", text.lower()).translate(_ALEF).translate(_DIGITS)
    t = re.sub(r"[^\w\s]", " ", t)  # punctuation to space (keeps Arabic letters, digits, Latin letters)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


# Phrases are matched on normalised text with word boundaries (the surrounding spaces).
ENGLISH = (
    "stop", "stop messaging", "stop texting", "stop contacting", "stop sending", "unsubscribe", "opt out", "optout",
    "do not contact", "dont contact", "do not message", "dont message", "do not text", "dont text", "do not call",
    "dont call", "do not send", "dont send", "no more messages", "no more texts", "no more reminders", "remove me",
    "remove my number", "take me off", "leave me alone", "withdraw my consent", "i withdraw", "i do not consent",
    "i dont consent", "dont want messages", "do not want messages", "do not want any messages", "dont want any messages",
    "stop all", "cancel my subscription",
)
ARABIC = (
    "ايقاف",  # ايقاف
    "وقف",  # وقف
    "توقفوا",  # توقفوا
    "توقف",  # توقف
    "لا تراسل",  # لا تراسل(ني/وني)
    "لا ترسل",  # لا ترسل(وا)
    "لا تتصل",  # لا تتصل(وا)
    "لا تكلم",  # لا تكلم(وني)
    "لا تزعج",  # لا تزعج(ونا)
    "ما ابي رسايل",  # ما ابي رسايل
    "ما ابغى رسايل",  # ما ابغى رسايل
    "ما ابى رسايل",  # ما ابى رسايل
    "ما ابي اي رسايل",  # ما ابي اي رسايل
    "لا اريد رسائل",  # لا اريد رسائل
    "لا ارغب باستلام",  # لا ارغب باستلام
    "الغاء الاشتراك",  # الغاء الاشتراك
    "الغاء اشتراكي",  # الغاء اشتراكي
    "اشطب رقمي",  # اشطب رقمي
    "احذف رقمي",  # احذف رقمي
    "الغوا رقمي",  # الغوا رقمي
    "انسحب",  # انسحب
    "سحبت موافقتي",  # سحبت موافقتي
    "لا اوافق",  # لا اوافق
    "خلوني بحالي",  # خلوني بحالي
    "اتركوني",  # اتركوني
)
ARABIZI = (
    "wa2af", "wa2eaf", "wa2fo", "wagef", "waqf", "waqaf", "tawaqaf", "tawa2af", "tawaqqaf", "twa2af", "twaqaf",
    "la tkalmoni", "la tkalmouni", "la tkalmooni", "la tkalem", "la tkalmo", "la trasel", "la trasloni", "la traslo",
    "la tersel", "la tersilo", "la tetesel", "la tetasel", "la tt3ezj", "la tez3ej", "la tez3ejo", "la tez3ejoni",
    "ma abi rasayel", "ma abgha rasayel", "ma abga rasayel", "ma abi ay rasayel", "ma abgha ay rasayel",
    "ma abi rsayel", "ma abgha rsayel", "mabi rasayel", "mabgha rasayel", "mabgha rasail", "mabi rasail",
    "ma bede rasayel", "ma bidi rasayel", "ma bidi rasail", "ma urid rasail", "la areed rasail",
    "elghi el eshtirak", "elgha el eshterak", "elgha al eshterak", "elghi al ishtirak", "ilghi el ishtirak",
    "ishtib rakmi", "ishtab raqmi", "ishtib raqmi", "oshtob raqmi", "ashtob raqmi", "ahzef raqmi", "ihzif raqmi",
    "7ezf raqmi", "7ezef raqmi", "ma ba3ref we ma abi", "sa7abt mawafaqati", "sahabt mowafaqati", "sa7abt mowafaqti",
    "khalooni b7ali", "khaloni b7ali", "khalouni b7ali", "utrokoni", "otrokoni", "etrokoni", "ma abi ahad yetsel",
    "la tetsel", "la titasil", "la tet2asel", "la tet3asal",
)


def _compile(phrases: tuple[str, ...]) -> re.Pattern[str]:
    parts = sorted({normalise(p).strip() for p in phrases}, key=len, reverse=True)
    # Arabic phrases take suffixes (لا تراسلوني), so only Latin ones need a closing word boundary.
    return re.compile("|".join(
        r"(?<=\s)" + re.escape(p) + ("" if re.search("[\u0600-\u06ff]", p) and p not in _WHOLE_WORD else r"(?=\s)") for p in parts))


# Short Arabic stems that would match inside ordinary words (وقفت السيارة, "I parked") need the closing boundary too.
_WHOLE_WORD = {normalise(w).strip() for w in ("وقف", "توقف", "توقفوا", "ايقاف", "انسحب")}
_PATTERN = _compile(ENGLISH + ARABIC + ARABIZI)

# A negated stop is not an opt-out ("please don't stop the reminders", "dont stop sending").
_NEGATED_STOP = re.compile(r"(?:\bdo not|\bdont|\bdon t|\bnever|\bplease do not) stop\b")


def is_opt_out(text: str) -> bool:
    """True when the reply contains an unambiguous STOP-style phrase. Pure, no I/O."""
    if not isinstance(text, str) or not text.strip():
        return False
    norm = normalise(text)
    if _NEGATED_STOP.search(norm) and not _PATTERN.search(_NEGATED_STOP.sub(" ", norm)):
        return False
    return _PATTERN.search(norm) is not None
