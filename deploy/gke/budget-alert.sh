#!/usr/bin/env bash
# OPTIONAL and NOT run by `make gke-loadtest`: create a billing budget with e-mail alerts for the project's billing account.
# A budget is an ALERT, not a spending cap: Google Cloud has no hard cap. It is the independent net under the script's own wall-clock cap
# and label-based teardown, for the case where this laptop dies while the cluster is up.
#
#   make gke-budget-alert PROJECT=my-project            (USD 30 by default; BUDGET_USD=15 to change)
#
# Needs: gcloud signed in as a billing-account administrator (or Billing Account Costs Manager), and the Cloud Billing Budget API
# (billingbudgets.googleapis.com), which this script enables on the project (free). Alerts go to the billing administrators by e-mail
# at 50 / 90 / 100 percent of the amount. Remove it later with the delete command printed at the end.
set -euo pipefail
PROJECT="${1:-${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
BUDGET_USD="${BUDGET_USD:-30}"
NAME="sc-loadtest-budget"
[ -n "$PROJECT" ] || { echo "usage: $0 PROJECT" >&2; exit 64; }
ACCOUNT=$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)' | sed 's|billingAccounts/||')
[ -n "$ACCOUNT" ] || { echo "project $PROJECT has no billing account" >&2; exit 1; }
echo "Creating budget $NAME of USD $BUDGET_USD on billing account $ACCOUNT, filtered to project $PROJECT (alerts at 50/90/100 percent)."
read -r -p 'Type "yes" to continue: ' ans
[ "$ans" = yes ] || { echo "not confirmed"; exit 1; }
gcloud services enable billingbudgets.googleapis.com --project "$PROJECT"
if gcloud billing budgets list --billing-account "$ACCOUNT" --filter="displayName=$NAME" --format='value(name)' | grep -q .; then
  echo "a budget named $NAME already exists on this billing account; not creating another"
else
  gcloud billing budgets create --billing-account "$ACCOUNT" --display-name "$NAME" --budget-amount "${BUDGET_USD}USD" \
    --filter-projects "projects/$PROJECT" \
    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
fi
echo "To remove it: gcloud billing budgets delete \$(gcloud billing budgets list --billing-account $ACCOUNT --filter=displayName=$NAME --format='value(name)')"
