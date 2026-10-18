"""T-E17: the reviewer packs are complete and in sync with their sources (E10).

* The committed packs equal a fresh build (so nobody edits them by hand or forgets to regenerate).
* Every Arabic string in the sources appears in the Arabic pack. The sources are read here with
  their own, simpler readers (regular expressions and PyYAML) so a bug in the builder's reader
  cannot hide a missing string.
* The clinician pack lists every state, event, time limit, rung, handoff reason, forbidden phrase
  and English patient string.

Run: `make test-review-packs`.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BUILDER = ROOT / "tools" / "review" / "build_packs.py"

spec = importlib.util.spec_from_file_location("build_packs", BUILDER)
bp = importlib.util.module_from_spec(spec)
sys.modules["build_packs"] = bp
spec.loader.exec_module(bp)

ARABIC = re.compile("[؀-ۿ]")
LITERAL_OR_COMMENT = re.compile(
    r"""(?P<lit>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(?P<comment>/\*.*?\*/|//[^\n]*)""",
    re.S,
)
INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def pack_text(path: Path) -> str:
    """The pack as a reader sees it: table line breaks restored."""
    return path.read_text(encoding="utf-8").replace("<br>", "\n")


def ts_arabic_pieces(path: Path) -> list[str]:
    """Arabic text in the string and template literals of a TypeScript file (comments ignored)."""
    pieces: list[str] = []
    for m in LITERAL_OR_COMMENT.finditer(path.read_text(encoding="utf-8")):
        lit = m.group("lit")
        if not lit:
            continue
        body = lit[1:-1].replace("\\n", "\n").replace('\\"', '"')
        for part in INTERPOLATION.split(body):
            part = part.strip()
            if ARABIC.search(part):
                pieces.append(part)
    return pieces


def py_arabic_literals(path: Path) -> list[str]:
    return [s for s in re.findall(r'"([^"\n]*)"', path.read_text(encoding="utf-8")) if ARABIC.search(s)]


def template_texts(language: str) -> list[str]:
    data = yaml.safe_load((ROOT / "api" / "app" / "ai" / "templates" / f"{language}.yaml").read_text(encoding="utf-8"))
    out = []
    for block in data["purposes"].values():
        out += [str(v).strip() for k, v in block.items() if k != "description"]
    return out


# ------------------------------------------------------------------------ sync with the sources


def test_committed_packs_equal_a_fresh_build():
    fresh = bp.build()
    for path, text in fresh.items():
        assert path.exists(), f"{path} is missing: run `make review-packs`"
        assert path.read_text(encoding="utf-8") == text, f"{path.name} is stale: run `make review-packs`"


def test_build_is_deterministic():
    assert bp.build() == bp.build()


def test_check_flag_agrees(capsys):
    assert bp.main(["--check"]) == 0


def test_every_arabic_string_in_the_sources_is_in_the_arabic_pack():
    pack = pack_text(bp.ARABIC_PACK)
    sources: dict[str, list[str]] = {
        "web/src/i18n/strings.ts": ts_arabic_pieces(ROOT / "web/src/i18n/strings.ts"),
        "web/src/api/fixtures.ts": ts_arabic_pieces(ROOT / "web/src/api/fixtures.ts"),
        "web/src/api/mockAdapter.ts": ts_arabic_pieces(ROOT / "web/src/api/mockAdapter.ts"),
        "api/app/ai/templates/ar.yaml": [t for t in template_texts("ar") if ARABIC.search(t)],
        "api/app/ai/templates.py": py_arabic_literals(ROOT / "api/app/ai/templates.py"),
    }
    forbidden = yaml.safe_load((ROOT / "api/app/ai/forbidden_phrases.yaml").read_text(encoding="utf-8"))["ar"]
    sources["forbidden_phrases.yaml (phrase)"] = [e["phrase"] for e in forbidden]
    sources["forbidden_phrases.yaml (example)"] = [e["example"] for e in forbidden]

    assert len(sources["web/src/i18n/strings.ts"]) >= 35, "the independent reader found too few catalogue strings"
    assert len(sources["api/app/ai/templates/ar.yaml"]) == 18
    missing = [(src, s) for src, strings in sources.items() for s in strings if s not in pack]
    assert not missing, f"Arabic strings missing from the Arabic pack: {missing}"


def test_every_english_patient_string_is_in_the_clinician_pack():
    pack = pack_text(bp.CLINICIAN_PACK)
    en, _, role_en, _, _ = bp.load_web_catalogue()
    expected = list(en.values()) + list(role_en.values()) + template_texts("en")
    expected += [e for _, e, _ in bp.load_mock_drafts()]
    missing = [s for s in expected if s.strip() not in pack]
    assert not missing, f"English strings missing from the clinician pack: {missing}"


def test_web_catalogue_has_the_same_keys_in_both_languages():
    en, ar, role_en, role_ar, direction = bp.load_web_catalogue()
    assert list(en) == list(ar) and len(en) == 42
    assert set(role_en) == set(role_ar)
    assert direction == {"en": "ltr", "ar": "rtl"}


# ------------------------------------------------------------------------ the clinician pack


@pytest.fixture(scope="module")
def pathway():
    return yaml.safe_load((ROOT / "config/pathways/colorectal_fit.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def clinician():
    return pack_text(bp.CLINICIAN_PACK)


def test_clinician_pack_lists_every_state_event_sla_rung_and_reason(pathway, clinician):
    for state in pathway["states"] + pathway["side_states"]:
        assert f"`{state}`" in clinician, state
    events = {t["event"] for t in pathway["transitions"]} | set(pathway["annotations"])
    for event in events:
        assert f"`{event}`" in clinician, event
    sla_section = clinician.split("## 5. Time limits (SLAs)")[1].split("## 6.")[0]
    for event, days in pathway["slas_working_days"].items():
        row = next(line for line in sla_section.splitlines() if line.startswith(f"| `{event}` |"))
        assert f"| {days} |" in row, (event, days)
    for rung in pathway["escalation_ladder"]:
        assert f"| `{rung['rung']}` |" in clinician
    for reason in pathway["handoff_reject_reasons"] + [pathway["handoff_bounce_reason"]]:
        assert f"| `{reason}` |" in clinician
    for event in pathway["human_only_events"]:
        assert event in clinician.split("Events listed as human only")[1].split("\n")[0]
    assert pathway["closure_rule"] in clinician


def test_clinician_pack_lists_the_national_timelines_with_their_sources(pathway, clinician):
    """A18: the milestones are in section 5, each with its allowance and where the figure comes from; the 31 is
    attributed to the National Guideline Appendix 2, not to the IFHAS standard."""
    section = clinician.split("### National timelines (milestones)")[1].split("## 6.")[0]
    assert set(pathway["milestones"]) == {"referral_requested", "confirmatory_completed"}
    for name, m in pathway["milestones"].items():
        row = next(line for line in section.splitlines() if line.startswith(f"| `{name}` |"))
        assert f"| {m['working_days']} |" in row and m["source"] in row, name
    assert "are **not** from IFHAS" in section and "Appendix 2" in section
    sla_table = clinician.split("## 5. Time limits (SLAs)")[1].split("### National timelines")[0]
    assert "| `complete_confirmatory` |" not in sla_table  # the colonoscopy clock is a milestone, not a per-step limit
    assert "M:referral_requested" in clinician and "M:confirmatory_completed" in clinician


def test_pathway_diagram_has_every_transition_and_is_mermaid(pathway, clinician):
    m = re.search(r"```mermaid\n(.*?)```", clinician, re.S)
    assert m, "no mermaid block"
    diagram = m.group(1)
    assert diagram.startswith("stateDiagram-v2")
    edges = [line for line in diagram.splitlines() if " --> " in line]
    expected = sum(len(t["from"]) if isinstance(t["from"], list) else 1 for t in pathway["transitions"])
    assert len(edges) == expected + 2  # plus the start and end markers
    for t in pathway["transitions"]:
        for f in (t["from"] if isinstance(t["from"], list) else [t["from"]]):
            assert any(line.strip().startswith(f"{f} --> ") and f": {t['event']}" in line for line in edges), (f, t["event"])


def test_clinician_pack_has_ten_numbered_questions_and_a_feedback_table(clinician):
    section = clinician.split("## 11. Ten questions we need answered")[1].split("## 12. Feedback table")[0]
    numbered = re.findall(r"^(\d+)\. \*\*", section, re.M)
    assert numbered == [str(n) for n in range(1, 11)]
    fb = clinician.split("## 12. Feedback table")[1]
    assert "| Item | What | Keep / change | Comment |" in fb
    for n in range(1, 11):
        assert f"| Q{n} |" in fb
    for phrase in ("lost contact", "opt-out", "31 working days", "evidence", "informed refusal"):
        assert phrase.lower() in clinician.lower(), phrase


def test_clinician_pack_has_every_english_forbidden_phrase():
    clinician = pack_text(bp.CLINICIAN_PACK)
    for e in yaml.safe_load((ROOT / "api/app/ai/forbidden_phrases.yaml").read_text(encoding="utf-8"))["en"]:
        assert f"`{e['id']}`" in clinician, e["id"]


def test_sla_working_days_stay_in_sync_with_the_web_mirror(pathway):
    ts = (ROOT / "web/src/lib/pathway.ts").read_text(encoding="utf-8")
    for event, days in pathway["slas_working_days"].items():
        if event == "reassign":
            continue  # the UI does not mirror the re-route limit
        assert re.search(rf"{event}:\s*{days}\b", ts), (event, days)


def test_milestones_stay_in_sync_with_the_web_mirror(pathway):
    ts = (ROOT / "web/src/lib/pathway.ts").read_text(encoding="utf-8")
    for name, m in pathway["milestones"].items():
        block = re.search(rf'name: "{name}".*?source: "([^"]+)"', ts, re.S)
        assert block, name
        assert f'from_event: "{m["from_event"]}"' in block.group(0), name
        assert f'until_event: "{m["until_event"]}"' in block.group(0), name
        assert f"working_days: {m['working_days']}" in block.group(0), name
        assert block.group(1) == m["source"], name


# ------------------------------------------------------------------------ the Arabic pack


def test_arabic_pack_has_context_conventions_checklist_and_feedback_table():
    pack = pack_text(bp.ARABIC_PACK)
    for needle in ("Reading level", "Length limit", "dir=\"rtl\"", "Arabic-Indic", "## 7. Checklist",
                   "## 8. Feedback table", "| ID | What | Keep / change | Suggested Arabic | Comment |"):
        assert needle in pack, needle
    checklist = pack.split("## 7. Checklist")[1].split("## 8. Feedback table")[0].lower()
    for word in ("accuracy", "gulf", "plain language", "stigma", "colonoscopy", "screening", "care team"):
        assert word in checklist, word
    items = bp.build_items()
    for item in items:
        assert f"| {item.id} |" in pack, item.id


def test_every_arabic_forbidden_phrase_has_a_rationale_in_the_arabic_pack():
    pack = pack_text(bp.ARABIC_PACK)
    for e in yaml.safe_load((ROOT / "api/app/ai/forbidden_phrases.yaml").read_text(encoding="utf-8"))["ar"]:
        row = next((line for line in pack.splitlines() if line.startswith(f"| `{e['id']}` |")), None)
        assert row, e["id"]
        assert " ".join(e["rationale"].split()) in row, e["id"]


def test_ids_match_between_packs():
    ids = [i.id for i in bp.build_items()]
    assert len(ids) == len(set(ids))
    cl, ar = pack_text(bp.CLINICIAN_PACK), pack_text(bp.ARABIC_PACK)
    for i in ids:
        assert f"| {i} |" in cl and f"| {i} |" in ar, i


# ------------------------------------------------------------------------ the builder itself


def test_a_state_without_a_gloss_stops_the_build(monkeypatch):
    monkeypatch.delitem(bp.STATE_GLOSS, "lost_contact")
    with pytest.raises(SystemExit, match="lost_contact"):
        bp.clinician_pack()


def test_a_catalogue_key_without_context_stops_the_build(monkeypatch):
    monkeypatch.delitem(bp.WEB_CONTEXT, "saved")
    with pytest.raises(SystemExit, match="saved"):
        bp.build_items()


def test_ts_reader_handles_the_literal_forms_used_by_the_catalogue():
    src = r'''
    // comment with "quotes"
    export const X: Record<string, unknown> = {
      a: "line\nbreak ا",
      list: ["one", `two ${iso(name)} and ${role}`,],
      nested: { k: (n, r) => `${iso(n)} (${r})` }, /* block */
    };'''
    got = bp.ts_const(src, "X")
    assert got["a"] == "line\nbreak ا"
    assert got["list"] == ["one", "two {name} and {role}"]
    assert got["nested"]["k"].text == "{n} ({r})"
    assert bp.flatten(got) == {"a": "line\nbreak ا", "list[0]": "one", "list[1]": "two {name} and {role}", "nested.k": "{n} ({r})"}
