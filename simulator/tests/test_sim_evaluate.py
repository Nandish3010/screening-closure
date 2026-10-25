import json

from simulator.sim import DEFAULT_PARAMS
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


def test_a_single_replication_reports_no_interval_not_a_zero_width_one(tmp_path):
    """SEC-2: with one replication there is no spread to bootstrap. The interval is null, not [mean, mean],
    and the results and their markdown still render."""
    ctx = load_context(warn=False)
    res = evaluate(jobs=1, **{**SMALL, "reps": 1})
    cell = res["arms"]["orchestrated"]["completion_90d"]
    assert cell["n_reps"] == 1 and cell["mean"] is not None
    assert cell["ci_low"] is None and cell["ci_high"] is None
    assert res["difference"]["completion_90d"]["ci_low"] is None
    write_results(res, tmp_path, ctx)
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["arms"]["orchestrated"]["completion_90d"]["ci_low"] is None
    assert (tmp_path / "results.md").read_text().startswith("# Evaluation results")
    # and with two or more replications the interval is there, as before
    many = evaluate(jobs=1, **{**SMALL, "reps": 2})["arms"]["orchestrated"]["completion_90d"]
    assert many["ci_low"] is not None and many["ci_low"] <= many["mean"] <= many["ci_high"]


def test_parameter_override_is_recorded_in_the_output_metadata(tmp_path):
    """T-B13: a layered --params file changes the numbers and is listed, key by key, in the results metadata."""
    over = tmp_path / "over.yaml"
    over.write_text("referral_rejection_rate: {value: 0.31, source: test override, status: sourced}\n")
    ctx = load_context(warn=False)
    default = evaluate(jobs=1, **SMALL)
    key = "referral_rejection_rate"
    default_value = next(p["value"] for p in default["parameters"] if p["key"] == key)
    assert "parameter_overrides" not in default["meta"]  # the default run is unchanged: no override, no new key

    layered = evaluate(jobs=1, params_paths=[DEFAULT_PARAMS, over], **SMALL)
    meta = layered["meta"]
    assert meta["params_hash"] != default["meta"]["params_hash"]
    assert any(f.endswith("over.yaml") for f in meta["params_files"])
    assert meta["parameter_overrides"] == [
        {"key": key, "value": 0.31, "default_value": default_value, "source": "test override", "status": "sourced"}
    ]
    assert layered["arms"] != default["arms"]

    write_results(layered, tmp_path / "out", ctx)
    on_disk = json.loads((tmp_path / "out" / "results.json").read_text())
    assert on_disk["meta"]["parameter_overrides"][0]["key"] == key


def test_cli_override_params_file_is_recorded(tmp_path, capsys):
    over = tmp_path / "over.yaml"
    over.write_text("referral_rejection_rate: {value: 0.31, source: test override, status: sourced}\n")
    out = tmp_path / "out"
    main(["evaluate", "--reps", "2", "--cases", "100", "--days", "100", "--seed", "7", "--bootstrap", "50", "--no-conservative",
          "--params", str(DEFAULT_PARAMS), "--params", str(over), "--out-dir", str(out)])
    meta = json.loads((out / "results.json").read_text())["meta"]
    assert [o["key"] for o in meta["parameter_overrides"]] == ["referral_rejection_rate"]
