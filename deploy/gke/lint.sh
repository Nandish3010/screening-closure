#!/usr/bin/env bash
# helm lint + helm template of both charts with the load-test values, then kubeconform if it is installed (it is not installed by this
# script). No cluster and no cloud. The product chart is also rendered with the HPA on and with NetworkPolicy on, which the load test itself does not use.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
P=deploy/helm/screening-closure
I=deploy/gke/chart
command -v helm >/dev/null || { echo "gke-lint: helm not found (brew install helm)" >&2; exit 1; }
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
helm lint "$P" -f "$P/values-gke-loadtest.yaml" --set secrets.create=true
helm lint "$P" --set secrets.create=true
helm lint "$I"
helm template t "$P" -f "$P/values-gke-loadtest.yaml" >"$out/product-gke.yaml"
helm template t "$P" -f "$P/values-gke-loadtest.yaml" --set api.autoscaling.enabled=true --set networkPolicy.enabled=true >"$out/product-hpa-netpol.yaml"
helm template t "$P" -f "$P/values-kind.yaml" >"$out/product-kind.yaml"
helm template t "$I" --set loadgen.enabled=true >"$out/support.yaml"
grep -q 'kind: HorizontalPodAutoscaler' "$out/product-hpa-netpol.yaml" || { echo "gke-lint: the HPA did not render" >&2; exit 1; }
! grep -q 'kind: HorizontalPodAutoscaler' "$out/product-gke.yaml" || { echo "gke-lint: the HPA is on in the load-test values" >&2; exit 1; }
if grep -E '^\s+(DATABASE_URL|MIGRATE_DATABASE_URL|[A-Z_]+_TOKEN|DEV_AUTH_SECRET|APP_DB_PASSWORD): *"?[^ "{]' "$out"/product-gke.yaml | grep -vE 'secretKeyRef|CHANGE_ME'; then
  echo "gke-lint: a secret value is rendered into a manifest" >&2; exit 1
fi
echo "gke-lint: helm lint and helm template pass (5 renders)"
if command -v kubeconform >/dev/null; then
  kubeconform -strict -summary -ignore-missing-schemas "$out"/*.yaml
else
  echo "gke-lint: kubeconform not installed: schema validation skipped (brew install kubeconform to enable it)"
fi
