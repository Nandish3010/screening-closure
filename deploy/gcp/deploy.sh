#!/bin/bash
# Creates the hosted demo's cloud resources and delivers the code. THIS SPENDS MONEY: run it only after you approved the cost in
# docs/governance/hosting-gcp.md. It refuses to run without --yes-spend. Idempotent (re-running reuses what exists).
#   deploy.sh --yes-spend [--model tools/triage/gguf/triage-q5_k_m.gguf]
# --model uploads the converted triage model (about 1-1.7 GB, over IAP SSH) to the VM; without it replies fail safe to a person.
# Creates: APIs enabled, a service account with NO project roles, secrets in Secret Manager (generated here, never printed),
# firewall (80/443 from anywhere, SSH only through IAP), one static IP, one VM, a billing budget alert. It does NOT start serving
# the public: the VM boots in `seed` mode. Next: seed.sh.
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/lib.sh"
[ "${1:-}" = "--yes-spend" ] || { echo "Refusing: pass --yes-spend to confirm you approved the cost (see hosting-gcp.md)."; exit 2; }
MODEL=""; [ "${2:-}" = "--model" ] && MODEL=${3:-}
if [ -n "$MODEL" ] && [ ! -f "$MODEL" ]; then echo "model file not found: $MODEL" >&2; exit 2; fi
need_project
P=(--project "$GCP_PROJECT")

echo "== APIs"
gcloud services enable compute.googleapis.com secretmanager.googleapis.com iap.googleapis.com billingbudgets.googleapis.com "${P[@]}"

echo "== service account (no project roles; it can read only the sc-demo secrets)"
gcloud iam service-accounts describe "$SC_SA@$GCP_PROJECT.iam.gserviceaccount.com" "${P[@]}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SC_SA" --display-name "Screening Closure demo VM" "${P[@]}"
SA_EMAIL="$SC_SA@$GCP_PROJECT.iam.gserviceaccount.com"

echo "== secrets (generated now, stored in Secret Manager, never printed)"
for n in $SC_ITEMS; do
  s="$SC_PREFIX-$n"
  if ! gcloud secrets describe "$s" "${P[@]}" >/dev/null 2>&1; then
    openssl rand -hex 24 | tr -d '\n' | gcloud secrets create "$s" --data-file=- --replication-policy=automatic "${P[@]}" >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$s" --member "serviceAccount:$SA_EMAIL" --role roles/secretmanager.secretAccessor "${P[@]}" >/dev/null
done

echo "== firewall"
gcloud compute firewall-rules describe "$SC_PREFIX-web" "${P[@]}" >/dev/null 2>&1 || \
  gcloud compute firewall-rules create "$SC_PREFIX-web" --network default --direction INGRESS --allow tcp:80,tcp:443 --target-tags "$SC_TAG" --source-ranges 0.0.0.0/0 "${P[@]}"
gcloud compute firewall-rules describe "$SC_PREFIX-iap-ssh" "${P[@]}" >/dev/null 2>&1 || \
  gcloud compute firewall-rules create "$SC_PREFIX-iap-ssh" --network default --direction INGRESS --allow tcp:22 --target-tags "$SC_TAG" --source-ranges "$IAP_RANGE" "${P[@]}"

echo "== static IP"
gcloud compute addresses describe "$SC_ADDR" --region "$GCP_REGION" "${P[@]}" >/dev/null 2>&1 || \
  gcloud compute addresses create "$SC_ADDR" --region "$GCP_REGION" "${P[@]}"
HOST=$(public_host)
echo "public host: $HOST"

echo "== VM ($SC_MACHINE_TYPE, $GCP_ZONE)"
if ! gcloud compute instances describe "$SC_VM_NAME" --zone "$GCP_ZONE" "${P[@]}" >/dev/null 2>&1; then
  gcloud compute instances create "$SC_VM_NAME" --zone "$GCP_ZONE" --machine-type "$SC_MACHINE_TYPE" \
    --image-family debian-12 --image-project debian-cloud --boot-disk-size "${SC_DISK_GB}GB" --boot-disk-type pd-balanced \
    --address "$SC_ADDR" --tags "$SC_TAG" --service-account "$SA_EMAIL" --scopes cloud-platform \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata "enable-oslogin=TRUE,sc-public-host=$HOST" --metadata-from-file "startup-script=$here/vm/startup.sh" "${P[@]}"
else
  gcloud compute instances add-metadata "$SC_VM_NAME" --zone "$GCP_ZONE" --metadata "sc-public-host=$HOST" "${P[@]}" >/dev/null
fi

echo "== budget alert (email to billing admins at 50/90/100 percent of ${SC_BUDGET_USD} USD; an alert, not a spending cap)"
ba=$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingAccountName)' | sed 's|billingAccounts/||')
if [ -n "$ba" ] && ! gcloud billing budgets list --billing-account "$ba" --format='value(name)' >/dev/null 2>&1; then
  echo "budget: this identity cannot read budgets on billing account $ba (e.g. a deployer service account); skipped. Create or check sc-demo-budget in the console or as a billing admin."
elif [ -n "$ba" ]; then
  gcloud billing budgets list --billing-account "$ba" --filter="displayName=sc-demo-budget" --format='value(name)' | grep -q . || \
    gcloud billing budgets create --billing-account "$ba" --display-name sc-demo-budget --budget-amount "${SC_BUDGET_USD}USD" \
      --filter-projects "projects/$GCP_PROJECT" --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
else echo "no billing account found for the project: budget not created"; fi

echo "== waiting for the VM to finish installing Docker (first boot, a few minutes)"
for i in $(seq 1 40); do gssh --command 'test -f /var/lib/sc/ready-for-bundle' >/dev/null 2>&1 && break; sleep 15; done

echo "== delivering the code (the committed tree of this checkout: git archive, no secrets, no .env)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$REPO_ROOT" archive --format=tar.gz -o "$tmp/sc.tgz" HEAD
echo "commit: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
gcloud compute scp --tunnel-through-iap --zone "$GCP_ZONE" "${P[@]}" "$tmp/sc.tgz" "$SC_VM_NAME:/tmp/sc.tgz"
gssh --command 'sudo rm -rf /srv/sc/repo.new && sudo mkdir -p /srv/sc/repo.new && sudo tar -xzf /tmp/sc.tgz -C /srv/sc/repo.new && \
  if [ -d /srv/sc/repo/data ]; then sudo mv /srv/sc/repo/data /srv/sc/repo.new/data; fi && \
  sudo rm -rf /srv/sc/repo && sudo mv /srv/sc/repo.new /srv/sc/repo && rm /tmp/sc.tgz'
if [ -n "$MODEL" ]; then
  echo "== uploading the triage model ($(du -h "$MODEL" | cut -f1)); sha256 $(shasum -a 256 "$MODEL" | cut -d' ' -f1)"
  gcloud compute scp --tunnel-through-iap --zone "$GCP_ZONE" "${P[@]}" "$MODEL" "$SC_VM_NAME:/tmp/triage.gguf"
  gssh --command 'sudo mkdir -p /srv/sc/models && sudo mv /tmp/triage.gguf /srv/sc/models/triage.gguf && sudo chmod 644 /srv/sc/models/triage.gguf && sha256sum /srv/sc/models/triage.gguf'
fi
echo "done. Next:  deploy/gcp/seed.sh   then   deploy/gcp/smoke-test.sh   then   deploy/gcp/credentials.sh"
