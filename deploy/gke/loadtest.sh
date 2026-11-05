#!/usr/bin/env bash
# NOT RUN: the owner has not approved cloud spend. `run` refuses unless SC_ALLOW_SPEND=1 is set.
# The GKE load test of the screening-closure engine: ONE command after `gcloud auth login` and choosing a project.
#
#   make gke-loadtest PROJECT=my-project [REGION=me-central1]      (this file, subcommand `run`)
#   make gke-teardown PROJECT=my-project                           (subcommand `teardown`: find and delete everything by label)
#   make gke-cost                                                   (subcommand `estimate`: the cost table, no cloud calls)
#   make gke-rehearse                                               (subcommand `rehearse`: the same pipeline on a local kind cluster)
#
# What `run` does, in order (docs/governance/gke-loadtest.md has the commands, the cost table and the teardown verification):
#   1  preflight: gcloud signed in, project, billing enabled, tools present;   2  cost table from deploy/gke/cost.py, REFUSES above MAX_USD
#   (default 30), then asks you to type "yes";   3  enable APIs, check quota and the network;   4  Artifact Registry repo, a zonal Standard
#   cluster with three node pools (app / data / load) and the images, in parallel;   5  secrets, the support chart (Postgres, JWKS), the
#   product chart (deploy/helm/screening-closure + values-gke-loadtest.yaml);   6  the load-generator POD in the cluster runs
#   deploy/gke/scenarios.sh (seed 1M, sweep, ladder, soak);   7  results are copied to results/gke-loadtest/<run id>/ every 5 minutes and at the end;
#   8  teardown, ALWAYS.
#
# Teardown guarantees: (a) an EXIT/INT/TERM/HUP trap runs the teardown on success, failure, Ctrl-C and kill; (b) a wall-clock watchdog
# (WALL_CAP_HOURS, default 4) signals this process, and if the teardown has not started 5 minutes later it runs `teardown` itself;
# (c) everything is created with the label sc-loadtest=true and removed by label, so `teardown` also finds what a crashed or killed
# run left behind; (d) it ends with a verification listing and a non-zero exit if anything is left. NOT covered: the laptop losing
# power or network for the whole cap while this script runs (nothing else is running anywhere); see the runbook, and set the budget alert.
#
# kubectl and helm only ever use the kubeconfig this script writes under results/ (KUBECONFIG is set to it): your own kubeconfig,
# contexts and clusters are never read or changed.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
SELF="$HERE/loadtest.sh"
MAIN_PID=$$

CMD="${1:-run}"
[ $# -gt 0 ] && shift
ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;               # teardown only: skip the confirmation (the trap and the watchdog use it)
    --project) PROJECT="$2"; shift ;;
    --run-id) RUN_ID="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

# ---- configuration (environment overrides) ---------------------------------------------------------------------------------------
: "${PROJECT:=}"
: "${REGION:=me-central1}"                   # Doha (docs/governance/hosting-gcp.md); override with a nearby region if it has no capacity
: "${ZONE_SUFFIXES:=a b c}"                  # zones tried in order when one has no capacity
: "${APP_MACHINE:=e2-standard-2}" ; : "${APP_NODES:=3}"     # the API (3 pods, one per node), scheduler x2, watchdog
: "${DATA_MACHINE:=e2-standard-8}" ; : "${DATA_NODES:=1}"   # Postgres
: "${LOAD_MACHINE:=e2-standard-4}" ; : "${LOAD_NODES:=1}"   # the load generator and the seeders
: "${BOOT_DISK_GB:=30}" ; : "${PG_DISK_GB:=120}"
: "${WALL_CAP_HOURS:=4}" ; : "${EXPECTED_HOURS:=3}" ; : "${MAX_USD:=30}"
: "${KEEP_RESULTS_EVERY:=300}"               # seconds between partial copies of the results folder
: "${RUN_ID:=$(date -u +%Y%m%d-%H%M%S)}"
: "${OUT_ROOT:=$ROOT/results/gke-loadtest}"
: "${SEED_SIZE:=1000000}" ; : "${SWEEP_SIZES:=1000000}" ; : "${SWEEP_WORKERS:=1,4}"
: "${LADDER_RATES:=50,100,175,250,300,400,500}" ; : "${LADDER_SECONDS:=180}"
: "${SOAK_RATE:=50}" ; : "${SOAK_SECONDS:=4500}" ; : "${SC_SEED_PROCS:=4}"
: "${MODE:=gke}"                              # gke | kind (rehearse sets kind)
LABEL_KEY=sc-loadtest
NS=closure
REL=closure
TAG=gke
CLUSTER="sc-lt-$RUN_ID"
REPO="sc-lt-$RUN_ID"
WALL_CAP_SECONDS=$(python3 -c "print(int(float('$WALL_CAP_HOURS') * 3600))")
export REGION APP_MACHINE APP_NODES DATA_MACHINE DATA_NODES LOAD_MACHINE LOAD_NODES BOOT_DISK_GB PG_DISK_GB WALL_CAP_HOURS EXPECTED_HOURS

OUT="$OUT_ROOT/$RUN_ID"
export KUBECONFIG="$OUT/kubeconfig"            # never the user's own kubeconfig
K() { kubectl "$@"; }
H() { helm "$@"; }
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

with_timeout() {  # with_timeout SECONDS CMD...: kill CMD after SECONDS (no coreutils `timeout` on macOS)
  local s="$1"; shift
  "$@" & local p=$!
  ( sleep "$s"; kill "$p" 2>/dev/null ) </dev/null >/dev/null 2>&1 & local w=$!
  wait "$p" 2>/dev/null; local rc=$?
  pkill -P "$w" 2>/dev/null; kill "$w" 2>/dev/null; wait "$w" 2>/dev/null   # the watcher and its sleep (children of that one pid only)
  return $rc
}

# ---- estimate ---------------------------------------------------------------------------------------------------------------------
if [ "$CMD" = estimate ]; then
  exec python3 "$HERE/cost.py" --max-usd "$MAX_USD"
fi

# ---- helpers for gcloud -----------------------------------------------------------------------------------------------------------
api_enabled() { [ -n "$(gcloud services list --enabled --project "$PROJECT" --filter="config.name=$1" --format='value(config.name)' 2>/dev/null)" ]; }
# the node service account must be able to pull from the registry (new projects may not grant the default compute account Editor)
node_sa() { echo "$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"; }

# ---- teardown: by label, idempotent, in dependency order ------------------------------------------------------------------------
LEFT=0
teardown_all() {
  local only_run="${1:-}"      # empty = every run labelled sc-loadtest=true in the project
  [ -n "$PROJECT" ] || die "teardown needs PROJECT"
  local sel="labels.$LABEL_KEY=true" csel="resourceLabels.$LABEL_KEY=true" namepat="^gke-sc-lt-"
  if [ -n "$only_run" ]; then
    sel="$sel AND labels.sc-loadtest-run=\"$only_run\""; csel="$csel AND resourceLabels.sc-loadtest-run=\"$only_run\""; namepat="^gke-sc-lt-$only_run"
  fi
  log "teardown: project $PROJECT, selector: $sel (plus the names $namepat that GKE gives its own resources)"
  local pvdisks; pvdisks=$(mktemp)

  if api_enabled container.googleapis.com; then
    local rows c loc
    rows=$(gcloud container clusters list --project "$PROJECT" --filter="$csel" --format='value(name,location)' 2>/dev/null)
    while read -r c loc; do
      [ -n "$c" ] || continue
      # Delete the StatefulSet and the PVCs first: a PersistentVolume's disk is not removed by deleting the cluster. The volume handles are
      # kept so a disk that lost its label (or a class without the `labels` parameter) is still deleted by name below.
      local kc; kc=$(mktemp)
      if KUBECONFIG="$kc" gcloud container clusters get-credentials "$c" --location "$loc" --project "$PROJECT" >/dev/null 2>&1; then
        KUBECONFIG="$kc" kubectl get pv -o jsonpath='{range .items[*]}{.spec.csi.volumeHandle}{"\n"}{end}' 2>/dev/null >>"$pvdisks"
        with_timeout 180 env KUBECONFIG="$kc" kubectl -n "$NS" delete statefulset postgres --wait=true --timeout=150s >/dev/null 2>&1 || true
        with_timeout 240 env KUBECONFIG="$kc" kubectl -n "$NS" delete pvc --all --wait=true --timeout=200s >/dev/null 2>&1 || true
      fi
      rm -f "$kc"
      log "teardown: deleting cluster $c ($loc)"
      gcloud container clusters delete "$c" --location "$loc" --project "$PROJECT" --quiet 2>&1 | tail -2
    done <<<"$rows"
  fi

  if api_enabled compute.googleapis.com; then
    local name zone handle
    # persistent disks: the Postgres volume (by label, and by the volume handle recorded above), leftovers of a cluster deleted before its
    # PVCs, boot disks of a node whose VM is gone
    while read -r handle; do
      [ -n "$handle" ] || continue
      name="${handle##*/}"; zone=$(echo "$handle" | sed -nE 's|.*/zones/([^/]+)/disks/.*|\1|p')
      [ -n "$zone" ] && gcloud compute disks describe "$name" --zone "$zone" --project "$PROJECT" >/dev/null 2>&1 &&
        { log "teardown: deleting volume disk $name ($zone)"; gcloud compute disks delete "$name" --zone "$zone" --project "$PROJECT" --quiet 2>&1 | tail -1; }
    done <"$pvdisks"
    gcloud compute disks list --project "$PROJECT" --filter="$sel OR name~$namepat" --format='value(name,zone.basename())' 2>/dev/null |
      while read -r name zone; do [ -n "$name" ] && { log "teardown: deleting disk $name ($zone)"; gcloud compute disks delete "$name" --zone "$zone" --project "$PROJECT" --quiet 2>&1 | tail -1; }; done
    # VMs, instance groups and templates of a node pool the cluster delete did not finish removing
    gcloud compute instances list --project "$PROJECT" --filter="name~$namepat" --format='value(name,zone.basename())' 2>/dev/null |
      while read -r name zone; do [ -n "$name" ] && { log "teardown: deleting instance $name"; gcloud compute instances delete "$name" --zone "$zone" --project "$PROJECT" --quiet --delete-disks=all 2>&1 | tail -1; }; done
    gcloud compute instance-groups managed list --project "$PROJECT" --filter="name~$namepat" --format='value(name,zone.basename())' 2>/dev/null |
      while read -r name zone; do [ -n "$name" ] && gcloud compute instance-groups managed delete "$name" --zone "$zone" --project "$PROJECT" --quiet 2>&1 | tail -1; done
    gcloud compute instance-templates list --project "$PROJECT" --filter="name~$namepat" --format='value(name)' 2>/dev/null |
      while read -r name; do [ -n "$name" ] && gcloud compute instance-templates delete "$name" --project "$PROJECT" --quiet 2>&1 | tail -1; done
    # static IP addresses (none are created; a Service of type LoadBalancer someone added would reserve one)
    gcloud compute addresses list --project "$PROJECT" --filter="$sel" --format='value(name,region.basename())' 2>/dev/null |
      while read -r name zone; do
        [ -n "$name" ] || continue
        if [ -n "$zone" ]; then gcloud compute addresses delete "$name" --region "$zone" --project "$PROJECT" --quiet 2>&1 | tail -1
        else gcloud compute addresses delete "$name" --global --project "$PROJECT" --quiet 2>&1 | tail -1; fi
      done
    # firewall rules and routes GKE created for the cluster (the cluster delete normally removes them)
    gcloud compute firewall-rules list --project "$PROJECT" --filter="name~$namepat" --format='value(name)' 2>/dev/null |
      while read -r name; do [ -n "$name" ] && gcloud compute firewall-rules delete "$name" --project "$PROJECT" --quiet 2>&1 | tail -1; done
    gcloud compute routes list --project "$PROJECT" --filter="name~$namepat" --format='value(name)' 2>/dev/null |
      while read -r name; do [ -n "$name" ] && gcloud compute routes delete "$name" --project "$PROJECT" --quiet 2>&1 | tail -1; done
  fi
  rm -f "$pvdisks"

  if api_enabled artifactregistry.googleapis.com; then
    local full
    gcloud artifacts repositories list --project "$PROJECT" --filter="$sel" --format='value(name)' 2>/dev/null |
      while read -r full; do
        [ -n "$full" ] || continue
        log "teardown: deleting registry repository $full (and every image in it)"
        gcloud artifacts repositories delete "${full##*/}" --location "$(echo "$full" | sed -E 's|.*/locations/([^/]+)/.*|\1|')" --project "$PROJECT" --quiet 2>&1 | tail -1
      done
  fi

  # Cloud SQL: this test does not create an instance (Postgres runs in the cluster); the sweep is here so a manual one cannot be forgotten
  if api_enabled sqladmin.googleapis.com; then
    gcloud sql instances list --project "$PROJECT" --filter="settings.userLabels.$LABEL_KEY=true" --format='value(name)' 2>/dev/null |
      while read -r name; do [ -n "$name" ] && { log "teardown: deleting Cloud SQL instance $name"; gcloud sql instances delete "$name" --project "$PROJECT" --quiet 2>&1 | tail -1; }; done
  fi
  verify_clean "$sel" "$csel" "$namepat"
}

verify_clean() {  # prints what is left; sets LEFT=1 when anything is
  local sel="$1" csel="$2" namepat="$3" out="" part
  LEFT=0
  if api_enabled container.googleapis.com; then
    part=$(gcloud container clusters list --project "$PROJECT" --filter="$csel" --format='value(name)' 2>/dev/null); [ -n "$part" ] && out="$out\nclusters:\n$part"; fi
  if api_enabled compute.googleapis.com; then
    part=$(gcloud compute disks list --project "$PROJECT" --filter="$sel OR name~$namepat" --format='value(name)' 2>/dev/null); [ -n "$part" ] && out="$out\ndisks:\n$part"
    part=$(gcloud compute instances list --project "$PROJECT" --filter="name~$namepat" --format='value(name)' 2>/dev/null); [ -n "$part" ] && out="$out\ninstances:\n$part"
    part=$(gcloud compute addresses list --project "$PROJECT" --filter="$sel" --format='value(name)' 2>/dev/null); [ -n "$part" ] && out="$out\naddresses:\n$part"
  fi
  if api_enabled artifactregistry.googleapis.com; then
    part=$(gcloud artifacts repositories list --project "$PROJECT" --filter="$sel" --format='value(name)' 2>/dev/null); [ -n "$part" ] && out="$out\nregistry repositories:\n$part"; fi
  if api_enabled sqladmin.googleapis.com; then
    part=$(gcloud sql instances list --project "$PROJECT" --filter="settings.userLabels.$LABEL_KEY=true" --format='value(name)' 2>/dev/null); [ -n "$part" ] && out="$out\nSQL instances:\n$part"; fi
  if [ -n "$out" ]; then
    LEFT=1
    printf 'TEARDOWN INCOMPLETE, still present in project %s:%b\n' "$PROJECT" "$out" >&2
    echo "Delete them by hand (docs/governance/gke-loadtest.md, 'If the teardown fails') or run: make gke-teardown PROJECT=$PROJECT" >&2
  else
    log "teardown verified: no cluster, disk, instance, address, registry repository or SQL instance labelled $LABEL_KEY=true is left in $PROJECT"
  fi
  printf '%b\n' "${out:-none left}" > "${OUT:-/dev/null}/teardown-verification.txt" 2>/dev/null || true
}

if [ "$CMD" = teardown ]; then
  [ "$MODE" = kind ] && die "use: kind delete cluster --name sc-gke-rehearsal"
  [ -n "$PROJECT" ] || PROJECT=$(gcloud config get-value project 2>/dev/null || true)
  [ -n "$PROJECT" ] || die "PROJECT=... is required (or gcloud config set project ID)"
  gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q . || die "gcloud is not signed in: gcloud auth login"
  echo "Resources labelled $LABEL_KEY=true in project $PROJECT that would be deleted:"
  gcloud container clusters list --project "$PROJECT" --filter="resourceLabels.$LABEL_KEY=true" --format='table(name,location,status)' 2>/dev/null || true
  gcloud compute disks list --project "$PROJECT" --filter="labels.$LABEL_KEY=true OR name~^gke-sc-lt-" --format='table(name,zone.basename(),sizeGb,status)' 2>/dev/null || true
  gcloud artifacts repositories list --project "$PROJECT" --filter="labels.$LABEL_KEY=true" --format='table(name)' 2>/dev/null || true
  if [ "$ASSUME_YES" != 1 ]; then
    read -r -p 'Type "yes" to delete all of it: ' ans </dev/tty || ans=""
    [ "$ans" = yes ] || { echo "not confirmed; nothing deleted"; exit 1; }
  fi
  teardown_all "${RUN_ID_FILTER:-}"
  exit "$LEFT"
fi

# ---- run / rehearse ----------------------------------------------------------------------------------------------------------------
[ "$CMD" = run ] || [ "$CMD" = rehearse ] || die "unknown command: $CMD (run | teardown | estimate | rehearse)"
if [ "$CMD" = rehearse ]; then
  MODE=kind
  CLUSTER="sc-gke-rehearsal"
  SEED_SIZE="${REHEARSAL_SEED_SIZE:-20000}"; SWEEP_SIZES="$SEED_SIZE"; SWEEP_WORKERS=1
  LADDER_RATES="${REHEARSAL_RATES:-20,40}"; LADDER_SECONDS="${REHEARSAL_STAGE_SECONDS:-30}"
  SOAK_RATE=20; SOAK_SECONDS="${REHEARSAL_SOAK_SECONDS:-120}"; SC_SEED_PROCS=2
fi

# The owner has NOT approved any cloud spend (budget limit): the paid run is refused unless SC_ALLOW_SPEND=1 is set on purpose, before any
# gcloud call, on top of the typed "yes" below. The local kind rehearsal costs nothing and is not affected.
if [ "$MODE" = gke ] && [ "${SC_ALLOW_SPEND:-}" != 1 ]; then
  echo "REFUSED: this run would create billable Google Cloud resources and the owner has not approved spend (budget limit)." >&2
  echo "If that changes, set SC_ALLOW_SPEND=1 (and read docs/governance/gke-loadtest.md). Nothing was called or created." >&2
  exit 3
fi

mkdir -p "$OUT/results" "$OUT/logs"
START_EPOCH=$(date +%s)
CREATED=0; CLEANED=0; CAP_PID=""; LOGS_PID=""; CAFF_PID=""
ZONE=""; REGISTRY=""; RESULT_RC=0

stamp() { printf '{"event":"%s","at":"%s","epoch":%s}\n' "$1" "$(date -u +%FT%TZ)" "$(date +%s)" >> "$OUT/timeline.jsonl"; }

# ---- cleanup: ALWAYS --------------------------------------------------------------------------------------------------------------
collect_results() {  # copy /results out of the load-generator pod (best effort, bounded)
  [ -s "$KUBECONFIG" ] || return 0
  K -n "$NS" get pod loadgen >/dev/null 2>&1 || return 0
  mkdir -p "$OUT/results"
  with_timeout 180 bash -c "kubectl -n $NS exec loadgen -- tar cf - -C /results . 2>/dev/null | tar xf - -C '$OUT/results' 2>/dev/null" || true
}

cleanup() {
  local rc="${1:-$?}"
  [ "$CLEANED" = 1 ] && return
  CLEANED=1
  # the teardown is never interrupted: a signal now only prints a line (a handler, not "ignore": ignored signals stay ignored in the subshells with_timeout starts)
  trap 'echo "(signal received during the teardown: it continues)"' INT TERM HUP
  echo
  log "cleanup (exit code so far $rc): collecting results, then tearing everything down"
  [ -n "$LOGS_PID" ] && kill "$LOGS_PID" 2>/dev/null
  if [ "$CREATED" = 1 ]; then
    collect_results
    capture_final_state
    if [ "$MODE" = kind ] && [ "${REHEARSE_KEEP:-}" = 1 ]; then
      log "REHEARSE_KEEP=1: the kind cluster $CLUSTER is kept for debugging; delete it with: kind delete cluster --name $CLUSTER"
      KEEP_KC=1
    elif [ "$MODE" = kind ]; then
      kind delete cluster --name "$CLUSTER" 2>&1 | tail -1
      log "kind cluster $CLUSTER deleted"
    else
      stamp teardown_start
      teardown_all "$RUN_ID"
      stamp teardown_end
      [ "$LEFT" = 0 ] || rc=1
    fi
  fi
  write_run_json "$rc"
  [ -n "$CAP_PID" ] && { pkill -P "$CAP_PID" 2>/dev/null; kill "$CAP_PID" 2>/dev/null; }   # its sleep too (children of that one pid only)
  [ -n "$CAFF_PID" ] && kill "$CAFF_PID" 2>/dev/null
  [ "${KEEP_KC:-}" = 1 ] || rm -f "$KUBECONFIG"           # the file holds a cluster credential; the cluster is gone
  log "results: $OUT"
  exit "$rc"
}
trap 'cleanup $?' EXIT
trap 'echo "interrupted (SIGINT)"; exit 130' INT
trap 'echo "terminated (SIGTERM)"; exit 143' TERM
trap 'echo "hang-up (SIGHUP)"; exit 129' HUP

capture_final_state() {
  [ -s "$KUBECONFIG" ] || return 0
  mkdir -p "$OUT/cluster"
  with_timeout 60 bash -c "kubectl get pods -A -o wide > '$OUT/cluster/pods.txt' 2>&1; kubectl get nodes -o wide > '$OUT/cluster/nodes.txt' 2>&1; kubectl -n $NS get events --sort-by=.lastTimestamp > '$OUT/cluster/events.txt' 2>&1; kubectl top pods -A > '$OUT/cluster/top-pods.txt' 2>&1; kubectl -n $NS get pods -o json > '$OUT/cluster/pods.json' 2>&1" || true
}

write_run_json() {
  local rc="$1"
  python3 - "$OUT" "$rc" <<'PY' || true
import json, os, sys, subprocess
out, rc = sys.argv[1], int(sys.argv[2])
def jl(p):
    try: return [json.loads(l) for l in open(p) if l.strip()]
    except OSError: return []
env = os.environ
cost = None
try:
    cost = json.loads(subprocess.run([sys.executable, os.path.join(env["ROOT_DIR"], "deploy/gke/cost.py"), "--json", "--max-usd", "1000"], capture_output=True, text=True).stdout)
except Exception:
    pass
status = {}
try: status = json.load(open(os.path.join(out, "results", "result-status.json")))
except Exception: pass
doc = {"schema_version": 1, "tool": "deploy/gke/loadtest.sh", "mode": env["MODE"], "run_id": env["RUN_ID"], "exit_code": rc,
       "region": env["REGION"] if env["MODE"] == "gke" else "local kind", "zone": env.get("ZONE_USED", ""),
       "machines": {"app": [env["APP_MACHINE"], int(env["APP_NODES"])], "data": [env["DATA_MACHINE"], int(env["DATA_NODES"])], "load": [env["LOAD_MACHINE"], int(env["LOAD_NODES"])]},
       "wall_cap_hours": float(env["WALL_CAP_HOURS"]), "scenario": {k: env[k] for k in ("SEED_SIZE", "SWEEP_SIZES", "SWEEP_WORKERS", "LADDER_RATES", "LADDER_SECONDS", "SOAK_RATE", "SOAK_SECONDS")},
       "timeline": jl(os.path.join(out, "timeline.jsonl")), "phases": jl(os.path.join(out, "results", "phases.jsonl")), "result_status": status,
       "cost_estimate": cost, "git": {"commit": env.get("GIT_COMMIT", ""), "branch": env.get("GIT_BRANCH", "")}}
for name in ("resources-created.txt", "teardown-verification.txt"):
    try: doc[name.rsplit(".", 1)[0]] = open(os.path.join(out, name)).read().splitlines()
    except OSError: pass
json.dump(doc, open(os.path.join(out, "run.json"), "w"), indent=2)
PY
}
export ROOT_DIR="$ROOT" MODE RUN_ID SEED_SIZE SWEEP_SIZES SWEEP_WORKERS LADDER_RATES LADDER_SECONDS SOAK_RATE SOAK_SECONDS

# ---- 1. preflight -----------------------------------------------------------------------------------------------------------------
log "run $RUN_ID (mode $MODE), output $OUT"
GIT_COMMIT=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown); GIT_BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
export GIT_COMMIT GIT_BRANCH
DIRTY=$(git -C "$ROOT" status --porcelain 2>/dev/null | grep -v '^??' | head -3)
[ -z "$DIRTY" ] || log "note: the working tree has uncommitted changes; the images are built from it and the results record the commit only"

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"; }
need kubectl "Install kubectl."; need helm "brew install helm"; need docker "Docker is needed to build the images."; need openssl "openssl is needed to generate the Secrets."; need python3 "python3 is needed."
docker info >/dev/null 2>&1 || die "docker is not running"
if [ "$MODE" = gke ]; then
  need gcloud "Install the Google Cloud CLI."
  need gke-gcloud-auth-plugin "kubectl needs it for GKE: gcloud components install gke-gcloud-auth-plugin (not installed by this script)."
  docker buildx version >/dev/null 2>&1 || die "docker buildx is needed to build linux/amd64 images"
  ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)
  [ -n "$ACCOUNT" ] || die "gcloud has no active account: run 'gcloud auth login' first"
  [ -n "$PROJECT" ] || PROJECT=$(gcloud config get-value project 2>/dev/null || true)
  [ -n "$PROJECT" ] || die "no project: make gke-loadtest PROJECT=<id>  (or gcloud config set project <id>)"
  gcloud projects describe "$PROJECT" --format='value(projectId)' >/dev/null 2>&1 || die "cannot read project $PROJECT with account $ACCOUNT"
  BILLING=$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null || true)
  [ "$BILLING" = True ] || die "billing is not enabled on project $PROJECT (billingEnabled='$BILLING'). Link a billing account in the console, then rerun."
  log "signed in as $ACCOUNT; project $PROJECT; billing enabled"
  export PROJECT

  # ---- 2. cost guard ---------------------------------------------------------------------------------------------------------------
  python3 "$HERE/cost.py" --max-usd "$MAX_USD" | tee "$OUT/cost-estimate.txt"
  [ "${PIPESTATUS[0]}" = 0 ] || die "the worst-case cost is above MAX_USD=$MAX_USD; shrink the machine types or the wall-clock cap (nothing was created)"
  echo
  echo "This will CREATE BILLABLE resources in project $PROJECT, region $REGION (zone $REGION-{${ZONE_SUFFIXES// /,}}), labelled $LABEL_KEY=true:"
  echo "  1 zonal GKE Standard cluster, node pools app ${APP_NODES}x$APP_MACHINE, data ${DATA_NODES}x$DATA_MACHINE, load ${LOAD_NODES}x$LOAD_MACHINE,"
  echo "  a ${PG_DISK_GB} GB pd-ssd volume, an Artifact Registry repository, and it will delete all of them at the end (hard cap ${WALL_CAP_HOURS} h)."
  [ -t 0 ] || die "stdin is not a terminal: the confirmation must be typed"
  read -r -p 'Type "yes" to continue: ' ans
  [ "$ans" = yes ] || { echo "not confirmed; nothing was created"; CREATED=0; exit 1; }
fi
stamp confirmed

# ---- the wall-clock watchdog and sleep prevention (started before the first resource exists) ----------------------------------------
if [ "$WALL_CAP_SECONDS" -gt 0 ]; then
  (
    sleep "$WALL_CAP_SECONDS"
    echo "WALL-CLOCK CAP of $WALL_CAP_HOURS h reached: tearing down" >&2
    kill -TERM "$MAIN_PID" 2>/dev/null
    sleep 300
    echo "the run did not start its teardown within 5 minutes: running it from the watchdog" >&2
    if [ "$MODE" = gke ]; then PROJECT="$PROJECT" RUN_ID_FILTER="$RUN_ID" "$SELF" teardown --yes --project "$PROJECT"; else kind delete cluster --name "$CLUSTER"; fi
  ) >"$OUT/logs/cap-watchdog.log" 2>&1 &
  CAP_PID=$!
fi
command -v caffeinate >/dev/null 2>&1 && { caffeinate -i -w "$MAIN_PID" </dev/null >/dev/null 2>&1 & CAFF_PID=$!; }

# ---- 3-4. infrastructure ----------------------------------------------------------------------------------------------------------
build_images() {  # build_images PLATFORM_ARGS... : the four images; gke pushes, kind loads
  local registry="$1" mode="$2"
  local -a common
  if [ "$mode" = gke ]; then common=(docker buildx build --platform linux/amd64 --provenance=false --push --progress=plain)
  else common=(docker build --provenance=false --progress=quiet); fi
  local pids=() names=()
  for spec in "api|--target runtime api" "scheduler|scheduler" "watchdog|watchdog" "loadgen|-f deploy/gke/loadgen.Dockerfile ."; do
    local name="${spec%%|*}" args="${spec#*|}"
    ( cd "$ROOT" && "${common[@]}" -t "$registry/screening-closure-$name:$TAG" $args ) >"$OUT/logs/build-$name.log" 2>&1 &
    pids+=($!); names+=("$name")
  done
  local i rc=0
  for i in "${!pids[@]}"; do
    wait "${pids[$i]}" || { echo "image build failed: ${names[$i]} (see $OUT/logs/build-${names[$i]}.log)" >&2; rc=1; }
  done
  return $rc
}

create_cluster_any_zone() {
  local sfx z
  for sfx in $ZONE_SUFFIXES; do
    z="$REGION-$sfx"
    log "creating cluster $CLUSTER in $z"
    if gcloud container clusters create "$CLUSTER" --project "$PROJECT" --zone "$z" --node-locations "$z" \
        --release-channel None --no-enable-autoupgrade --no-enable-autorepair \
        --num-nodes "$APP_NODES" --machine-type "$APP_MACHINE" --disk-type pd-balanced --disk-size "$BOOT_DISK_GB" \
        --node-labels sc-pool=app --labels "$LABEL_KEY=true,sc-loadtest-run=$RUN_ID" \
        --logging=SYSTEM --monitoring=SYSTEM --enable-ip-alias --quiet >"$OUT/logs/cluster-create-$sfx.log" 2>&1; then
      echo "$z" >"$OUT/zone"; return 0
    fi
    log "cluster creation failed in $z (see logs/cluster-create-$sfx.log); removing what it left and trying the next zone"
    gcloud container clusters delete "$CLUSTER" --zone "$z" --project "$PROJECT" --quiet >/dev/null 2>&1 || true
  done
  return 1
}

create_pool() {  # create_pool NAME MACHINE COUNT
  gcloud container node-pools create "$1" --cluster "$CLUSTER" --zone "$ZONE" --project "$PROJECT" --num-nodes "$3" --machine-type "$2" \
    --disk-type pd-balanced --disk-size "$BOOT_DISK_GB" --node-labels "sc-pool=$1" --no-enable-autoupgrade --no-enable-autorepair --quiet \
    >"$OUT/logs/pool-$1.log" 2>&1
}

CREATED=1   # from here on a failure runs the teardown (it is idempotent and finds nothing to delete if nothing was created)
if [ "$MODE" = gke ]; then
  stamp infra_start
  log "enabling APIs (free; they stay enabled)"
  gcloud services enable container.googleapis.com artifactregistry.googleapis.com compute.googleapis.com --project "$PROJECT" || die "could not enable the APIs"
  gcloud compute networks describe default --project "$PROJECT" >/dev/null 2>&1 || die "project $PROJECT has no 'default' VPC network; create one or adapt create_cluster_any_zone"
  python3 - "$REGION" "$PROJECT" "$((APP_NODES * 2 + DATA_NODES * 8 + LOAD_NODES * 4))" "$PG_DISK_GB" <<'PY' || die "regional quota check failed"
import json, subprocess, sys
region, project, need_cpu, need_ssd = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
q = {x["metric"]: x for x in json.loads(subprocess.run(["gcloud", "compute", "regions", "describe", region, "--project", project, "--format=json"], capture_output=True, text=True, check=True).stdout)["quotas"]}
bad = []
for metric, need in (("CPUS", need_cpu), ("SSD_TOTAL_GB", need_ssd), ("IN_USE_ADDRESSES", 6)):
    if metric in q:
        free = q[metric]["limit"] - q[metric]["usage"]
        print(f"quota {metric}: {free:g} free of {q[metric]['limit']:g}, need about {need}")
        if free < need: bad.append(metric)
if bad:
    sys.exit(f"not enough regional quota for: {', '.join(bad)}. Request an increase (IAM & Admin > Quotas) or shrink APP_MACHINE/DATA_MACHINE/LOAD_MACHINE; nothing was created.")
PY
  REGISTRY_HOST="$REGION-docker.pkg.dev"
  REGISTRY="$REGISTRY_HOST/$PROJECT/$REPO"
  log "creating Artifact Registry repository $REPO"
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION" --project "$PROJECT" \
    --labels "$LABEL_KEY=true,sc-loadtest-run=$RUN_ID" --description "sc-loadtest $RUN_ID (deleted by the test)" --quiet || die "could not create the registry repository"
  gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" --project "$PROJECT" \
    --member "serviceAccount:$(node_sa)" --role roles/artifactregistry.reader --quiet >/dev/null || log "warning: could not grant the node service account read access to the repository"
  gcloud auth configure-docker "$REGISTRY_HOST" --quiet >/dev/null 2>&1 || die "gcloud auth configure-docker failed"

  # the cluster and the image builds run at the same time
  ( create_cluster_any_zone ) & CLUSTER_JOB=$!
  build_images "$REGISTRY" gke & BUILD_JOB=$!
  wait "$CLUSTER_JOB" || die "no zone of $REGION could create the cluster (capacity or quota). Rerun with another REGION= (nothing is left: the teardown ran)."
  ZONE=$(cat "$OUT/zone"); export ZONE_USED="$ZONE"
  log "cluster created in $ZONE; adding the data and load node pools"
  create_pool data "$DATA_MACHINE" "$DATA_NODES" & P1=$!
  create_pool load "$LOAD_MACHINE" "$LOAD_NODES" & P2=$!
  wait "$P1" || die "node pool data failed (logs/pool-data.log)"
  wait "$P2" || die "node pool load failed (logs/pool-load.log)"
  wait "$BUILD_JOB" || die "image build or push failed (logs/build-*.log)"
  stamp infra_ready
  gcloud container clusters get-credentials "$CLUSTER" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1 || die "get-credentials failed"
  {
    echo "== resources created in project $PROJECT (label $LABEL_KEY=true, run $RUN_ID) =="
    gcloud container clusters list --project "$PROJECT" --filter="resourceLabels.sc-loadtest-run=\"$RUN_ID\"" --format='table(name,location,status,currentNodeCount,currentMasterVersion)'
    gcloud container node-pools list --cluster "$CLUSTER" --zone "$ZONE" --project "$PROJECT" --format='table(name,config.machineType,initialNodeCount,config.diskSizeGb,version)'
    gcloud compute instances list --project "$PROJECT" --filter="name~^gke-$CLUSTER" --format='table(name,zone.basename(),machineType.basename(),status)'
    gcloud compute disks list --project "$PROJECT" --filter="name~^gke-$CLUSTER OR labels.sc-loadtest-run=\"$RUN_ID\"" --format='table(name,zone.basename(),sizeGb,type.basename())'
    gcloud artifacts repositories list --project "$PROJECT" --filter="labels.sc-loadtest-run=\"$RUN_ID\"" --format='table(name,format,location)'
  } 2>&1 | tee "$OUT/resources-created.txt"
else
  need kind "brew install kind"
  stamp infra_start
  REGISTRY="local"
  cat >"$OUT/kind.yaml" <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: $CLUSTER
nodes:
  - role: control-plane
  - role: worker
    labels: { sc-pool: app }
  - role: worker
    labels: { sc-pool: data }
  - role: worker
    labels: { sc-pool: load }
YAML
  kind get clusters 2>/dev/null | grep -qx "$CLUSTER" && die "kind cluster $CLUSTER already exists (kind delete cluster --name $CLUSTER)"
  kind create cluster --config "$OUT/kind.yaml" --kubeconfig "$KUBECONFIG" --wait 180s || die "kind create failed"
  # every pool label must exist on a node; the three API pods spread over the app-labelled nodes if there are several
  build_images "$REGISTRY" kind || die "image build failed (logs/build-*.log)"
  platform="linux/$(docker info --format '{{.Architecture}}' | sed 's/aarch64/arm64/; s/x86_64/amd64/')"
  docker image inspect postgres:16 >/dev/null 2>&1 || docker pull postgres:16 >/dev/null
  tmp=$(mktemp -d)
  for img in "$REGISTRY/screening-closure-api:$TAG" "$REGISTRY/screening-closure-scheduler:$TAG" "$REGISTRY/screening-closure-watchdog:$TAG" "$REGISTRY/screening-closure-loadgen:$TAG" postgres:16; do
    docker save --platform "$platform" -o "$tmp/image.tar" "$img" && kind load image-archive --name "$CLUSTER" "$tmp/image.tar" >/dev/null || die "could not load $img into kind"
    rm -f "$tmp/image.tar"
  done
  rmdir "$tmp" 2>/dev/null
  stamp infra_ready
fi

# ---- 5. secrets, support chart, product chart --------------------------------------------------------------------------------------
log "namespace, Secrets, ConfigMaps"
K create namespace "$NS" >/dev/null 2>&1 || true
K label namespace "$NS" "$LABEL_KEY=true" --overwrite >/dev/null
tmp="$(umask 077 && mktemp -d)"
rand() { openssl rand -hex 32; }
printf 'password=%s\n' "$(rand)" >"$tmp/pg.env"
K -n "$NS" create secret generic sc-postgres --from-env-file="$tmp/pg.env" >/dev/null || die "could not create the Postgres Secret"
pg="$(K -n "$NS" get secret sc-postgres -o jsonpath='{.data.password}' | base64 -d)"
app="$(rand)"
{
  echo "DATABASE_URL=postgresql://closure_app:${app}@postgres:5432/closure?sslmode=disable"
  echo "MIGRATE_DATABASE_URL=postgresql://closure_owner:${pg}@postgres:5432/closure?sslmode=disable"
  echo "APP_DB_PASSWORD=${app}"
  for key in DEV_AUTH_SECRET SCHEDULER_SERVICE_TOKEN WATCHDOG_SERVICE_TOKEN AUTOMATION_SERVICE_TOKEN AGENCY_SERVICE_TOKEN METRICS_SCRAPE_TOKEN; do echo "${key}=$(rand)"; done
} >"$tmp/app.env"
K -n "$NS" create secret generic screening-closure-secrets --from-env-file="$tmp/app.env" >/dev/null || die "could not create the application Secret"
openssl genrsa -out "$tmp/private.pem" 2048 2>/dev/null
K -n "$NS" create secret generic sc-oidc-signing --from-file=private.pem="$tmp/private.pem" >/dev/null
PYBIN="$ROOT/.venv/bin/python"; [ -x "$PYBIN" ] || PYBIN=python3
"$PYBIN" "$ROOT/tools/k8s/oidc_keys.py" "$tmp/private.pem" >"$tmp/jwks.json" || die "oidc_keys.py failed (needs the repository .venv)"
K -n "$NS" create configmap sc-jwks --from-file=jwks.json="$tmp/jwks.json" >/dev/null
rm -rf "$tmp"
( cd "$ROOT" && K -n "$NS" create configmap screening-closure-config \
  --from-file=calendar.yaml=config/calendar.yaml --from-file=colorectal_fit.yaml=config/pathways/colorectal_fit.yaml \
  --from-file=providers.yaml=config/providers.yaml --from-file=outreach_policy.yaml=config/outreach_policy.yaml \
  --from-file=agency.yaml=config/agency.yaml >/dev/null ) || die "could not create the config ConfigMap"

INFRA_SETS=(--set "runId=$RUN_ID" --set "images.api=$REGISTRY/screening-closure-api:$TAG" --set "images.loadgen=$REGISTRY/screening-closure-loadgen:$TAG")
PRODUCT_SETS=(--set "images.api.repository=$REGISTRY/screening-closure-api" --set "images.scheduler.repository=$REGISTRY/screening-closure-scheduler"
              --set "images.watchdog.repository=$REGISTRY/screening-closure-watchdog" --set "images.api.tag=$TAG" --set "images.scheduler.tag=$TAG"
              --set "images.watchdog.tag=$TAG" --set scheduler.replicas=0 --set watchdog.replicas=0)
if [ "$MODE" = kind ]; then
  # the rehearsal: Docker Desktop's VM is small and shared; same objects, smaller numbers
  INFRA_SETS+=(--set storageClass.create=false --set images.postgres=postgres:16 --set images.pullPolicy=Never
               --set postgres.storage=5Gi --set postgres.resources.requests.cpu=250m --set postgres.resources.requests.memory=512Mi
               --set postgres.resources.limits.memory=2Gi --set postgres.shmSize=256Mi --set loadgen.resources.requests.cpu=500m
               --set loadgen.resources.requests.memory=512Mi --set loadgen.resources.limits.cpu=2 --set loadgen.resources.limits.memory=2Gi
               --set 'postgres.args={-c,max_connections=200,-c,shared_buffers=256MB,-c,random_page_cost=1.1,-c,max_wal_size=2GB}')
  PRODUCT_SETS+=(--set images.api.pullPolicy=Never --set images.scheduler.pullPolicy=Never --set images.watchdog.pullPolicy=Never
                 --set api.resources.requests.cpu=100m --set api.resources.requests.memory=128Mi --set api.resources.limits.memory=768Mi
)
fi

log "installing the support chart (Postgres, JWKS)"
H upgrade --install sc-loadtest "$HERE/chart" -n "$NS" "${INFRA_SETS[@]}" --wait --timeout 15m >"$OUT/logs/helm-infra.log" 2>&1 || { tail -20 "$OUT/logs/helm-infra.log" >&2; die "support chart failed"; }
log "installing the product chart (migration job, 3 API pods; scheduler and watchdog start after the seed)"
H upgrade --install "$REL" "$ROOT/deploy/helm/screening-closure" -n "$NS" -f "$ROOT/deploy/helm/screening-closure/values-gke-loadtest.yaml" \
  "${PRODUCT_SETS[@]}" --wait --timeout 15m >"$OUT/logs/helm-product.log" 2>&1 || { tail -20 "$OUT/logs/helm-product.log" >&2; K -n "$NS" get pods >&2; die "product chart failed"; }
stamp deployed
K -n "$NS" get pods -o wide | tee "$OUT/logs/pods-after-install.txt"

# ---- run-info for the in-pod tools (they have no helm, docker or sysctl) ----------------------------------------------------------
python3 - "$OUT" "$MODE" "$REGION" "${ZONE:-}" <<'PY'
import json, subprocess, sys
out, mode, region, zone = sys.argv[1:5]
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()
nodes = json.loads(sh("kubectl", "get", "nodes", "-o", "json") or "{}").get("items", [])
spec = {"environment": "GKE zonal Standard cluster" if mode == "gke" else "local kind rehearsal (not a measurement)",
        "region": region if mode == "gke" else None, "zone": zone or None,
        "kubernetes_version": (json.loads(sh("kubectl", "version", "-o", "json") or "{}").get("serverVersion") or {}).get("gitVersion"),
        "helm": sh("helm", "version", "--short"),
        "nodes": [{"name": n["metadata"]["name"], "pool": n["metadata"]["labels"].get("sc-pool"),
                   "machine_type": n["metadata"]["labels"].get("node.kubernetes.io/instance-type"), "arch": n["status"]["nodeInfo"]["architecture"],
                   "os_image": n["status"]["nodeInfo"]["osImage"], "kubelet": n["status"]["nodeInfo"]["kubeletVersion"],
                   "allocatable_cpu": n["status"]["allocatable"]["cpu"], "allocatable_memory": n["status"]["allocatable"]["memory"]} for n in nodes],
        "postgres_version": sh("kubectl", "-n", "closure", "exec", "statefulset/postgres", "--", "psql", "-U", "closure_owner", "-d", "closure", "-At", "-c", "show server_version")}
json.dump(spec, open(f"{out}/machine_spec.json", "w"), indent=2)
json.dump(json.loads(sh("helm", "-n", "closure", "get", "values", "closure", "--all", "-o", "json") or "{}"), open(f"{out}/chart_values.json", "w"), indent=2)
PY
K -n "$NS" create configmap sc-run-info --from-file=machine_spec.json="$OUT/machine_spec.json" --from-file=chart_values.json="$OUT/chart_values.json" >/dev/null || die "run-info ConfigMap failed"

# ---- 6. the load generator pod ----------------------------------------------------------------------------------------------------
esc() { printf '%s' "${1//,/\\,}"; }   # helm --set splits on commas: 20,40 must be passed as 20\,40
LG_SETS=(--set loadgen.enabled=true --set-string "loadgen.env.SEED_SIZE=$SEED_SIZE" --set-string "loadgen.env.SWEEP_SIZES=$(esc "$SWEEP_SIZES")"
         --set-string "loadgen.env.SWEEP_WORKERS=$(esc "$SWEEP_WORKERS")" --set-string "loadgen.env.LADDER_RATES=$(esc "$LADDER_RATES")"
         --set-string "loadgen.env.LADDER_SECONDS=$LADDER_SECONDS" --set-string "loadgen.env.SOAK_RATE=$SOAK_RATE" --set-string "loadgen.env.SOAK_SECONDS=$SOAK_SECONDS"
         --set-string "loadgen.env.SC_SEED_PROCS=$SC_SEED_PROCS" --set-string "loadgen.env.GIT_COMMIT=$GIT_COMMIT" --set-string "loadgen.env.GIT_BRANCH=$GIT_BRANCH")
H upgrade --install sc-loadtest "$HERE/chart" -n "$NS" "${INFRA_SETS[@]}" "${LG_SETS[@]}" --wait --timeout 10m >"$OUT/logs/helm-loadgen.log" 2>&1 || { tail -20 "$OUT/logs/helm-loadgen.log" >&2; die "load-generator pod failed to start"; }
stamp loadgen_started
log "load generator running in the cluster; following its log into $OUT/logs/loadgen.log"
( K -n "$NS" logs -f loadgen >"$OUT/logs/loadgen.log" 2>&1 ) & LOGS_PID=$!

# ---- 7. wait, copy results as they appear -----------------------------------------------------------------------------------------
last_copy=$(date +%s)
while :; do
  sleep 15
  phase=$(K -n "$NS" get pod loadgen -o jsonpath='{.status.phase}' 2>/dev/null || echo unknown)
  if K -n "$NS" exec loadgen -- test -e /results/DONE >/dev/null 2>&1; then
    log "load generator finished"; break
  fi
  case "$phase" in Failed|Succeeded) log "load generator pod ended ($phase) without DONE"; RESULT_RC=1; break ;; esac
  now=$(date +%s)
  if [ $((now - last_copy)) -ge "$KEEP_RESULTS_EVERY" ]; then
    collect_results; last_copy=$now
    log "elapsed $(( (now - START_EPOCH) / 60 )) min; last phase: $(tail -1 "$OUT/results/phases.jsonl" 2>/dev/null | cut -c1-80)"
  fi
done
collect_results
K -n "$NS" exec loadgen -- touch /results/COLLECTED >/dev/null 2>&1 || true
stamp scenarios_done
if [ -s "$OUT/results/result-status.json" ] && grep -q '"failed_phases":1' "$OUT/results/result-status.json"; then RESULT_RC=1; fi
log "results copied to $OUT/results ($(find "$OUT/results" -type f | wc -l | tr -d ' ') files)"
exit "$RESULT_RC"   # the EXIT trap tears everything down
