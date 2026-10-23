"""Output validation and template fallback for model-drafted messages (D3).

`validate(text, req)` checks one draft against the rules below. `draft_with_fallback` calls a
provider and, if it raises or its output fails validation, returns the deterministic template
instead (fallback_used=True, with the abstention reason) and records the abstention.
The rules are deliberately strict: a false rejection costs a template message, a false accept
could put a diagnosis or an identifier in front of a patient.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.ai.provider import (
    CHANNEL_MAX_CHARS,
    DraftRequest,
    CacheMiss,
    DraftResult,
    ProviderUnavailable,
    Provider,
    TemplateProvider,
)

FORBIDDEN_PHRASES_PATH = Path(__file__).parent / "forbidden_phrases.yaml"

# Reason codes. A validation reason is one of these, or "forbidden_phrase:<id>".
EMPTY = "empty"
UNFILLED_PLACEHOLDER = "unfilled_placeholder"
TOO_LONG = "too_long"
WRONG_LANGUAGE = "wrong_language"
MISSING_OWNER_NAME = "missing_owner_name"
MISSING_NEXT_STEP = "missing_next_step"
MISSING_APPOINTMENT_DATE = "missing_appointment_date"
FORBIDDEN_PHRASE = "forbidden_phrase"
PII_DIGITS = "pii_digits"
PII_EMAIL = "pii_email"
PII_IDENTIFIER = "pii_identifier"
URL = "url"
INVISIBLE_CHARACTER = "invisible_character"

RULE_CODES: tuple[str, ...] = (
    EMPTY, UNFILLED_PLACEHOLDER, TOO_LONG, WRONG_LANGUAGE, MISSING_OWNER_NAME, MISSING_NEXT_STEP,
    MISSING_APPOINTMENT_DATE, FORBIDDEN_PHRASE, PII_DIGITS, PII_EMAIL, PII_IDENTIFIER, URL, INVISIBLE_CHARACTER,
)

# Abstention reasons for the fallback (not validation rules).
PROVIDER_UNAVAILABLE = "provider_unavailable"
PROVIDER_ERROR = "provider_error"
VALIDATION_FAILED = "validation_failed"
INVALID_RESULT = "invalid_result"
CACHE_MISS = "cache_miss"  # replay mode had no recorded output (D6)

MIN_LETTERS = 5  # fewer letters than this once our own values are removed is not a message


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...] = ()


# ---------------------------------------------------------------- text helpers

_TASHKEEL = re.compile("[ً-ْـٰ]")  # diacritics and tatweel
# Persian and Urdu letter forms that look like (and are typed instead of) the Arabic ones.
_ARABIC_VARIANTS = str.maketrans({"ک": "ك", "ی": "ي", "ې": "ي", "ے": "ي", "ہ": "ه", "ھ": "ه", "ۀ": "ه", "ۃ": "ة"})

# Characters a reader cannot see (or that reorder, join or hide text). None of them belongs in a message to a
# patient, and each is a way to slip a forbidden word past a matcher: Cf format (zero-width space/joiner, bidi marks and
# overrides, soft hyphen, tag characters), Cc control, Co private use; plus Cs/Cn (never valid text) and the
# Zl/Zp line and paragraph separators. A message with any of them is refused outright (SEC-1). The one exception is
# the plain line feed U+000A, which the vetted WhatsApp and letter templates use for paragraphs; tab and carriage
# return are not allowed.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cs", "Cn", "Zl", "Zp"})
_ALLOWED_CONTROLS = frozenset("\n")


def _invisible(char: str) -> bool:
    return unicodedata.category(char) in _INVISIBLE_CATEGORIES and char not in _ALLOWED_CONTROLS


def has_invisible_character(text: str) -> bool:
    return any(_invisible(c) for c in text)


def normalise_arabic(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _TASHKEEL.sub("", text).translate(_ARABIC_VARIANTS)
    for src in "أإآٱ":
        text = text.replace(src, "ا")
    return text.replace("ى", "ي")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", normalise_arabic(text)).strip().casefold()


# A small Latin/Cyrillic/Greek homoglyph map (the letters that are drawn alike, after casefolding): the "skeleton" of a
# text folds them to the Latin letter, so "cаncer" (Cyrillic а) and "cαncer" (Greek α) match the forbidden "cancer".
# Not the full Unicode confusables table, deliberately: it is the Latin look-alikes that matter for the English list.
_CONFUSABLES = {
    # Cyrillic (lower case; capitals fold to these)
    "а": "a", "в": "b", "г": "r", "е": "e", "ё": "e", "з": "3", "и": "u", "й": "u", "к": "k", "м": "m", "н": "h",
    "о": "o", "п": "n", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ч": "4", "ь": "b", "ы": "bl", "ѕ": "s",
    "і": "i", "ї": "i", "ј": "j", "ӏ": "l", "ԁ": "d", "ԛ": "q", "ԝ": "w", "ӓ": "a", "ӧ": "o", "ѵ": "v", "һ": "h",
    "ɡ": "g", "ɑ": "a", "ɩ": "i", "ʋ": "v", "ս": "u", "օ": "o", "ո": "n", "ꞵ": "b",
    # Greek
    "α": "a", "β": "b", "γ": "y", "ε": "e", "ζ": "z", "η": "n", "ι": "i", "κ": "k", "μ": "u", "ν": "v", "ο": "o",
    "π": "n", "ρ": "p", "σ": "o", "ς": "c", "τ": "t", "υ": "u", "φ": "o", "χ": "x", "ω": "w", "ϲ": "c", "ϳ": "j",
    "ϵ": "e", "ϑ": "o", "ϰ": "k", "ϱ": "p", "ϖ": "n",
    # Latin look-alikes outside plain ASCII
    "ı": "i", "ȷ": "j", "ł": "l", "ø": "o", "đ": "d", "ƒ": "f", "ʀ": "r", "ɴ": "n", "ɢ": "g", "ʜ": "h", "ᴀ": "a",
    "ᴄ": "c", "ᴇ": "e", "ʟ": "l", "ᴍ": "m", "ᴏ": "o", "ᴘ": "p", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ᴢ": "z",
    "ǀ": "l", "|": "l",
}
_CONFUSABLE_TABLE = str.maketrans(_CONFUSABLES)


def skeleton(text: str) -> str:
    """The text as a reader would take it for Latin: NFKC, invisible characters dropped, combining marks dropped,
    case folded, Cyrillic/Greek homoglyphs folded to the Latin letter they imitate. Only for matching against the
    forbidden list; the message itself is never rewritten."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not _invisible(c))
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if unicodedata.category(c) != "Mn")
    # Fold twice: normalisation and case folding both rewrite some letters (U+03F2 becomes a final sigma, then sigma).
    text = unicodedata.normalize("NFKC", text).translate(_CONFUSABLE_TABLE)
    return text.casefold().translate(_CONFUSABLE_TABLE)


# A request value (owner name, next step...) is masked only where it stands alone as a token. Without this, an owner
# named "Om" removed the "om" of "evil.com" and let the link through (SEC-1). A neighbouring word character, `@`, `/`,
# `:` or `-` joins the match to a longer token, and so does a dot with a word character beyond it.
_TOKEN_BEFORE = r"(?<![\w@/:\-])(?<!\w\.)"
_TOKEN_AFTER = r"(?![\w@/:\-])(?!\.\w)"


def _strip_values(text: str, req: DraftRequest) -> str:
    """Remove the request's own values (name, next step, date, role) from the text, as whole tokens only."""
    for value in (req.owner_name, req.next_step, req.appointment_date, req.owner_role):
        if value:
            text = re.sub(_TOKEN_BEFORE + re.escape(value) + _TOKEN_AFTER, " ", text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------- forbidden phrases

_AR_BOUNDARY_BEFORE = "(?<![ء-ي])"
_AR_PREFIX = "(?:ال|وال|بال|فال|كال|لل|[وفبلك])?"
_AR_SUFFIX = "(?:ات|ان|ين|ون|ها|هم|ه|ك|ي|ة|ا)?(?![ء-ي])"


@dataclass(frozen=True)
class ForbiddenPhrase:
    id: str
    language: str
    category: str
    rationale: str
    example: str
    source: str  # the phrase or regex as written in the YAML
    pattern: re.Pattern


def _compile(language: str, entry: dict) -> ForbiddenPhrase:
    if language == "ar":
        phrase = normalise_arabic(entry["phrase"])
        body = r"\s+".join(re.escape(w) for w in phrase.split())
        pattern = re.compile(_AR_BOUNDARY_BEFORE + _AR_PREFIX + body + _AR_SUFFIX)
    elif "phrase" in entry:
        body = r"\s+".join(re.escape(w) for w in entry["phrase"].split())
        pattern = re.compile(rf"\b{body}\b", re.IGNORECASE)
    else:
        pattern = re.compile(rf"\b(?:{entry['regex']})\b", re.IGNORECASE)
    return ForbiddenPhrase(entry["id"], language, entry["category"], entry["rationale"].strip(),
                           entry["example"], entry.get("phrase") or entry["regex"], pattern)


@lru_cache(maxsize=1)
def load_forbidden_phrases() -> tuple[ForbiddenPhrase, ...]:
    data = yaml.safe_load(FORBIDDEN_PHRASES_PATH.read_text(encoding="utf-8"))
    out: list[ForbiddenPhrase] = []
    seen: set[tuple[str, str]] = set()
    for language in ("en", "ar"):
        for entry in data[language]:
            key = (language, entry["id"])
            if key in seen:
                raise ValueError(f"duplicate forbidden phrase id {key}")
            seen.add(key)
            out.append(_compile(language, entry))
    return tuple(out)


# Spelling tricks: a word is broken up with spaces, dots, dashes or underscores ("c a n c e r", "can-cer"), stretched with
# repeated letters ("canccer", "سرررطان") or written with digits and symbols for letters ("c4nc3r"). For the disease
# stems below the text is also matched with every non-letter dropped, repeats collapsed and digits read as letters.
# A stem found only that way is a hit on the entry named here. The price is an occasional false rejection of text such
# as "can certainly", which costs one template message (RT-V-05).
_LEET = str.maketrans({"4": "a", "3": "e", "0": "o", "1": "i", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_SQUEEZED_EN = (
    ("cancer", "cancer"), ("tumou?r", "tumour"), ("malignan", "malignant"), ("carcinoma|neoplas", "carcinoma_neoplasm"),
    ("diagnos", "diagnos"), ("saratan|sartan", "arabizi_cancer"),
)
_SQUEEZED_AR = (("سرطان", "cancer"), ("خبيث", "malignant"), ("تشخيص", "diagnosis"), ("كارسينوما", "carcinoma"))


def _squeezed_hits(text: str, found: set[tuple[str, str]]) -> list[ForbiddenPhrase]:
    by_key = {(fp.language, fp.id): fp for fp in load_forbidden_phrases()}
    latin = re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]+", "", skeleton(text).translate(_LEET)))
    arabic = re.sub(r"(.)\1+", r"\1", re.sub(r"[^ء-ي]+", "", normalise_arabic("".join(c for c in text if not _invisible(c)))))
    hits = []
    for lang, haystack, table in (("en", latin, _SQUEEZED_EN), ("ar", arabic, _SQUEEZED_AR)):
        for stem, entry_id in table:
            key = (lang, entry_id)
            if key not in found and key in by_key and re.search(stem, haystack):
                found.add(key)
                hits.append(by_key[key])
    return hits


def forbidden_hits(text: str) -> list[ForbiddenPhrase]:
    """Every forbidden-phrase entry (either language) found in `text`. English entries are matched on the
    text's skeleton, so look-alike Cyrillic or Greek letters and hidden characters do not hide a word, and the disease
    stems are matched again with the text squeezed (spaces, punctuation, repeats, digit-for-letter)."""
    plain = text.casefold()
    latin = skeleton(text)
    arabic = normalise_arabic("".join(c for c in text if not _invisible(c)))
    hits = [
        fp for fp in load_forbidden_phrases()
        if (fp.pattern.search(arabic) if fp.language == "ar" else (fp.pattern.search(plain) or fp.pattern.search(latin)))
    ]
    return hits + _squeezed_hits(text, {(fp.language, fp.id) for fp in hits})


# ---------------------------------------------------------------- personal data and links

_PLACEHOLDER = re.compile(r"[{}]|\[[^\]]{0,40}\]|<[^>]{0,40}>")
# Seven digits, however they are punctuated: space, dash, dot, brackets, and `/` and `,` (050/123/4567, 050,123,4567).
_LONG_DIGITS = re.compile(r"(?<![\w])\+?\d(?:[\s\-.()/,]*\d){6,}")
_EMAIL = re.compile(r"@|\b[\w.+-]+\s*(?:\[at\]|\(at\))\s*[\w-]+")
_ID_WORDS = re.compile(
    r"\b(?:mrn|emirates\s*id|eid|passport|nhs\s*number|id\s*(?:no|number|card)|file\s*(?:no|number)|"
    r"member\s*id|policy\s*(?:no|number)|medical\s*record)\b"
    r"|رقم\s+(?:ال)?(?:هوية|هويه|ملف|جواز|بطاقة|بطاقه|سجل)|الهوية\s+الاماراتية|الهويه\s+الاماراتيه",
    re.IGNORECASE,
)
_MIXED_TOKEN = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{6,}\b")
_URL = re.compile(
    r"https?\s*:|://|\bwww\.|\bftp\.|\b(?:t\.me|wa\.me|bit\.ly|goo\.gl)\b|"
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|ae|gov|edu|io|co|me|ly|app|link|info|biz|xyz|sa|uk|ai)\b",
    re.IGNORECASE,
)


_SCRIPT_PREFIX = {"en": "LATIN", "ar": "ARABIC"}


@lru_cache(maxsize=4096)
def _script_of(char: str) -> str:
    """The script word of a letter's Unicode name (LATIN, ARABIC, CYRILLIC, GREEK...), '' if it has no name."""
    try:
        return unicodedata.name(char).split(" ", 1)[0]
    except ValueError:
        return ""


_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "صفر": "0", "واحد": "1", "اثنان": "2", "اثنين": "2", "ثلاثة": "3", "ثلاثه": "3", "اربعة": "4", "اربعه": "4",
    "خمسة": "5", "خمسه": "5", "ستة": "6", "سته": "6", "سبعة": "7", "سبعه": "7", "ثمانية": "8", "ثمانيه": "8",
    "تسعة": "9", "تسعه": "9",
}
_DIGIT_WORD = re.compile(r"(?<![\w])(" + "|".join(_DIGIT_WORDS) + r")(?![\w])", re.IGNORECASE)


def _words_to_digits(text: str) -> str:
    """Number words, English and Arabic, as digits, so "nine seven one five ..." is a phone number like 9715...."""
    return _DIGIT_WORD.sub(lambda m: _DIGIT_WORDS[m.group(1).lower()], normalise_arabic(text))


def _ascii_digits(text: str) -> str:
    """Every decimal digit of every script (Arabic-Indic, Devanagari, Bengali, fullwidth...) as 0-9."""
    return "".join(str(unicodedata.decimal(c)) if unicodedata.category(c) == "Nd" else c for c in text)


def _language_ok(text: str, language: str) -> bool:
    """Every letter must belong to the language's script: Latin for English, Arabic for Arabic. No ratio: one
    Cyrillic or Greek letter among Latin ones is how a look-alike word is made, so one is too many."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return False
    want = _SCRIPT_PREFIX[language]
    return all(_script_of(c) == want for c in letters)


def validate(text: str, req: DraftRequest) -> ValidationResult:
    """Check a draft against every rule. Returns all failed rules, not only the first."""
    if not isinstance(text, str) or not text.strip():
        return ValidationResult(False, (EMPTY,))
    reasons: list[str] = []

    if has_invisible_character(text):
        reasons.append(INVISIBLE_CHARACTER)
    if _PLACEHOLDER.search(text):
        reasons.append(UNFILLED_PLACEHOLDER)
    if len(text) > CHANNEL_MAX_CHARS[req.channel]:
        reasons.append(TOO_LONG)

    residue = _strip_values(text, req)  # what the model wrote beyond our own field values
    if not _language_ok(residue, req.language):
        reasons.append(WRONG_LANGUAGE)

    haystack = _collapse(text)
    if _collapse(req.owner_name) not in haystack:
        reasons.append(MISSING_OWNER_NAME)
    if _collapse(req.next_step) not in haystack:
        reasons.append(MISSING_NEXT_STEP)
    if req.purpose == "appointment_confirmation" and _collapse(req.appointment_date or "") not in haystack:
        reasons.append(MISSING_APPOINTMENT_DATE)

    for hit in forbidden_hits(text):
        reasons.append(f"{FORBIDDEN_PHRASE}:{hit.id}")

    scan = _ascii_digits(unicodedata.normalize("NFKC", residue))
    if _LONG_DIGITS.search(scan) or _LONG_DIGITS.search(_words_to_digits(scan)):
        reasons.append(PII_DIGITS)
    if _EMAIL.search(scan):
        reasons.append(PII_EMAIL)
    if _ID_WORDS.search(normalise_arabic(scan)) or _MIXED_TOKEN.search(scan):
        reasons.append(PII_IDENTIFIER)
    if _URL.search(scan):
        reasons.append(URL)

    return ValidationResult(not reasons, tuple(reasons))


# ---------------------------------------------------------------- abstention log


@dataclass(frozen=True)
class AbstentionRecord:
    seq: int
    purpose: str
    channel: str
    language: str
    provider: str  # the provider whose output was rejected or which failed
    reason: str  # e.g. "validation_failed:forbidden_phrase:cancer,url" or "provider_unavailable"
    prompt_hash: str

    @property
    def category(self) -> str:
        return self.reason.split(":", 1)[0]


@dataclass
class AbstentionLog:
    """In-memory record of every template fallback (D11 wires it to the metrics).

    Automation abstention rate = abstentions / draft attempts: the share of draft requests
    for which the model route was not used because it failed or was rejected.
    """

    _records: list[AbstentionRecord] = field(default_factory=list)
    _attempts: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note_attempt(self) -> None:
        with self._lock:
            self._attempts += 1

    def record(self, req: DraftRequest, provider: str, reason: str, prompt_hash: str) -> AbstentionRecord:
        with self._lock:
            rec = AbstentionRecord(len(self._records) + 1, req.purpose, req.channel, req.language,
                                   provider, reason, prompt_hash)
            self._records.append(rec)
            return rec

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def count(self) -> int:
        return len(self._records)

    def records(self) -> tuple[AbstentionRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def rate(self) -> float:
        return self.count / self._attempts if self._attempts else 0.0

    def by_category(self) -> Counter:
        return Counter(r.category for r in self.records())

    def by_rule(self) -> Counter:
        """Counts per validation rule (a record can fail several rules)."""
        c: Counter = Counter()
        for r in self.records():
            if r.category == VALIDATION_FAILED:
                c.update(r.reason.split(":", 1)[1].split(",") if ":" in r.reason else [])
        return c

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._attempts = 0


DEFAULT_ABSTENTION_LOG = AbstentionLog()


def draft_with_fallback(
    req: DraftRequest, provider: Provider, log: AbstentionLog | None = None
) -> DraftResult:
    """Draft with `provider`; on any failure return the template and record the abstention.

    Never raises for a provider problem. The template provider is the vetted fallback, so it
    is returned directly (its whole matrix is tested) and never counts as an abstention.
    """
    log = DEFAULT_ABSTENTION_LOG if log is None else log
    log.note_attempt()
    if isinstance(provider, TemplateProvider):
        return provider.draft(req)

    reason: str | None = None
    try:
        result = provider.draft(req)
    except CacheMiss:  # before ProviderUnavailable: CacheMiss is a subclass
        reason = CACHE_MISS
    except ProviderUnavailable:
        reason = PROVIDER_UNAVAILABLE
    except Exception as exc:  # noqa: BLE001 - any provider failure must fall back
        reason = f"{PROVIDER_ERROR}:{type(exc).__name__}"
    else:
        if not isinstance(result, DraftResult) or result.language != req.language:
            reason = INVALID_RESULT
        else:
            verdict = validate(result.text, req)
            if not verdict.ok:
                reason = f"{VALIDATION_FAILED}:{','.join(verdict.reasons)}"
    if reason is None:
        return result

    fallback = TemplateProvider().draft(req)
    log.record(req, getattr(provider, "name", type(provider).__name__), reason, fallback.prompt_hash)
    return dataclasses.replace(fallback, fallback_used=True, abstention_reason=reason)
