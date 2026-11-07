# Deploy verification: gated API restart and its installation

Status as of 2026-11-06: **code and unit tests done; real-VM verification NOT yet run.** Nothing below claims a result that was not observed.

## What was verified (no cloud, no Docker)

`tests/test_gcp_hosting.py` (25 tests passed on the branch rebased onto `deploy-budget-tolerant`):

- the gated restart script against a fake `docker`: no restart below the gate, restart of the `api` container only above 70% of its limit, the daily fallback at 40%, the one-hour loop guard, `check` mode, the `docker stats` and host-memory fallbacks, and the reason written to the log;
- `sc-up.sh` installs and enables all four units on every run, `startup.sh` no longer carries a conditional copy, `deploy.sh` refreshes the startup-script metadata on an existing VM, and `compose.gcp.yml` gives the `api` service `mem_limit: 1024m` (gate about 717 MiB).
- `sc-up.sh` also creates `/var/lib/sc-demo` (owner uid 10001, the API container's user) and installs and enables the ADR-058 reset units `sc-demo-reset-watch.timer` (15 s) and `sc-demo-reset-scheduled.timer` (daily, 03:00 Asia/Dubai = 23:00 UTC, `Persistent=false`); `compose.gcp.yml` bind-mounts the folder into `api`. No cron and no other restart is added.

## To verify on the real VM (owner or an authorised session)

From this branch, with the `humans-decide` gcloud configuration active:

1. `deploy/gcp/deploy.sh --yes-spend --no-model` (idempotent; adds no resources; keeps `data/`; no reseed or reset).
2. `sudo systemctl list-timers 'sc-api-restart*'` shows `sc-api-restart.timer` (15 min) and `sc-api-restart-daily.timer` (08:00 UTC).
3. `sudo /srv/sc/repo/deploy/gcp/vm/sc-api-restart.sh check` prints a reading such as `api memory ... = N% of 1073741824 B (container limit; usage from cgroup anon)`; `sudo /srv/sc/repo/deploy/gcp/vm/sc-api-restart.sh gate` prints `no restart` and `journalctl -u sc-api-restart` shows the decision.
4. `sudo systemctl list-timers 'sc-demo-reset*'` shows `sc-demo-reset-watch.timer` and `sc-demo-reset-scheduled.timer`; `stat -c '%u %a' /var/lib/sc-demo` prints `10001 755`; `sudo systemctl start sc-demo-reset-watch.service` with no request pending exits cleanly and `journalctl -u sc-demo-reset-watch` shows it. Do not trigger a real reset unless the owner wants one. Outside the demo gate every `/demo/*` path must still return 404.
5. `make gcp-smoke` ends with `SMOKE TEST PASSED`.

## Unverified until then

systemd unit installation on Debian 12, the cgroup v2 `anon` reading, the effect of the 1024m limit on real API growth, certificates, and the smoke test against the live host. A local production-mode rehearsal script exists (`deploy/gcp/rehearse-local.sh`) but has not been run.
