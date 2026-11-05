#!/bin/bash
# The one-command deploy: preflight, create, upload the triage model, seed, smoke test, print the judge logins once.
#   deploy/gcp/up.sh --yes-spend [--model FILE | --no-model]
# Run it yourself after: gcloud auth login, gcloud config set project <id>, billing linked, and the cost approved (hosting-gcp.md).
# Stops at the first failing step; every step is idempotent, so re-running continues. ~45-75 minutes the first time.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
[ "${1:-}" = "--yes-spend" ] || { echo "Refusing: pass --yes-spend (this creates billable resources; see hosting-gcp.md)."; exit 2; }
model_args=()
case "${2:-}" in
  --no-model) echo "No triage model: replies will fail safe to a person." ;;
  --model) model_args=(--model "${3:?--model needs a file}") ;;
  "") f="$here/../../tools/triage/gguf/triage-${SC_QUANT:-q5_k_m}.gguf"
      [ -f "$f" ] || { echo "No GGUF model at $f. Build it first (make triage-gguf) or pass --no-model." >&2; exit 2; }
      model_args=(--model "$f") ;;
  *) echo "unknown argument $2" >&2; exit 2 ;;
esac
"$here/preflight.sh"
"$here/deploy.sh" --yes-spend "${model_args[@]}"
"$here/seed.sh"
"$here/smoke-test.sh"
"$here/credentials.sh"
