#!/usr/bin/env python3
"""Trigger ONE agency run on demand against the running stack and print its summary (A22).

    make agency-run                       the same through make
    python3 tools/demo/agency_run.py      POST /agency/run as the supervisor (dev-login)

The scheduler hook starts a run every `AGENCY_EVERY_SWEEPS` sweeps; compose sets that to 0 (never) so the demo snapshot
stays deterministic, which makes this the way to show the agency. The agency prepares work only: it writes no case event
as itself, and drafts a message only through the drafting route (the owner still approves). A second call at the same
simulated time returns the same run.

Environment: API_URL (default http://localhost:8000). With AGENCY_SERVICE_TOKEN set, that token is used instead of the
supervisor's dev-login (the token may start a run and nothing else).
Exit codes: 0 a run was returned, 1 the API refused, 2 the API is not reachable.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")


def main() -> int:
    token = os.environ.get("AGENCY_SERVICE_TOKEN")
    try:
        with httpx.Client(timeout=300) as http:
            if not token:
                r = http.post(f"{API}/auth/dev-login", json={"role": "supervisor", "name": "Dr. Noura Al Hammadi"})
                r.raise_for_status()
                token = r.json()["token"]
            r = http.post(f"{API}/agency/run", headers={"Authorization": f"Bearer {token}"})
    except httpx.TransportError as exc:
        print(f"agency-run: the API at {API} is not reachable ({exc}); start the stack with `make up`", file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as exc:
        print(f"agency-run: dev login refused: HTTP {exc.response.status_code} {exc.response.text[:200]}", file=sys.stderr)
        return 1
    if r.status_code != 200:
        print(f"agency-run: POST /agency/run refused: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 1
    run = r.json()
    counts = run.get("counts") or {}
    by_status = ", ".join(f"{n} {k}" for k, n in sorted((counts.get("by_status") or {}).items()) if n)
    print(f"agency-run: run {run.get('run_id')} {run.get('status')} at sim {run.get('sim_at')} "
          f"({'started now' if run.get('created') else 'the run of this simulated time, returned again'}); "
          f"{counts.get('total', 0)} task(s): {by_status or 'none'}")
    for department, text in sorted((run.get("rationale") or {}).items()):
        print(f"  {text}")
    print(json.dumps(run, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
