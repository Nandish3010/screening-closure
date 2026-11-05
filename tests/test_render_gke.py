"""The "Cloud run (GKE)" section of docs/results/scale.md (tools/k8s/render_gke.py): pending until a real run is imported, never rendered from a rehearsal."""

from __future__ import annotations

import json
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools" / "k8s"))
import render_gke  # noqa: E402


def test_pending_section_until_a_run_is_imported(tmp_path):
    lines = render_gke.build(tmp_path)
    text = "\n".join(lines)
    assert "not run: owner budget limit" in text and lines[0].startswith("# Cloud run (GKE)")
    assert "Nothing in this section has been measured yet" in text
    # the committed repository has no imported run yet, so the generated report says the same
    committed = (REPO_ROOT / "docs/results/scale.md").read_text(encoding="utf-8")
    if not (REPO_ROOT / "docs/results/scale/gke/run.json").exists():
        assert "not run: owner budget limit" in committed


def test_a_rehearsal_is_never_rendered_as_a_measurement(tmp_path):
    (tmp_path / "gke").mkdir()
    (tmp_path / "gke" / "run.json").write_text(json.dumps({"mode": "kind", "run_id": "x"}))
    assert "not run: owner budget limit" in "\n".join(render_gke.build(tmp_path))


def test_import_refuses_a_rehearsal_and_an_unfinished_run(tmp_path, monkeypatch):
    monkeypatch.setattr(render_gke, "GKE_DIR", tmp_path / "dest")
    src = tmp_path / "run"
    (src / "results").mkdir(parents=True)
    (src / "run.json").write_text(json.dumps({"mode": "kind", "exit_code": 0}))
    assert render_gke.import_run(src, force=True) == 1
    (src / "run.json").write_text(json.dumps({"mode": "gke", "exit_code": 0}))
    assert render_gke.import_run(src, force=False) == 1  # result files missing
    assert not (tmp_path / "dest").exists()


def test_scale_report_is_current():
    import render_scale

    assert render_scale.main(["--check"]) == 0
