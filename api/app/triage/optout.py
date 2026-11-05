"""A deterministic opt-out gate that runs BEFORE the model (D15). Standard library only.

An opt-out is a consent decision and the costliest error to get wrong (docs/governance/hazard-log.md HZ-25), so it must not
depend on a model being loaded, reachable or right. A reply that says STOP in English, Arabic or Arabizi is answered here,
with `opt_out` and `needs_human` true, and the model is never asked. The gate is deliberately over-eager: a false positive
costs a person reading one reply; a miss is what the model (which still labels `opt_out`, always for a person) is for.
"""

from __future__ import annotations

import re

GATE_MODEL_ID = "rule:opt-out-gate-v2"

_ARABIC_MARKS = re.compile("[ؐ-ًؚ-ٰٟـ]")  # harakat, superscript alef, tatweel
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ئ": "ي", "ؤ": "و"})
# digits used as Arabizi letters stay as they are; Arabic-Indic digits are mapped to ASCII
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# F3 (ADR-059): every kind of apostrophe is dropped, not turned into a space, so "don't" and "don’t" are "dont" (the form the
# phrase lists use) and not "don t". Straight, curly, modifier-letter, prime, grave and acute.
_APOSTROPHES = re.compile("['\u2018\u2019\u201b\u02bc\u02b9\u2032\u0060\u00b4]")
_SPLIT_CONTRACTION = re.compile(r"\b(do|does|did|can|could|would|should|is|are|was|were|have|has|had|won) n ?t\b")


def normalise(text: str) -> str:
    t = _ARABIC_MARKS.sub("", text.lower()).translate(_ALEF).translate(_DIGITS)
    t = _APOSTROPHES.sub("", t)
    t = re.sub(r"[^\w\s]", " ", t)  # punctuation to space (keeps Arabic letters, digits, Latin letters)
    t = _SPLIT_CONTRACTION.sub(lambda m: m.group(1) + "nt", t)  # "do n't" typed with a stray space
    t = re.sub(r"\bdon t\b", "dont", t)  # "don t" (an apostrophe typed as a space)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


# Phrases are matched on normalised text with word boundaries (the surrounding spaces).
ENGLISH = (
    "stop", "stop messaging", "stop texting", "stop contacting", "stop sending", "unsubscribe", "opt out", "optout",
    "do not contact", "dont contact", "do not message", "dont message", "do not text", "dont text", "do not call",
    "dont call", "do not send", "dont send", "no more messages", "no more texts", "no more reminders", "remove me",
    "remove my number", "take me off", "leave me alone", "withdraw my consent", "i withdraw", "i do not consent",
    "i dont consent", "dont want messages", "do not want messages", "do not want any messages", "dont want any messages",
    "stop all", "cancel my subscription",
    # F3: the phrasings the first list missed once the apostrophe is gone ("don't text me again", "please don't contact me")
    "dont want these messages", "dont want these texts", "dont want more messages", "dont want any more messages",
    "dont want to receive", "dont want to be contacted", "dont want you to contact", "dont want texts", "dont want calls",
    "do not want to receive", "do not want these messages", "do not want to be contacted", "dont wish to receive",
    "do not wish to receive", "no longer wish to receive", "no longer want to receive", "no longer want messages",
    "dont send me", "do not send me", "dont text me", "dont contact me", "dont call me", "dont message me",
    "quit texting", "quit messaging", "quit contacting", "enough messages", "enough texts", "enough reminders",
    "too many messages", "too many texts", "no more sms", "no more whatsapp", "take my number off", "delete my number",
    "erase my number", "remove my details", "not interested in receiving", "i want out", "i opt out", "opting out",
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
    # F3: stop/opt-out stems in the imperative and with the person as subject ("stop the messages", "I want to stop")
    "لا ترسلوا",  # لا ترسلوا
    "لا تبعث",  # لا تبعث(وا)
    "لا تكلموني",  # لا تكلموني
    "لا تتواصل",  # لا تتواصل(وا)
    "كفايه رسايل",  # كفاية رسائل
    "بطلوا رسايل",  # بطلوا رسائل
    "بطلوا ارسال",  # بطلوا ارسال
    "لا اريد استلام",  # لا اريد استلام
    "لا ابغى استلام",  # لا ابغى استلام
    "لا ابي استلام",  # لا ابي استلام
    "ما ابغى استلام",  # ما ابغى استلام
    "ما ابي استلام",  # ما ابي استلام
    "ما ابي تواصل",  # ما ابي تواصل
    "ما ابغى تواصل",  # ما ابغى تواصل
    "عدم ارسال",  # عدم ارسال
    "عدم التواصل",  # عدم التواصل
    "ايقاف الرسايل",  # ايقاف الرسائل
    "ايقاف التواصل",  # ايقاف التواصل
)
# Stop verbs followed, within two words, by what is being stopped: اوقفوا الرسائل, ابي اوقف الرسائل, وقفوا التواصل, اوقفوا الاتصال.
# On NORMALISED text (hamza forms folded to alef or yeh, ة to ه), hence رسايل / رساله.
ARABIC_STOP_THE_MESSAGES = re.compile(
    "(?<=\\s)(?:ا?وقف\\w*|ايقاف|بطل\\w*|كف\\w*)\\s+(?:\\w+\\s+){0,2}?"
    "(?:ال)?(?:رسال|رسايل|رساي|تواصل|اتصال|مكالم|اشعار|تنبيه|ارسال|تذكير|بريد)")
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
    # F3: "stop the messages" as people type it (w2fo / wa2fo / waqfo / waqqfo + el rasayel), and "enough messages"
    "bas rasayel", "bas rasail", "kefaya rasayel", "kfaya rasayel", "khalas rasayel", "ma abi tawasol", "ma abgha tawasol",
    "la tersloni", "la trsloni", "la trsl", "la tkalmooni", "la tkellemoni", "la tkallemoni", "la tkellmoni",
    "ma abi astalem", "ma abgha astalem", "mabi astlm",
)
# Arabizi stop verbs (w2f, wa2f, waqf, waqqaf, waggef, twa2af) followed, within three words, by the messages / contact.
ARABIZI_STOP_THE_MESSAGES = re.compile(
    r"(?<=\s)(?:wa?[2qg]{1,2}[ae]?f|twa?[2q]{1,2}[ae]?f|wag[ae]f)\w*\s+(?:\w+\s+){0,2}?"
    r"(?:(?:el|al|il|l)\s+)?(?:r[a-z0-9]{0,3}s[a-z0-9]{0,3}l|tawasol|tawasul|ettisal|etisal|ersal)\w*")


def _compile(phrases: tuple[str, ...]) -> re.Pattern[str]:
    parts = sorted({normalise(p).strip() for p in phrases}, key=len, reverse=True)
    # Arabic phrases take suffixes (لا تراسلوني), so only Latin ones need a closing word boundary.
    return re.compile("|".join(
        r"(?<=\s)" + re.escape(p) + ("" if re.search("[\u0600-\u06ff]", p) and p not in _WHOLE_WORD else r"(?=\s)") for p in parts))


# Short Arabic stems that would match inside ordinary words (وقفت السيارة, "I parked") need the closing boundary too.
_WHOLE_WORD = {normalise(w).strip() for w in ("وقف", "توقف", "توقفوا", "ايقاف", "انسحب")}
_PATTERN = _compile(ENGLISH + ARABIC + ARABIZI)
_STEMS = (ARABIC_STOP_THE_MESSAGES, ARABIZI_STOP_THE_MESSAGES)

# A negated stop is not an opt-out ("please don't stop the reminders", "dont stop sending"; Arabic "لا توقفوا الرسائل").
_NEGATED_STOP = re.compile(r"(?:\bdo not|\bdont|\bdon t|\bnever|\bplease do not) stop\b|(?<=\s)(?:لا|ما) (?:ت|ا|ن)?وقف\w*")


def _matches(norm: str) -> bool:
    return _PATTERN.search(norm) is not None or any(stem.search(norm) for stem in _STEMS)


def is_opt_out(text: str) -> bool:
    """True when the reply contains an unambiguous STOP-style phrase. Pure, no I/O."""
    if not isinstance(text, str) or not text.strip():
        return False
    norm = normalise(text)
    if _NEGATED_STOP.search(norm) and not _matches(_NEGATED_STOP.sub(" ", norm)):
        return False
    return _matches(norm)
