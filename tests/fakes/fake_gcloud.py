#!/usr/bin/env python3
"""A stand-in for gcloud, docker, kubectl, helm and gke-gcloud-auth-plugin for tests/test_gke_loadtest.py. It never talks to a network.
Every call is appended to $FAKE_LOG as one line (the tool name and its arguments). A few resources are kept as files in $FAKE_STATE so
list/delete behave like a cloud: cluster, disk, repo. Behaviour switches (environment): FAKE_NO_AUTH, FAKE_NO_BILLING, FAKE_FAIL_CLUSTER."""
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["FAKE_STATE"])
state.mkdir(parents=True, exist_ok=True)
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(tool + " " + " ".join(args) + "\n")
line = " ".join(args)
env = os.environ

if tool == "kubectl" and env.get("FAKE_HANG"):  # the load generator never finishes: the wall-clock cap has to end the run
    if "exec loadgen -- test -e /results/DONE" in line:
        sys.exit(1)
    if "get pod loadgen" in line and "jsonpath" in line:
        print("Running")
if tool != "gcloud":
    sys.exit(0)  # docker, kubectl, helm, plugin: succeed silently

if args[:2] == ["auth", "list"]:
    if not env.get("FAKE_NO_AUTH"):
        print("tester@example.com")
elif args[:2] == ["config", "get-value"]:
    print("fake-project")
elif args[:2] == ["projects", "describe"]:
    print("123456789" if "projectNumber" in line else "fake-project")
elif args[:3] == ["billing", "projects", "describe"]:
    print("False" if env.get("FAKE_NO_BILLING") else "True")
elif args[:2] == ["services", "list"]:
    for api in ("container", "compute", "artifactregistry"):
        if f"config.name={api}.googleapis.com" in line:
            print(f"{api}.googleapis.com")
elif args[:2] == ["compute", "regions"] and "describe" in args:
    print(json.dumps({"quotas": [{"metric": "CPUS", "limit": 100, "usage": 0}, {"metric": "SSD_TOTAL_GB", "limit": 500, "usage": 0},
                                 {"metric": "IN_USE_ADDRESSES", "limit": 8, "usage": 0}]}))
elif args[:3] == ["container", "clusters", "create"]:
    if env.get("FAKE_FAIL_CLUSTER"):
        print("ERROR: ZONE_RESOURCE_POOL_EXHAUSTED", file=sys.stderr)
        sys.exit(1)
    (state / "cluster").write_text("sc-lt-x me-central1-a")
elif args[:3] == ["container", "clusters", "list"]:
    if (state / "cluster").exists():
        print((state / "cluster").read_text())
elif args[:3] == ["container", "clusters", "delete"]:
    (state / "cluster").unlink(missing_ok=True)
elif args[:3] == ["artifacts", "repositories", "create"]:
    (state / "repo").write_text("projects/fake-project/locations/me-central1/repositories/sc-lt-x")
elif args[:3] == ["artifacts", "repositories", "list"]:
    if (state / "repo").exists():
        print((state / "repo").read_text())
elif args[:3] == ["artifacts", "repositories", "delete"]:
    (state / "repo").unlink(missing_ok=True)
elif args[:3] == ["compute", "disks", "list"]:
    if (state / "disk").exists():
        print((state / "disk").read_text())
elif args[:3] == ["compute", "disks", "delete"]:
    (state / "disk").unlink(missing_ok=True)
sys.exit(0)
