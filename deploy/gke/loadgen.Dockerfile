# The load-generator image of the GKE load test: the repository's own load tooling (tools/k8s/api_load.py, tools/k8s/soak.py,
# tools/load/scale_sweep.py and the seeders) plus the API package they import, and kubectl. Built from the REPOSITORY ROOT:
#   docker buildx build --platform linux/amd64 -f deploy/gke/loadgen.Dockerfile -t REGISTRY/screening-closure-loadgen:gke .
# (the context is trimmed by deploy/gke/loadgen.Dockerfile.dockerignore). Runs as the unprivileged uid 10001.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/repo/api
# kubectl: the tools call it for pods, logs, exec (cgroup samples) and scale. Pinned and checked against the published sha256.
ARG KUBECTL_VERSION=v1.33.0
# the architecture comes from the image itself (dpkg), not from a build argument: a default of amd64 once put an amd64 kubectl into an arm64 image
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar procps \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
 && curl -fsSL -o /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/kubectl && rm /tmp/kubectl.sha256 \
 && groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app --shell /bin/bash app

WORKDIR /repo
COPY api/pyproject.toml api/pyproject.toml
COPY api/app api/app
COPY api/migrations api/migrations
RUN pip install --no-cache-dir ./api httpx
COPY config config
COPY tools/k8s tools/k8s
COPY tools/load tools/load
COPY deploy/helm/screening-closure/values-gke-loadtest.yaml deploy/helm/screening-closure/values-gke-loadtest.yaml
COPY deploy/gke/scenarios.sh deploy/gke/scenarios.sh

USER app
ENV HOME=/home/app
CMD ["bash", "/repo/deploy/gke/scenarios.sh"]
