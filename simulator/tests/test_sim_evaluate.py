import json

from simulator.sim.cli import main
from simulator.sim.evaluate import evaluate, write_results
from simulator.sim.runner import load_context

SMALL = dict(reps=3, cases=200, days=200, seed=7, resamples=100)


def test_evaluate_is_byte_for_byte_reproducible_and_job_count_independent(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ctx = load_context(warn=False)
    write_results(evaluate(jobs=1, **SMALL), a, ctx)
    write_results(evaluate(jobs=2, **SMALL), b, ctx)
    assert (a / "results.json").read_bytes() == (b / "results.json").read_bytes()


def test_results_json_content_and_no_timestamps(tmp_path):
    ctx = load_context(warn=False)
    res = evaluate(jobs=1, **SMALL)
    write_results(res, tmp_path, ctx)
    text = (tmp_path / "results.json").read_text()
    data = json.loads(text)
    assert "generated_at" not in text
    meta = data["meta"]
    assert meta["seed"] == 7 and meta["replications"] == 3 and len(meta["params_hash"]) == 64
    assert meta["git_commit"] and "pathway_hash" in meta
    assert set(data["arms"]) == {"baseline", "orchestrated"}
    cell = data["arms"]["orchestrated"]["completion_90d"]
    assert cell["ci_low"] <= cell["mean"] <= cell["ci_high"]
    assert data["difference"]["completion_90d"]["mean"] > 0
    assert all({"key", "value", "source", "status"} <= set(p) for p in data["parameters"])
    assert json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n" == text  # sorted keys
    md = (tmp_path / "results.md").read_text()
    assert md.startswith("# Evaluation results") and "generated_at:" in md
    assert "## Fairness" in md and "## Messy-case handling" in md and "## Parameters" in md


def test_different_seed_changes_results():
    a = evaluate(jobs=1, **SMALL)
    b = evaluate(jobs=1, **{**SMALL, "seed": 8})
    assert a["arms"]["baseline"]["completion_90d"]["mean"] != b["arms"]["baseline"]["completion_90d"]["mean"]


def test_cli_run_smoke(capsys):
    rc = main(["run", "--arm", "both", "--cases", "100", "--days", "100", "--seed", "3", "--verify"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["orchestrated"]["invariant_violations"] == 0
    assert len(out["baseline"]["event_log_sha256"]) == 64
