#!/bin/bash
# On the VM, as root. Called by demo-reset-watch.sh when a reset FAILED half way: the reset script stops the API, scheduler, watchdog and
# HAPI before it replaces the databases, so a failure leaves them stopped. This starts whatever is not running again (no build, no
# secret rendering: that is sc-up.sh), so the site is back even if the database is not what the reset meant it to be. The operator
# sees why in /health demo.last_reset_error and decides (make gcp-reset again, or sc-up.sh). Idempotent.
set -uo pipefail
export SC_REPO=${SC_REPO:-/srv/sc/repo}
# shellcheck disable=SC1091
. "$SC_REPO/deploy/gcp/vm/env.sh"
docker compose up -d
