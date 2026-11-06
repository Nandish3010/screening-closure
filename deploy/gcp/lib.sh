# Shared settings for the operator scripts (run on YOUR machine). Override any of these in the environment.
# Nothing in this file creates anything; deploy.sh does, and only with --yes-spend.
set -euo pipefail
: "${GCP_PROJECT:=$(gcloud config get-value project 2>/dev/null || true)}"
: "${GCP_REGION:=me-central1}"            # Doha; see docs/governance/hosting-gcp.md "Region": synthetic data only
: "${GCP_ZONE:=${GCP_REGION}-a}"          # me-central1-a/b/c all offer E2 (Google zone table)
: "${SC_VM_NAME:=sc-demo}"
: "${SC_MACHINE_TYPE:=e2-medium}"           # 2 shared vCPU / 4 GB (owner budget about INR 3,000 a month)
: "${SC_QUANT:=q5_k_m}"                      # the GGUF quantization delivered by deploy.sh --model (Q5_K_M: about 1.1 GB, fits 4 GB)
: "${SC_DISK_GB:=40}"
: "${SC_BUDGET_USD:=80}"                   # monthly budget ALERT (email), not a cap
: "${SC_DOMAIN:=}"                         # optional own domain pointing at the static IP; else <ip-with-dashes>.sslip.io (later: SC_DOMAIN=demo.example.org)
SC_PREFIX=sc-demo
SC_SA="${SC_PREFIX}-vm"
SC_ADDR="${SC_PREFIX}-ip"
SC_TAG="${SC_PREFIX}"
IAP_RANGE=35.235.240.0/20
SC_ITEMS="dev-auth-secret scheduler-token watchdog-token automation-token agency-token metrics-token postgres-password app-db-password keycloak-db-password keycloak-admin-password pw-coordinator pw-clinician pw-supervisor pw-patient pw-lab pw-owner pw-specialist demo-client-secret"
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

need_project() { [ -n "$GCP_PROJECT" ] || { echo "No project: set GCP_PROJECT or run: gcloud config set project <id>" >&2; exit 1; }; }
gssh() { gcloud compute ssh "$SC_VM_NAME" --project "$GCP_PROJECT" --zone "$GCP_ZONE" --tunnel-through-iap "$@"; }
public_host() {
  if [ -n "$SC_DOMAIN" ]; then echo "$SC_DOMAIN"; return; fi
  ip=$(gcloud compute addresses describe "$SC_ADDR" --project "$GCP_PROJECT" --region "$GCP_REGION" --format='value(address)')
  echo "${ip//./-}.sslip.io"
}
