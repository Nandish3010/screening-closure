"""Deterministic message templates (D2).

`templates/{ar,en}.yaml` hold one text per purpose x channel (plus optional `<channel>_with_date`
variants). The template text is both the default draft and the fallback when a model output
is rejected, so it is checked hard at load time: every combination present, only known
placeholders, every channel limit holds even with worst-case field lengths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import yaml

from app.ai.provider import CHANNEL_MAX_CHARS, CHANNELS, LANGUAGES, MAX_FIELD_CHARS, PURPOSES

TEMPLATE_DIR = Path(__file__).parent / "templates"
PLACEHOLDERS = ("owner_name", "next_step", "appointment_date")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_WITH_DATE = "_with_date"

DEFAULT_NEXT_STEP = {"en": "a colonoscopy", "ar": "تنظير القولون"}

SAMPLE_VALUES = {
    "en": {
        "owner_name": "Dr Sara Khan",
        "next_step": "a colonoscopy",
        "appointment_date": "Tuesday 3 November 2026",
    },
    "ar": {
        "owner_name": "الدكتورة سارة خان",
        "next_step": "تنظير القولون",
        "appointment_date": "الثلاثاء 3 نوفمبر 2026",
    },
}


class TemplateError(ValueError):
    """A template file is incomplete or malformed. Raised at load time, never at render time."""


@dataclass(frozen=True)
class TemplateEntry:
    language: str
    purpose: str
    channel: str
    with_date: bool
    text: str
    description: str

    @property
    def key(self) -> str:
        return self.channel + (_WITH_DATE if self.with_date else "")

    @property
    def placeholders(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_PLACEHOLDER_RE.findall(self.text)))


@dataclass(frozen=True)
class TemplateSet:
    version: int
    entries: tuple[TemplateEntry, ...]

    def get(self, language: str, purpose: str, channel: str, with_date: bool = False) -> TemplateEntry:
        for e in self.entries:
            if (e.language, e.purpose, e.channel, e.with_date) == (language, purpose, channel, with_date):
                return e
        raise KeyError((language, purpose, channel, with_date))

    def has(self, language: str, purpose: str, channel: str, with_date: bool) -> bool:
        try:
            self.get(language, purpose, channel, with_date)
            return True
        except KeyError:
            return False


def _fill(text: str, values: dict[str, str | None]) -> str:
    def repl(m: re.Match) -> str:
        v = values.get(m.group(1))
        if v is None:
            raise ValueError(f"no value for placeholder {{{m.group(1)}}}")
        return v

    return _PLACEHOLDER_RE.sub(repl, text)


def _load_file(language: str) -> tuple[int, list[TemplateEntry]]:
    path = TEMPLATE_DIR / f"{language}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("language") != language:
        raise TemplateError(f"{path.name}: language field must be {language!r}")
    purposes = data.get("purposes") or {}
    unknown = set(purposes) - set(PURPOSES)
    if unknown:
        raise TemplateError(f"{path.name}: unknown purposes {sorted(unknown)}")
    entries: list[TemplateEntry] = []
    for purpose in PURPOSES:
        block = purposes.get(purpose)
        if not block:
            raise TemplateError(f"{path.name}: missing purpose {purpose}")
        allowed = {"description", *CHANNELS, *(c + _WITH_DATE for c in CHANNELS)}
        extra = set(block) - allowed
        if extra:
            raise TemplateError(f"{path.name}:{purpose}: unknown keys {sorted(extra)}")
        for channel in CHANNELS:
            if channel not in block:
                raise TemplateError(f"{path.name}:{purpose}: missing channel {channel}")
            for with_date in (False, True):
                key = channel + (_WITH_DATE if with_date else "")
                if key not in block:
                    continue
                entries.append(
                    TemplateEntry(language, purpose, channel, with_date, str(block[key]).strip(),
                                  str(block.get("description", "")))
                )
    return int(data.get("version", 1)), entries


def _check_entry(e: TemplateEntry) -> None:
    where = f"{e.language}/{e.purpose}/{e.key}"
    found = set(e.placeholders)
    if not found <= set(PLACEHOLDERS):
        raise TemplateError(f"{where}: unknown placeholders {sorted(found - set(PLACEHOLDERS))}")
    for required in ("owner_name", "next_step"):
        if required not in found:
            raise TemplateError(f"{where}: missing {{{required}}}")
    needs_date = e.with_date or e.purpose == "appointment_confirmation"
    if needs_date != ("appointment_date" in found):
        raise TemplateError(f"{where}: {{appointment_date}} must appear exactly in dated templates")
    worst = {p: "x" * MAX_FIELD_CHARS for p in PLACEHOLDERS}
    if len(_fill(e.text, worst)) > CHANNEL_MAX_CHARS[e.channel]:
        raise TemplateError(f"{where}: over {CHANNEL_MAX_CHARS[e.channel]} chars with {MAX_FIELD_CHARS}-char fields")


@lru_cache(maxsize=1)
def load_templates() -> TemplateSet:
    entries: list[TemplateEntry] = []
    versions = set()
    for language in LANGUAGES:
        version, file_entries = _load_file(language)
        versions.add(version)
        entries.extend(file_entries)
    if len(versions) != 1:
        raise TemplateError(f"template files disagree on version: {sorted(versions)}")
    for e in entries:
        _check_entry(e)
    return TemplateSet(version=versions.pop(), entries=tuple(entries))


def render(
    purpose: str,
    channel: str,
    language: str,
    *,
    owner_name: str,
    next_step: str,
    appointment_date: str | None = None,
) -> str:
    """Fill the template. Uses the `_with_date` variant when a date is given and one exists."""
    ts = load_templates()
    with_date = bool(appointment_date) and ts.has(language, purpose, channel, True)
    entry = ts.get(language, purpose, channel, with_date)
    values = {"owner_name": owner_name, "next_step": next_step, "appointment_date": appointment_date}
    return _fill(entry.text, values)


def iter_templates() -> Iterator[TemplateEntry]:
    yield from load_templates().entries


def reviewer_pack_markdown() -> str:
    """Every template string with its context, as Markdown for the clinician and Arabic reviewers.

    Used by E10. Shows the raw text with placeholders, a rendering with sample values, and the
    length against the channel limit. Arabic is flagged as pending native-speaker review (H2).
    """
    ts = load_templates()
    out = [
        "# Message template reviewer pack",
        "",
        f"Template set version {ts.version}. Generated from `api/app/ai/templates/*.yaml`.",
        "",
        "What to check: plain words at roughly grade-6 reading level; no diagnosis; no advice beyond "
        '"the next step is ...; your care team will contact you"; the only clinical fact is that the '
        "screening test found blood; the tone is calm and respectful; Arabic reads naturally.",
        "",
        "Placeholders: `{owner_name}` is the care owner the patient will hear from (not the patient). "
        "`{next_step}` is the next pathway step in the message language. `{appointment_date}` is a "
        "human-readable date.",
    ]
    for language, label in (("en", "English"), ("ar", "Arabic")):
        out += ["", f"## {label}", ""]
        if language == "ar":
            out += [
                "> Status: Arabic draft, unreviewed. Pending native-speaker review (H2). "
                "Right-to-left text; check mixed-direction items such as names and dates.",
                "",
            ]
        else:
            out += ["> Status: not clinically reviewed (H1).", ""]
        samples = SAMPLE_VALUES[language]
        for purpose in PURPOSES:
            first = next(e for e in ts.entries if e.language == language and e.purpose == purpose)
            out += [f"### {purpose}", "", first.description, ""]
            for e in (x for x in ts.entries if x.language == language and x.purpose == purpose):
                rendered = _fill(e.text, samples)
                limit = CHANNEL_MAX_CHARS[e.channel]
                out += [
                    f"**{e.key}** ({len(rendered)} of {limit} characters with sample values)",
                    "",
                    "Template:",
                    "",
                    *[f"    {line}" for line in e.text.splitlines()],
                    "",
                    "Rendered with sample values:",
                    "",
                    *[f"    {line}" for line in rendered.splitlines()],
                    "",
                ]
    from app.ai.validate import load_forbidden_phrases  # lazy: validate imports this package

    out += [
        "",
        "## Forbidden phrases",
        "",
        "A model draft containing any of these is rejected and replaced by the template above. "
        "The Arabic list is pending native-speaker review (H2); both lists pending clinician review (H1). "
        "Please add anything missing.",
    ]
    for language, label in (("en", "English"), ("ar", "Arabic")):
        out += ["", f"### {label}", "", "| id | category | phrase or pattern | why |", "|---|---|---|---|"]
        for fp in (f for f in load_forbidden_phrases() if f.language == language):
            why = " ".join(fp.rationale.split()).replace("|", "/")
            src = fp.source.replace("|", "/")
            out.append(f"| `{fp.id}` | {fp.category} | {src} | {why} |")
    return "\n".join(out).rstrip() + "\n"
