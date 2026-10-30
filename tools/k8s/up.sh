#!/usr/bin/env bash
# `make k8s-up`: a local Kubernetes deployment of deploy/helm/screening-closure on kind (cluster sc-m76). Idempotent.
# Touches only the kind cluster sc-m76 (every kubectl/helm call names --context kind-sc-m76) and docker images tagged :m76.
# Secrets are generated here (openssl) into Kubernetes Secrets, never into a file in the repository; an existing Secret is kept
# so a rerun does not desynchronise the database passwords.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER=sc-m76
CTX="kind-${CLUSTER}"
NS=closure
REL=closure
TAG=m76
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
cd "$ROOT"

for tool in docker kind helm kubectl openssl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "k8s-up: '$tool' not found (brew install kind helm; kubectl ships with Docker Desktop)" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "k8s-up: docker is not running" >&2; exit 1; }

k() { kubectl --context "$CTX" "$@"; }
kn() { kubectl --context "$CTX" -n "$NS" "$@"; }

tmp="$(umask 077 && mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# 1. cluster -----------------------------------------------------------------------------------------------------------------
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "k8s-up: cluster $CLUSTER exists"
else
  echo "k8s-up: creating kind cluster $CLUSTER"
  kind create cluster --config tools/k8s/kind-config.yaml --wait 180s
fi
k wait --for=condition=Ready nodes --all --timeout=180s >/dev/null

# 2. images: built the way compose builds them (api: ./api target runtime; scheduler, watchdog: their directories) -------------
echo "k8s-up: building images (tag :$TAG)"
build() {  # pip inside the build occasionally fails to reach PyPI; layers are cached, so a retry resumes at the failing step
  local tag="$1"; shift
  for attempt in 1 2 3; do
    docker build --progress=quiet --provenance=false "$@" -t "$tag" >/dev/null && return 0
    echo "k8s-up: docker build $tag failed (attempt $attempt)" >&2
  done
  return 1
}
build "screening-closure-api:$TAG" --target runtime api
build "screening-closure-scheduler:$TAG" scheduler
build "screening-closure-watchdog:$TAG" watchdog
if ! docker image inspect postgres:16 >/dev/null 2>&1; then docker pull postgres:16 >/dev/null; fi
echo "k8s-up: loading images into the cluster"
# `kind load docker-image` fails on Docker Desktop's containerd image store (multi-platform index, "content digest not found"),
# so each image goes in as a single-platform archive (the node's own platform).
platform="linux/$(docker info --format '{{.Architecture}}' | sed 's/aarch64/arm64/; s/x86_64/amd64/')"
for img in "screening-closure-api:$TAG" "screening-closure-scheduler:$TAG" "screening-closure-watchdog:$TAG" postgres:16; do
  docker save --platform "$platform" -o "$tmp/image.tar" "$img"
  kind load image-archive --name "$CLUSTER" "$tmp/image.tar" >/dev/null
  rm -f "$tmp/image.tar"
done

# 3. namespace, secrets ------------------------------------------------------------------------------------------------------
k create namespace "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
rand() { openssl rand -hex 32; }

if ! kn get secret sc-postgres >/dev/null 2>&1; then
  printf 'password=%s\n' "$(rand)" > "$tmp/pg.env"
  kn create secret generic sc-postgres --from-env-file="$tmp/pg.env" >/dev/null
fi
if ! kn get secret screening-closure-secrets >/dev/null 2>&1; then
  pg="$(kn get secret sc-postgres -o jsonpath='{.data.password}' | base64 -d)"
  app="$(rand)"
  {
    echo "DATABASE_URL=postgresql://closure_app:${app}@postgres:5432/closure?sslmode=disable"
    echo "MIGRATE_DATABASE_URL=postgresql://closure_owner:${pg}@postgres:5432/closure?sslmode=disable"
    echo "APP_DB_PASSWORD=${app}"
    for key in DEV_AUTH_SECRET SCHEDULER_SERVICE_TOKEN WATCHDOG_SERVICE_TOKEN AUTOMATION_SERVICE_TOKEN AGENCY_SERVICE_TOKEN METRICS_SCRAPE_TOKEN; do
      echo "${key}=$(rand)"
    done
  } > "$tmp/app.env"
  kn create secret generic screening-closure-secrets --from-env-file="$tmp/app.env" >/dev/null
  echo "k8s-up: generated Secrets sc-postgres and screening-closure-secrets"
fi
if ! kn get secret sc-oidc-signing >/dev/null 2>&1; then
  openssl genrsa -out "$tmp/private.pem" 2048 2>/dev/null
  kn create secret generic sc-oidc-signing --from-file=private.pem="$tmp/private.pem" >/dev/null
  "$PY" tools/k8s/oidc_keys.py "$tmp/private.pem" > "$tmp/jwks.json"
  kn create configmap sc-jwks --from-file=jwks.json="$tmp/jwks.json" >/dev/null
  echo "k8s-up: generated the OIDC signing key (Secret sc-oidc-signing) and its public JWKS (ConfigMap sc-jwks)"
fi

# 4. application config (values.yaml's recipe) ---------------------------------------------------------------------------------
kn create configmap screening-closure-config \
  --from-file=calendar.yaml=config/calendar.yaml --from-file=colorectal_fit.yaml=config/pathways/colorectal_fit.yaml \
  --from-file=providers.yaml=config/providers.yaml --from-file=outreach_policy.yaml=config/outreach_policy.yaml \
  --from-file=agency.yaml=config/agency.yaml --dry-run=client -o yaml | kn apply -f - >/dev/null

# 5. Postgres and the JWKS server ----------------------------------------------------------------------------------------------
kn apply -f tools/k8s/postgres.yaml >/dev/null
kn apply -f tools/k8s/jwks.yaml >/dev/null
kn rollout status statefulset/postgres --timeout=240s
kn rollout status deployment/sc-jwks --timeout=120s

# 6. the chart (the migrate pre-install/pre-upgrade hook runs first) -----------------------------------------------------------
host_cidr="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr ' ' '\n' | grep -v ':' | head -1 || true)"
extra=()
[ -n "$host_cidr" ] && extra=(--set "networkPolicy.ingressCidrs={$host_cidr}")
echo "k8s-up: helm upgrade --install $REL"
helm --kube-context "$CTX" upgrade --install "$REL" deploy/helm/screening-closure -n "$NS" \
  -f deploy/helm/screening-closure/values-kind.yaml "${extra[@]}" --wait --timeout 8m
# the ConfigMap/Secret/image can change without the pod template changing: roll when asked (K8S_RESTART=1)
if [ "${K8S_RESTART:-0}" = 1 ]; then
  kn rollout restart deployment/${REL}-api deployment/${REL}-scheduler deployment/${REL}-watchdog >/dev/null
fi

for d in ${REL}-api ${REL}-scheduler ${REL}-watchdog; do kn rollout status deployment/$d --timeout=300s; done

echo
kn get pods -o wide
echo
echo "k8s-up: API      http://127.0.0.1:18376   (mint a JWT with tools/k8s/mint_token.py --role coordinator)"
echo "k8s-up: Postgres 127.0.0.1:15476 (user closure_owner or closure_app; passwords in Secrets sc-postgres / screening-closure-secrets)"
echo "k8s-up: context  $CTX, namespace $NS; tear down with 'make k8s-down'"
