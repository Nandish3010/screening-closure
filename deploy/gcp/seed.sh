#!/bin/bash
# Seeds the synthetic demo data on the VM and switches to production mode (see vm/seed-on-vm.sh). 20-40 minutes the first time.
#   seed.sh            first seed
#   seed.sh --fresh    wipe the database volumes and seed again
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/lib.sh"; need_project
gssh --command "sudo /srv/sc/repo/deploy/gcp/vm/seed-on-vm.sh ${1:-}"
echo "Public URL: https://$(public_host)/   (the certificate can take a minute on the first request)"
