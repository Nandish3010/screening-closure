#!/bin/bash
# Compute Engine startup script (instance metadata `startup-script`). Runs as root on EVERY boot. First boot: install Docker.
# Every boot: if the repository bundle has been delivered (deploy.sh), start the stack (sc-up.sh). Installs no secret.
set -euo pipefail
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg python3
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  mkdir -p /etc/docker
  echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
  systemctl restart docker
fi
mkdir -p /srv/sc /var/lib/sc
# 2 GB swap: a safety net for the JVMs on a small instance (not a substitute for the memory sizing in hosting-gcp.md).
if [ ! -f /swapfile ]; then fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab; fi
touch /var/lib/sc/ready-for-bundle
if [ -x /srv/sc/repo/deploy/gcp/vm/sc-up.sh ]; then /srv/sc/repo/deploy/gcp/vm/sc-up.sh; fi
