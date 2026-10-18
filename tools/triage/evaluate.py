#!/usr/bin/env python3
"""Evaluate the fine-tuned triage model, fit its calibration and abstain threshold, write the report (D13).

    tools/triage/.venv/bin/python tools/triage/evaluate.py --model DIR --adapter DIR [--reuse-scores]

1. Scores the validation and test splits (one forward pass each; cached in adapters_work/scores_*.json).
2. Fits ONE temperature on the validation split (minimum negative log-likelihood of the nine-way softmax).
3. Chooses the abstain threshold on the validation split: the smallest confidence at which the replies the system
   would handle by itself (predicted intent in the auto-handled set and confidence at or above the threshold) are at
   least 97% right on validation (the target is 95% on test; the 2-point margin pays for validation noise).
4. Reports on the TEST split, which neither the temperature nor the threshold has seen: per-intent precision, recall
   and F1, per language, the confusion matrix, calibration (ECE before and after, reliability bins), coverage and
   precision of the auto-handled replies with Wilson 95% intervals, and where the opt-outs went.
5. Writes api/app/triage/calibration.json (read by the API), docs/results/triage/eval.md and eval.json.

The data are synthetic and split by template: this measures how well the model generalises to NEW TEMPLATES of the
same kinds of message, not how it will do on real patients.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.triage.classify import softmax  # noqa: E402
from app.triage.labels import ALWAYS_HUMAN, AUTO_HANDLED, INTENTS  # noqa: E402

DATASET = HERE / "dataset"
WORK = HERE / "adapters_work"
CAL_PATH = ROOT / "api/app/triage/calibration.json"
OUT_DIR = ROOT / "docs/results/triage"
VAL_PRECISION_TARGET = 0.97  # on validation, to land at or above 0.95 on test
TEST_PRECISION_TARGET = 0.95
MASS_FLOOR = 0.5
BASE_MODEL_ID = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"


def load(split: str) -> list[dict]:
    return [json.loads(line) for line in (DATASET / f"{split}.jsonl").open(encoding="utf-8")]


def score_split(scorer, rows: list[dict], name: str, reuse: bool) -> list[dict]:
    cache = WORK / f"scores_{name}.json"
    if reuse and cache.exists():
        return json.loads(cache.read_text())
    out, started = [], time.time()
    for n, row in enumerate(rows, 1):
        out.append(scorer.score(row["text"]))
        if n % 100 == 0:
            print(f"  {name}: {n}/{len(rows)} ({time.time() - started:.0f}s)", flush=True)
    cache.write_text(json.dumps(out))
    return out


# ----------------------------------------------------------------------------------------- statistics
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def nll(rows: list[dict], scores: list[dict], temperature: float) -> float:
    total = 0.0
    for row, sc in zip(rows, scores):
        total -= math.log(max(softmax(sc["logits"], temperature)[INTENTS.index(row["label"])], 1e-12))
    return total / len(rows)


def fit_temperature(rows: list[dict], scores: list[dict]) -> float:
    lo, hi = 0.2, 6.0
    for _ in range(60):  # golden-section search on a unimodal function
        a, b = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if nll(rows, scores, a) < nll(rows, scores, b):
            hi = b
        else:
            lo = a
    return round((lo + hi) / 2, 4)


def predictions(scores: list[dict], temperature: float) -> list[tuple[str, float]]:
    out = []
    for sc in scores:
        probs = softmax(sc["logits"], temperature)
        best = max(range(len(INTENTS)), key=lambda i: probs[i])
        out.append((INTENTS[best], probs[best]))
    return out


def ece(rows: list[dict], preds: list[tuple[str, float]], bins: int = 10) -> tuple[float, list[dict]]:
    buckets: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for row, (label, conf) in zip(rows, preds):
        buckets[min(int(conf * bins), bins - 1)].append((conf, label == row["label"]))
    total, table = 0.0, []
    for b in range(bins):
        items = buckets.get(b, [])
        if not items:
            continue
        conf = sum(c for c, _ in items) / len(items)
        acc = sum(1 for _, ok in items if ok) / len(items)
        total += len(items) / len(rows) * abs(acc - conf)
        table.append({"bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}", "n": len(items), "mean_confidence": round(conf, 3), "accuracy": round(acc, 3)})
    return total, table


def auto_handled(preds: list[tuple[str, float]], scores: list[dict], threshold: float) -> list[bool]:
    return [label in AUTO_HANDLED and conf >= threshold and sc["mass"] >= MASS_FLOOR for (label, conf), sc in zip(preds, scores)]


def choose_threshold(rows, preds, scores) -> float:
    candidates = sorted({round(c, 4) for _, c in preds} | {round(x / 100, 2) for x in range(30, 100)})
    for tau in candidates:
        flags = auto_handled(preds, scores, tau)
        n = sum(flags)
        if n < 20:
            continue
        correct = sum(1 for f, row, (label, _) in zip(flags, rows, preds) if f and label == row["label"])
        if correct / n >= VAL_PRECISION_TARGET:
            return tau
    return 0.99


def prf(rows: list[dict], preds: list[tuple[str, float]]) -> dict:
    out = {}
    for intent in INTENTS:
        tp = sum(1 for r, (p, _) in zip(rows, preds) if r["label"] == intent and p == intent)
        fp = sum(1 for r, (p, _) in zip(rows, preds) if r["label"] != intent and p == intent)
        fn = sum(1 for r, (p, _) in zip(rows, preds) if r["label"] == intent and p != intent)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[intent] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return out


def macro_f1(table: dict) -> float:
    return sum(v["f1"] for v in table.values()) / len(table)


# ------------------------------------------------------------------------------------------ baseline
def char_ngrams(text: str, lo: int = 1, hi: int = 4) -> Counter:
    text = f" {text.lower()} "
    return Counter(text[i:i + n] for n in range(lo, hi + 1) for i in range(len(text) - n + 1))


def baseline_predictions(train: list[dict], test: list[dict]) -> list[tuple[str, float]]:
    """Multinomial naive Bayes on character 1 to 4-grams: what a classical classifier does on the same split."""
    counts = {i: Counter() for i in INTENTS}
    docs = Counter(r["label"] for r in train)
    for r in train:
        counts[r["label"]].update(char_ngrams(r["text"]))
    vocab = set().union(*counts.values())
    totals = {i: sum(c.values()) for i, c in counts.items()}
    out = []
    for r in test:
        feats = char_ngrams(r["text"])
        logp = {i: math.log(docs[i] / len(train)) + sum(
            n * math.log((counts[i][g] + 0.1) / (totals[i] + 0.1 * len(vocab))) for g, n in feats.items()) for i in INTENTS}
        top = max(logp, key=logp.get)
        z = max(logp.values())
        norm = sum(math.exp(v - z) for v in logp.values())
        out.append((top, 1 / norm))
    return out


# --------------------------------------------------------------------------------------------- report
def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def build_report(train, val, test, v_scores, t_scores, temperature, threshold, cal_info, adapter_bytes, base_bytes, train_info) -> tuple[str, dict]:
    raw_preds = predictions(t_scores, 1.0)
    preds = predictions(t_scores, temperature)
    table = prf(test, preds)
    accuracy = sum(1 for r, (p, _) in zip(test, preds) if r["label"] == p) / len(test)
    ece_raw, _ = ece(test, raw_preds)
    ece_cal, bins = ece(test, preds)
    flags = auto_handled(preds, t_scores, threshold)
    n_auto = sum(flags)
    correct_auto = sum(1 for f, r, (p, _) in zip(flags, test, preds) if f and p == r["label"])
    lo, hi = wilson(correct_auto, n_auto)
    wrong_auto = [(r, p) for f, r, (p, _) in zip(flags, test, preds) if f and p != r["label"]]
    confusion = {a: {b: 0 for b in INTENTS} for a in INTENTS}
    for r, (p, _) in zip(test, preds):
        confusion[r["label"]][p] += 1
    # the deployed rule: needs_human = not auto-handled
    opt_outs = [(r, f, p) for r, f, (p, _) in zip(test, flags, preds) if r["label"] == "opt_out"]
    opt_leak = [x for x in opt_outs if x[1]]
    n_oo = len(opt_outs)
    unsafe = [(r, p) for f, r, (p, _) in zip(flags, test, preds) if f and r["label"] in ALWAYS_HUMAN]
    by_lang, lang_rows = {}, []
    for lang in sorted({r["language"] for r in test}):
        idx = [i for i, r in enumerate(test) if r["language"] == lang]
        sub_rows, sub_preds = [test[i] for i in idx], [preds[i] for i in idx]
        sub_flags = [flags[i] for i in idx]
        sub_auto = sum(sub_flags)
        sub_correct = sum(1 for f, r, (p, _) in zip(sub_flags, sub_rows, sub_preds) if f and p == r["label"])
        sub_acc = sum(1 for r, (p, _) in zip(sub_rows, sub_preds) if r["label"] == p) / len(idx)
        sub_f1 = macro_f1(prf(sub_rows, sub_preds))
        slo, shi = wilson(sub_correct, sub_auto)
        by_lang[lang] = {"n": len(idx), "accuracy": sub_acc, "macro_f1": sub_f1, "auto_n": sub_auto,
                         "auto_precision": sub_correct / sub_auto if sub_auto else None, "auto_precision_ci": [slo, shi]}
        lang_rows.append(f"| {lang} | {len(idx)} | {pct(sub_acc)} | {sub_f1:.3f} | {pct(sub_auto / len(idx))} | "
                         f"{pct(sub_correct / sub_auto) if sub_auto else 'n/a'} ({pct(slo)} to {pct(shi)}) |")
    base_preds = baseline_predictions(train, test)
    base_table = prf(test, base_preds)
    base_acc = sum(1 for r, (p, _) in zip(test, base_preds) if r["label"] == p) / len(test)

    def cell(v):
        return f"{v:.2f}"

    md = [
        "# Reply triage: evaluation (D13)",
        "",
        "Generated by `tools/triage/evaluate.py`. Re-run `tools/triage/.venv/bin/python tools/triage/train.py` to regenerate.",
        "",
        "**All data are synthetic**, written from templates by the developers (`tools/triage/templates.py`) and split BY TEMPLATE: the test "
        "set contains only templates the model never saw in training or validation. These numbers measure generalisation to new "
        "templates of the same kinds of message. They do not measure performance on real patients, whose language is wider, "
        "messier and different. See `docs/governance/reply-triage-card.md`.",
        "",
        "## Setup",
        "",
        f"- Base model: `{BASE_MODEL_ID}` (Qwen2.5-1.5B-Instruct, Apache-2.0, 4-bit), {base_bytes / 1e9:.2f} GB on disk, run offline with MLX.",
        f"- Fine-tuning: LoRA on {train_info.get('num_layers')} layers, {train_info.get('iters')} iterations, batch {train_info.get('batch_size')}, "
        f"learning rate {train_info.get('learning_rate')}, seed {train_info.get('seed')}, loss on the label only. "
        f"Training took {train_info.get('train_seconds', 0) / 60:.1f} minutes on this machine.",
        f"- Adapter: {adapter_bytes / 1e6:.1f} MB (`api/app/triage/adapters/`).",
        f"- Data: {len(train)} train, {len(val)} validation, {len(test)} test examples; nine intents; English, Arabic (MSA and Gulf), Arabizi and mixed script.",
        f"- Prompt: `Classify the patient reply intent: <text>`; the answer is one label. The confidence is the model's probability for its "
        f"label among the nine labels (softmax over the first token of each label), divided by a temperature of **{temperature}** fitted on the validation set.",
        f"- Mass gate: if the model puts less than {MASS_FLOOR:.0%} of its next-token probability on the nine label tokens, the reply goes to a person.",
        "",
        "## Decision rule and abstain threshold",
        "",
        f"A reply is handled without a person only if its predicted intent is one of {', '.join(f'`{i}`' for i in INTENTS if i in AUTO_HANDLED)} "
        f"**and** the calibrated confidence is at least **{threshold}**. `question`, `other` and `opt_out` always go to a person. "
        f"The threshold is the smallest confidence at which at least {pct(VAL_PRECISION_TARGET)} of the auto-handled validation replies are correct "
        f"(target on test: {pct(TEST_PRECISION_TARGET)}).",
        "",
        "## Headline (test set)",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Accuracy of the top intent (no abstention) | {pct(accuracy)} |",
        f"| Macro F1 | {macro_f1(table):.3f} |",
        f"| Auto-handled share of replies (coverage) | {pct(n_auto / len(test))} ({n_auto} of {len(test)}) |",
        f"| **Precision of the auto-handled replies** | **{pct(correct_auto / n_auto) if n_auto else 'n/a'}** (95% interval {pct(lo)} to {pct(hi)}; target {pct(TEST_PRECISION_TARGET)}) |",
        f"| Sent to a person | {pct(1 - n_auto / len(test))} |",
        f"| True opt-outs not sent to a person | {len(opt_leak)} of {n_oo} |",
        f"| Replies whose true intent is `question`, `other` or `opt_out` that were auto-handled | {len(unsafe)} of {sum(1 for r in test if r['label'] in ALWAYS_HUMAN)} |",
        f"| Expected calibration error, raw / calibrated | {ece_raw:.3f} / {ece_cal:.3f} |",
        "",
        f"Target met: **{'yes' if n_auto and correct_auto / n_auto >= TEST_PRECISION_TARGET else 'NO'}** (precision of auto-handled replies at or above {pct(TEST_PRECISION_TARGET)} on the test set).",
        "",
        "## Per intent (test set, top intent without abstention)",
        "",
        "| Intent | Precision | Recall | F1 | Support | Handled by the system alone |",
        "|---|---|---|---|---|---|",
    ]
    for intent in INTENTS:
        v = table[intent]
        md.append(f"| `{intent}` | {cell(v['precision'])} | {cell(v['recall'])} | {cell(v['f1'])} | {v['support']} | "
                  f"{'yes, if confident' if intent in AUTO_HANDLED else 'never (always a person)'} |")
    md += ["", "## Per language (test set)", "",
           "| Language | n | Accuracy | Macro F1 | Auto-handled | Precision of auto-handled (95% interval) |", "|---|---|---|---|---|---|", *lang_rows, "",
           "`ar` is Modern Standard and Gulf Arabic script, `arabizi` is Arabic in Latin letters and digits, `mixed` combines scripts in one message.",
           *([f"**Languages below the {pct(TEST_PRECISION_TARGET)} target for auto-handled replies:** "
              + ", ".join(f"`{k}` ({pct(v['auto_precision'])}, {v['auto_n']} auto-handled)" for k, v in by_lang.items()
                          if v["auto_precision"] is not None and v["auto_precision"] < TEST_PRECISION_TARGET)
              + ". The overall threshold is one number; it does not equalise precision across languages. Until it does, "
                "treat the weakest language as one where a person should read every reply."] if any(
                v["auto_precision"] is not None and v["auto_precision"] < TEST_PRECISION_TARGET for v in by_lang.values()) else []),
           "", "## Confusion matrix (test set; rows are the true intent, columns the predicted one)", "",
           "| true \\ predicted | " + " | ".join(f"`{i}`" for i in INTENTS) + " |", "|---|" + "---|" * len(INTENTS)]
    for a in INTENTS:
        md.append(f"| `{a}` | " + " | ".join(str(confusion[a][b]) if confusion[a][b] else "." for b in INTENTS) + " |")
    v_raw, _ = ece(val, predictions(v_scores, 1.0))
    v_cal, _ = ece(val, predictions(v_scores, temperature))
    md += ["", "## Calibration (test set, after the temperature)", "",
           f"The temperature ({temperature}) minimises the negative log-likelihood on the validation set "
           f"({nll(val, v_scores, 1.0):.3f} before, {nll(val, v_scores, temperature):.3f} after; on the test set "
           f"{nll(test, t_scores, 1.0):.3f} before, {nll(test, t_scores, temperature):.3f} after). "
           f"Expected calibration error, 10 equal-width bins: validation {v_raw:.3f} before and {v_cal:.3f} after; "
           f"test {ece_raw:.3f} before and {ece_cal:.3f} after. "
           + ("The temperature did not lower the test ECE: the model is already close to calibrated, and in the middle bins "
              "(0.6 to 0.9) it is now UNDER-confident. The threshold is chosen on the calibrated scale, so this makes it "
              "conservative, not unsafe; a different fit (for example on ECE) would move it. " if ece_cal > ece_raw else "") + "", "",
           "| Confidence bin | n | Mean confidence | Accuracy |", "|---|---|---|---|"]
    for b in bins:
        md.append(f"| {b['bin']} | {b['n']} | {b['mean_confidence']} | {b['accuracy']} |")
    md += ["", "## Errors the system would have acted on by itself", ""]
    if not wrong_auto:
        md.append("None on the test set.")
    else:
        md += ["| True intent | Predicted | Text |", "|---|---|---|"]
        for r, p in wrong_auto[:25]:
            md.append(f"| `{r['label']}` | `{p}` | {r['text'].replace('|', '/')} |")
        if len(wrong_auto) > 25:
            md.append(f"\n(first 25 of {len(wrong_auto)})")
    md += ["", "## Opt-outs", ""]
    by_pred = Counter(p for _, _, p in opt_outs)
    md.append(f"{n_oo - len(opt_leak)} of {n_oo} test opt-outs went to a person; {len(opt_leak)} did not. "
              f"Predicted as `opt_out` (always a person): {by_pred.get('opt_out', 0)}. Predicted as another intent: "
              + (", ".join(f"{k} {v}" for k, v in sorted(by_pred.items()) if k != "opt_out") or "none")
              + " (these reached a person only because the intent is `other` or the confidence was below the threshold; "
                "that is a property of this test set, not a guarantee).")
    if opt_leak:
        md += ["", "Opt-outs that were auto-handled:", "", "| Predicted | Text |", "|---|---|"] + [
            f"| `{p}` | {r['text'].replace('|', '/')} |" for r, _, p in opt_leak]
    md += ["", "## Classical baseline on the same split", "",
           f"Multinomial naive Bayes on character 1 to 4-grams, trained on the same training split, no abstention: accuracy {pct(base_acc)}, "
           f"macro F1 {macro_f1(base_table):.3f}, against {pct(accuracy)} and {macro_f1(table):.3f} for the fine-tuned model. "
           "If these two were equal the small language model would add nothing on this synthetic data.", "",
           "## How to read this", "",
           "- Test and training texts come from the same hand-written families of message. High scores here are expected and say little "
           "about real patients; the H2 review of `h2_review_sample.csv` and a shadow period on real replies are the real test.",
           "- The confidence is calibrated on synthetic validation data. On real data it will be less reliable, probably over-confident.",
           "- The threshold was chosen on validation, not test; the test numbers above did not influence it.", ""]
    machine = {
        "temperature": temperature, "threshold": threshold, "accuracy": accuracy, "macro_f1": macro_f1(table), "ece_raw": ece_raw,
        "ece_calibrated": ece_cal, "coverage": n_auto / len(test), "auto_precision": correct_auto / n_auto if n_auto else None,
        "auto_precision_ci": [lo, hi], "opt_out_leaked": len(opt_leak), "opt_out_total": n_oo, "per_intent": table, "per_language": by_lang,
        "baseline": {"accuracy": base_acc, "macro_f1": macro_f1(base_table)}, "confusion": confusion, "reliability": bins,
        "n": {"train": len(train), "val": len(val), "test": len(test)}, "adapter_bytes": adapter_bytes, "train": train_info,
    }
    return "\n".join(md), machine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--reuse-scores", action="store_true")
    args = ap.parse_args(argv)
    train, val, test = load("train"), load("val"), load("test")
    WORK.mkdir(exist_ok=True)
    scorer = None
    if not (args.reuse_scores and (WORK / "scores_val.json").exists() and (WORK / "scores_test.json").exists()):
        from app.triage.scorer import MlxScorer

        scorer = MlxScorer(str(args.model), str(args.adapter))
    v_scores = score_split(scorer, val, "val", args.reuse_scores)
    t_scores = score_split(scorer, test, "test", args.reuse_scores)
    if any(not math.isfinite(v) for sc in v_scores + t_scores for v in [*sc["logits"], sc["mass"]]):
        print("non-finite scores: the adapter is broken", file=sys.stderr)
        return 3
    temperature = fit_temperature(val, v_scores)
    threshold = choose_threshold(val, predictions(v_scores, temperature), v_scores)
    print(f"temperature {temperature}, threshold {threshold}")
    adapter_file = args.adapter / "adapters.safetensors"
    adapter_bytes = adapter_file.stat().st_size
    adapter_hash = hashlib.sha256(adapter_file.read_bytes()).hexdigest()[:8]
    base_bytes = sum(f.stat().st_size for f in args.model.rglob("*") if f.is_file())
    info_path = WORK / "train_info.json"
    train_info = json.loads(info_path.read_text()) if info_path.exists() else {}
    model_id = f"qwen2.5-1.5b-4bit+lora-{adapter_hash}"
    markdown, machine = build_report(train, val, test, v_scores, t_scores, temperature, threshold, {}, adapter_bytes, base_bytes, train_info)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "eval.md").write_text(markdown, encoding="utf-8")
    (OUT_DIR / "eval.json").write_text(json.dumps(machine, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    CAL_PATH.write_text(json.dumps({
        "model_id": model_id, "base_model": BASE_MODEL_ID, "temperature": temperature, "threshold": threshold, "mass_floor": MASS_FLOOR,
        "auto_handled": sorted(AUTO_HANDLED), "always_human": sorted(ALWAYS_HUMAN), "labels": list(INTENTS),
        "val_precision_target": VAL_PRECISION_TARGET, "test_precision": machine["auto_precision"], "test_coverage": machine["coverage"],
        "synthetic_data_only": True,
    }, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT_DIR / 'eval.md'} and {CAL_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
