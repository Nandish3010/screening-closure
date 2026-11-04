# Sourced on the VM (by sc-up.sh, seed-on-vm.sh and demo-reset-on-vm.sh): points `docker compose` at the repository plus the GCP overlay.
SC_REPO=${SC_REPO:-/srv/sc/repo}
export COMPOSE_FILE="$SC_REPO/docker-compose.yml:$SC_REPO/deploy/gcp/compose.gcp.yml"
export COMPOSE_ENV_FILES=/etc/sc/compose.env
export COMPOSE_PROJECT_NAME=screening-closure
SC_MODE=$(cat /var/lib/sc/mode 2>/dev/null || echo seed)
if [ "$SC_MODE" = production ]; then export COMPOSE_PROFILES=public; else unset COMPOSE_PROFILES; fi
# tools/demo/snapshot.sh: the production API has no dev login, so the reset cannot move the simulation clock (it is wall time there).
[ "$SC_MODE" = production ] && export SNAPSHOT_SKIP_CLOCK_RESET=1
cd "$SC_REPO"
