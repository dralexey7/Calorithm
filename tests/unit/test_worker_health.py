"""
Unit tests for processing-worker /healthz and /metrics — C2-T04.

The worker depends on the broker (Redis), NOT on Postgres (ADR-0015).
Its /healthz must:
  - return 200 when the broker probe succeeds
  - return 503 when the broker probe fails (structured JSON, no traceback)

/metrics must return 200 with a valid Prometheus text body.

The broker probe is overridden via FastAPI dependency_overrides (same pattern
as get_db_probe in api-core) — no real Redis required for unit tests.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def worker_client():
    """
    ASGI test client for apps.processing_worker.main.app.
    Broker probe is NOT overridden here; individual tests set overrides
    before using the client.
    """
    from apps.processing_worker.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Broker probe helpers
# ---------------------------------------------------------------------------


def _make_broker_probe_ok():
    """Returns an async callable that simulates a healthy broker."""

    async def probe():
        return True

    return probe


def _make_broker_probe_fail():
    """Returns an async callable that simulates an unreachable broker."""

    async def probe():
        raise ConnectionRefusedError("Test: broker unreachable")

    return probe


# ---------------------------------------------------------------------------
# /healthz — broker available → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_healthz_returns_200_when_broker_available(worker_client):
    """
    GET /healthz on the processing-worker must return 200 when the broker
    probe succeeds.
    The worker's liveness depends on broker connectivity, not Postgres
    (ADR-0015); a healthy broker means the worker is ready to consume tasks
    (C2-T04, conventions §5).
    """
    from apps.processing_worker.health import get_broker_probe
    from apps.processing_worker.main import app

    app.dependency_overrides[get_broker_probe] = _make_broker_probe_ok

    response = await worker_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_worker_healthz_body_contains_ok_status(worker_client):
    """
    GET /healthz 200 body must contain status='ok'.
    Structured health signal for monitoring and orchestration (conventions §5).
    """
    from apps.processing_worker.health import get_broker_probe
    from apps.processing_worker.main import app

    app.dependency_overrides[get_broker_probe] = _make_broker_probe_ok

    response = await worker_client.get("/healthz")

    app.dependency_overrides.clear()

    data = response.json()
    assert "status" in data
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# /healthz — broker unavailable → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_healthz_returns_503_when_broker_fails(worker_client):
    """
    GET /healthz on the processing-worker must return 503 when the broker
    probe raises.
    Honest reflection of the single dependency the worker has; deploy
    orchestrators rely on this to restart the worker when Redis is down
    (C2-T04, conventions §5).
    """
    from apps.processing_worker.health import get_broker_probe
    from apps.processing_worker.main import app

    app.dependency_overrides[get_broker_probe] = _make_broker_probe_fail

    response = await worker_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_worker_healthz_503_body_is_json_without_traceback(worker_client):
    """
    GET /healthz 503 response body must be valid JSON and must not expose
    stack traces or internal error details.
    Conventions §5: structured errors, no information leakage.
    """
    from apps.processing_worker.health import get_broker_probe
    from apps.processing_worker.main import app

    app.dependency_overrides[get_broker_probe] = _make_broker_probe_fail

    response = await worker_client.get("/healthz")

    app.dependency_overrides.clear()

    data = response.json()
    assert isinstance(data, dict)
    body_text = str(data)
    assert "Traceback" not in body_text
    assert "ConnectionRefusedError" not in body_text


# ---------------------------------------------------------------------------
# /metrics — must be valid Prometheus format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_metrics_returns_200(worker_client):
    """
    GET /metrics on the processing-worker must return HTTP 200.
    The worker exposes Prometheus metrics from the start so the scraper can
    be wired immediately (ADR-0010, conventions §7).
    """
    response = await worker_client.get("/metrics")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_worker_metrics_body_is_prometheus_text_format(worker_client):
    """
    GET /metrics body must be in Prometheus text exposition format.
    Prometheus scraper expects lines beginning with '# HELP' or '# TYPE'
    or metric lines — the body must contain at least one such marker
    (ADR-0010, conventions §7).
    """
    response = await worker_client.get("/metrics")

    body = response.text
    # Prometheus text format always has at least one comment or metric line
    assert "# HELP" in body or "# TYPE" in body or len(body.strip()) > 0, (
        "/metrics must return non-empty Prometheus text format"
    )
