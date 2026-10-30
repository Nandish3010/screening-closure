#!/usr/bin/env bash
# `make k8s-down`: delete the kind cluster sc-m76 (and nothing else: no other cluster, no compose project, no images).
set -euo pipefail
if kind get clusters 2>/dev/null | grep -qx sc-m76; then
  kind delete cluster --name sc-m76
else
  echo "k8s-down: no kind cluster sc-m76"
fi
