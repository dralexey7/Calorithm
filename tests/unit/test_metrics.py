"""
Unit tests for GET /metrics — C1-T04.

Tests use the ASGI test client (no real network, no real Prometheus scraper).
No dependency overrides needed — /metrics must work without a DB connection
(it reads from the in-process prometheus_client registry).

What is verified and why:
  - 200 response (endpoint is reachable, part of the C1 kaркас).
  - Content-Type matches Prometheus text format (ADR-0010, conventions §7 —
    metrics are part of the feature from day one; format must be valid for
    future Prometheus scrape in С5).
  - Response body has at least one metric line in Prometheus exposition format
    (confirms prometheus_client is wired up and producing output, not returning
    an empty or stub body).
  - Default process metrics are present (prometheus_client emits these by
    default; their presence proves the registry is connected, not bypassed).
"""

import pytest

# ---------------------------------------------------------------------------
# Prometheus text format helpers
# ---------------------------------------------------------------------------

_PROMETHEUS_CONTENT_TYPE_PREFIX = "text/plain"
_SAMPLE_LINE_PATTERN = None  # checked via string logic below


def _is_valid_prometheus_exposition(body: str) -> bool:
    """
    Minimal structural check: at least one non-comment, non-blank line must
    exist that does NOT start with '#', indicating a sample line like:
        process_cpu_seconds_total 0.0
    This is a necessary (not sufficient) condition for a valid exposition.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_returns_200(api_client):
    """
    GET /metrics must return HTTP 200.
    Ensures the endpoint exists and is routed correctly (C1 kaркас, §7 conventions).
    """
    response = await api_client.get("/metrics")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_metrics_content_type_is_prometheus_text(api_client):
    """
    GET /metrics Content-Type must start with 'text/plain'.
    Prometheus scraper identifies the exposition format via Content-Type;
    wrong type causes silent scrape failure (ADR-0010, conventions §7).
    """
    response = await api_client.get("/metrics")
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith(_PROMETHEUS_CONTENT_TYPE_PREFIX), (
        f"Expected Content-Type starting with '{_PROMETHEUS_CONTENT_TYPE_PREFIX}', "
        f"got '{content_type}'"
    )


@pytest.mark.asyncio
async def test_metrics_body_contains_at_least_one_sample_line(api_client):
    """
    GET /metrics body must contain at least one Prometheus sample line
    (a non-comment, non-blank line).
    Confirms prometheus_client is wired to the endpoint and produces exposition
    output rather than an empty or stub response (ADR-0010).
    """
    response = await api_client.get("/metrics")
    assert _is_valid_prometheus_exposition(
        response.text
    ), "Response body does not contain any Prometheus sample lines"


@pytest.mark.asyncio
async def test_metrics_body_contains_default_process_metrics(api_client):
    """
    GET /metrics body must include default process metrics emitted by
    prometheus_client (e.g. process_cpu_seconds_total or python_info).
    Presence of these built-in metrics proves the prometheus_client registry
    is connected and not bypassed by a stub response (ADR-0010).
    """
    response = await api_client.get("/metrics")
    body = response.text

    # At least one of the standard default metrics must appear
    default_metric_names = [
        "process_cpu_seconds_total",
        "python_info",
        "process_resident_memory_bytes",
        "process_virtual_memory_bytes",
    ]
    found = any(name in body for name in default_metric_names)
    assert found, (
        f"None of the expected default prometheus_client metrics found in /metrics body. "
        f"Expected at least one of: {default_metric_names}"
    )
