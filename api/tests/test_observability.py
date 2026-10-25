"""F14: the protected /metrics endpoint, the metric names, the sweep recorder and the JSON log format."""

import json
import logging

import pytest

from api_helpers import login_headers, make_client
from app import observability
from app.config import METRICS_TOKEN_DEFAULT, settings

pytest.importorskip("opentelemetry.sdk")


@pytest.fixture(scope="module")
def client():
    observability.setup()
    return make_client()


def test_metrics_requires_a_token(client):
    r = client.get("/metrics")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert client.get("/metrics", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_metrics_refuses_other_roles_and_services(client):
    for role in ("coordinator", "lab", "patient"):
        assert client.get("/metrics", headers=login_headers(client, role)).status_code == 403
    assert client.get("/metrics", headers={"Authorization": f"Bearer {settings.scheduler_service_token}"}).status_code == 403


def test_metrics_accepts_a_supervisor_and_the_scrape_token(client):
    r = client.get("/metrics", headers=login_headers(client, "supervisor"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "closure_" in r.text
    assert client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN_DEFAULT}"}).status_code == 200
    assert client.post("/metrics", headers=login_headers(client, "supervisor")).status_code == 405


def test_the_scrape_token_default_is_dev_only(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN_DEFAULT}"}).status_code == 401
    monkeypatch.setattr(settings, "metrics_scrape_token", "s" * 40)
    assert client.get("/metrics", headers={"Authorization": f"Bearer {'s' * 40}"}).status_code == 200


def test_metrics_is_not_in_the_route_table_or_openapi(client):
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]
    assert "/metrics/closure" in client.get("/openapi.json").json()["paths"]  # the existing closure metrics are untouched


def test_sweep_and_llm_recorders_show_up_in_the_exposition(client):
    observability.record_sweep({"escalate": 2, "mark_overdue": 3, "errors": []}, 0.2)
    observability.record_llm_usage("azure_openai", {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})
    observability.record_llm_usage("azure_openai", {"prompt_tokens": True, "completion_tokens": "x"})  # ignored
    text = client.get("/metrics", headers=login_headers(client, "supervisor")).text
    assert "closure_escalations_emitted_total" in text and "closure_overdue_marked_total" in text
    assert "closure_sweep_duration_seconds_bucket" in text
    assert "closure_llm_tokens_total" in text and "azure_openai" in text
    observability.record_llm_usage("anthropic", {"input_tokens": 120, "output_tokens": 40, "cache_read_input_tokens": 5,
                                                 "cache_creation_input_tokens": 0})  # A35: Claude's usage counts
    text = client.get("/metrics", headers=login_headers(client, "supervisor")).text
    assert 'provider="anthropic"' in text and 'kind="cache_read"' in text
    assert "closure_leader" in text and "closure_cases_overdue" in text and "closure_outbox_pending" in text


def test_json_log_lines_carry_the_trace_id():
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("t")
    fmt = observability.JsonFormatter()
    with tracer.start_as_current_span("s") as span:
        rec = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        doc = json.loads(fmt.format(rec))
        assert doc["message"] == "hello world" and doc["level"] == "INFO" and doc["logger"] == "app.x"
        assert doc["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert "trace_id" not in json.loads(fmt.format(rec))
