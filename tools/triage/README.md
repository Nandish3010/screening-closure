# Reply triage: data, training, evaluation (D13)

A small multilingual model (Qwen2.5-1.5B-Instruct, 4-bit, Apache-2.0) fine-tuned with LoRA to label an inbound
patient reply, in Arabic, English, Arabizi or mixed script, with one of nine intents. It runs offline on this Mac with
MLX. Low-confidence replies, and every `question`, `other` and `opt_out`, go to a person. The model card is
[docs/governance/reply-triage-card.md](../../docs/governance/reply-triage-card.md); the numbers are in
[docs/results/triage/eval.md](../../docs/results/triage/eval.md). **All training data are synthetic.**

## One-time setup (needs the network once; everything after runs offline)

```sh
python3 -m venv tools/triage/.venv                      # gitignored; not the API's venv
tools/triage/.venv/bin/pip install -r tools/triage/requirements.txt
tools/triage/.venv/bin/python -c "from huggingface_hub import snapshot_download; \
  snapshot_download('mlx-community/Qwen2.5-1.5B-Instruct-4bit', local_dir='tools/triage/models/qwen2.5-1.5b-4bit')"   # 0.84 GB, gitignored
```

## Regenerate the data, retrain, re-evaluate

```sh
python3 tools/triage/generate_dataset.py                 # seeded; writes tools/triage/dataset/ and the H2 sample
tools/triage/.venv/bin/python tools/triage/train.py      # LoRA, then evaluate.py: adapter, calibration.json, eval.md
```

`generate_dataset.py --check` fails if the committed dataset differs from a fresh generation. `train.py --skip-train`
re-runs only the evaluation; `evaluate.py --reuse-scores` re-fits the temperature and threshold from cached scores.

## Where things go

| What | Where | In git |
|---|---|---|
| Templates, slot pools | `templates.py` | yes |
| Dataset (train, val, test, manifest) | `dataset/` | yes (about 0.5 MB) |
| H2 review sample (100 lines) | `docs/results/triage/h2_review_sample.csv` | yes |
| Base model | `models/qwen2.5-1.5b-4bit/` | no |
| Training scratch, scores | `adapters_work/` | no |
| LoRA adapter (about 10 MB) | `api/app/triage/adapters/` | yes (under 50 MB) |
| Temperature, threshold, model id | `api/app/triage/calibration.json` | yes |

## Serving

`api/app/triage/classify.py` loads the model lazily. The API's virtualenv has no `mlx`, so it starts
`python -m app.triage.worker` with `tools/triage/.venv/bin/python` (override with `TRIAGE_PYTHON`, `TRIAGE_MODEL_PATH`,
`TRIAGE_ADAPTER_PATH`). `TRIAGE_ENABLED=0` turns the model off. With no model, every reply is `needs_human`.
