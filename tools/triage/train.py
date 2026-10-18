#!/usr/bin/env python3
"""LoRA fine-tune of a small multilingual instruct model for reply triage (D13), then evaluate it.

    tools/triage/.venv/bin/python tools/triage/train.py [--model DIR] [--iters N] [--skip-train] [--skip-eval]

Steps: (1) make sure the synthetic dataset exists, (2) write it in mlx-lm's prompt/completion format
("Classify the patient reply intent: <text>" -> label), (3) run `mlx_lm lora` with a fixed seed and time it,
(4) copy the adapter into api/app/triage/adapters/ if it is under 50 MB, (5) run evaluate.py (calibration,
abstain threshold, docs/results/triage/eval.md).

The base model is downloaded once to tools/triage/models/ (gitignored): see tools/triage/README.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(HERE))
from app.triage.labels import build_prompt  # noqa: E402

SEED = 20261018
DEFAULT_MODEL = HERE / "models" / "qwen2.5-1.5b-4bit"
BASE_MODEL_ID = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"  # Apache-2.0
DATASET = HERE / "dataset"
WORK = HERE / "adapters_work"
ADAPTER_DEST = ROOT / "api/app/triage/adapters"
MAX_ADAPTER_BYTES = 50 * 1024 * 1024


def write_mlx_data() -> Path:
    data = WORK / "data"
    data.mkdir(parents=True, exist_ok=True)
    for split, name in (("train", "train"), ("val", "valid"), ("test", "test")):
        with (DATASET / f"{split}.jsonl").open(encoding="utf-8") as src, (data / f"{name}.jsonl").open("w", encoding="utf-8") as dst:
            for line in src:
                row = json.loads(line)
                dst.write(json.dumps({"prompt": build_prompt(row["text"]), "completion": row["label"]}, ensure_ascii=False) + "\n")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--iters", type=int, default=250)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--num-layers", type=int, default=16)
    ap.add_argument("--learning-rate", type=float, default=5e-5)
    ap.add_argument("--skip-train", action="store_true")
    ap.add_argument("--skip-eval", action="store_true")
    args = ap.parse_args(argv)

    if not (DATASET / "train.jsonl").exists():
        subprocess.run([sys.executable, str(HERE / "generate_dataset.py")], check=True)
    if not args.model.exists():
        print(f"base model not found at {args.model}; download {BASE_MODEL_ID} first (tools/triage/README.md)", file=sys.stderr)
        return 2
    adapters = WORK / "adapters"
    if not args.skip_train:
        data = write_mlx_data()
        adapters.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "mlx_lm", "lora", "--model", str(args.model), "--train", "--data", str(data),
               "--fine-tune-type", "lora", "--num-layers", str(args.num_layers), "--batch-size", str(args.batch_size),
               "--iters", str(args.iters), "--learning-rate", str(args.learning_rate), "--mask-prompt",
               "--max-seq-length", "256", "--val-batches", "12", "--steps-per-report", "20", "--steps-per-eval", "50",
               "--save-every", "50", "--adapter-path", str(adapters), "--seed", str(SEED)]
        print("+", " ".join(cmd), flush=True)
        started = time.time()
        subprocess.run(cmd, check=True)
        elapsed = time.time() - started
        (WORK / "train_info.json").write_text(json.dumps({
            "base_model": BASE_MODEL_ID, "seed": SEED, "iters": args.iters, "batch_size": args.batch_size,
            "num_layers": args.num_layers, "learning_rate": args.learning_rate, "train_seconds": round(elapsed, 1),
            "train_examples": sum(1 for _ in (DATASET / "train.jsonl").open()),
        }, indent=2) + "\n")
        print(f"training took {elapsed / 60:.1f} min", flush=True)
    weights = adapters / "adapters.safetensors"
    import mlx.core as mx

    if any(bool(mx.any(mx.isnan(v))) for v in mx.load(str(weights)).values()):
        # lr 1e-4 diverged to NaN between iterations 200 and 220 in the first run: never ship such an adapter
        print("the adapter contains NaN: training diverged; lower --learning-rate", file=sys.stderr)
        return 3
    size = weights.stat().st_size
    print(f"adapter size: {size / 1e6:.1f} MB")
    if size <= MAX_ADAPTER_BYTES:
        ADAPTER_DEST.mkdir(parents=True, exist_ok=True)
        shutil.copy2(weights, ADAPTER_DEST / "adapters.safetensors")
        config = json.loads((adapters / "adapter_config.json").read_text())
        # mlx-lm writes absolute local paths into the config; the loader does not read them, so keep them out of git
        config.update({"adapter_path": "api/app/triage/adapters", "data": "tools/triage/adapters_work/data",
                       "model": "tools/triage/models/qwen2.5-1.5b-4bit"})
        (ADAPTER_DEST / "adapter_config.json").write_text(json.dumps(config, indent=4) + "\n")
        print(f"copied to {ADAPTER_DEST}")
    else:
        print(f"adapter is over 50 MB: kept outside the repo at {adapters}", file=sys.stderr)
    if not args.skip_eval:
        subprocess.run([sys.executable, str(HERE / "evaluate.py"), "--model", str(args.model),
                        "--adapter", str(ADAPTER_DEST if size <= MAX_ADAPTER_BYTES else adapters)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
