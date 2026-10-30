#!/usr/bin/env bash
# Prints the machine and cluster specification as JSON (embedded in the M7-6 results). Read-only; never prints secrets.
set -uo pipefail
CTX=kind-sc-m76
NS=closure
j() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
cpu="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
cores="$(sysctl -n hw.ncpu 2>/dev/null || echo 0)"
pcores="$(sysctl -n hw.physicalcpu 2>/dev/null || echo 0)"
ram="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
macos="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
dd_cpus="$(docker info --format '{{.NCPU}}' 2>/dev/null || echo 0)"
dd_mem="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
dd_ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
dd_os="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || echo unknown)"
kind_ver="$(kind version 2>/dev/null | head -1 || echo unknown)"
helm_ver="$(helm version --short 2>/dev/null || echo unknown)"
node_image="$(docker inspect sc-m76-control-plane --format '{{.Config.Image}}' 2>/dev/null || echo unknown)"
nodes="$(docker ps --filter name=sc-m76- --format '{{.Names}}' 2>/dev/null | sort | tr '\n' ' ')"
k8s_ver="$(kubectl --context $CTX version -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])' 2>/dev/null || echo unknown)"
pg_ver="$(kubectl --context $CTX -n $NS exec statefulset/postgres -- psql -U closure_owner -d closure -At -c 'show server_version' 2>/dev/null || echo unknown)"
cat <<JSON
{
  "host": {"cpu_model": $(j "$cpu"), "logical_cores": $cores, "physical_cores": $pcores, "ram_bytes": $ram, "macos": $(j "$macos")},
  "docker": {"version": $(j "$dd_ver"), "os": $(j "$dd_os"), "cpus": $dd_cpus, "memory_bytes": $dd_mem},
  "kind": {"version": $(j "$kind_ver"), "node_image": $(j "$node_image"), "nodes": $(j "${nodes% }")},
  "kubernetes_version": $(j "$k8s_ver"),
  "helm": $(j "$helm_ver"),
  "postgres_version": $(j "$pg_ver")
}
JSON
