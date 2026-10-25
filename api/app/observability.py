"""Observability (F14, M6): OpenTelemetry metrics and traces, a protected Prometheus `/metrics`, structured JSON logs.

What leaves the process is counts, durations, state names and opaque ids; never a patient reference, a name, a message body
or a query parameter value. The metric labels are bounded (a state name, a lease name, a service name, a provider name).

* Metrics are always collected in process. `GET /metrics` serves them in the Prometheus text format; it is answered by a
  small ASGI middleware ahead of the router (so it is not in the OpenAPI document or the route policy table) and needs
  `Authorization: Bearer <token>`: a supervisor's token, or `METRICS_SCRAPE_TOKEN` (a service-style secret for a scraper;
  the development default works only while APP_ENV=dev). No token: 401. A token of any other role: 403.
* With `OTEL_EXPORTER_OTLP_ENDPOINT` set (the standard variable) the same instruments are also pushed over OTLP/HTTP, and
  FastAPI request spans and Postgres query spans are exported (the spans carry the parameterised SQL text, no values).
  With it unset nothing is exported and no request span is built.
* `LOG_FORMAT=json` makes every log line one JSON object with `trace_id` and `span_id` (when a span is active).
* Gauges that need the database are computed on demand and cached (`METRICS_CACHE_SECONDS`, default 15; the overdue count
  replays every open case, so it has its own, longer `METRICS_OVERDUE_CACHE_SECONDS`, default 60).

The instruments (Prometheus names in brackets):
  closure_cases_open{state}, closure_cases_overdue, closure_outbox_pending, closure_projection_queue_depth,
  closure_projection_dead_letters, closure_leader{lease}, closure_service_heartbeat_age_seconds{service},
  closure_escalations_emitted_total, closure_overdue_marked_total, closure_sweeps_total{result},
  closure_sweep_duration_seconds, closure_llm_tokens_total{provider,kind}, and the FastAPI HTTP server metrics.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import threading
import time
from typing import Callable

from app.config import METRICS_TOKEN_DEFAULT, settings

log = logging.getLogger("app.observability")

SERVICE_NAME = "screening-closure-api"

_lock = threading.Lock()
_ready = False
_meter = None
_registry_reader = None
_tracer_provider = None
_pool = None  # the Postgres pool, when there is one (set by configure())

# instruments, created in setup()
_escalations = _overdue_marked = _sweeps = _sweep_duration = _llm_tokens = None


# ---------------------------------------------------------------------------------------------------- JSON logs

class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, and the active trace and span ids. The message is what the
    code logged (the PHI scan, tools/check/phi_scan.py, covers it); the traceback, if any, is a string field."""

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        try:
            from opentelemetry import trace

            ctx = trace.get_current_span().get_span_context()
            if ctx.is_valid:
                doc["trace_id"] = format(ctx.trace_id, "032x")
                doc["span_id"] = format(ctx.span_id, "016x")
        except Exception:  # noqa: BLE001 - logging must never fail because tracing did
            pass
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


def configure_logging() -> None:
    """LOG_FORMAT=json: replace the root handler's formatter and fold uvicorn's loggers into the root, so the access log is
    JSON too. Anything else leaves the existing text format."""
    if os.environ.get("LOG_FORMAT", "text").strip().lower() != "json":
        return
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


# ---------------------------------------------------------------------------------------------------- caching

class _Cached:
    """Compute a value at most once per `ttl` seconds, whatever the number of readers; on failure keep the last good one."""

    def __init__(self, fn: Callable[[], object], ttl: Callable[[], float]) -> None:
        self._fn, self._ttl = fn, ttl
        self._value: object = None
        self._at = float("-inf")
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            if time.monotonic() - self._at >= self._ttl():  # wall-clock-ok: cache age only
                try:
                    self._value = self._fn()
                except Exception:  # noqa: BLE001 - a gauge must not break a scrape
                    log.exception("metrics: could not refresh %s", getattr(self._fn, "__name__", "gauge"))
                self._at = time.monotonic()  # wall-clock-ok: cache age only
            return self._value


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.environ.get(name, "").strip() or default), 0.0)
    except ValueError:
        return default


# ---------------------------------------------------------------------------------------------------- gauge sources

def _store():
    from app.routers import cases

    return cases.store


def _scalar(sql: str) -> int:
    if _pool is None:
        return 0
    with _pool.connection() as conn:
        return int(conn.execute(sql).fetchone()[0])


def _open_by_state() -> dict[str, int]:
    from app.routers import cases

    terminal = cases.pathway.terminal_state
    counts: dict[str, int] = {}
    for c in _store().list_cases():
        if c.current_state != terminal:
            counts[c.current_state] = counts.get(c.current_state, 0) + 1
    return counts


def _overdue_count() -> int:
    """Open cases that are overdue now (the state SLA or any milestone), by replay. Cases that do not replay are skipped."""
    from app.routers import cases

    n = 0
    for _cid, model, _err in _store().iter_open_case_models(cases.pathway):
        if model is not None and model.any_overdue:
            n += 1
    return n


def _outbox_pending() -> int:
    return _scalar("SELECT count(*) FROM outbox WHERE status = 'pending'")


def _queue_depth() -> int:
    return _scalar("SELECT count(*) FROM fhir_projection_queue")


def _dead_letters() -> int:
    return _scalar("SELECT count(*) FROM fhir_projection_dead_letter")


def _leaders() -> dict[str, int]:
    from app import leader

    out = {"api-background": 1 if leader.api_is_leader() else 0}
    try:
        out["scheduler-present"] = 1 if leader.store.current(leader.SCHEDULER_LEASE) is not None else 0
    except Exception:  # noqa: BLE001
        out["scheduler-present"] = 0
    return out


def _heartbeat_ages() -> dict[str, float]:
    from app import ops

    beats = ops.store.heartbeats()
    return {name: float(beats[name].age_seconds) for name in ("scheduler", "watchdog") if name in beats}


# ---------------------------------------------------------------------------------------------------- setup

def configure(pool) -> None:
    """Called by the API lifespan once the Postgres pool exists (None for the in-memory store)."""
    global _pool
    _pool = pool


def _resource():
    from opentelemetry.sdk.resources import Resource

    return Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", SERVICE_NAME),
        "service.version": "0.1.0",
        "service.instance.id": os.environ.get("HOSTNAME") or socket.gethostname(),
        "deployment.environment": settings.app_env,
    })


def setup() -> None:
    """Idempotent. Builds the meter provider (Prometheus reader always, OTLP reader and tracer when an endpoint is set)."""
    global _ready, _meter, _registry_reader, _tracer_provider
    global _escalations, _overdue_marked, _sweeps, _sweep_duration, _llm_tokens
    with _lock:
        if _ready:
            return
        _ready = True
        try:
            from opentelemetry.exporter.prometheus import PrometheusMetricReader
            from opentelemetry.metrics import Observation
            from opentelemetry.sdk.metrics import MeterProvider
        except ImportError:
            log.warning("observability: the OpenTelemetry packages are not installed; metrics and traces are off")
            return

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        resource = _resource()
        readers = [PrometheusMetricReader()]
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            interval_ms = int(_env_float("OTEL_METRIC_EXPORT_INTERVAL", 15000))
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval_ms))
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            _tracer_provider = TracerProvider(resource=resource)
            _tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        provider = MeterProvider(resource=resource, metric_readers=readers)
        _meter = provider.get_meter("screening-closure")
        _registry_reader = readers[0]

        ttl = lambda: _env_float("METRICS_CACHE_SECONDS", 15.0)  # noqa: E731
        ttl_slow = lambda: _env_float("METRICS_OVERDUE_CACHE_SECONDS", 60.0)  # noqa: E731
        by_state, overdue = _Cached(_open_by_state, ttl), _Cached(_overdue_count, ttl_slow)
        outbox, queue, dead = _Cached(_outbox_pending, ttl), _Cached(_queue_depth, ttl), _Cached(_dead_letters, ttl)
        leaders, beats = _Cached(_leaders, ttl), _Cached(_heartbeat_ages, ttl)

        def obs_open(_options):
            return [Observation(n, {"state": s}) for s, n in sorted((by_state.get() or {}).items())]

        def single(cache):
            return lambda _options: [Observation(cache.get() or 0)]

        _meter.create_observable_gauge("closure_cases_open", callbacks=[obs_open],
                                       description="Open (not closed) cases by state")
        _meter.create_observable_gauge("closure_cases_overdue", callbacks=[single(overdue)],
                                       description="Open cases overdue now (state SLA or a milestone)")
        _meter.create_observable_gauge("closure_outbox_pending", callbacks=[single(outbox)],
                                       description="Approved messages not yet sent (the outbox)")
        _meter.create_observable_gauge("closure_projection_queue_depth", callbacks=[single(queue)],
                                       description="Events waiting to be projected to FHIR")
        _meter.create_observable_gauge("closure_projection_dead_letters", callbacks=[single(dead)],
                                       description="Events the FHIR projection gave up on")
        _meter.create_observable_gauge(
            "closure_leader", description="1 when this lease is held (api-background: by this replica; scheduler-present: by any scheduler)",
            callbacks=[lambda _o: [Observation(v, {"lease": k}) for k, v in (leaders.get() or {}).items()]])
        _meter.create_observable_gauge(
            "closure_service_heartbeat_age_seconds", unit="s", description="Seconds since the service's last heartbeat",
            callbacks=[lambda _o: [Observation(v, {"service": k}) for k, v in (beats.get() or {}).items()]])

        _escalations = _meter.create_counter("closure_escalations_emitted", description="escalate events emitted by sweeps")
        _overdue_marked = _meter.create_counter("closure_overdue_marked", description="mark_overdue events emitted by sweeps")
        _sweeps = _meter.create_counter("closure_sweeps", description="Sweeps run, by result (ok, errors)")
        _sweep_duration = _meter.create_histogram(
            "closure_sweep_duration", unit="s", description="Wall time of one POST /scheduler/sweep",
            explicit_bucket_boundaries_advisory=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120])
        _llm_tokens = _meter.create_counter("closure_llm_tokens", description="LLM tokens used, by provider and kind")
        for counter in (_escalations, _overdue_marked):
            counter.add(0)  # the series exists from the start, so rate() and increase() have a zero to begin from
        log.info("observability: metrics on; OTLP export %s", f"to {endpoint}" if endpoint else "off")
        _provider_holder.append(provider)


_provider_holder: list = []


def instrument_app(app) -> None:
    """FastAPI request metrics (always) and spans (when an OTLP endpoint is set); psycopg query spans likewise."""
    setup()
    if not _provider_holder:
        return
    # Stable HTTP semantic conventions: the request-duration metric is labelled with the route template (`/cases/{case_id}`),
    # not the raw path, which carries a case id and would make one series per case.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=_tracer_provider, meter_provider=_provider_holder[0], excluded_urls="health,metrics$"
        )
        if _tracer_provider is not None:
            from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

            PsycopgInstrumentor().instrument(tracer_provider=_tracer_provider, skip_dep_check=True)
    except Exception:  # noqa: BLE001 - observability never stops the API from starting
        log.exception("observability: instrumentation failed; continuing without it")


def shutdown() -> None:
    for p in _provider_holder:
        try:
            p.force_flush()
        except Exception:  # noqa: BLE001
            pass
    if _tracer_provider is not None:
        try:
            _tracer_provider.force_flush()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------------------------------- recording

def stopwatch() -> float:
    """A monotonic reading for measuring a duration (the sweep-duration metric); never a case time."""
    return time.perf_counter()  # wall-clock-ok: a duration, not a decision


def record_sweep(result: dict, seconds: float) -> None:
    """Called by POST /scheduler/sweep with the sweep's JSON result (counts only) and its duration."""
    if _sweeps is None:
        return
    errors = result.get("errors") or []
    _sweeps.add(1, {"result": "errors" if errors else "ok"})
    _sweep_duration.record(seconds)
    if result.get("escalate"):
        _escalations.add(int(result["escalate"]))
    if result.get("mark_overdue"):
        _overdue_marked.add(int(result["mark_overdue"]))


def record_llm_usage(provider: str, usage: object) -> None:
    """`usage` is the provider's usage object (OpenAI-compatible: prompt_tokens, completion_tokens). Anything else is ignored."""
    if _llm_tokens is None or not isinstance(usage, dict):
        return
    # OpenAI-compatible keys, and Anthropic's (input_tokens, output_tokens, cache reads and writes; A35)
    for kind, key in (("prompt", "prompt_tokens"), ("completion", "completion_tokens"), ("prompt", "input_tokens"),
                      ("completion", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
                      ("cache_write", "cache_creation_input_tokens")):
        n = usage.get(key)
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            _llm_tokens.add(n, {"provider": provider, "kind": kind})


# ---------------------------------------------------------------------------------------------------- /metrics

def _token_ok(token: str) -> tuple[bool, bool]:
    """(accepted, authenticated): a scrape token or a supervisor's token is accepted; any other valid token is known but refused."""
    from app import auth

    scrape = settings.metrics_scrape_token
    if scrape and (settings.app_env == "dev" or scrape != METRICS_TOKEN_DEFAULT) and hmac.compare_digest(
        token.encode(), scrape.encode()
    ):
        return True, True
    try:
        identity = auth.authenticate(token)
    except Exception:  # noqa: BLE001 - AuthError, a malformed token, an unreachable key set: all "not authenticated"
        return False, False
    return identity.kind == "user" and identity.role == "supervisor", True


class MetricsMiddleware:
    """Answers `GET /metrics` (exactly that path) and passes everything else through."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/metrics":
            return await self.app(scope, receive, send)
        status, body, headers = self._answer(scope)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _answer(scope) -> tuple[int, bytes, list]:
        def plain(status: int, text: str, extra=()):
            return status, text.encode(), [(b"content-type", b"text/plain; charset=utf-8"), *extra]

        if scope["method"] != "GET":
            return plain(405, "method not allowed", [(b"allow", b"GET")])
        auth_header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return plain(401, "authentication required", [(b"www-authenticate", b"Bearer")])
        accepted, authenticated = _token_ok(token.strip())
        if not authenticated:
            return plain(401, "invalid token", [(b"www-authenticate", b"Bearer")])
        if not accepted:
            return plain(403, "forbidden")
        if _registry_reader is None:
            return plain(503, "metrics are off (OpenTelemetry packages not installed)")
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return 200, generate_latest(), [(b"content-type", CONTENT_TYPE_LATEST.encode())]
