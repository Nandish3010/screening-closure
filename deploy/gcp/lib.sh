# Shared settings for the operator scripts (run on YOUR machine). Override any of these in the environment.
# Nothing in this file creates anything; deploy.sh does, and only with --yes-spend.
set -euo pipefail
: "${GCP_PROJECT:=$(gcloud config get-value project 2>/dev/null || true)}"
: "${GCP_REGION:=europe-west4}"            # see docs/governance/hosting-gcp.md "Region": synthetic data only
: "${GCP_ZONE:=${GCP_REGION}-a}"
: "${SC_VM_NAME:=sc-demo}"
: "${SC_MACHINE_TYPE:=e2-standard-2}"      # 2 vCPU / 8 GB. e2-medium (4 GB) is the cheapest-viable option, see the doc
: "${SC_DISK_GB:=40}"
: "${SC_BUDGET_USD:=60}"                   # monthly budget ALERT (email), not a cap
: "${SC_DOMAIN:=}"                         # optional own domain pointing at the static IP; else <ip>.sslip.io
SC_PREFIX=sc-demo
SC_SA="${SC_PREFIX}-vm"
SC_ADDR="${SC_PREFIX}-ip"
SC_TAG="${SC_PREFIX}"
IAP_RANGE=35.235.240.0/20
SC_ITEMS="dev-auth-secret scheduler-token watchdog-token automation-token agency-token metrics-token postgres-password app-db-password keycloak-db-password keycloak-admin-password pw-coordinator pw-clinician pw-supervisor pw-patient pw-lab pw-owner pw-specialist"
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

need_project() { [ -n "$GCP_PROJECT" ] || { echo "No project: set GCP_PROJECT or run: gcloud config set project <id>" >&2; exit 1; }; }
gssh() { gcloud compute ssh "$SC_VM_NAME" --project "$GCP_PROJECT" --zone "$GCP_ZONE" --tunnel-through-iap "$@"; }
public_host() {
  if [ -n "$SC_DOMAIN" ]; then echo "$SC_DOMAIN"; return; fi
  ip=$(gcloud compute addresses describe "$SC_ADDR" --project "$GCP_PROJECT" --region "$GCP_REGION" --format='value(address)')
  echo "${ip//./-}.sslip.io"
}
