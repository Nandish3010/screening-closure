#!/bin/bash
# On the VM, as root (run through deploy/gcp/demo-reset.sh over IAP SSH, so IAM is the protection: only a project member with
# SSH-over-IAP rights can do this). Restores the snapshot taken by seed-on-vm.sh (cases, events, drafts, FHIR) and checks the state hash.
set -euo pipefail
export SC_REPO=${SC_REPO:-/srv/sc/repo}
# shellcheck disable=SC1091
. "$SC_REPO/deploy/gcp/vm/env.sh"
[ "$SC_MODE" = production ] || { echo "demo-reset: the stack is in mode '$SC_MODE', expected production" >&2; exit 1; }
tools/demo/snapshot.sh reset
