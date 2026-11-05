#!/bin/bash
# BUG-77 / P1-16 workaround: restart ONLY the API container once a day. The API's anonymous memory was seen to grow (see
# docs/submission/assumptions-and-limitations.md 6.1); the cause is not found, so this bounds the growth instead of fixing it.
# State is in Postgres, so a restart loses nothing; requests in flight at that moment fail and the browser retries (a few seconds).
# Run by the systemd timer sc-api-restart.timer (installed by startup.sh) as root.
set -euo pipefail
export SC_REPO=${SC_REPO:-/srv/sc/repo}
cd "$SC_REPO"
# shellcheck disable=SC1091
. "$SC_REPO/deploy/gcp/vm/env.sh"
docker compose restart api
echo "sc-api-restart: api restarted at $(date -u +%FT%TZ)"
