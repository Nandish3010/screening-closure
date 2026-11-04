#!/bin/sh
# Merge the LoRA adapter into the base model and convert it to GGUF for the hosted demo (llama.cpp on the CPU). CPU-heavy (a few
# minutes, about 6 GB of disk for the fp16 intermediate). Offline from the LOCAL weights: no model is downloaded.
#
#   tools/triage/convert_gguf.sh            # fuse, convert to f16, quantize Q8_0 and Q5_K_M into tools/triage/gguf/
#
# Needs (approved one-time installs on the Mac; this script checks and tells you, it installs nothing):
#   - torch and gguf in tools/triage/.venv:  tools/triage/.venv/bin/pip install torch "$LLAMA_CPP_DIR/gguf-py"
#   - llama.cpp binaries (llama-quantize, llama-server):  brew install llama.cpp
#   - the llama.cpp source for convert_hf_to_gguf.py:  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_DIR"
#     (LLAMA_CPP_DIR defaults to tools/triage/llama.cpp, git-ignored)
#
# Honest caveat, printed again by evaluate_gguf.py: the local base is MLX 4-bit. `mlx_lm fuse --dequantize` writes the 4-bit weights
# plus the merged LoRA delta back out as fp16, and GGUF quantization then quantizes AGAIN. The result is a different artefact from the
# evaluated MLX model; tools/triage/evaluate_gguf.py measures it on the same splits before it is used.
set -eu
cd "$(dirname "$0")/../.."
PY=tools/triage/.venv/bin/python
LLAMA_CPP_DIR=${LLAMA_CPP_DIR:-tools/triage/llama.cpp}
OUT=tools/triage/gguf
BASE=${TRIAGE_MODEL_PATH:-tools/triage/models/qwen2.5-1.5b-4bit}
ADAPTER=${TRIAGE_ADAPTER_PATH:-api/app/triage/adapters}
missing=0
need() { printf 'MISSING: %s\n' "$1" >&2; missing=1; }
[ -x "$PY" ] || need "tools/triage/.venv (see tools/triage/README.md)"
"$PY" -c "import torch, gguf" 2>/dev/null || need "torch and gguf in tools/triage/.venv:  $PY -m pip install torch $LLAMA_CPP_DIR/gguf-py"
command -v llama-quantize >/dev/null 2>&1 || need "llama-quantize:  brew install llama.cpp"
[ -f "$LLAMA_CPP_DIR/convert_hf_to_gguf.py" ] || need "llama.cpp source at $LLAMA_CPP_DIR:  git clone --depth 1 https://github.com/ggml-org/llama.cpp $LLAMA_CPP_DIR"
[ -d "$BASE" ] && [ -f "$ADAPTER/adapters.safetensors" ] || need "local base model $BASE and adapter $ADAPTER"
[ "$missing" = 0 ] || exit 1

mkdir -p "$OUT"
echo "1/3 merge the adapter into the base and write fp16 weights (offline)"
rm -rf "$OUT/fused-fp16"
"$PY" -m mlx_lm fuse --model "$BASE" --adapter-path "$ADAPTER" --save-path "$OUT/fused-fp16" --dequantize
echo "2/3 convert to GGUF f16"
"$PY" "$LLAMA_CPP_DIR/convert_hf_to_gguf.py" "$OUT/fused-fp16" --outfile "$OUT/triage-f16.gguf" --outtype f16
echo "3/3 quantize"
llama-quantize "$OUT/triage-f16.gguf" "$OUT/triage-q8_0.gguf" Q8_0
llama-quantize "$OUT/triage-f16.gguf" "$OUT/triage-q5_k_m.gguf" Q5_K_M
ls -l "$OUT"/*.gguf
shasum -a 256 "$OUT"/*.gguf
