"""The generated test-count table in evaluation-summary.md (tools/check/evaluation_counts.py): rendering, splicing, staleness."""

from __future__ import annotations

import pytest

import run_check as rc  # noqa: F401  (conftest puts tools/check on the path)
import evaluation_counts as ec

COUNTS = {"test": 4561, "test-db": 328, "test-security": 73, "test-tools": 344, "test-redteam": 981, "web-test": 649, "web-e2e": 94}


def test_the_table_has_a_row_per_suite_with_thousands_separators_and_labels_the_counts_as_collected():
    out = ec.render(COUNTS)
    assert "| Tests collected |" in out and "4,561" in out and "`make test-redteam`" in out and "`make verify-quick`" in out
    assert out.count("\n") >= 11


def test_splice_replaces_only_the_marked_block():
    text = f"before\n{ec.START}\nold table\n{ec.END}\nafter\n"
    new = ec.splice(text, ec.block(COUNTS))
    assert new.startswith("before\n") and new.endswith("\nafter\n") and "old table" not in new and "4,561" in new
    assert ec.splice(new, ec.block(COUNTS)) == new  # idempotent


def test_a_document_without_the_markers_is_refused():
    with pytest.raises(SystemExit):
        ec.splice("no markers here", ec.block(COUNTS))


def test_the_check_fails_when_a_count_moved_and_passes_when_it_did_not(monkeypatch, tmp_path, capsys):
    doc = tmp_path / "evaluation-summary.md"
    doc.write_text(f"x\n{ec.START}\n{ec.END}\ny\n", encoding="utf-8")
    monkeypatch.setattr(ec, "DOC", doc)
    monkeypatch.setattr(ec, "rbac_probed", lambda: 1155)
    monkeypatch.setattr(ec, "collect", lambda: dict(COUNTS))
    assert ec.main(["--check"]) == 1  # empty block is out of date
    assert ec.main([]) == 0 and "4,561" in doc.read_text(encoding="utf-8")
    assert ec.main(["--check"]) == 0
    monkeypatch.setattr(ec, "collect", lambda: {**COUNTS, "test": 4562})  # one test added
    assert ec.main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().out


def test_the_committed_block_has_its_markers():
    text = ec.DOC.read_text(encoding="utf-8")
    assert ec.START in text and ec.END in text
