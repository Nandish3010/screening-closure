# Reply triage: the GGUF build (Q8_0) against the MLX model

The hosted demo cannot run MLX. This is the same fine-tuned Qwen2.5-1.5B + LoRA, merged and converted to GGUF (`qwen2.5-1.5b-q8_0-gguf+lora-8b5a49fc`) and run on the CPU by llama.cpp on our own server: no third-party AI. It is a **different artefact** from the MLX model (the 4-bit weights are written back as fp16 with the adapter merged, then quantized again), so it is evaluated here on the same synthetic splits, with the same scripts (`tools/triage/evaluate.py` statistics, `tools/rai/fairness.py` group results). Synthetic data only; split by template.

## Before and after (test split)

| Metric | MLX (shipped, before) | GGUF, MLX calibration unchanged | GGUF, re-fitted (after, shipped on the host) |
|---|---|---|---|
| Accuracy (test) | 89.8% | 89.7% | 89.7% |
| Macro F1 (test) | 0.896 | 0.894 | 0.894 |
| Auto-handled precision (test) | 96.2% | 96.2% | 96.2% |
| Coverage: handled without a person (test) | 58.1% | 58.1% | 58.1% |
| ECE calibrated (test) | 0.053 | 0.054 | 0.054 |
| Opt-outs leaked to automatic handling | 0 of 61 | see json | 0 of 61 |
| Temperature | 1.7036 | 1.7036 | 1.7062 |
| Abstain threshold | 0.553 | 0.553 | 0.5512 |

Re-fitted the documented way (one temperature on validation by minimum NLL; the smallest threshold at which the replies the system would handle by itself reach 97% precision on validation): temperature 1.7062, threshold 0.5512.

## Per language, through the deployed decision path (opt-out gate, calibration, dialect group rule)

| Group | n | accuracy | handled without a person | auto precision (rule on) | auto precision (rule off) |
|---|---|---|---|---|---|
| all | 551 | 89.7% | 43.6% | 100.0% (0 wrong of 240) | 96.2% (12 wrong of 320) |
| ar | 194 | 89.7% | 33.0% | 100.0% (0 wrong of 64) | 96.3% (4 wrong of 109) |
| en | 180 | 100.0% | 68.3% | 100.0% (0 wrong of 123) | 100.0% (0 wrong of 123) |
| arabizi | 99 | 68.7% | 21.2% | 100.0% (0 wrong of 21) | 81.0% (8 wrong of 42) |
| mixed | 78 | 92.3% | 41.0% | 100.0% (0 wrong of 32) | 100.0% (0 wrong of 46) |

## Arabic dialect

| Variant | n | accuracy | handled without a person | auto precision (rule on) | auto precision (rule off) |
|---|---|---|---|---|---|
| ar_msa | 102 | 99.0% | 62.7% | 100.0% | 100.0% |
| ar_gulf | 92 | 79.3% | 0.0% | n/a | 91.1% |

Dialect rule (`api/app/triage/dialect.py`): Gulf dialect always to a person; Arabizi auto-handled only above its own threshold. MLX value 0.918; fitted on this build's validation Arabizi split: 0.915; in force on the host: 0.915.

Caveat: the Arabic was written by developers, not by Arabic-speaking clinicians; no native-speaker review of this build has been done. Per-group intervals are wide (see eval-gguf json). These numbers describe generalisation to new templates of the same synthetic kinds of message, not performance on real patients.
