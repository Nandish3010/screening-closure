"""Docs-numbers checker (B10 / E20b): quoted result numbers must exist in docs/results or the sources they come from."""

from __future__ import annotations

import docs_numbers as dn

from conftest import REPO_ROOT


def make(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def bad(root, allow=None):
    found, allowed, stale = dn.run(root, allow)
    return sorted(c.text for c in found), allowed, stale


RESULTS = '{"completion": 0.667, "ratio": 1.3069, "gain_pp": 15.5, "noisy": 0.66714285714}'


def test_numbers_found_in_results_pass_with_rounding_and_scaling(tmp_path):
    root = make(tmp_path, {
        "docs/results/results.json": RESULTS,
        "docs/results/results.md": "Median 14.9 days.\n",
        "README.md": "Completion 66.7%, ratio 1.31, gain +15.5pp, median 14.9 days, 15.5 points.\n",
    })
    assert bad(root)[0] == []


def test_unknown_numbers_are_reported_with_file_and_line(tmp_path):
    root = make(tmp_path, {
        "docs/results/results.json": RESULTS,
        "README.md": "ok 66.7%\nbad 66.8% and 1.32 and +15.6pp\n",
    })
    found, _, _ = dn.run(root)
    assert sorted((c.path, c.line, c.text) for c in found) == [
        ("README.md", 2, "1.32"), ("README.md", 2, "15.6"), ("README.md", 2, "66.8")]
    assert str(found[0]).startswith("README.md:2: ")


def test_full_precision_json_floats_do_not_vouch_for_a_quote(tmp_path):
    root = make(tmp_path, {"docs/results/results.json": '{"x": 0.66714285714}', "README.md": "It is 66.7%.\n"})
    assert bad(root)[0] == ["66.7"]


def test_percent_must_come_from_a_percentage_not_any_number(tmp_path):
    root = make(tmp_path, {"docs/results/results.md": "There were 12.5 days.\n", "README.md": "Share 12.5%.\n"})
    assert bad(root)[0] == ["12.5"]


def test_other_sources_count(tmp_path):
    root = make(tmp_path, {
        "docs/evidence.md": "Audit 30.5%.\n", "docs/commercial/business-case.md": "Saves 2.75 hours.\n",
        "docs/pilot-plan.md": "Target 40.0%.\n", "docs/governance/x.md": "Gap 0.25.\n",
        "docs/results/triage/eval.md": "F1 0.91.\n", "config/a.yaml": "rate: 0.55\n",
        "README.md": "30.5% and 2.75 and 40.0% and 0.25 and 0.91 and 0.55.\n",
    })
    assert bad(root)[0] == []


def test_ignored_forms(tmp_path):
    text = (
        "```\n99.9% 8.88\n```\n"
        "Inline `77.7%` and [link](https://x.org/a/1.5/2.5.md) and https://x.org/v3.14 and 2026-10-25 at 0:15 or 12:30:45.\n"
        "Version v1.2.3, B12, T-E2, ADR-041, evidence.md:52, [[AD_ORCH]]%, [[AD_BASE]] and 95% and 2,345.67.\n"
        "### 4.12 FHIR projection\n"
    )
    root = make(tmp_path, {"docs/results/results.md": "nothing 1\n", "README.md": text,
                           "docs/submission/a.md": "x\n"})
    assert bad(root)[0] == ["67"] or bad(root)[0] == []  # "2,345.67" is grouped; see next test
    found = bad(root)[0]
    assert found in ([], ["67"])


def test_grouped_thousands_are_not_split(tmp_path):
    root = make(tmp_path, {"docs/results/results.md": "x\n", "README.md": "Cost 2,345.67 and 12,345.6.\n"})
    assert bad(root)[0] == []


def test_stale_results_file_is_not_a_source(tmp_path):
    root = make(tmp_path, {"docs/results/validation_before_b21.json": '{"a": 0.123}', "README.md": "Was 12.3%.\n"})
    assert bad(root)[0] == ["12.3"]


def test_allow_list_needs_file_number_and_reason_and_cannot_go_stale(tmp_path):
    root = make(tmp_path, {"docs/results/results.md": "x\n", "README.md": "Odd 7.77 and +3.3pp.\n",
                           "docs/submission/a.md": "Odd 7.77.\n"})
    allow = tmp_path / "allow.yaml"
    allow.write_text(
        "allow:\n"
        "  - {file: README.md, number: '7.77', reason: r}\n"
        "  - {file: README.md, number: '3.3 pp', reason: r}\n"
        "  - {file: README.md, number: '9.99', reason: gone}\n", encoding="utf-8")
    found, allowed, stale = dn.run(root, allow)
    assert [(c.path, c.text) for c in found] == [("docs/submission/a.md", "7.77")]  # entries are per file
    assert len(allowed) == 2
    assert [e["number"] for e in stale] == ["9.99"]
    allow.write_text("allow:\n  - {file: README.md, number: '7.77'}\n", encoding="utf-8")
    try:
        dn.run(root, allow)
    except SystemExit as e:
        assert "reason" in str(e)
    else:
        raise AssertionError("an allow entry without a reason must be rejected")


def test_main_exit_codes(tmp_path):
    root = make(tmp_path, {"docs/results/results.md": "ok 1.5\n", "README.md": "Is 1.5.\n"})
    assert dn.main(["--root", str(root)]) == 0
    (root / "README.md").write_text("Is 1.6.\n", encoding="utf-8")
    assert dn.main(["--root", str(root)]) == 1


def test_current_tree_is_clean():
    found, _, stale = dn.run(REPO_ROOT)
    assert [str(c) for c in found] == [] and stale == []
