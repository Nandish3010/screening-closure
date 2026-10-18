#!/usr/bin/env python3
"""Build the two reviewer packs (E10, T-E17) from the sources, so they cannot drift.

    python tools/review/build_packs.py            write docs/review/clinician-pack.md and arabic-pack.md
    python tools/review/build_packs.py --check    exit 1 if the committed packs differ from a fresh build
    python tools/review/build_packs.py --counts   print the string counts per pack

Sources (nothing here is copied by hand except the plain-language glosses and the review
questions, which live in this file and are checked against the sources: a state, event, SLA
step, handoff reason or web string without a gloss stops the build with a message):

    config/pathways/colorectal_fit.yaml      states, transitions, SLAs, ladder, handoff, human-only events
    config/calendar.yaml                      working week and holidays
    web/src/i18n/strings.ts                   patient page catalogue (English and Arabic) and role labels
    web/src/api/{fixtures,mockAdapter}.ts     demo draft texts shown in the coordinator app (text_en/text_ar)
    api/app/ai/templates/{en,ar}.yaml         message templates (through app.ai.templates)
    api/app/ai/forbidden_phrases.yaml         forbidden phrase lists (through app.ai.validate)
    api/app/state_machine.py                  default consent

The output is deterministic: no dates, no hashes, no environment. Run `make review-packs`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from app.ai.provider import CHANNEL_MAX_CHARS, CHANNELS, PURPOSES  # noqa: E402
from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES, iter_templates, load_templates  # noqa: E402
from app.ai.validate import forbidden_hits  # noqa: E402
from app.state_machine import DEFAULT_CONSENT  # noqa: E402

PATHWAY_PATH = ROOT / "config" / "pathways" / "colorectal_fit.yaml"
CALENDAR_PATH = ROOT / "config" / "calendar.yaml"
STRINGS_TS = ROOT / "web" / "src" / "i18n" / "strings.ts"
MOCK_DRAFT_FILES = (ROOT / "web" / "src" / "api" / "fixtures.ts", ROOT / "web" / "src" / "api" / "mockAdapter.ts")
FORBIDDEN_PATH = ROOT / "api" / "app" / "ai" / "forbidden_phrases.yaml"
OUT_DIR = ROOT / "docs" / "review"
CLINICIAN_PACK = OUT_DIR / "clinician-pack.md"
ARABIC_PACK = OUT_DIR / "arabic-pack.md"

REGENERATE = "Do not edit by hand. Regenerate with `make review-packs` (tools/review/build_packs.py)."


# ====================================================================== markdown helpers


def cell(text: object) -> str:
    """One Markdown table cell: pipes escaped, line breaks as <br>."""
    s = str(text).strip("\n")
    s = s.replace("|", "\\|").replace("\r", "")
    return s.replace("\n", "<br>")


def table(headers: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(cell(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(cell(c) for c in r) + " |")
    return "\n".join(out)


def code(text: object) -> str:
    return "`" + str(text).replace("`", "'") + "`"


# ====================================================================== TypeScript literal reader


class TsParseError(ValueError):
    pass


@dataclass(frozen=True)
class TsFn:
    """An arrow function returning a template literal; `text` has `{param}` where `${...}` was."""

    text: str


class TsLiteral:
    """Reads the small subset of TypeScript used by the catalogue: objects, arrays, strings,
    template literals and `(a, b) => \\`...\\`` arrow functions. Anything else is an error."""

    ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "`": "`", "$": "$"}

    def __init__(self, text: str, pos: int = 0):
        self.s = text
        self.i = pos

    def ws(self) -> None:
        s = self.s
        while self.i < len(s):
            if s[self.i].isspace():
                self.i += 1
            elif s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = len(s) if j < 0 else j + 1
            elif s.startswith("/*", self.i):
                j = s.find("*/", self.i)
                if j < 0:
                    raise TsParseError("unterminated comment")
                self.i = j + 2
            else:
                break

    def peek(self) -> str:
        self.ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise TsParseError(f"expected {ch!r} at offset {self.i}, found {self.s[self.i:self.i + 20]!r}")
        self.i += 1

    def value(self):
        c = self.peek()
        if c in "\"'":
            return self.string(c)
        if c == "`":
            return self.template()
        if c == "[":
            return self.array()
        if c == "{":
            return self.object()
        if c == "(":
            return self.arrow()
        raise TsParseError(f"unexpected {self.s[self.i:self.i + 20]!r} at offset {self.i}")

    def escape(self) -> str:
        self.i += 1  # backslash
        c = self.s[self.i]
        if c == "u":
            if self.s[self.i + 1] == "{":
                j = self.s.index("}", self.i)
                ch = chr(int(self.s[self.i + 2:j], 16))
                self.i = j + 1
                return ch
            ch = chr(int(self.s[self.i + 1:self.i + 5], 16))
            self.i += 5
            return ch
        self.i += 1
        if c == "\n":  # line continuation
            return ""
        return self.ESC.get(c, c)

    def string(self, quote: str) -> str:
        self.i += 1
        out: list[str] = []
        while self.s[self.i] != quote:
            if self.s[self.i] == "\\":
                out.append(self.escape())
            else:
                out.append(self.s[self.i])
                self.i += 1
        self.i += 1
        return "".join(out)

    def template(self) -> str:
        self.i += 1
        out: list[str] = []
        while self.s[self.i] != "`":
            c = self.s[self.i]
            if c == "\\":
                out.append(self.escape())
            elif c == "$" and self.s[self.i + 1] == "{":
                depth, j = 1, self.i + 2
                while depth:
                    depth += {"{": 1, "}": -1}.get(self.s[j], 0)
                    j += 1
                expr = self.s[self.i + 2:j - 1].strip()
                m = re.fullmatch(r"\w+\((\w+)\)", expr)  # iso(name) -> {name}
                out.append("{" + (m.group(1) if m else expr) + "}")
                self.i = j
            else:
                out.append(c)
                self.i += 1
        self.i += 1
        return "".join(out)

    def arrow(self) -> TsFn:
        self.expect("(")
        while self.peek() != ")":
            self.i += 1
        self.i += 1
        self.ws()
        if not self.s.startswith("=>", self.i):
            raise TsParseError(f"expected '=>' at offset {self.i}")
        self.i += 2
        if self.peek() != "`":
            raise TsParseError("arrow functions must return a template literal")
        return TsFn(self.template())

    def array(self) -> list:
        self.expect("[")
        out = []
        while self.peek() != "]":
            out.append(self.value())
            if self.peek() == ",":
                self.i += 1
        self.i += 1
        return out

    def key(self) -> str:
        c = self.peek()
        if c in "\"'":
            return self.string(c)
        m = re.compile(r"[A-Za-z_$][\w$]*").match(self.s, self.i)
        if not m:
            raise TsParseError(f"bad key at offset {self.i}")
        self.i = m.end()
        return m.group(0)

    def object(self) -> dict:
        self.expect("{")
        out: dict = {}
        while self.peek() != "}":
            k = self.key()
            self.expect(":")
            out[k] = self.value()
            if self.peek() == ",":
                self.i += 1
        self.i += 1
        return out


def ts_const(source: str, name: str):
    m = re.search(rf"(?:export\s+)?const\s+{name}\b[^=]*=\s*", source)
    if not m:
        raise TsParseError(f"{name} not found")
    return TsLiteral(source, m.end()).value()


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, path))
        elif isinstance(v, list):
            for n, item in enumerate(v):
                out[f"{path}[{n}]"] = item.text if isinstance(item, TsFn) else item
        else:
            out[path] = v.text if isinstance(v, TsFn) else v
    return out


# ====================================================================== loaders


@dataclass(frozen=True)
class Item:
    """One patient-facing string in one language pair."""

    id: str  # W01, T01, D01: stable within a build, shared by both packs
    source: str  # web | template | draft
    key: str  # catalogue key, "purpose/variant", or fixture name
    en: str
    ar: str
    group: str
    where: str


def load_pathway() -> dict:
    return yaml.safe_load(PATHWAY_PATH.read_text(encoding="utf-8"))


def expand_transitions(pw: dict) -> list[dict]:
    """One row per (from, event, to); `any_open` kept as written."""
    rows = []
    for t in pw["transitions"]:
        froms = t["from"] if isinstance(t["from"], list) else [t["from"]]
        for f in froms:
            rows.append({
                "from": f, "event": t["event"], "to": t["to"], "requires": t.get("requires", []),
                "when": t.get("when"), "min": t.get("min"),
            })
    return rows


def load_web_catalogue() -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict]:
    """(en, ar, role_en, role_ar, dir) flattened from strings.ts."""
    src = STRINGS_TS.read_text(encoding="utf-8")
    strings = ts_const(src, "STRINGS")
    en, ar = flatten(strings["en"]), flatten(strings["ar"])
    if list(en) != list(ar):
        raise SystemExit(f"strings.ts: English and Arabic keys differ: {sorted(set(en) ^ set(ar))}")
    roles = ts_const(src, "ROLE_AR")
    direction = ts_const(src, "DIR")
    return en, ar, {r: r for r in roles}, dict(roles), direction


def load_mock_drafts() -> list[tuple[str, str, str]]:
    """(file, text_en, text_ar) for each demo draft text in the web mock.

    The mock adapter keeps one text per language and purpose in `DRAFT_TEXT = { en: {purpose: ...},
    ar: {purpose: ...} }`; the pairs are matched by purpose. (Before the C-fix the texts were
    `text_en`/`text_ar` pairs in the fixtures; those two English-only seed drafts are no longer read.)
    """
    out = []
    for path in MOCK_DRAFT_FILES:
        src = path.read_text(encoding="utf-8")
        m = re.search(r"const DRAFT_TEXT\b[^=]*=\s*\{(.*?)\n\};", src, re.S)
        if not m:
            continue
        block = m.group(1)
        langs = {}
        for lm in re.finditer(r"\b(en|ar):\s*\{(.*?)\n  \},?", block, re.S):
            langs[lm.group(1)] = dict(
                (k, json.loads(f'"{v}"'))
                for k, v in re.findall(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', lm.group(2))
            )
        for purpose, en_text in langs.get("en", {}).items():
            out.append((str(path.relative_to(ROOT)), en_text, langs["ar"][purpose]))
    return out


def load_forbidden() -> dict:
    return yaml.safe_load(FORBIDDEN_PATH.read_text(encoding="utf-8"))


def forbidden_header() -> str:
    """The leading comment block of forbidden_phrases.yaml (how matching works)."""
    lines = []
    for line in FORBIDDEN_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        lines.append(line[1:].strip())
    return "\n".join(lines).strip()


def load_calendar() -> dict:
    return yaml.safe_load(CALENDAR_PATH.read_text(encoding="utf-8"))


# ====================================================================== plain-language glosses
# These are the only hand-written descriptions. Every state, event, SLA step, rung and handoff
# reason in the YAML must have one: the build stops with a message otherwise.

STATE_GLOSS = {
    "result_received": "A positive FIT (stool blood test) result has arrived from the laboratory. Nobody has taken responsibility yet.",
    "acknowledged": "The laboratory, coordinator or supervisor has confirmed the result is logged and is being routed. Still no named owner.",
    "owner_accepted": "A named clinician or service has accepted responsibility (the care owner). From here one named person owns the next step.",
    "patient_contacted": "The care owner or coordinator has contacted the patient (call, message or letter) with the result and the next step.",
    "patient_acknowledged": "The patient has confirmed they understood (tapped \"I understand\" or told the coordinator). The colonoscopy can now be booked.",
    "confirmatory_booked": "A confirmatory colonoscopy appointment is booked. If the booking went through a referral to another provider, that provider has accepted it.",
    "confirmatory_completed": "The specialist has recorded that the colonoscopy was done. Findings are not yet reviewed.",
    "clinician_reviewed": "A clinician has reviewed the colonoscopy outcome. The case can now be closed, but only with evidence and a signature.",
    "closed": "The loop is closed. Only possible with an evidence reference and a named clinician's sign-off. No automated actor can do this.",
    "referral_rejected": "The receiving provider turned the referral down, or did not answer in time. The reason is recorded and the case must be re-routed.",
    "consent_withdrawn": "The patient withdrew consent. Outreach stops, the case is flagged for a person to review, and the earlier state is remembered so it can be restored.",
    "lost_contact": "After repeated failed attempts the patient could not be reached. The case stays open and a person decides what to do next. It never closes by itself.",
    "reopened": "A clinician reopened a closed case, giving a reason. It returns for clinician review and can close again only with fresh evidence and a sign-off.",
}

# event -> (plain meaning, who may do it). "Who" follows api/app/policy.py (EVENT_POLICY) and
# docs/governance/decision-matrix.md, which is the authority.
EVENT_GLOSS = {
    "acknowledge": ("Confirm the positive result is received and logged.", "Laboratory, coordinator, supervisor; the automation does it when the result arrives"),
    "assign_ownership": ("The automation names the case's owner from the patient's registered provider. The owner still has to accept; nothing waits on the naming.", "The automation only"),
    "accept_ownership": ("A clinician or service accepts responsibility for the case.", "Primary care owner, specialist owner, supervisor"),
    "contact_patient": ("Contact the patient with the result and next step.", "Care owner, coordinator, supervisor"),
    "patient_acknowledge": ("The patient confirms they understood.", "Patient, coordinator"),
    "book_confirmatory": ("Book the confirmatory colonoscopy.", "Care owner, coordinator"),
    "complete_confirmatory": ("Record that the colonoscopy was done.", "Specialist owner; the automation when it finds the colonoscopy report"),
    "clinician_review": ("Review the colonoscopy outcome.", "Primary care owner, specialist owner"),
    "close": ("Close the case. Needs an evidence reference and a signature.", "Primary care owner, specialist owner (never automated)"),
    "reopen": ("Reopen a closed case, with a reason.", "Primary care owner, specialist owner (human only)"),
    "reassign": ("Give a rejected case a new owner.", "Coordinator, supervisor"),
    "handoff_requested": ("Ask another provider to take over the case (an annotation; ownership has not moved).", "Care owner, supervisor; the automation, to the next provider on the list after a refusal or no answer"),
    "handoff_accepted": ("The receiving provider accepts. Ownership moves only now.", "Receiving clinician (human only)"),
    "handoff_rejected": ("The receiving provider refuses, with a reason from the fixed list. The scheduler may also bounce a request that got no answer.", "Receiving clinician; scheduler (timeout only)"),
    "consent_updated": ("Record the patient's choice of channels, sharing, or withdrawal.", "Patient, coordinator recording the patient's instruction (human only)"),
    "mark_lost_contact": ("Record that the patient could not be reached after repeated attempts.", "Care owner, coordinator, supervisor"),
    "mark_overdue": ("Flag that the time limit for the current step has passed.", "Scheduler only"),
    "escalate": ("Send the late case up one rung of the ladder.", "Scheduler, coordinator, supervisor"),
    "escalation_accepted": ("A person accepts an escalation, which stops further rungs for that delay.", "A human in the rung's target role (human only)"),
    "review_requested": ("Flag the case for human review (consent withdrawn, or every provider on the reroute list has refused).", "The system itself; nobody can post it"),
    "message_drafted": ("A message draft is prepared (by a template or a language model) for a person to approve.", "Care owner, coordinator, model drafting provider"),
    "message_approved": ("A person approves a draft. Nothing is sent without this.", "Care owner (human only)"),
    "message_rejected": ("A person rejects a draft, giving a reason from a fixed list. Nothing is sent.", "Care owner (human only)"),
    "message_sent": ("The message is sent through the notification mock: an approved draft, or a pre-approved template the automation sends within the weekly limit.", "The approval-gated send path, or the automation for a listed template; nobody can post it"),
    "closure_proposed": ("The automation found the colonoscopy report and proposes closing the case. The clinician still reviews and signs; this closes nothing.", "The automation only"),
    "callback_requested": ("The patient asks for a person to call because they do not understand.", "Patient, coordinator (human only)"),
    "watchdog_flagged": ("The watchdog flags a case that has been idle too long.", "Watchdog / scheduler"),
}

SLA_STEP_GLOSS = {
    "acknowledge": "Lab or coordinator confirms the result is received and logged.",
    "accept_ownership": "A clinician or service accepts the case.",
    "contact_patient": "The patient is contacted with the result.",
    "patient_acknowledge": "The patient confirms they understood.",
    "book_confirmatory": "The colonoscopy is booked.",
    "clinician_review": "A clinician reviews the outcome.",
    "close": "A clinician signs off the closure with evidence (a report or procedure on file).",
    "reassign": "A rejected or bounced referral is given a new owner.",
}

# The national timelines (A18): `milestones` in the pathway YAML. Clocks that span states, from one event to another.
MILESTONE_GLOSS = {
    "referral_requested": "The referral for the colonoscopy is requested.",
    "confirmatory_completed": "The colonoscopy is done.",
}
# How a milestone's start event reads in plain words.
MILESTONE_START = {
    "case_created": "the case is created, which is when the positive result is logged (taken as the result release)",
    "handoff_accepted": ("the latest referral acceptance (`handoff_accepted`) on the case, or, if no referral was "
                         "accepted, when the case enters `confirmatory_booked`"),
}

REASON_GLOSS = {
    "capacity": "The receiving provider has no capacity.",
    "wrong_service": "The receiving provider does not offer the service needed.",
    "missing_info": "The referral lacks information the receiving provider needs.",
    "patient_declined": "The patient declined this referral.",
    "timeout": "No answer within the response time. Written only by the scheduler (a bounce), never by a person.",
}

RUNG_GLOSS = {
    "reminder": "A reminder to the case owner.",
    "supervisor": "The owner's supervisor is told and asked to act.",
    "coordinator": "The programme coordinator is told and asked to act.",
}

FORBIDDEN_CATEGORY_GLOSS = {
    "diagnosis_statement": "Saying or implying the patient has a condition.",
    "disease_term": "Naming a disease or a finding the screening test cannot show.",
    "positive_result_jargon": "Calling the result \"positive\".",
    "result_disclosure": "Stating or hinting at what the screening found (\"found blood\", \"positive result\", \"abnormal\"). IFHAS: abnormal results are communicated with the participant present.",
    "false_reassurance": "Telling the patient not to worry or that all is well.",
    "clinical_judgement": "Judging urgency or severity.",
    "medication_or_treatment": "Mentioning medicines, doses, treatment or surgery.",
    "advice": "Telling the patient what they should do beyond the next step.",
}

# Arabic forbidden-phrase glosses (English). Optional: a missing id shows "-".
AR_FORBIDDEN_GLOSS = {
    "you_are_affected": "you are affected (by a disease)", "affected": "affected (by a disease)",
    "diagnosis": "diagnosis", "cancer": "cancer", "tumour": "tumour", "tumours_plural": "tumours",
    "malignant": "malignant", "malignancy": "malignancy", "polyp": "polyp", "polyps_plural": "polyps",
    "carcinoma": "carcinoma", "positive": "positive",
    "found_blood": "found blood", "found_blood_we": "we found blood", "blood_presence": "presence of blood",
    "blood_in_stool": "blood in the stool", "occult_blood": "hidden (occult) blood", "occult_blood_def": "the hidden (occult) blood",
    "positive_result": "positive (medical sense)", "abnormal_result": "not normal", "abnormal_alt": "abnormal / atypical",
    "result_shows_test": "the test showed", "result_shows_result": "the result showed", "result_shows_your": "your result showed",
    "found_by_test": "the test found", "found_by_test_def": "the test found (with the article)", "do_not_worry": "do not worry (to a man)",
    "do_not_worry_f": "do not worry (to a woman)", "no_need_to_worry": "no need to worry",
    "do_not_fear": "do not be afraid", "do_not_fear_alt": "do not be afraid (variant)",
    "nothing_to_worry_about": "nothing to worry about", "all_fine": "everything is fine",
    "you_will_be_fine": "you will be fine", "not_dangerous": "not dangerous", "normal": "normal",
    "dangerous": "dangerous", "urgent": "urgent", "emergency": "emergency", "medicine": "medicine",
    "medicines_plural": "medicines", "dose": "dose", "pills": "pills", "laxative": "laxative",
    "treatment": "treatment", "surgery": "surgery", "chemotherapy": "chemotherapy (colloquial)",
    "you_must": "you must", "we_advise": "we advise you", "you_should": "it is fitting that / you should",
    "you_need_to": "you need to", "take": "take / consume", "fasting": "fasting",
}


def need(table_: dict, key: str, kind: str):
    if key not in table_:
        raise SystemExit(
            f"build_packs: no plain-language entry for {kind} {key!r}. "
            f"Add it to the matching *_GLOSS table in tools/review/build_packs.py."
        )
    return table_[key]


# ====================================================================== web string context

WEB_GROUPS = [
    ("frame", "Page frame and language", "Title, language switch and start page."),
    ("result", "What the result means", "The first thing the patient reads."),
    ("progress", "Where you are now", "Four-step tracker."),
    ("next", "What happens next", "Three numbered steps."),
    ("owner", "Who is looking after you", "The named care owner."),
    ("actions", "I understand / I don't understand", "The two buttons and their confirmations."),
    ("consent", "Your choices (contact and sharing)", "Channel, sharing and their save status."),
    ("status", "Loading, offline and errors", "System messages."),
]

# key -> (group, where it appears)
WEB_CONTEXT: dict[str, tuple[str, str]] = {
    "appTitle": ("frame", "Main heading (h1) at the top of the patient page."),
    "language": ("frame", "Not shown. Used in the accessible label of the language button, as \"Language: <other language>\"."),
    "switchTo": ("frame", "Language button, top of the page. It names the other language in that language: the English column is the button on the English page, the Arabic column the button on the Arabic page."),
    "homeTitle": ("frame", "Heading of the app start page (/patient) when no case has been opened on this device."),
    "homeBody": ("frame", "Text under that heading: how to open the result and add the page to the home screen."),
    "resultHeading": ("result", "Heading of the first section."),
    "resultBody": ("result", "Large paragraph under the heading. It says the result is ready and that the care team needs to talk with the patient. It states no finding."),
    "explainInPerson": ("result", "Line under the paragraph: the care team explains the result in person or by phone (IFHAS: abnormal results are given with the participant present)."),
    "progressHeading": ("progress", "Heading of the progress section."),
    "progress[0]": ("progress", "Step 1 of the tracker. Current while the case is waiting to be acknowledged."),
    "progress[1]": ("progress", "Step 2. Current from acknowledgement until the colonoscopy is booked."),
    "progress[2]": ("progress", "Step 3. Current once the colonoscopy has been done."),
    "progress[3]": ("progress", "Step 4. Current at clinician review and after."),
    "nextHeading": ("next", "Heading of the next-steps section."),
    "nextSteps[0]": ("next", "Numbered step 1."),
    "nextSteps[1]": ("next", "Numbered step 2."),
    "nextSteps[2]": ("next", "Numbered step 3."),
    "ownerHeading": ("owner", "Heading of the care-owner section."),
    "ownerLine": ("owner", "Large text under that heading. {name} is the owner's name (Latin or Arabic script), {role} the role from the role labels below. In Arabic the sentence is split over three lines so a Latin name does not scramble the line."),
    "ownerPending": ("owner", "Shown instead of the line above while nobody has accepted the case."),
    "ownerByRole.primary_care_owner": ("owner", "Shown instead of the owner line when the owner is the patient's primary care physician. The server shows a patient the owner by role only (SEC-2), so no name is available."),
    "ownerByRole.specialist_owner": ("owner", "Same, when the owner is the specialist who will do the test."),
    "ownerByRole.coordinator": ("owner", "Same, when the owner is a care coordinator."),
    "ownerByRole.supervisor": ("owner", "Same, when the case is with a senior member of the care team."),
    "ownerByRole.other": ("owner", "Same, for any role not listed above."),
    "understand": ("actions", "Primary button (one tap: the patient has read the message and asks the care team to contact them). Disabled until the patient has been contacted."),
    "understandWait": ("actions", "Hint under the disabled button."),
    "understandDone": ("actions", "Green confirmation that replaces the button once the patient has acknowledged."),
    "dontUnderstand": ("actions", "Second button. Asks a person from the care team to call."),
    "callbackDone": ("actions", "Green confirmation after that button is pressed, or while a callback is pending."),
    "actionFailed": ("actions", "Red banner if either button fails."),
    "consentHeading": ("consent", "Heading of the choices section."),
    "channelLabel": ("consent", "Legend of the group of contact-channel check boxes. Each box is on unless the patient turned it off (an opt-out default); changing one saves at once."),
    "channels.phone": ("consent", "Label of the check box for phone calls."),
    "channels.sms": ("consent", "Label of the check box for text messages."),
    "channels.whatsapp": ("consent", "Label of the check box for WhatsApp messages."),
    "channels.letter": ("consent", "Label of the check box for letters."),
    "shareLabel": ("consent", "Label of the check box that lets the care team share the result with the specialist."),
    "withdrawButton": ("consent", "Button under the choices. Withdraws all consent to contact at once: the case is frozen for outreach and a person is asked to review it. Hidden after withdrawal."),
    "withdrawHint": ("consent", "Small grey text above that button. It says what stopping contact means and that a person will check the case."),
    "withdrawnNotice": ("consent", "Yellow banner at the top of the choices section after the patient has stopped all contact."),
    "resumeButton": ("consent", "Replaces the stop button after withdrawal. Pressing it records that the patient may be contacted again."),
    "saved": ("consent", "Status line under the choices after a successful save."),
    "saveFailed": ("consent", "Status line if saving the choices fails."),
    "loading": ("status", "Shown instead of the page while the case loads."),
    "loadError": ("status", "Shown if the case cannot be loaded and nothing is saved on the device."),
    "offline": ("status", "Banner above the result when the device is offline. The last saved information is shown."),
}

ROLE_WHERE = "Role shown in brackets after the owner's name in the line \"Who is looking after you\". The English role is the value stored in the case; unknown roles are shown as stored."

MOCK_WHERE = (
    "Draft text offered to the coordinator in the demo (mock) app, shown in both languages and stored in the "
    "`message_drafted` event. It stands in for a model or template draft. A person approves it before anything is \"sent\"."
)

# purpose -> (who receives it and when). The sending flow (D5) is not built yet, so this is the intended use.
PURPOSE_CONTEXT = {
    "result_notification": "First message to the patient once a result has an accepted care owner. States no finding: the care team will explain the result in person or by phone.",
    "reminder": "Reminder when the patient has not yet taken the next step. The dated variant is used once an appointment date exists.",
    "appointment_confirmation": "Confirms a booked appointment (a camera test). Always carries the date. Names the appointment, never a disease or a finding.",
    "escalation_notice": "Tells the patient a more senior member of the care team has been told, so the next step is not delayed.",
    "callback_ack": "Reply when the patient pressed \"I don't understand this\". A person will call.",
}

CHANNEL_LABEL = {"sms": "SMS", "whatsapp": "WhatsApp", "letter": "Letter"}
READING_LEVEL = "About grade 6: short sentences, everyday words."


# ====================================================================== the item inventory


def build_items() -> list[Item]:
    en, ar, role_en, role_ar, _ = load_web_catalogue()
    groups = {g: title for g, title, _ in WEB_GROUPS}
    missing = [k for k in en if k not in WEB_CONTEXT]
    if missing:
        raise SystemExit(f"build_packs: web catalogue keys without context in WEB_CONTEXT: {missing}")
    stale = [k for k in WEB_CONTEXT if k not in en]
    if stale:
        raise SystemExit(f"build_packs: WEB_CONTEXT keys that are not in strings.ts any more: {stale}")

    items: list[Item] = []
    n = 0
    for gid, _, _ in WEB_GROUPS:
        for key in en:
            if WEB_CONTEXT[key][0] == gid:
                n += 1
                items.append(Item(f"W{n:02d}", "web", key, en[key], ar[key], groups[gid], WEB_CONTEXT[key][1]))
        if gid == "owner":  # role labels sit with the owner line they complete
            for role in role_en:
                n += 1
                items.append(Item(f"W{n:02d}", "web", f"role:{role}", role_en[role], role_ar[role], groups[gid], ROLE_WHERE))

    ts = load_templates()
    t = 0
    for purpose in PURPOSES:
        for channel in CHANNELS:
            for with_date in (False, True):
                if not ts.has("en", purpose, channel, with_date):
                    if ts.has("ar", purpose, channel, with_date):
                        raise SystemExit(f"build_packs: {purpose}/{channel} exists in Arabic but not English")
                    continue
                e_en = ts.get("en", purpose, channel, with_date)
                e_ar = ts.get("ar", purpose, channel, with_date)
                t += 1
                items.append(Item(f"T{t:02d}", "template", f"{purpose}/{e_en.key}", e_en.text, e_ar.text,
                                  purpose, PURPOSE_CONTEXT[purpose]))
    for entry in ts.entries:  # an Arabic variant with no English twin is a source error
        if not ts.has("en", entry.purpose, entry.channel, entry.with_date):
            raise SystemExit(f"build_packs: Arabic template {entry.purpose}/{entry.key} has no English twin")

    for d, (path, d_en, d_ar) in enumerate(load_mock_drafts(), 1):
        items.append(Item(f"D{d:02d}", "draft", f"{path}", d_en, d_ar, "Demo draft texts", MOCK_WHERE))
    return items


def template_entry(item: Item, language: str):
    purpose, variant = item.key.split("/")
    ts = load_templates()
    with_date = variant.endswith("_with_date")
    return ts.get(language, purpose, variant.replace("_with_date", ""), with_date)


def sample_len(item: Item, language: str) -> int:
    e = template_entry(item, language)
    text = e.text
    for k, v in SAMPLE_VALUES[language].items():
        text = text.replace("{" + k + "}", v)
    return len(text)


# ====================================================================== pathway computations


def main_chain(pw: dict) -> list[dict]:
    return [t for t in expand_transitions(pw) if t["from"] in pw["states"] and t["to"] in pw["states"]
            and pw["states"].index(t["to"]) == pw["states"].index(t["from"]) + 1]


def milestone_rows(pw: dict) -> list[dict]:
    """One row per milestone (A18): what must happen, what starts and meets the clock, the allowance, the source."""
    rows = []
    for name, m in (pw.get("milestones") or {}).items():
        need(MILESTONE_GLOSS, name, "milestone")
        need(MILESTONE_START, m["from_event"], "milestone start")
        rows.append({"name": name, "gloss": MILESTONE_GLOSS[name], "starts": MILESTONE_START[m["from_event"]],
                     "until": m["until_event"], "days": m["working_days"], "source": m["source"]})
    return rows


def sla_rows(pw: dict) -> list[dict]:
    """One row per SLA step: the state the clock runs in, what starts it, the allowance. `cumulative` adds the
    allowances of the steps on the main path up to the colonoscopy, whose own clock is a milestone."""
    trans = expand_transitions(pw)
    first = pw["states"][0]
    rows = []
    cumulative = 0
    chain_events = [t["event"] for t in main_chain(pw)]
    # steps after the event that ends a colonoscopy milestone are counted from the colonoscopy, not from the result
    after = {e for m in (pw.get("milestones") or {}).values() if m["from_event"] != "case_created"
             for e in chain_events[chain_events.index(m["until_event"]) + 1:]} if chain_events else set()
    for event, days in pw["slas_working_days"].items():
        need(SLA_STEP_GLOSS, event, "SLA step")
        states = sorted({t["from"] for t in trans if t["event"] == event}, key=lambda s: (pw["states"] + pw["side_states"]).index(s))
        state = states[0]
        entering = [t["event"] for t in trans if t["to"] == state]
        entering = list(dict.fromkeys(entering))
        starts = f"the case enters `{state}`"
        if state == first:
            starts += " (result received from the laboratory)"
        elif entering:
            starts += " (after " + ", ".join(f"`{e}`" for e in entering) + ")"
        if pw.get("sla_clock_start", {}).get(event) == "handoff_accepted":
            starts = ("the latest referral acceptance (`handoff_accepted`) on the case, or, if no referral was "
                      f"accepted, when the case enters `{state}`")
        on_path = event in chain_events and event not in after
        if on_path:
            cumulative += days
        rows.append({"event": event, "state": state, "days": days, "starts": starts,
                     "cumulative": cumulative if on_path else None,
                     "after_colonoscopy": event in after, "gloss": SLA_STEP_GLOSS[event]})
    return rows


def pretty_role(name: str) -> str:
    return name.replace("_", " ")


# ====================================================================== mermaid


def mermaid(pw: dict) -> str:
    sla = pw["slas_working_days"]
    lines = ["stateDiagram-v2", '    state "Any open state" as any_open']
    lines.append(f"    [*] --> {pw['states'][0]} : positive FIT result arrives")
    for t in expand_transitions(pw):
        label = t["event"]
        extra = []
        if t["event"] in sla:
            extra.append(f"{sla[t['event']]} wd")
        if t["requires"]:
            extra.append("needs " + " and ".join(t["requires"]))
        if t["min"]:
            extra.append(", ".join(f"at least {v} {k.replace('_', ' ')}" for k, v in t["min"].items()))
        if t["when"]:
            extra.append("when " + ", ".join(f"{k} is {str(v).lower()}" for k, v in t["when"].items()))
        src, dst = t["from"], t["to"]
        if dst == "resume_state":
            dst = "any_open"
            extra.append("returns to the earlier state")
        if extra:
            label += ", " + ", ".join(extra)
        lines.append(f"    {src} --> {dst} : {label}")
    lines.append("    closed --> [*] : loop closed")
    side = ",".join(pw["side_states"])
    lines += ["    classDef side fill:#fff3cd,stroke:#b8860b,color:#000",
              "    classDef final fill:#d4edda,stroke:#2e7d32,color:#000",
              f"    class {side} side", "    class closed final"]
    return "\n".join(lines)


# ====================================================================== shared pieces


def forbidden_by_language(language: str) -> list[dict]:
    return load_forbidden()[language]


def static_hits(items: list[Item]) -> list[tuple[Item, str, list[str]]]:
    """Patient-facing strings that the forbidden-phrase validator would reject if a model had drafted them."""
    out = []
    for it in items:
        for lang, text in (("en", it.en), ("ar", it.ar)):
            hits = [h.id for h in forbidden_hits(text) if h.language == lang]
            if hits:
                out.append((it, lang, hits))
    return out


def counts() -> dict:
    items = build_items()
    f = load_forbidden()
    web = [i for i in items if i.source == "web"]
    tpl = [i for i in items if i.source == "template"]
    drafts = [i for i in items if i.source == "draft"]
    return {
        "web_catalogue_strings": len(web), "template_strings": len(tpl), "demo_draft_texts": len(drafts),
        "patient_strings_total": len(items),
        "clinician_pack": {"english_patient_strings": len(items), "english_forbidden_phrases": len(f["en"])},
        "arabic_pack": {"arabic_patient_strings": len(items), "arabic_forbidden_phrases": len(f["ar"]),
                         "arabic_strings_incl_forbidden_phrases": len(items) + len(f["ar"])},
    }


def doc_header(title: str, audience: str) -> str:
    pw = load_pathway()
    ts = load_templates()
    return "\n".join([
        f"# {title}",
        "",
        f"> Generated file. {REGENERATE}",
        f"> Built from pathway `{pw['pathway']}` v{pw['version']}, template set v{ts.version}, forbidden-phrase list v{load_forbidden()['version']}.",
        f"> For: {audience}",
        "> All data in this project is synthetic. Nothing here is clinical advice or a claim of real-world performance.",
        "",
    ])


# ====================================================================== clinician pack

def questions(pw: dict) -> list[tuple[str, str, str, str]]:
    """(key, title, context, question) for the ten questions; numbers come from the pathway."""
    sla = pw["slas_working_days"]
    lad = pw["escalation_ladder"]
    ms = pw["milestones"]
    ref, col = ms["referral_requested"], ms["confirmatory_completed"]
    pre_referral = sum(sla[e] for e in ("acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge"))
    # every allowance used to the full: referral requested on the last day, answered on the last day, then the colonoscopy
    to_colonoscopy = ref["working_days"] + pw["handoff_response_working_days"] + col["working_days"]
    n_fail = next(t for t in expand_transitions(pw) if t["event"] == "mark_lost_contact")["min"]["failed_attempts"]
    reasons = ", ".join(r.replace("_", " ") for r in pw["handoff_reject_reasons"])
    return [
        ("ack", f"Acknowledge in {sla['acknowledge']} working days, accept in {sla['accept_ownership']}",
         f"The pathway gives the laboratory or coordinator {sla['acknowledge']} working days to acknowledge a positive result, and a clinician {sla['accept_ownership']} more working day to accept ownership.",
         f"Are {sla['acknowledge']} and {sla['accept_ownership']} working days realistic for these two steps in your setting? If not, what would you set, and who in practice does each step?"),
        ("limits", "The other time limits",
         f"Contact the patient in {sla['contact_patient']} working days, patient confirms in {sla['patient_acknowledge']}, book the colonoscopy in {sla['book_confirmatory']}, clinician review in {sla['clinician_review']} after the colonoscopy. The four steps before the referral (acknowledge, accept, contact, patient confirms) add up to {pre_referral} working days, so that a case which meets each of them still has time to request the referral inside the {ref['working_days']}-working-day referral timeline.",
         "Which of these are too tight or too loose? The patient-confirms limit runs against the team although the patient controls it: should that clock exist, and who should be chased when it passes?"),
        ("disclosure", "No result in any text",
         "No message (SMS, WhatsApp or letter) and no line on the patient page states or hints at what the screening found. A message says that the result is ready, that the care team needs to talk with the patient about the next step, and who will call. The patient page says the same and adds \"Your care team will explain your result in person or by phone.\" This follows the IFHAS standard that abnormal results are given with the participant present. The next step is named only as an appointment (\"a camera test\"; the patient page also calls it a colonoscopy). The forbidden-phrase list rejects any model draft that states a result (category `result_disclosure`).",
         "Please confirm the non-disclosure wording (H1). Is \"Your screening result is ready and your care team needs to talk with you about the next step\" acceptable as the first message, or does it alarm patients or hint at a finding by itself? Is naming the next step (\"a camera test\", \"a colonoscopy\") acceptable, or should even that wait for the conversation? Is \"in person or by phone\" right, and what should happen when a patient replies and asks for the result by text? Is anything in section 9 alarming, wrong, or missing?"),
        ("kpi", f"What the {ref['working_days']} and {col['working_days']} working days are measured from",
         f"The pathway has two national timelines. The IFHAS standard (DoH, V10, July 2025) has referral to secondary care within {ref['working_days']} working days of releasing the result; our clock starts when the case is created (the positive result is logged) and stops when a referral is requested (`handoff_requested`). The UAE National Guideline (Appendix 2, not the IFHAS standard) has the colonoscopy within {col['working_days']} working days of referral; our {col['working_days']}-working-day clock starts at the latest referral acceptance (`handoff_accepted`), or at the booking if no referral was accepted, and stops when the colonoscopy is recorded (`complete_confirmatory`). That is a stated, slightly lenient reading of \"within {col['working_days']} working days of referral\": the days between the referral request and its acceptance are not counted against the {col['working_days']}; they are bounded instead by the {pw['handoff_response_working_days']}-working-day handoff response timer, after which an unanswered referral bounces back to the referrer. Using every allowance to the full (request on the last day, a {pw['handoff_response_working_days']}-working-day wait for acceptance, then {col['working_days']} working days), a colonoscopy could happen {to_colonoscopy} working days after the result and still meet both timelines.",
         f"Should the {ref['working_days']} working days stop at the referral request or at its acceptance, and does the clock start at the result release or at the laboratory report date? Please confirm or change our reading that the {col['working_days']} working days count from the latest acceptance rather than from the request (the request-to-acceptance days being bounded by the {pw['handoff_response_working_days']}-working-day response timer instead): is that acceptable, or should the clock start at the request? Any referral, including a transfer between primary care owners, counts today: should it? Should we also report the programme-level share (the guideline's measure is a percentage, above 90%), not only a limit on each case?"),
        ("evidence", "What counts as evidence to close a case",
         "A case can close only with an evidence reference and a named clinician's signature after clinician review. What the evidence reference may point to is not restricted today.",
         "What is acceptable: the colonoscopy report alone, report plus histology, a documented refusal, a letter to the GP? What must be in the sign-off? Who may sign, and is clinician review by itself ever enough?"),
        ("refusal", "Informed refusal",
         "There is no state for a patient who, after a proper discussion, declines the colonoscopy. Today the nearest things are the handoff reason `patient_declined` (declined a referral) and keeping the case open.",
         "What should happen: close with documented informed refusal (signed by a clinician), keep open with a recall date, offer FIT again next year, or something else? What must be recorded so that the closure is defensible?"),
        ("lost", f"Lost contact after {n_fail} failed attempts",
         f"A case can be marked lost after {n_fail} failed attempts (the engine checks only that the number given is at least {n_fail}). The case stays open.",
         f"Is {n_fail} the right number? What counts as an attempt (call, SMS, WhatsApp, letter), how far apart, and what else must be tried before marking lost (the patient's GP, a registered letter, an emergency contact)?"),
        ("consent", "Contact allowed unless the patient says no",
         "Until the patient says otherwise, every channel (SMS, WhatsApp, phone, letter) and sharing the result with the specialist are treated as allowed (opt-out). The patient page, by contrast, shows sharing unticked and the channel set to phone until the patient chooses.",
         "Is opt-out acceptable for contacting a patient about a positive result and for sharing with the specialist, or must any channel (especially WhatsApp and SMS) be opt-in? Which of the two defaults should the patient page follow?"),
        ("ladder", "Escalation and handoff",
         f"A late step gets a reminder to the owner on the due date, the supervisor {lad[1]['after_breach_days']} working days after the due date and the programme coordinator {lad[2]['after_breach_days']} working days after the due date. A referral unanswered for {pw['handoff_response_working_days']} working days bounces back. Rejection reasons are: {reasons}.",
         f"Are these offsets and the {pw['handoff_response_working_days']}-day response time realistic? Should any delay (for example before the colonoscopy) climb faster? Are the rejection reasons complete, and is `patient_declined` a rejection at all or a separate outcome?"),
        ("exits", "Missing exits",
         "The pathway has one way forward and a few side states. It has no state for: patient moved abroad or out of the emirate, died, already had a colonoscopy elsewhere, unfit for colonoscopy, an incomplete colonoscopy or poor bowel preparation needing a repeat, or a symptomatic patient who should skip the pathway.",
         "Which of these (or others) happen often enough to need their own state or exit, and how should each close? Who may reopen a closed case, and for what reasons?"),
    ]


def qn(qs: list, key: str) -> int:
    return [q[0] for q in qs].index(key) + 1


def clinician_pack() -> str:
    pw = load_pathway()
    cal = load_calendar()
    items = build_items()
    f = load_forbidden()
    trans = expand_transitions(pw)
    slas = sla_rows(pw)
    c = counts()
    qs = questions(pw)
    assert len(qs) == 10
    out: list[str] = [doc_header("Clinician review pack (H1)", "a GP or public-health doctor, 30 minutes")]

    # ---- 0
    out += [
        "## 0. How to use this pack in 30 minutes", "",
        table(["Minutes", "Section", "What to do"], [
            ["3", "1 Purpose", "Read the one-page summary."],
            ["5", "2 to 4 Pathway, states, events", "Check the picture and the plain-language table: is anything wrong or missing?"],
            ["7", "5 to 8 Time limits, escalation, handoff, two flagged rules", "Say whether the numbers are realistic."],
            ["5", "9 and 10 What the patient reads, words that are never allowed", "Read the English wording as a patient would."],
            ["10", "11 and 12 Ten questions, feedback table", "Answer the questions. Fill the last table: keep or change, with a comment."],
        ]), "",
        "Anything you cannot judge, mark \"cannot say\" in the comment. A short answer is more useful than none.", "",
    ]

    # ---- 1
    out += [
        "## 1. Purpose (one page)", "",
        "**The problem.** A positive FIT (faecal immunochemical test) result is only the start of screening. In a review of 2015 to 2016 data for Emirati nationals in Abu Dhabi Health Services clinics, 436 of 1,429 positive results (30.5%) were followed by a colonoscopy, at a median of 72 days (Almansoori et al., BMC Cancer 2021; source row in `docs/evidence.md` section 8b). Results get lost between the laboratory, the primary care clinic, the specialist and the patient, because no one is named as responsible and nothing notices a delay.", "",
        "**What this prototype does.** It tracks every positive FIT result from arrival to a documented outcome:", "",
        "- one named owner at every step, and a time limit (in working days) for every step;",
        "- a late step is flagged and climbs a fixed escalation ladder to a person, who must accept it;",
        "- handing a case to another provider changes the owner only when the receiving provider accepts;",
        "- a case can be closed only with an evidence reference and a named clinician's signature. No automated part of the system can close a case;",
        "- the patient sees a short page, and receives messages, in Arabic or English, with an \"I don't understand this\" button that asks a person to call.", "",
        "**What it does not do.** It does not diagnose, give advice, decide care or contact anyone without a person approving. A language model may draft a message for a person to approve, nothing else. A rejected model draft is replaced by the fixed template. All data is synthetic; this is a demonstration, not a medical device and not connected to any real record.", "",
        "**What we need from you.** Tell us where the pathway, the time limits or the wording would not work in a real clinic. The ten questions in section 11 are the ones we cannot answer ourselves. Everything else in this pack is generated from the files the prototype actually uses, so what you read is what the system does.", "",
        f"Working days: {', '.join(d.capitalize() for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'] if d not in cal['weekend'])} count; "
        f"{' and '.join(d.capitalize() for d in cal['weekend'])} are the weekend; public holidays: "
        f"{'none are configured' if not cal.get('holidays') else str(len(cal['holidays'])) + ' configured'}. "
        "Friday counts as a full day (in government it is a half day). Time zone " + cal["timezone"] + ".", "",
    ]

    # ---- 2
    out += [
        "## 2. The pathway as a picture", "",
        "Generated from `config/pathways/colorectal_fit.yaml`. Boxes are states. Yellow boxes are side states that change who may act next. Green is the end. \"wd\" means working days allowed for that step. \"Any open state\" means any state except closed.", "",
        "```mermaid", mermaid(pw), "```", "",
        "Overdue and escalated are not states. A case can be overdue and in any state, so they are flags shown beside the state.", "",
        "**Closure rule (verbatim from the configuration):** " + pw["closure_rule"], "",
    ]

    # ---- 3
    out += ["## 3. States in plain language", ""]
    rows = []
    sla_by_state = {r["state"]: r for r in slas}
    for s in pw["states"] + pw["side_states"]:
        kind = "main path" if s in pw["states"] else "side state"
        leaves = []
        for t in trans:
            if t["from"] == s or (t["from"] == "any_open" and s not in ("closed", "consent_withdrawn")):
                if t["from"] == "any_open":
                    continue
                who = need(EVENT_GLOSS, t["event"], "event")[1]
                leaves.append(f"`{t['event']}` ({who})")
        if s != "closed" and s != "consent_withdrawn":
            leaves.append("`consent_updated` with withdrawal (patient or coordinator) moves the case to `consent_withdrawn`")
        if s == "consent_withdrawn":
            leaves = ["`consent_updated` restoring consent (patient or coordinator) returns the case to the state it was in"]
        sla = sla_by_state.get(s)
        rows.append([f"`{s}`", kind, need(STATE_GLOSS, s, "state"),
                     "<br>".join(dict.fromkeys(leaves)) or "none",
                     f"{sla['days']} working days to `{sla['event']}`" if sla else "no time limit"])
    out += [table(["State", "Type", "Meaning", "How the case leaves it, and who may do that", "Time limit in this state"], rows), ""]

    # ---- 4
    out += ["## 4. Events: what can happen and who may do it", "",
            "Events are the facts the system records. The \"who\" column follows the authorisation table in the code (`api/app/policy.py`) and `docs/governance/decision-matrix.md`. \"Human only\" means the state machine itself refuses any automated actor (scheduler, watchdog, model, system).", ""]
    events = list(dict.fromkeys([t["event"] for t in trans] + pw["annotations"]))
    human = set(pw["human_only_events"])
    rows = []
    for e in events:
        meaning, who = need(EVENT_GLOSS, e, "event")
        kind = "changes state" if any(t["event"] == e for t in trans) else "annotation"
        if e in pw["annotations"] and any(t["event"] == e for t in trans):
            kind = "changes state or annotates"
        rows.append([f"`{e}`", meaning, who, kind, "yes" if e in human else ""])
    out += [table(["Event", "Meaning", "Who may do it", "Kind", "Human only"], rows), "",
            "Events listed as human only in the configuration: " + ", ".join(f"`{e}`" for e in pw["human_only_events"]) + ". Also: no automated actor may emit `close` (checked separately by the state machine).", ""]

    # ---- 5
    out += ["## 5. Time limits (SLAs)", "",
            "Each step has a limit in working days. The clock starts when the case enters the state, and a case is late when time is strictly after the due date. Counting uses the working week above. These per-step limits are design choices with no public anchor. The two national timelines are separate clocks that span states (the milestones below): they are the only limits with a public anchor.", ""]
    rows = []
    for r in slas:
        rows.append([f"`{r['event']}`", r["gloss"], f"`{r['state']}`", r["starts"], r["days"],
                     r["cumulative"] if r["cumulative"] is not None
                     else ("counted from the colonoscopy" if r["after_colonoscopy"] else "not on the main path")])
    out += [table(["Step (event)", "What must happen", "State where the clock runs", "Clock starts when", "Working days allowed", "Latest, counted from the result if every step uses its full allowance"], rows), ""]
    mrows = milestone_rows(pw)
    out += ["### National timelines (milestones)", "",
            "A milestone is a clock from one event to another, measured against a national standard. If the ending event has not happened when the allowance runs out, the scheduler flags it once (`mark_overdue` naming the milestone, not a state) and climbs the escalation ladder below, exactly as for a late step. Doing the thing later clears the flag; the log keeps both events.", "",
            table(["Milestone", "What must happen", "Clock starts when", "Met when", "Working days allowed", "Source"],
                  [[f"`{m['name']}`", m["gloss"], m["starts"], f"`{m['until']}` is recorded", m["days"], m["source"]] for m in mrows]), ""]
    ref_m = pw["milestones"]["referral_requested"]
    col_m = pw["milestones"]["confirmatory_completed"]
    pre = sum(pw["slas_working_days"][e] for e in ("acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge"))
    out += [
        f"**Where the figures come from.** The {ref_m['working_days']} working days to request a referral are the DoH IFHAS standard (V10, July 2025): referral to secondary care within {ref_m['working_days']} working days of releasing results. The {col_m['working_days']} working days for the colonoscopy are **not** from IFHAS: they are the UAE National Guideline for Colorectal Cancer Screening and Diagnosis (3rd edition, 2022, prepared by a task force that included DoH Abu Dhabi; Appendix 2), which sets follow-up colonoscopy **within {col_m['working_days']} working days of referral** in more than 90% of cases (acceptable) and more than 95% (desirable). The same guideline is inconsistent: clause 12.5.2 says 15 working days for the urgent referral, Appendix 3 says 31 days. Sources are listed in `docs/evidence.md` section 8; the register marks them as read from the primary source (check marker P).", "",
        f"Three differences from the standards matter (question {qn(qs, 'kpi')}): (1) the standards are shares of cases, ours is a limit on each case; (2) the referral clock stops at the referral request, not at its acceptance, and any handoff counts as a referral; (3) the {col_m['working_days']}-working-day colonoscopy clock starts at the latest referral acceptance, a stated and slightly lenient reading of \"from referral\" (the days between request and acceptance are bounded instead by the {pw['handoff_response_working_days']}-working-day handoff response timer), and we ask you to confirm or change it. The four steps before the referral allow {pre} working days in total, which fits inside the {ref_m['working_days']}. "
        + (f"`close` has its own limit of {pw['slas_working_days']['close']} working days after clinician review, so a reviewed case can go overdue."
           if "close" in pw["slas_working_days"] else "There is no limit on `close`, so a case can sit at `clinician_reviewed` without breaching."), "",
        "The same working-day arithmetic applies to the referral response time and the escalation offsets below.", "",
    ]

    # ---- 6
    out += ["## 6. Escalation ladder", "",
            "When a step or a national timeline passes its due date, the scheduler flags it (`mark_overdue`, once) and then climbs the ladder. A rung is written only after the previous one, never skipped. A person in the rung's accepting role must accept it (`escalation_accepted`); that stops further rungs for that delay. The scheduler can notify but cannot accept, reassign or close. A regulator rung is not built.", "",
            "A case can have a late step and a late national timeline at once, each with its own ladder. One person is not told twice on the same working day: when two ladders reach the same rung at the same time, the scheduler writes one escalation that names both delays (`also_for`); a rung that falls due later the same working day, for a person already told and not yet answering, is recorded as attached to that escalation (`attached_to`) and tells nobody. One acceptance answers the whole escalation. A rung on another working day is a new escalation.", ""]
    rows = []
    for r in pw["escalation_ladder"]:
        rows.append([f"`{r['rung']}`", need(RUNG_GLOSS, r["rung"], "rung"),
                     f"{r['after_breach_days']} working days after the due date", pretty_role(r["notify"]),
                     ", ".join(pretty_role(x) for x in r["accepted_by"])])
    out += [table(["Rung", "What it does", "When", "Who is told", "Who must accept"], rows), ""]

    # ---- 7
    resp = pw["handoff_response_working_days"]
    out += ["## 7. Handoff and rejection reasons", "",
            f"A case is handed to another provider with `handoff_requested`. Ownership moves only when the receiving clinician accepts (`handoff_accepted`). If the receiving provider rejects, the case goes to `referral_rejected` and must be given a new owner within {pw['slas_working_days']['reassign']} working days (`reassign`), or accepted by another provider. A request that gets no answer for {resp} working days is bounced back by the scheduler.", "",
            table(["Reason", "Meaning", "Who may write it"],
                  [[f"`{r}`", need(REASON_GLOSS, r, "handoff reason"), "Receiving clinician" ] for r in pw["handoff_reject_reasons"]]
                  + [[f"`{pw['handoff_bounce_reason']}`", need(REASON_GLOSS, pw["handoff_bounce_reason"], "handoff reason"), "Scheduler only"]]), ""]

    # ---- 8
    lost = next(t for t in trans if t["event"] == "mark_lost_contact")
    n_fail = lost["min"]["failed_attempts"]
    cons_label = {"channel_sms": "SMS", "channel_whatsapp": "WhatsApp", "channel_phone": "phone calls",
                  "channel_letter": "letters", "share_with_specialist": "sharing the result with the specialist"}
    cons_on = ", ".join(cons_label.get(k_, k_.replace("_", " ")) for k_, v in DEFAULT_CONSENT.items() if v is True)
    out += ["## 8. Two rules we want you to challenge", "",
            f"### 8.1 \"Lost contact after {n_fail} failed attempts\" (question {qn(qs, 'lost')})", "",
            f"- What the system does: from `patient_contacted`, a person can record `mark_lost_contact`; the case moves to `lost_contact` and stays open. The engine accepts it only if the number of failed attempts supplied is at least {n_fail}. It does not count attempts itself yet.",
            f"- Why {n_fail}: it is an assumption (decision record ADR-011; row L14 in `docs/assumptions-limitations.md`), not taken from a guideline.",
            "- What we do not know: what counts as an attempt, how long to wait between them, and what a clinic must try before giving up.", "",
            f"### 8.2 \"Opt-out consent default\" (question {qn(qs, 'consent')})", "",
            f"- What the system does: before the patient says anything, these are treated as allowed: {cons_on}. The patient can switch channels off, stop sharing, or withdraw. Withdrawal freezes contacting, drafting, approving, sending and handing off, and flags the case for a person (decision record ADR-017).",
            "- Why: with opt-in, the notification mock would refuse every message for the whole demo cohort. It is an assumption for you to judge, not a legal position.",
            "- A visible inconsistency: the patient page shows the sharing box unticked and the channel set to phone until the patient chooses, while the engine treats all channels and sharing as allowed.", ""]

    # ---- 9
    out += ["## 9. What the patient reads (English)", "",
            f"{c['patient_strings_total']} English strings in three places. The reading-level target is grade 6. Arabic versions are reviewed separately (H2); the IDs below match the Arabic pack. Placeholders in braces are filled in: {{owner_name}} (the care owner), {{next_step}} (default \"{DEFAULT_NEXT_STEP['en']}\"), {{appointment_date}} (for example \"{SAMPLE_VALUES['en']['appointment_date']}\"), {{name}} and {{role}} on the patient page.", ""]
    out += ["### 9.1 The patient page (web)", "",
            "Shown in the installed app or browser at `/patient`. Source: `web/src/i18n/strings.ts`.", ""]
    for gid, title, blurb in WEB_GROUPS:
        group_items = [i for i in items if i.source == "web" and i.group == title]
        if not group_items:
            continue
        out += [f"**{title}.** {blurb}", "",
                table(["ID", "English text", "Where it appears"], [[i.id, i.en, i.where] for i in group_items]), ""]
    out += ["Not in the catalogue (hard-coded in `web/src/pages/PatientPage.tsx`): a \"Demo case\" link on the start page and the accessible label \"Actions\". They are demo scaffolding and not part of this review.", ""]
    out += ["### 9.2 Messages (SMS, WhatsApp, letter)", "",
            "Source: `api/app/ai/templates/en.yaml`. These are the default drafts and the fallback if a model draft is rejected. A person approves a message before it is sent. Limits: SMS 320, WhatsApp 600, letter 1200 characters. \"Chars\" is the length with the sample values.", ""]
    for purpose in PURPOSES:
        tp = [i for i in items if i.source == "template" and i.group == purpose]
        out += [f"**{purpose}.** {PURPOSE_CONTEXT[purpose]}", "",
                table(["ID", "Channel", "English text", "Chars / limit"],
                      [[i.id, f"{CHANNEL_LABEL[template_entry(i, 'en').channel]}{' with date' if template_entry(i, 'en').with_date else ''}",
                        i.en, f"{sample_len(i, 'en')} / {CHANNEL_MAX_CHARS[template_entry(i, 'en').channel]}"] for i in tp]), ""]
    out += ["### 9.3 Demo draft texts (coordinator app)", "",
            "Two draft texts in the demo data of the coordinator app. They stand in for a model or template draft and are shown to the coordinator, who approves them.", "",
            table(["ID", "English text", "Where it appears"], [[i.id, i.en, i.where] for i in items if i.source == "draft"]), ""]
    hits = static_hits(items)
    en_hits = [(it, ids) for it, lang, ids in hits if lang == "en"]
    out += ["### 9.4 A check we ran on our own wording", "",
            "The forbidden-phrase list in section 10 applies to model drafts. We ran it over every string above as a self-check. "
            + ("None matched." if not en_hits else
               f"These strings would be rejected if a model had written them. The patient page is fixed text, not a model draft, so they are allowed today. Please say whether each is acceptable (question {qn(qs, 'disclosure')})."), ""]
    if en_hits:
        out += [table(["ID", "English text", "Matching list entries"], [[it.id, it.en, ", ".join(f"`{i}`" for i in ids)] for it, ids in en_hits]), ""]

    # ---- 10
    out += ["## 10. Words the system never lets a model write", "",
            "A model draft containing any of these is rejected and replaced by the template (and counted as an abstention). Matching ignores case and, for phrases, whole words only; a regular expression is shown where one word stem stands for several forms. The Arabic list has the same categories and is in the Arabic pack. Please say what is missing and what blocks a perfectly good sentence.", "",
            table(["Category", "What it prevents"], [[f"`{k_}`", FORBIDDEN_CATEGORY_GLOSS.get(k_, "-")] for k_ in dict.fromkeys(e["category"] for e in f["en"])]), "",
            table(["Id", "Category", "Phrase or pattern", "Why", "Example it catches"],
                  [[f"`{e['id']}`", e["category"], code(e.get("phrase") or e.get("regex")),
                    " ".join(e["rationale"].split()), e["example"]] for e in f["en"]]), "",
            f"The Arabic list has {len(f['ar'])} entries (see `arabic-pack.md`). The validator also rejects: empty text, text over the channel limit, text in the wrong language, a missing owner name or next step, leftover placeholders, runs of 7 or more digits, email addresses, links, and identifier words (Emirates ID, passport, file number).", ""]

    # ---- 11
    out += ["## 11. Ten questions we need answered", ""]
    for n, (_, title, ctx, ask) in enumerate(qs, 1):
        out += [f"{n}. **{title}.** {ctx} *Question:* {ask}", ""]

    # ---- 12
    out += ["## 12. Feedback table", "",
            "For each row write keep or change and, if you change it, say what you would do. Leave a row empty if you did not look at it.", ""]
    fb: list[list[object]] = [["P1", "Purpose and scope (section 1)", "", ""],
                              ["P2", "Pathway picture and closure rule (section 2)", "", ""]]
    for s in pw["states"] + pw["side_states"]:
        fb.append([f"S:{s}", f"State `{s}`: {need(STATE_GLOSS, s, 'state').split('. ')[0].rstrip('.')}", "", ""])
    for r in slas:
        fb.append([f"SLA:{r['event']}", f"Time limit `{r['event']}`: {r['days']} working days, clock starts when {r['starts'].split(' (')[0].replace('the case enters', 'the case enters')}", "", ""])
    for m in milestone_rows(pw):
        fb.append([f"M:{m['name']}", f"National timeline `{m['name']}`: {m['days']} working days, clock starts when {m['starts'].split(' (')[0].split(',')[0]}", "", ""])
    for r in pw["escalation_ladder"]:
        fb.append([f"L:{r['rung']}", f"Rung `{r['rung']}` at {r['after_breach_days']} working days after the due date", "", ""])
    for r in pw["handoff_reject_reasons"] + [pw["handoff_bounce_reason"]]:
        fb.append([f"H:{r}", f"Handoff reason `{r}`", "", ""])
    fb += [["R1", f"Lost contact after {n_fail} failed attempts (8.1)", "", ""],
           ["R2", "Opt-out consent default (8.2)", "", ""]]
    for gid, title, _ in WEB_GROUPS:
        ids = [i.id for i in items if i.source == "web" and i.group == title]
        if ids:
            fb.append([f"W:{gid}", f"Patient page: {title} ({ids[0]} to {ids[-1]})", "", ""])
    for purpose in PURPOSES:
        ids = [i.id for i in items if i.source == "template" and i.group == purpose]
        fb.append([f"M:{purpose}", f"Message `{purpose}` ({ids[0]} to {ids[-1]})", "", ""])
    d_ids = [i.id for i in items if i.source == "draft"]
    fb.append(["D", f"Demo draft texts ({d_ids[0]} to {d_ids[-1]})", "", ""])
    for k_ in dict.fromkeys(e["category"] for e in f["en"]):
        fb.append([f"F:{k_}", f"Forbidden-phrase category `{k_}`", "", ""])
    for n, (_, title, _, _) in enumerate(qs, 1):
        fb.append([f"Q{n}", f"Question {n}: {title}", "", ""])
    fb.append(["X", "Anything missing or dangerous that this pack does not show", "", ""])
    out += [table(["Item", "What", "Keep / change", "Comment"], fb), "",
            "Reviewer name and role: ", "", "Date: ", ""]

    return "\n".join(out).rstrip() + "\n"


# ====================================================================== Arabic pack

AR_CHECKLIST = [
    ("Accuracy", "Does each Arabic text say what the English says, no more and no less? In particular: \"your screening result is ready and your care team needs to talk with you about the next step\" and \"your care team will explain your result in person or by phone\". Nothing in Arabic may state or hint at the result."),
    ("Register for Gulf Arabic readers", "Modern Standard Arabic, easy to read, no dialect. Is it natural for a reader in the UAE, neither stiff nor too colloquial? Greetings: \"مرحباً\" in SMS and WhatsApp, \"السلام عليكم\" in letters. Are they right for every patient, including non-Muslim readers of Arabic?"),
    ("Plain language", "Grade-6 reading level: short sentences, everyday words. Mark any word an ordinary reader might not know."),
    ("No stigma, no fear", "Calm, respectful tone. Nothing blames the patient, nothing frightens, nothing minimises. Does any word carry a stigma in your community?"),
    ("Terminology: colonoscopy", "Is \"تنظير القولون\" the term patients know? Is \"فحص بالكاميرا\" (camera test) a helpful friendly term or confusing next to it? Which one should be used where?"),
    ("Terminology: screening", "\"الكشف المبكر\" in messages, \"فحص\" (test) on the page. Is one term better, and do patients tell the screening test apart from the colonoscopy?"),
    ("Terminology: care team and care owner", "\"فريق الرعاية\", \"منسق الرعاية\", \"المسؤول عن رعايتك\" and \"من يتابع حالتك\" are used. Choose one wording for each idea."),
    ("Gender", "Second person is written in the masculine form by default. Is that acceptable for women, or is a neutral wording available? Words such as مصاب are blocked in model drafts anyway."),
    ("Right-to-left display", "Open the patient page in Arabic (`/patient`, language button). Does the text read correctly with a Latin name in the owner line, with phone numbers and dates?"),
    ("Digits and dates", "Western digits (0 to 9) are used today. Prefer Arabic-Indic digits (٠ to ٩)? Are month and weekday names (for example نوفمبر, الثلاثاء) the ones used in the UAE?"),
    ("Orthography and punctuation", "Tanween is written two ways today (see section 3). Latin full stops and Arabic commas are mixed. Choose one convention."),
    ("Length", "Does each message still fit its channel limit in your corrected wording (SMS 320, WhatsApp 600, letter 1200 characters)?"),
    ("Forbidden phrases", "Is the list in section 6 complete and does it block ordinary sentences by mistake? Add what is missing."),
]


def terms_table(items: list[Item]) -> str:
    ar_texts = [(i.id, i.ar) for i in items]
    concepts = [
        ("colonoscopy", ["تنظير القولون"]),
        ("camera test (friendly term)", ["فحص بالكاميرا", "الفحص بالكاميرا", "فحص متابعة بالكاميرا"]),
        ("screening test", ["فحص الكشف المبكر"]),
        ("test (generic)", ["فحصك", "الفحص"]),
        ("care team", ["فريق الرعاية", "فريق رعايتك", "فريق رعايتي"]),
        ("care coordinator", ["منسق الرعاية", "منسق رعاية"]),
        ("care owner (the responsible person)", ["المسؤول عن رعايتك", "المسؤول عن متابعة حالتك", "من يتابع حالتك"]),
        ("result", ["نتيجة", "نتيجتك", "نتيجتي"]),
        ("appointment", ["موعد"]),
    ]
    rows = []
    for name, variants in concepts:
        cells = []
        for v in variants:
            where = [i for i, t in ar_texts if v in t]
            if where:
                shown = ", ".join(where[:8]) + (f" and {len(where) - 8} more" if len(where) > 8 else "")
                cells.append(f"{v} : {len(where)} string{'' if len(where) == 1 else 's'} ({shown})")
            else:
                cells.append(f"{v} : not used")
        rows.append([name, "<br>".join(cells)])
    return table(["Concept", "Arabic wording in use, with the strings that contain it"], rows)


def arabic_pack() -> str:
    items = build_items()
    f = load_forbidden()
    _, _, _, _, direction = load_web_catalogue()
    c = counts()
    ts = load_templates()
    out: list[str] = [doc_header("Arabic review pack (H2)", "a native Arabic speaker, 30 minutes")]

    out += [
        "## 0. How to use this pack in 30 minutes", "",
        table(["Minutes", "Section", "What to do"], [
            ["3", "1 and 2", "Read what the texts are for and the rules they follow."],
            ["5", "3 Conventions, terminology", "Look at the terminology table: which wording should win?"],
            ["12", "4 and 5 The strings", "Read each Arabic text beside its English. Mark what you would change."],
            ["5", "6 Forbidden phrases", "Is the Arabic list right and complete?"],
            ["5", "7 and 8 Checklist, feedback table", "Tick the checklist and fill the feedback table."],
        ]), "",
        f"There are {c['patient_strings_total']} Arabic texts (every one is also in English, with the same ID) and {c['arabic_pack']['arabic_forbidden_phrases']} Arabic forbidden-phrase entries. Every Arabic text in the prototype's sources is in this pack. All of it was written by the engineering team and has not been reviewed by a native speaker. Please correct freely; suggest better wording in the feedback table.", "",
    ]

    out += [
        "## 1. What these texts are for", "",
        "A patient in Abu Dhabi has had a screening stool test (FIT) and the care team has the result. The texts below tell the patient, in Arabic or English, that the result is ready and the care team needs to talk with them, who in the care team is responsible, and what the next step is, and let them say \"I understand, please contact me\" or \"I don't understand this\" (a person will call). They never say what the screening found: the care team explains the result in person or by phone.", "",
        "Hard rules the Arabic must keep:", "",
        "- No text states or hints at what the screening found (IFHAS: abnormal results are communicated with the participant present). No \"blood\", no \"positive\", no \"abnormal\", no diagnosis, no disease name.",
        "- No reassurance (\"don't worry\"), no judgement of urgency, no advice beyond the next step. The care team contacts the patient.",
        "- Every message names the care owner and the next step.",
        "- A person approves every message before it is sent.", "",
    ]

    out += ["## 2. Where each text appears and its limits", "",
            table(["Source", "Where", "Reading level", "Length limit"], [
                ["Patient page (IDs W01 to W%02d)" % len([i for i in items if i.source == "web"]), "Web app at `/patient`, Arabic with right-to-left layout", READING_LEVEL, "None on the page; short lines for a phone screen"],
                ["Messages (IDs T01 to T%02d)" % len([i for i in items if i.source == "template"]), "SMS, WhatsApp or letter, chosen from the patient's preferred channel", READING_LEVEL,
                 f"SMS {CHANNEL_MAX_CHARS['sms']}, WhatsApp {CHANNEL_MAX_CHARS['whatsapp']}, letter {CHANNEL_MAX_CHARS['letter']} characters"],
                ["Demo drafts (IDs D01 to D%02d)" % len([i for i in items if i.source == "draft"]), "Coordinator app, demo data only", READING_LEVEL, "None"],
            ]), "",
            "Placeholders in braces are filled in by the system: " + ", ".join(
                [f"`{{owner_name}}` (the care owner, for example {SAMPLE_VALUES['ar']['owner_name']})",
                 f"`{{next_step}}` (supplied in Arabic, default {DEFAULT_NEXT_STEP['ar']})",
                 f"`{{appointment_date}}` (for example {SAMPLE_VALUES['ar']['appointment_date']})",
                 "`{name}` and `{role}` on the patient page"]) + ".", ""]

    out += ["## 3. Conventions in use", "",
            f"**Right-to-left.** The patient page sets `lang=\"ar\"` and `dir=\"{direction['ar']}\"` on the page and the document when Arabic is chosen (English: `{direction['en']}`). Layout uses logical properties (start and end, not left and right), and the font list includes Noto Sans Arabic. The care owner's name is wrapped in a first-strong isolate (U+2068 to U+2069) so a Latin name does not scramble the neighbouring Arabic. SMS, WhatsApp and letter templates are plain text with no direction marks: a Latin `{{owner_name}}` inside a right-to-left sentence may display out of order on some phones. Please check this on a device.", "",
            f"**Digits.** Western digits (0 to 9) are used everywhere today, for example \"{SAMPLE_VALUES['ar']['appointment_date']}\". Arabic-Indic digits (٠ to ٩) are not used in any text. The system converts them only when checking for personal data (a long run of digits is blocked in either form). Which style should patients see? The appointment-date formatter is not built, so this is still open.", "",
            "**Dates and names.** The sample date uses Gregorian month and weekday names in the Gulf style (نوفمبر, الثلاثاء), not the Levantine month names. Titles such as \"الدكتورة\" are part of the owner's name, supplied by the clinic.", ""]
    tanween_web = sum(i.ar.count("ًا") for i in items if i.source != "template")
    tanween_tpl = sum(i.ar.count("اً") for i in items if i.source == "template")
    other_a = sum(i.ar.count("اً") for i in items if i.source != "template")
    other_b = sum(i.ar.count("ًا") for i in items if i.source == "template")
    comma = sum(i.ar.count("،") for i in items)
    out += [f"**Orthography we noticed.** The patient page and demo drafts write tanween as ـًا (for example تشخيصًا): {tanween_web} cases there against {other_a} of the other form. The templates write ـاً (for example دماً): {tanween_tpl} cases against {other_b} of the other form. The Arabic comma ، appears {comma} times (letter greetings and closings); sentences end with a Latin full stop. Choose one convention.", "",
            "**Terminology we use today.** Counts are generated from the texts below. Where one idea has several wordings, please choose one.", "",
            terms_table(items), ""]

    def side_by_side(rows_: list[Item], extra_header: str | None = None, extra=None) -> str:
        headers = ["ID", "English", "Arabic", "Where it appears"] if extra is None else ["ID", "English", "Arabic", extra_header]
        data = []
        for i in rows_:
            data.append([i.id, i.en, i.ar, i.where if extra is None else extra(i)])
        return table(headers, data)

    out += ["## 4. Patient page strings, by screen section", "",
            "Source: `web/src/i18n/strings.ts` (catalogue and role labels). Mark any string you would change.", ""]
    for gid, title, blurb in WEB_GROUPS:
        group_items = [i for i in items if i.source == "web" and i.group == title]
        if group_items:
            out += [f"### 4.{[g[0] for g in WEB_GROUPS].index(gid) + 1} {title}", "", blurb, "", side_by_side(group_items), ""]

    out += ["## 5. Messages, by purpose", "",
            "Source: `api/app/ai/templates/ar.yaml` beside `en.yaml`. Each purpose has an SMS, a WhatsApp message and a letter; the reminder also has a version with the appointment date. The Arabic is the default draft and the fallback if a model draft is rejected. The \"Chars\" column is the length with the sample values.", ""]
    for n, purpose in enumerate(PURPOSES, 1):
        tp = [i for i in items if i.source == "template" and i.group == purpose]
        out += [f"### 5.{n} {purpose}", "", PURPOSE_CONTEXT[purpose], "",
                table(["ID", "Channel", "English", "Arabic", "Chars en / ar, limit"],
                      [[i.id, CHANNEL_LABEL[template_entry(i, 'en').channel] + (" with date" if template_entry(i, 'en').with_date else ""),
                        i.en, i.ar, f"{sample_len(i, 'en')} / {sample_len(i, 'ar')}, {CHANNEL_MAX_CHARS[template_entry(i, 'en').channel]}"] for i in tp]), ""]
    out += [f"### 5.{len(PURPOSES) + 1} Demo draft texts (coordinator app)", "", MOCK_WHERE, "",
            side_by_side([i for i in items if i.source == "draft"]), ""]

    # forbidden
    out += ["## 6. Arabic forbidden phrases", "",
            "A model-drafted message containing any of these is rejected and replaced by the template above, so the model can never write them. The list is a keyword filter, not a proof of safety; a person also approves every message. Each entry says why it is blocked.", "",
            "How matching works (from the header of `api/app/ai/forbidden_phrases.yaml`):", "",
            "> " + forbidden_header().replace("\n", "\n> "), "",
            "Entries are stored in normalised spelling (no diacritics, bare alef, final ي). The Phrase column shows what is stored; the Example column shows a natural sentence it catches.", "",
            table(["Id", "Category", "Phrase (as stored)", "English gloss", "Why blocked", "Example caught"],
                  [[f"`{e['id']}`", e["category"], e["phrase"], AR_FORBIDDEN_GLOSS.get(e["id"], "-"),
                    " ".join(e["rationale"].split()), e["example"]] for e in f["ar"]]), "",
            "Questions for you: (a) Does any entry block an ordinary, harmless sentence? For example طبيعي (normal), علاج (treatment) inside a longer word, تناول (take) in a plain sense, صيام (fasting) in a message about Ramadan opening hours. (b) What is missing? Candidates we have not added and you may consider: مرض (disease), حالة حرجة (critical condition), لا بأس (it is fine), expressions such as إن شاء الله خير or الله يشفيك that patients may find natural but that reassure or imply illness. Our suggestions only; none is in the list today.", ""]
    hits = static_hits(items)
    ar_hits = [(it, ids) for it, lang, ids in hits if lang == "ar"]
    out += ["### Self-check on our own texts", "",
            "We ran the list over every Arabic text above. "
            + ("None matched." if not ar_hits else "These texts contain a forbidden entry. The patient page and demo drafts are fixed text, not model output, so the rule does not apply to them today, but please say whether the word is acceptable there."), ""]
    if ar_hits:
        out += [table(["ID", "Arabic", "Matching entries"], [[it.id, it.ar, ", ".join(f"`{i}`" for i in ids)] for it, ids in ar_hits]), ""]

    out += ["## 7. Checklist", ""]
    for title, text in AR_CHECKLIST:
        out += [f"- [ ] **{title}.** {text}"]
    out += [""]

    fb: list[list[object]] = []
    for i in items:
        fb.append([i.id, f"{i.source}: {i.key}", "", "", ""])
    for cat in dict.fromkeys(e["category"] for e in f["ar"]):
        ids = [e["id"] for e in f["ar"] if e["category"] == cat]
        fb.append([f"F:{cat}", f"Forbidden phrases, category `{cat}` ({len(ids)} entries: {', '.join(ids)})", "", "", ""])
    fb.append(["F:add", "Forbidden phrases to add", "", "", ""])
    for title, _ in AR_CHECKLIST:
        fb.append([f"C:{title}", f"Checklist: {title}", "", "", ""])
    fb.append(["X", "Anything missing (texts, phrases, concepts)", "", "", ""])
    out += ["## 8. Feedback table", "",
            "One row per text. Write keep or change; if change, write your Arabic wording and, if useful, why. The last rows cover the forbidden-phrase list (by category) and the checklist.", "",
            table(["ID", "What", "Keep / change", "Suggested Arabic", "Comment"], fb), "",
            "Reviewer name: ", "", "Date: ", ""]
    return "\n".join(out).rstrip() + "\n"


# ====================================================================== entry point


def build() -> dict[Path, str]:
    return {CLINICIAN_PACK: clinician_pack(), ARABIC_PACK: arabic_pack()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed packs differ from a fresh build")
    ap.add_argument("--counts", action="store_true", help="print string counts per pack as JSON")
    args = ap.parse_args(argv)
    if args.counts:
        print(json.dumps(counts(), indent=2, ensure_ascii=False))
        return 0
    packs = build()
    if args.check:
        stale = [str(p.relative_to(ROOT)) for p, text in packs.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print("review packs are out of date: " + ", ".join(stale) + ". Run `make review-packs`.", file=sys.stderr)
            return 1
        print("review packs are up to date")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, text in packs.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    c = counts()
    print(f"clinician pack: {c['clinician_pack']['english_patient_strings']} English patient strings, {c['clinician_pack']['english_forbidden_phrases']} forbidden phrases")
    print(f"arabic pack: {c['arabic_pack']['arabic_patient_strings']} Arabic patient strings, {c['arabic_pack']['arabic_forbidden_phrases']} forbidden phrases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
