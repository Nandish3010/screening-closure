#!/bin/bash
# Copies one media file (the submission video) to the VM, where the edge serves it read-only at https://<host>/media/<name>.
#   deploy/gcp/put-media.sh path/to/video.mp4 [published-name.mp4]
# The file is never committed to git. Allowed: .mp4 only, up to 100 MB. Egress for viewers is billed by Google Cloud (pennies per GB).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/lib.sh"; need_project
src=${1:?usage: put-media.sh FILE.mp4 [NAME.mp4]}
name=${2:-$(basename "$src")}
case "$name" in *[!A-Za-z0-9._-]*|.*) echo "bad name: $name" >&2; exit 2;; *.mp4) ;; *) echo "only .mp4" >&2; exit 2;; esac
[ "$(wc -c < "$src")" -le 104857600 ] || { echo "over 100 MB" >&2; exit 2; }
P=(--project "$GCP_PROJECT")
gcloud compute scp --tunnel-through-iap --zone "$GCP_ZONE" "${P[@]}" "$src" "$SC_VM_NAME:/tmp/sc-media.upload"
gssh --command "sudo install -d -m 0755 /srv/sc/media && sudo install -m 0644 /tmp/sc-media.upload /srv/sc/media/$name && rm -f /tmp/sc-media.upload && sha256sum /srv/sc/media/$name"
echo "https://$(public_host)/media/$name"
