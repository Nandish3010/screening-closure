#!/bin/bash
# "Reset demo": restore the seeded snapshot (cases, events, drafts and the FHIR store together) and verify the state hash.
# Protected by IAM: only a project member allowed to SSH through IAP can run it; there is no public reset endpoint.
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/lib.sh"; need_project
gssh --command 'sudo /srv/sc/repo/deploy/gcp/vm/demo-reset-on-vm.sh'
