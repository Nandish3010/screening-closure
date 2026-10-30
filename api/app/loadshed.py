"""Load shedding (M7-8, ADR-048): a bounded number of requests in flight per API process.

Without a bound an overloaded pod accepts everything, queues it behind a small thread pool and a small database pool, and the
caller sees timeouts and connection resets after 30 seconds (M7-7: 300 requests/s). With the bound, the request over the limit is
refused AT ONCE with `503 Service Unavailable` and `Retry-After`, which a client can act on: back off and retry, or tell the user.
Requests already in flight are unaffected.

* Pure ASGI, counted on the event loop (no lock needed: the counter is only touched between awaits).
* Never shed: the probe (`/health`), the scheduler and watchdog service calls (the lease renewal and the heartbeat must reach the
  API exactly when it is busy, or a busy API looks like a dead scheduler and the leader is lost), and the metrics scrape.
* The limit is per PROCESS (per pod, one uvicorn process): `MAX_INFLIGHT_REQUESTS`, chart `api.maxInflightRequests`. 0 switches it off.
"""

from __future__ import annotations

import json
import logging
import time

from app import observability

log = logging.getLogger("app.loadshed")

EXEMPT_PREFIXES = ("/health", "/scheduler/", "/watchdog/", "/metrics")
BODY = json.dumps({"detail": "overloaded: too many requests in flight, retry after the delay in Retry-After"}).encode()


class LoadShedMiddleware:
    def __init__(self, app, max_inflight: int, retry_after: int = 1) -> None:
        self.app = app
        self.max_inflight = int(max_inflight)
        self.retry_after = max(1, int(retry_after))
        self.inflight = 0
        self.peak = 0
        self.shed = 0
        self._last_log = 0.0

    def exempt(self, path: str) -> bool:
        return path == "/metrics" or path.startswith(EXEMPT_PREFIXES)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_inflight <= 0 or self.exempt(scope["path"]):
            return await self.app(scope, receive, send)
        if self.inflight >= self.max_inflight:
            self.shed += 1
            observability.record_shed()
            self._warn()
            await send({"type": "http.response.start", "status": 503, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(BODY)).encode()),
                (b"retry-after", str(self.retry_after).encode()), (b"x-shed", b"1")]})
            await send({"type": "http.response.body", "body": BODY})
            return
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            await self.app(scope, receive, send)
        finally:
            self.inflight -= 1

    def _warn(self) -> None:
        now = time.monotonic()  # wall-clock-ok: rate-limits a log line (cadence of a warning), never a decision
        if now - self._last_log >= 10:  # one line per 10 s, not one per refused request
            self._last_log = now
            log.warning("load shedding: %d in flight (limit %d), %d refused so far", self.inflight, self.max_inflight, self.shed)
