#!/usr/bin/env python3
"""Evaluate the GGUF build of the reply-triage model (the hosted demo's) on the SAME splits and statistics as the MLX model.

    tools/triage/.venv/bin/python tools/triage/evaluate_gguf.py --gguf tools/triage/gguf/triage-q8_0.gguf --quant Q8_0 [--promote]

It starts a local llama-server on the GGUF file (CPU), scores the validation and test splits through `LlamaServerScorer` (the code the
hosted service runs), and reuses tools/triage/evaluate.py for everything else, unchanged: one temperature fitted on validation, the
abstain threshold chosen on validation for 97% precision of what the system would handle itself, the report on TEST. Then, through the
DEPLOYED decision path (opt-out gate, calibration, dialect group rule: tools/rai/fairness.py), the per-language and per-dialect results.

It writes, per quantization: docs/results/triage/eval-gguf-<quant>.md and .json, and (with --promote) api/app/triage/calibration_gguf.json,
the calibration the hosted service loads (TRIAGE_CALIBRATION_PATH). The MLX files (eval.json, calibration.json, the adapter) are never
touched. `--reuse-scores` re-fits from the cached scores without a server.

The MLX numbers it compares with are the committed docs/results/triage/eval.json; the GGUF scores come from a different runtime on a
re-quantized fused model, so a drop is reported as measured, not explained away.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "tools" / "rai"))
import evaluate as ev  # noqa: E402

WORK = HERE / "adapters_work" / "gguf"
OUT_DIR = ROOT / "docs" / "results" / "triage"
MLX_EVAL = OUT_DIR / "eval.json"
MLX_CAL = ROOT / "api" / "app" / "triage" / "calibration.json"
PROMOTED = ROOT / "api" / "app" / "triage" / "calibration_gguf.json"
ADAPTER = ROOT / "api" / "app" / "triage" / "adapters" / "adapters.safetensors"
PORT = 8099


def start_server(gguf: Path, threads: int) -> subprocess.Popen:
    exe = shutil.which("llama-server")
    if not exe:
        raise SystemExit("llama-server not found (brew install llama.cpp)")
    proc = subprocess.Popen([exe, "-m", str(gguf), "--host", "127.0.0.1", "--port", str(PORT), "-c", "2048", "-t", str(threads)],  # noqa: S603
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(180):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:  # noqa: BLE001
            time.sleep(1)
    proc.kill()
    raise SystemExit("llama-server did not become healthy in 3 minutes")


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def delta(a: float, b: float) -> str:
    return f"{100 * (b - a):+.1f} pt"


def deployed_groups(test, val, t_scores, v_scores, cal_path: Path) -> dict:
    """The per-language and per-dialect results through the deployed path, with and without the dialect group rule, and the
    Arabizi threshold this build's validation split fits (fairness.py's own fitting function)."""
    os.environ["TRIAGE_CALIBRATION_PATH"] = str(cal_path)
    import fairness as fr  # noqa: PLC0415
    from app.triage import classify as triage  # noqa: PLC0415
    from app.triage import dialect  # noqa: PLC0415

    cal = triage.Calibration.load()
    groups, dialects = {}, {}

    def build(results):
        g = {"all": fr.group_metrics(test, results)}
        for lang in ("ar", "en", "arabizi", "mixed"):
            idx = [i for i, row in enumerate(test) if row["language"] == lang]
            g[lang] = fr.group_metrics([test[i] for i in idx], [results[i] for i in idx])
        d = {}
        for variant in ("ar_msa", "ar_gulf"):
            idx = [i for i, row in enumerate(test) if row["variant"] == variant]
            d[variant] = fr.group_metrics([test[i] for i in idx], [results[i] for i in idx])
        return g, d

    rule = dialect.apply_group_rule
    dialect.apply_group_rule = lambda result, text, thresholds=None: result  # the global rule alone
    try:
        without_g, without_d = build(fr.replay(test, t_scores))
    finally:
        dialect.apply_group_rule = rule
    with_g, with_d = build(fr.replay(test, t_scores))
    v_idx = [i for i, row in enumerate(val) if row["language"] == dialect.ARABIZI]
    fitted = fr.triage_threshold_for([val[i] for i in v_idx], [v_scores[i] for i in v_idx], cal, ev.VAL_PRECISION_TARGET)
    return {"with_rule": {"by_language": with_g, "by_dialect": with_d}, "without_rule": {"by_language": without_g, "by_dialect": without_d},
            "arabizi_threshold_in_force": (cal.group_thresholds or dialect.GROUP_THRESHOLDS).get(dialect.ARABIZI),
            "arabizi_threshold_fitted_on_this_build": fitted, "mlx_arabizi_threshold": dialect.GROUP_THRESHOLDS[dialect.ARABIZI]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gguf", type=Path)
    ap.add_argument("--quant", required=True, help="label of this build, e.g. Q8_0 or Q5_K_M")
    ap.add_argument("--reuse-scores", action="store_true")
    ap.add_argument("--promote", action="store_true", help="write api/app/triage/calibration_gguf.json (the hosted service's calibration)")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = ap.parse_args(argv)
    quant = args.quant.lower()
    cache = WORK / quant
    cache.mkdir(parents=True, exist_ok=True)
    val, test = ev.load("val"), ev.load("test")
    ev.WORK = cache  # score_split caches here, apart from the MLX scores

    proc = None
    scorer = None
    if not (args.reuse_scores and (cache / "scores_val.json").exists() and (cache / "scores_test.json").exists()):
        if not args.gguf or not args.gguf.is_file():
            raise SystemExit("--gguf FILE is required unless --reuse-scores finds cached scores")
        from app.triage.scorer import LlamaServerScorer  # noqa: PLC0415

        proc = start_server(args.gguf, args.threads)
        scorer = LlamaServerScorer(f"http://127.0.0.1:{PORT}")
    try:
        v_scores = ev.score_split(scorer, val, "val", args.reuse_scores)
        t_scores = ev.score_split(scorer, test, "test", args.reuse_scores)
    finally:
        if proc:
            proc.kill()

    mlx = json.loads(MLX_EVAL.read_text())
    mlx_cal = json.loads(MLX_CAL.read_text())
    # (1) this build's scores with the MLX temperature and threshold unchanged: the "do nothing" case
    keep = ev.predictions(t_scores, mlx_cal["temperature"])
    keep_flags = ev.auto_handled(keep, t_scores, mlx_cal["threshold"])
    keep_ok = sum(1 for f, r, (p, _) in zip(keep_flags, test, keep) if f and p == r["label"])
    keep_ece, _ = ev.ece(test, keep)
    # (2) re-fitted the documented way: temperature on validation, threshold on validation for 97%
    temperature = ev.fit_temperature(val, v_scores)
    threshold = ev.choose_threshold(val, ev.predictions(v_scores, temperature), v_scores)
    markdown, machine = ev.build_report(ev.load("train"), val, test, v_scores, t_scores, temperature, threshold, {}, ADAPTER.stat().st_size,
                                        args.gguf.stat().st_size if args.gguf and args.gguf.is_file() else 0, {})
    adapter_hash = hashlib.sha256(ADAPTER.read_bytes()).hexdigest()[:8]
    model_id = f"qwen2.5-1.5b-{quant}-gguf+lora-{adapter_hash}"

    cal_candidate = {
        "model_id": model_id, "base_model": "Qwen2.5-1.5B-Instruct (4-bit MLX weights, LoRA merged, converted to GGUF)", "quantization": args.quant,
        "temperature": temperature, "threshold": threshold, "mass_floor": ev.MASS_FLOOR, "labels": list(ev.INTENTS),
        "auto_handled": sorted(ev.AUTO_HANDLED), "always_human": sorted(ev.ALWAYS_HUMAN), "val_precision_target": ev.VAL_PRECISION_TARGET,
        "test_precision": machine["auto_precision"], "test_coverage": machine["coverage"], "synthetic_data_only": True,
    }
    cal_path = cache / "calibration_candidate.json"
    cal_path.write_text(json.dumps(cal_candidate, indent=2, sort_keys=True) + "\n")
    groups = deployed_groups(test, val, t_scores, v_scores, cal_path)
    fitted = groups["arabizi_threshold_fitted_on_this_build"]
    # the Arabizi group threshold is fitted per build; if this build's differs from the MLX one it travels with the calibration
    if fitted is not None and fitted != groups["mlx_arabizi_threshold"]:
        cal_candidate["group_thresholds"] = {"arabizi": fitted, "ar_gulf": None}
        cal_path.write_text(json.dumps(cal_candidate, indent=2, sort_keys=True) + "\n")
        groups = deployed_groups(test, val, t_scores, v_scores, cal_path)

    rows = [
        ("Accuracy (test)", pct(mlx["accuracy"]), pct(keep_acc := sum(1 for r, (p, _) in zip(test, keep) if r["label"] == p) / len(test)), pct(machine["accuracy"])),
        ("Macro F1 (test)", f"{mlx['macro_f1']:.3f}", f"{ev.macro_f1(ev.prf(test, keep)):.3f}", f"{machine['macro_f1']:.3f}"),
        ("Auto-handled precision (test)", pct(mlx["auto_precision"]), pct(keep_ok / sum(keep_flags) if sum(keep_flags) else None), pct(machine["auto_precision"])),
        ("Coverage: handled without a person (test)", pct(mlx["coverage"]), pct(sum(keep_flags) / len(test)), pct(machine["coverage"])),
        ("ECE calibrated (test)", f"{mlx['ece_calibrated']:.3f}", f"{keep_ece:.3f}", f"{machine['ece_calibrated']:.3f}"),
        ("Opt-outs leaked to automatic handling", f"{mlx['opt_out_leaked']} of {mlx['opt_out_total']}", "see json", f"{machine['opt_out_leaked']} of {machine['opt_out_total']}"),
        ("Temperature", str(mlx["temperature"]), str(mlx["temperature"]), str(temperature)),
        ("Abstain threshold", str(mlx["threshold"]), str(mlx["threshold"]), str(threshold)),
    ]
    md = [f"# Reply triage: the GGUF build ({args.quant}) against the MLX model\n",
          "The hosted demo cannot run MLX. This is the same fine-tuned Qwen2.5-1.5B + LoRA, merged and converted to GGUF "
          f"(`{model_id}`) and run on the CPU by llama.cpp on our own server: no third-party AI. It is a **different artefact** from the MLX model "
          "(the 4-bit weights are written back as fp16 with the adapter merged, then quantized again), so it is evaluated here on the same "
          "synthetic splits, with the same scripts (`tools/triage/evaluate.py` statistics, `tools/rai/fairness.py` group results). "
          "Synthetic data only; split by template.\n",
          "## Before and after (test split)\n", "| Metric | MLX (shipped, before) | GGUF, MLX calibration unchanged | GGUF, re-fitted (after, shipped on the host) |", "|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    md += ["", f"Re-fitted the documented way (one temperature on validation by minimum NLL; the smallest threshold at which the replies the system "
           f"would handle by itself reach 97% precision on validation): temperature {temperature}, threshold {threshold}.", "",
           "## Per language, through the deployed decision path (opt-out gate, calibration, dialect group rule)\n",
           "| Group | n | accuracy | handled without a person | auto precision (rule on) | auto precision (rule off) |", "|---|---|---|---|---|---|"]
    for name in ("all", "ar", "en", "arabizi", "mixed"):
        w, wo = groups["with_rule"]["by_language"][name], groups["without_rule"]["by_language"][name]
        md.append(f"| {name} | {w['n']} | {pct(w['accuracy'])} | {pct(w['coverage'])} | {pct(w['auto_precision'])} ({w['wrong_automatic_steps']} wrong of {w['auto_handled_n']}) | {pct(wo['auto_precision'])} ({wo['wrong_automatic_steps']} wrong of {wo['auto_handled_n']}) |")
    md += ["", "## Arabic dialect\n", "| Variant | n | accuracy | handled without a person | auto precision (rule on) | auto precision (rule off) |", "|---|---|---|---|---|---|"]
    for name in ("ar_msa", "ar_gulf"):
        w, wo = groups["with_rule"]["by_dialect"][name], groups["without_rule"]["by_dialect"][name]
        md.append(f"| {name} | {w['n']} | {pct(w['accuracy'])} | {pct(w['coverage'])} | {pct(w['auto_precision'])} | {pct(wo['auto_precision'])} |")
    md += ["", f"Dialect rule (`api/app/triage/dialect.py`): Gulf dialect always to a person; Arabizi auto-handled only above its own threshold. "
           f"MLX value {groups['mlx_arabizi_threshold']}; fitted on this build's validation Arabizi split: {fitted}; in force on the host: "
           f"{groups['arabizi_threshold_in_force']}.", "",
           "Caveat: the Arabic was written by developers, not by Arabic-speaking clinicians; no native-speaker review of this build has been done. "
           "Per-group intervals are wide (see eval-gguf json). These numbers describe generalisation to new templates of the same synthetic kinds "
           "of message, not performance on real patients.", ""]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"eval-gguf-{quant}.md").write_text("\n".join(md), encoding="utf-8")
    machine.update(model_id=model_id, quantization=args.quant, groups=groups, mlx_calibration_unchanged={
        "accuracy": keep_acc, "auto_precision": keep_ok / sum(keep_flags) if sum(keep_flags) else None, "coverage": sum(keep_flags) / len(test), "ece_calibrated": keep_ece})
    (OUT_DIR / f"eval-gguf-{quant}.json").write_text(json.dumps(machine, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if args.promote:
        shutil.copy(cal_path, PROMOTED)
        print(f"promoted {PROMOTED.relative_to(ROOT)} ({model_id})")
    print(f"{args.quant}: accuracy {pct(machine['accuracy'])} (MLX {pct(mlx['accuracy'])}), auto precision {pct(machine['auto_precision'])}, "
          f"coverage {pct(machine['coverage'])}, temperature {temperature}, threshold {threshold}; wrote eval-gguf-{quant}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
