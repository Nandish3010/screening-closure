"""Docs hygiene checker (part of T-E2 / F1): broken links, ragged tables, TODO markers."""

from __future__ import annotations

import docs_check as dc

from conftest import REPO_ROOT


def make(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def kinds(findings):
    return sorted(f.kind for f in findings)


def test_clean_docs_pass(tmp_path):
    root = make(tmp_path, {
        "docs/a.md": "# A\n\nSee [b](b.md#sec), [web](https://example.com), [anchor](#top) and ![i](img/x.png).\n\n"
                     "| a | b |\n|---|---|\n| 1 | 2 |\n",
        "docs/b.md": "text\n", "docs/img/x.png": "x", "project/P.md": "[a](../docs/a.md)\n",
    })
    assert dc.run(root) == []


def test_broken_relative_links_are_found(tmp_path):
    root = make(tmp_path, {
        "docs/a.md": "[gone](missing.md) and [ok](b.md) and ![img](nope.png)\n[ref]: ../project/gone.md\n",
        "docs/b.md": "x\n", "project/P.md": "[bad](../docs/zzz.md#frag)\n",
    })
    errors = dc.run(root)
    assert kinds(errors) == ["link"] * 4
    assert {f.path for f in errors} == {"docs/a.md", "project/P.md"}


def test_links_in_code_are_ignored(tmp_path):
    root = make(tmp_path, {"docs/a.md": "`[x](nope.md)`\n\n```\n[y](nope2.md)\n```\n"})
    assert dc.run(root) == []


def test_table_column_mismatch(tmp_path):
    root = make(tmp_path, {"docs/a.md": "| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |\n| 1 | 2 |\n| 1 | 2 | 3 | 4 |\n\n"
                                        "| ok | fine |\n|---|---|\n| esc \\| pipe | y |\n"})
    errors = dc.run(root)
    assert kinds(errors) == ["table", "table"]
    assert [f.line for f in errors] == [4, 5]


def test_separator_width_mismatch(tmp_path):
    root = make(tmp_path, {"docs/a.md": "| a | b |\n|---|\n| 1 | 2 |\n"})
    assert kinds(dc.run(root)) == ["table"]


def test_todo_is_always_an_error(tmp_path):
    root = make(tmp_path, {"docs/a.md": "TODO: write this\n", "docs/results/r.json": '{"source": "TODO"}\n'})
    assert kinds(dc.run(root)) == ["todo", "todo"]
    assert dc.main(["--root", str(root)]) == 1


def test_todo_exempt_on_review_marker_lines(tmp_path):
    root = make(tmp_path, {"docs/a.md": "- TODO after H1 clinician review\n- TODO until H2 Arabic review\n"
                                        "- TODOLIST is not a marker\n- todo lowercase is not a marker\n"})
    assert dc.run(root) == []


def test_cli_exit_codes(tmp_path, capsys):
    root = make(tmp_path, {"docs/a.md": "[x](missing.md)\n"})
    assert dc.main(["--root", str(root)]) == 1
    assert "broken relative link" in capsys.readouterr().out
    root = make(tmp_path / "ok", {"docs/a.md": "fine\n"})
    assert dc.main(["--root", str(root)]) == 0


def test_real_docs_links_and_tables_are_clean():
    errors = [f for f in dc.run(REPO_ROOT, include_readme=True) if f.kind != "todo"]
    assert errors == [], "\n".join(map(str, errors))
