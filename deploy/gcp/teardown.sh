#!/bin/bash
# Deletes everything deploy.sh created (VM and disk, IP, firewall rules, secrets, service account). Irreversible: the seeded
# database is lost (seed.sh rebuilds it). Asks you to type the project id.
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/lib.sh"; need_project
read -r -p "Type the project id ($GCP_PROJECT) to delete the sc-demo resources: " a
[ "$a" = "$GCP_PROJECT" ] || { echo "not confirmed"; exit 1; }
P=(--project "$GCP_PROJECT" --quiet)
gcloud compute instances delete "$SC_VM_NAME" --zone "$GCP_ZONE" --delete-disks=all "${P[@]}" || true
gcloud compute addresses delete "$SC_ADDR" --region "$GCP_REGION" "${P[@]}" || true
gcloud compute firewall-rules delete "$SC_PREFIX-web" "$SC_PREFIX-iap-ssh" "${P[@]}" || true
for n in $SC_ITEMS; do gcloud secrets delete "$SC_PREFIX-$n" "${P[@]}" || true; done
gcloud iam service-accounts delete "$SC_SA@$GCP_PROJECT.iam.gserviceaccount.com" "${P[@]}" || true
ba=$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingAccountName)' 2>/dev/null | sed 's|billingAccounts/||')
[ -n "$ba" ] && for b in $(gcloud billing budgets list --billing-account "$ba" --filter="displayName=sc-demo-budget" --format='value(name)'); do gcloud billing budgets delete "$b" --quiet || true; done
echo "Deleted. To remove the whole project instead: gcloud projects delete $GCP_PROJECT"
