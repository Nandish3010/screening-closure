# GKE load test: runbook

**Status: NOT RUN, because on 6 November 2026 the owner capped spend at the single demo VM (about INR 3,300 a month) and did not approve this load test. The harness is ready; results are pending a funded run.** Nothing here has been applied to any Google Cloud project, and nothing was called against one. What exists is the prepared tooling, the Helm chart fixes and a free local rehearsal on kind. `make gke-loadtest` **refuses to start unless `SC_ALLOW_SPEND=1` is set** (before any `gcloud` call), and then still needs the typed `yes`. Set it only if the owner approves spend; the rest of this runbook describes what would then happen. `docs/results/scale.md` section "Cloud run (GKE)" says "not run: owner budget limit" and no cloud figure exists.

**Budget.** The owner approved USD 10 to 30 in total. The script refuses to start if its own worst case is above `MAX_USD` (30). Its estimate for the default layout is about USD 3 expected and USD 5 worst case (table below). The prices are **estimates** from list prices as the author knew them, not fetched and not a quote: confirm them in the [pricing calculator](https://cloud.google.com/products/calculator) before approving.

## The command for the day

```bash
gcloud auth login                       # once; opens the browser
gcloud config set project MY_PROJECT    # a project with billing linked (the script checks)
gcloud components install gke-gcloud-auth-plugin   # once, if `gke-gcloud-auth-plugin --version` fails (the script does not install it)
SC_ALLOW_SPEND=1 make gke-loadtest PROJECT=MY_PROJECT    # only with the owner's approval; REGION=me-central1 is the default
```

It prints the checks, then the cost table, then asks `Type "yes" to continue:`. Nothing billable exists before you type it. The whole run is about 2.5 to 3 hours of wall clock; the laptop must stay awake and online (the script runs `caffeinate`) and the terminal must stay open. The results end up in `results/gke-loadtest/<run id>/` (git-ignored).

Needs on the laptop: `gcloud`, `gke-gcloud-auth-plugin`, `kubectl`, `helm`, Docker with `buildx` (images are built for `linux/amd64` and pushed from the laptop; Docker must be running), `openssl`, `python3`, and the repository `.venv` (`tools/k8s/oidc_keys.py` needs PyJWT). It writes its own kubeconfig under `results/` and sets `KUBECONFIG` to it for every `kubectl` and `helm` call, so your own kubeconfig and contexts are never read or changed.

Account needs: Owner or Editor on the project (or the container, compute, Artifact Registry and service-usage admin roles). The script enables three APIs (`container`, `compute`, `artifactregistry`); they are free and **stay enabled** after the teardown.

Other commands:

| Command | What it does |
|---|---|
| `make gke-cost` | Prints the cost table and its arithmetic. No cloud call. |
| `make gke-teardown PROJECT=MY_PROJECT` | Finds everything labelled `sc-loadtest=true` in the project and deletes it (asks `yes`; `FORCE=1` skips the question). |
| `make gke-budget-alert PROJECT=MY_PROJECT` | Optional, **not run by the load test**: creates a billing budget with e-mail alerts at 50/90/100 percent of USD 30 (`BUDGET_USD=15` to change). An alert, not a cap. |
| `make gke-rehearse` | The same pipeline on a local kind cluster with a small seed. Take the shared stack lock first. |
| `make gke-lint` | `helm lint` and `helm template` of both charts (and `kubeconform` if installed). No cluster. |
| `make gke-import RUN=results/gke-loadtest/<run id>` | After a good run: copies its JSON into `docs/results/scale/gke/` and re-renders `scale.md`. |

## What it creates

Everything carries the label `sc-loadtest=true` (and `sc-loadtest-run=<run id>`); GKE's own node resources are named `gke-sc-lt-<run id>-...`.

| Resource | Detail |
|---|---|
| Artifact Registry repository | `sc-lt-<run id>`, Docker, in `REGION`; four images (api, scheduler, watchdog, loadgen), about 1 GB |
| GKE cluster | `sc-lt-<run id>`, **zonal, Standard**, no release channel, auto-upgrade and auto-repair off (a node must not be replaced in the middle of a soak), logging and monitoring `SYSTEM` only |
| Node pool `app` | 3 x e2-standard-2: API (one pod per node, topology spread), scheduler x2, watchdog |
| Node pool `data` | 1 x e2-standard-8: Postgres 16 (24 GiB limit, `shared_buffers` 6 GB), 120 GB pd-ssd volume |
| Node pool `load` | 1 x e2-standard-4: the load-generator pod (and the seeding processes) |
| In-cluster objects | namespace `closure`; Secrets generated with `openssl` (never committed); Helm releases `closure` (`deploy/helm/screening-closure` with `values-gke-loadtest.yaml`) and `sc-loadtest` (`deploy/gke/chart`: Postgres, JWKS stand-in, the load-generator pod and its RBAC) |

No load balancer, no ingress, no public IP of its own (the nodes have ephemeral external addresses, as GKE nodes do by default), no Cloud SQL, no NAT, no logging sink. Before billable work starts the script checks the regional quotas (CPUS, SSD_TOTAL_GB, IN_USE_ADDRESSES) and prints the resources it actually created (`resources-created.txt`).

### Decisions

- **Region: `me-central1` (Doha) by default**, as the existing hosted demo (`docs/governance/hosting-gcp.md`): closest to the UAE, E2 machines are listed in all three zones. Zones `a`, `b`, `c` are tried in turn when one has no capacity. If none has, the script says so, leaves nothing behind (the teardown runs) and you rerun with a nearby region, for example `make gke-loadtest PROJECT=... REGION=europe-west4` (Doha is about 1.25 times the US price for E2, Dammam `me-central2` about 1.6, Netherlands about 1.1). The data are synthetic, so the region matters for price and capacity, not for residency. GKE capacity in `me-central1` was **not** checked against a project.
- **Standard, not Autopilot.** Autopilot bills the pods' requests and may be a few tenths of a dollar an hour cheaper at this size (an estimate, not computed here). It was not chosen because it changes what is measured: it picks the nodes (no separate pools for the database and the generator), and it sets CPU limits equal to requests, whereas the kind runs used a 1 CPU limit over a 250 to 500 m request. Standard gives the same cgroup limits as the laptop runs, so the two sets of numbers can be compared. The saving would be under USD 1 for the run.
- **Postgres in the cluster, not Cloud SQL.** Cheaper (one disk, no instance), no extra API, no private-service-access or authorised-network setup, nothing that outlives the cluster except a disk (which the teardown finds by label), and it is the same `postgres:16` with the same tuning as the kind runs (`docs/results/scale/sweep.json`). Cloud SQL would need 5 to 10 minutes to create, would reserve its instance name for a week after deletion, and adds a second billable teardown surface. The cost of this choice: the run measures the engine against a self-managed single-node Postgres without HA, TLS or backups, not against a managed service. The teardown still looks for a labelled Cloud SQL instance, in case one is created by hand.
- **The load generator is a pod in the cluster**, so the laptop's network is not part of any latency. It runs the repository's own tools unchanged (`tools/load/scale_sweep.py`, `tools/k8s/api_load.py`, `tools/k8s/soak.py`), which read their target from environment variables (`SC_K8S_CONTEXT=""` means kubectl's in-cluster config). The orchestrating laptop only deploys, watches and copies results out.
- **NetworkPolicy is off** in the load-test values: the cluster has no enforcer (neither did kind), so the rules would be unverified, and an untested rule could break a paid run. The chart's policies are still linted and rendered (`make gke-lint`).

## Cost

`make gke-cost` prints this from `deploy/gke/cost.py`. Prices: us-central1 on-demand list prices (e2-standard-2 USD 0.067006/h, -4 0.134012/h, -8 0.268024/h; pd-ssd 0.17 and pd-balanced 0.10 per GB-month; GKE management fee 0.10 per cluster-hour, **without** assuming the free-tier credit), times a region factor of 1.25 for `me-central1` (derived from the third-party figure in `hosting-gcp.md`: USD 0.0814 against 0.067 for e2-standard-2). Worst case = the wall-clock cap with a 1.25 contingency.

| Line | USD per hour | Arithmetic |
|---|---|---|
| Pool `app`: 3 x e2-standard-2 (6 vCPU) | 0.2513 | 3 x 0.067006 x 1.25 |
| Pool `data`: 1 x e2-standard-8 (8 vCPU) | 0.3350 | 1 x 0.268024 x 1.25 |
| Pool `load`: 1 x e2-standard-4 (4 vCPU) | 0.1675 | 1 x 0.134012 x 1.25 |
| GKE management fee | 0.1000 | 0.10 per cluster-hour |
| Boot disks, 5 x 30 GB pd-balanced | 0.0257 | 150 GB x 0.10 / 730 x 1.25 |
| Postgres volume, 120 GB pd-ssd | 0.0349 | 120 GB x 0.17 / 730 x 1.25 |
| **Total per hour** | **0.9144** | |
| Expected, 3 h | **2.74** | 0.9144 x 3 + registry 0.0014 |
| At the 4 h wall-clock cap | 3.66 | 0.9144 x 4 + registry 0.0014 |
| **Worst case** (cap x 1.25) | **4.57** | 3.66 x 1.25 |

Left out: Cloud Logging (system logs only), network egress (none leaves the zone), tax, support, discounts. What is left running if the teardown fails costs about USD 0.9 an hour (the nodes) or 0.04 an hour (only the disk): see below. Regional quota needed: 18 vCPU, 120 GB SSD, 5 addresses; a new project may have less, in which case the script stops before creating anything and says which quota.

The run is well inside the approved USD 10 to 30. There is room to spend more on a bigger run (for example `DATA_MACHINE=e2-standard-16`, `LOAD_MACHINE=e2-standard-8`), and `cost.py` recomputes the guard for any layout; the defaults were chosen to match the laptop topology, not to use the budget.

## What the scenarios do (inside the cluster, `deploy/gke/scenarios.sh`)

| # | Phase | Tool | Time | Result file |
|---|---|---|---|---|
| 1 | Seed 1,000,000 cases into `closure` (parallel COPY seeder, 4 processes), scheduler and watchdog scaled to 0 | `scale_sweep.py seed-closure` | 3 to 6 min | `seed.json` |
| 2 | **The 1M burst/sweep**: one unbounded sweep with 1 and 4 workers, the bounded drain loops, the incremental watchdog scans, on clones of a 1M template | `scale_sweep.py sweep --drain --watchdog` | 25 to 40 min | `sweep.json` |
| 3 | Scheduler and watchdog scaled up (2 and 1), 90 s settle | `kubectl scale` | 2 min | |
| 4 | Ladder: 50, 100, 175, 250 requests/s, then 300, 400, 500 (past the shedding limit: 503 with Retry-After) | `api_load.py`, 180 s per stage, open loop | 25 min | `latency.json` |
| 5 | Soak at the documented rate, 50 requests/s, for 75 minutes: p50/p95/p99 per 5-minute window, error rate, per-pod anonymous memory every 30 s, scheduler cadence | `soak.py` | 80 min | `soak.json` |

The 50 requests/s soak rate is the one of the 2-hour and 4-hour kind soaks (`docs/results/scale.md`); the kind envelope was 175/s with nothing shed. The memory series covers at least one hour after the warm-up (BUG-77: the kind pods grew by 40 to 50 MiB/h). A failing scenario is recorded in `phases.jsonl` and the next one still runs; without a seed nothing runs. Knobs (environment, all optional): `SEED_SIZE`, `SWEEP_SIZES`, `SWEEP_WORKERS`, `LADDER_RATES`, `LADDER_SECONDS`, `SOAK_RATE`, `SOAK_SECONDS`, `WALL_CAP_HOURS`.

## Teardown: what is guaranteed

1. **An `EXIT`/`INT`/`TERM`/`HUP` trap** runs the teardown whatever happens after `yes`: success, any failed step, Ctrl-C, `kill`. While it runs, further signals only print a line. Before deleting, it copies the results out of the pod.
2. **A wall-clock watchdog** (`WALL_CAP_HOURS`, default 4) starts before the first resource exists. At the cap it signals the script (which tears down through the trap); if the teardown has not started 5 minutes later, the watchdog runs `teardown` itself.
3. **Everything is deleted by label**, so `make gke-teardown` also removes what a crashed or killed run left, even from earlier runs: clusters (after deleting the Postgres StatefulSet and its PVC, so the disk goes with them), persistent disks (by label, by the recorded volume handle and by the `gke-sc-lt-` name), leftover VMs, instance groups and templates, labelled addresses, firewall rules and routes named `gke-sc-lt-*`, the Artifact Registry repository (with all its images), and any Cloud SQL instance labelled `sc-loadtest=true` (the test creates none).
4. **A verification listing** ends the teardown and the exit code is non-zero if anything labelled is left (`teardown-verification.txt`, `run.json`).

**Not covered:** the laptop dying, sleeping or going offline for longer than the cap while the cluster exists. Nothing else holds a timer. Then the cluster keeps billing (about USD 0.9 an hour) until you run `make gke-teardown`. The budget alert (`make gke-budget-alert`) and your own check are the net; run `make gke-teardown` whenever you are unsure, it is safe when nothing exists. The tests (`tests/test_gke_loadtest.py`, against a fake `gcloud`) cover the refusals, the trap after a failure, the wall-clock cap and the label teardown; they do not prove that real GKE accepts every flag.

### Verification commands (run them yourself afterwards)

```bash
P=MY_PROJECT
gcloud container clusters list --project $P --filter='resourceLabels.sc-loadtest=true'
gcloud compute disks list      --project $P --filter='labels.sc-loadtest=true OR name~^gke-sc-lt-'
gcloud compute instances list  --project $P --filter='name~^gke-sc-lt-'
gcloud compute addresses list  --project $P --filter='labels.sc-loadtest=true'
gcloud compute forwarding-rules list --project $P          # none are created; anything here is not from this test
gcloud artifacts repositories list --project $P --filter='labels.sc-loadtest=true'
gcloud sql instances list --project $P --filter='settings.userLabels.sc-loadtest=true'   # needs sqladmin enabled
gcloud billing projects describe $P                         # billing still linked; check the billing report next day
```

Each must print nothing. Costs appear in the billing report with a delay of up to a day; filter by label `sc-loadtest` to see this test only.

### If the teardown fails

1. Read the lines starting `TEARDOWN INCOMPLETE` (they name what is left) and `results/gke-loadtest/<run id>/logs/`.
2. Run `make gke-teardown PROJECT=MY_PROJECT` again; most failures are transient (a cluster still deleting, a disk still attached).
3. A **cluster stuck deleting**: `gcloud container operations list --project $P --filter='status!=DONE'`, wait, retry. A **disk "in use"**: delete its VM first (`gcloud compute instances delete NAME --zone Z --delete-disks=all`), then the disk. A **registry repository** that will not go: `gcloud artifacts repositories delete NAME --location REGION --project $P`.
4. If the CLI keeps failing, delete in the console (Kubernetes Engine > Clusters, Compute Engine > Disks, Artifact Registry), selecting by label `sc-loadtest`.
5. The nuclear option, if the project exists only for this test: `gcloud projects delete MY_PROJECT` (30-day recovery period; stops all billing for its resources).
6. Whatever you do, run the verification commands and look at the billing report the next day.

## After the run

```bash
ls results/gke-loadtest/<run id>/           # run.json, results/*.json, resources-created.txt, teardown-verification.txt, logs/
make gke-import RUN=results/gke-loadtest/<run id>     # refuses a rehearsal, an unfinished soak or failed phases (--force overrides, with reason)
make docs-check scale-report-check
```

`tools/k8s/render_gke.py` then renders the section "Cloud run (GKE)" of `docs/results/scale.md` (machine, ladder, shedding, sweep and drain at 1M, soak percentiles, memory slope per pod per hour against the kind figures, cost and teardown). Commit `docs/results/scale/gke/`, `docs/results/scale.md` and `scale.json`. Documents that quote the old laptop figures (README, `docs/submission/*`) need a hand edit afterwards; `docs/results/scale.md` is generated and must not be edited by hand. If the run found something wrong, say so in the report: it is a measurement.

## Rehearsal (done) and what it does not prove

`make gke-rehearse` ran the same script, the same charts, the same images and the same in-pod scenarios on a local kind cluster (nodes labelled as the pools) with a 20,000-case seed and short stages. The transcript is `docs/results/scale/gke/rehearsal-kind-transcript.txt`; the result of a rehearsal is never imported as a measurement. What it proves: the charts install, the migration job and the three-API topology come up, the support chart's Postgres and JWKS work, the load-generator image contains everything the tools import, the in-cluster `kubectl` (RBAC, `exec`, `logs`, `scale`) works, the four scenarios run to the end and write their JSON, results are copied out, and the teardown deletes the cluster.

What it does **not** prove (first-time risks on the day): the real `gcloud` flags (cluster, node pool and Artifact Registry commands), GKE's acceptance of the pd-ssd StorageClass `labels` parameter (if it ignores it, the teardown still finds the disk by its volume handle and name), that the node service account can pull from the repository (the script grants it `artifactregistry.reader`), regional quota and capacity, the `linux/amd64` cross-build time on an Apple-silicon laptop (a few minutes per image under emulation), and `kubectl` v1.33 against whatever version GKE creates. A failure in any of them stops the script at that step and runs the teardown; nothing is billed beyond what existed.

## Changes in the repository for this test

`deploy/gke/` (script, cost guard, support chart, load-generator image, in-pod scenarios, budget alert, lint), `deploy/helm/screening-closure` (HorizontalPodAutoscaler off by default, `terminationGracePeriodSeconds` and a `preStop` sleep, `values-gke-loadtest.yaml`), `tools/k8s/{api_load,soak,mint_token}.py` and `tools/load/scale_sweep.py` (target read from the environment; the defaults are unchanged), `tools/k8s/render_gke.py` and `render_scale.py` (the new section), `Makefile`, `tests/test_gke_loadtest.py`, `tests/fakes/fake_gcloud.py`.
