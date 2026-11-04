#!/bin/bash
# READ-ONLY checks before anything is created: gcloud installed and signed in, a project selected, billing linked, the machine type
# offered in the zone. Creates nothing, enables nothing, spends nothing.
here=$(cd "$(dirname "$0")" && pwd)
command -v gcloud >/dev/null || { echo "gcloud is not installed. Homebrew: brew install --cask gcloud-cli   (or: brew install --cask google-cloud-sdk)"; exit 1; }
# shellcheck disable=SC1091
. "$here/lib.sh"
fail=0
acct=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)
if [ -z "$acct" ]; then echo "NOT SIGNED IN. Run yourself:  gcloud auth login"; exit 1; fi
echo "signed in as: $acct"
need_project
echo "project: $GCP_PROJECT   region: $GCP_REGION   zone: $GCP_ZONE   machine: $SC_MACHINE_TYPE"
billing=$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingEnabled,billingAccountName)' 2>/dev/null || true)
case "$billing" in True*) echo "billing: linked ($billing)";; *) echo "billing: NOT linked or not visible to you ($billing). Link it in the console before deploy.sh"; fail=1;; esac
if gcloud compute machine-types describe "$SC_MACHINE_TYPE" --zone "$GCP_ZONE" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "machine type: $SC_MACHINE_TYPE is offered in $GCP_ZONE"
else
  echo "machine type: $SC_MACHINE_TYPE not confirmed in $GCP_ZONE (Compute API may be off yet, or the type is not offered there)"; fail=1
fi
echo "existing sc-demo VM: $(gcloud compute instances list --project "$GCP_PROJECT" --filter="name=$SC_VM_NAME" --format='value(name,status)' 2>/dev/null || echo unknown)"
exit $fail
