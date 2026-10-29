"""M7-5: the fairness audit and the oversight report are deterministic, reproduce their sources and fail on drift."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RAI = ROOT / "tools" / "rai"
ENV = {**os.environ, "PYTHONPATH": f"{ROOT / 'api'}{os.pathsep}{ROOT}"}


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RAI / script), *args], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=300)


@pytest.mark.parametrize("script", ["fairness.py", "oversight.py"])
def test_committed_report_equals_a_fresh_run(script):
    p = run(script, "--check")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "PASS" in p.stdout


def test_fairness_reports_every_language_and_dialect_and_the_flags_carry_a_mitigation():
    data = json.loads((ROOT / "docs/results/fairness.json").read_text(encoding="utf-8"))
    assert set(data["triage"]["by_language"]) == {"all", "ar", "en", "arabizi", "mixed"}
    assert set(data["triage"]["by_dialect"]) == {"ar_msa", "ar_gulf"}
    assert {"language", "channel", "age_band"} <= set(data["risk"]["groups"])
    assert data["risk"]["source"]["excluded_features"] == ["language"]
    assert set(data["validator"]["false_negative_by_language"]) >= {"ar", "en", "arabizi"}
    # no opt-out may ever be auto-handled, in any group
    for g in [*data["triage"]["by_language"].values(), *data["triage"]["by_dialect"].values()]:
        assert g["opt_out_to_person"] == g["opt_out_n"]
    md = (ROOT / "docs/results/fairness.md").read_text(encoding="utf-8")
    for f in data["flags"]:
        assert f"| {f['area']} | {f['group']} | {f['metric']} |" in md


def test_fairness_refuses_scores_that_do_not_reproduce_the_committed_evaluation(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "api"))
    sys.path.insert(0, str(RAI))
    import fairness

    test = fairness.load_split("test")
    scores = json.loads((fairness.DATA / "triage_scores_test.json").read_text())
    cal = fairness.triage.Calibration.load()
    fairness.verify_against_eval(test, fairness.model_only(scores, cal))  # the committed scores pass
    scores[0] = {**scores[0], "logits": list(reversed(scores[0]["logits"]))}
    with pytest.raises(SystemExit, match="do not reproduce"):
        fairness.verify_against_eval(test, fairness.model_only(scores, cal))


def test_attack_language_labels():
    sys.path.insert(0, str(RAI))
    import fairness

    assert fairness.attack_language("RT-V-02.7", "3andak saratan", "en") == "arabizi"
    assert fairness.attack_language("RT-V-02.12", "You have سرطان.", "en") == "mixed"
    assert fairness.attack_language("RT-V-02.1", "عندك سرطان", "ar") == "ar"
    assert fairness.attack_language("RT-V-12.11", "{owner_name}", "ar") == "ar"
    assert fairness.attack_language("RT-V-01.1", "You have cancer.", "en") == "en"


def test_oversight_guarantee_holds_and_every_staff_draft_reached_a_person():
    data = json.loads((ROOT / "docs/results/oversight.json").read_text(encoding="utf-8"))
    g, d = data["guarantee"], data["drafts"]
    assert g["holds"] and g["ai_events_that_changed_state"] == [] and g["sent_without_prior_human_approval"] == []
    assert d["decided_by_a_person"] == d["requested_by_staff"]
    assert d["approved"] + d["rejected"] == d["requested_by_staff"]
    assert g["messages_sent_from_drafts"] == d["approved"]


def test_is_ai_covers_model_triage_and_agency_actors_only():
    sys.path.insert(0, str(RAI))
    import oversight

    assert oversight.is_ai("model:template") and oversight.is_ai("system:triage") and oversight.is_ai("system:agency:outreach")
    assert not oversight.is_ai("primary_care_owner:abc") and not oversight.is_ai("system:notifier")
