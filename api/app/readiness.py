"""`GET /ready`: can this stack do its job right now? `/health` stays a cheap liveness answer ("the process is up"); this asks the three
things the demo cannot work without, each with a short time limit so a hung dependency makes the answer "no" instead of making
the probe hang:

* **db**      the event store answers (the heartbeat table is read: a round trip through the pool on Postgres, trivial in memory)
* **hapi**    the FHIR server answers its capability statement (only when this deployment uses HAPI: evidence verification or the projection)
* **scheduler** the scheduler's last heartbeat is recent (ops.scheduler_status; "warming" while this process is young is not a failure)

200 when all pass, 503 with the same body when one does not. Booleans and short reasons only: no patient data, no hostnames.
"""

from __future__ import annotations

import concurrent.futures
import time
import urllib.error
import urllib.request

from app import ops
from app.config import settings

CHECK_TIMEOUT_SECONDS = 2.0
_STARTED = time.monotonic()  # wall-clock-ok: process age for the scheduler warm-up, never a case time
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ready")


def _db() -> dict:
    ops.store.heartbeats()
    return {"ok": True}


def _hapi() -> dict:
    if not (settings.fhir_verify_evidence or settings.fhir_projection):
        return {"ok": True, "skipped": "FHIR is not used by this deployment"}
    url = settings.fhir_base_url.rstrip("/") + "/metadata?_summary=true"
    with urllib.request.urlopen(url, timeout=CHECK_TIMEOUT_SECONDS) as resp:  # noqa: S310 (a configured http URL)
        return {"ok": 200 <= resp.status < 300}


def _scheduler() -> dict:
    beat = ops.store.heartbeats().get("scheduler")
    status, detail = ops.scheduler_status(beat, time.monotonic() - _STARTED)
    out = {"ok": status in ("ok", "warming"), "status": status}
    if beat is not None:
        out["age_seconds"] = round(beat.age_seconds, 1)
    elif detail.get("reason"):
        out["reason"] = detail["reason"]
    return out


def _run(fn) -> dict:
    fut = _pool.submit(fn)
    try:
        return fut.result(timeout=CHECK_TIMEOUT_SECONDS + 0.5)
    except concurrent.futures.TimeoutError:
        return {"ok": False, "reason": "timed out"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "reason": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001 - a failing probe is an answer, never a stack trace
        return {"ok": False, "reason": type(exc).__name__}


def check() -> tuple[bool, dict]:
    checks = {"db": _run(_db), "hapi": _run(_hapi), "scheduler": _run(_scheduler)}
    ok = all(c["ok"] for c in checks.values())
    return ok, {"status": "ready" if ok else "not_ready", "checks": checks}
