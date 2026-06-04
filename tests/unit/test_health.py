"""
Unit tests for GET /healthz — C1-T04 (C1) + C2-T05 broker probe extension (C2).

Tests use the ASGI test client (no real network).
DB availability is controlled by overriding the FastAPI dependency that
performs the DB probe — mock at the boundary, never inside the unit under test.

C1 tests:
  - 200 when DB reachable (liveness + dependency probe, C1 plan §5).
  - Response body signals "ok" (structured health response).
  - 503 when DB probe raises (conventions §5, Q-C1-3).
  - No stack trace in 503 body (conventions §5).

C2 additions (broker probe, TДЕ-7):
  api-core now depends on BOTH Postgres and Redis. /healthz must return 200
  only when both probes succeed, and 503 if either fails. Four state
  combinations are covered:
    - DB ok + broker ok  → 200
    - DB ok + broker fail → 503
    - DB fail + broker ok → 503
    - DB fail + broker fail → 503
  The broker probe is a separate overrideable FastAPI dependency (get_broker_probe)
  following the same pattern as get_db_probe (conventions §5, C2-T05).
"""

import pytest

# ---------------------------------------------------------------------------
# Helpers — dependency override factories
# ---------------------------------------------------------------------------


def _make_db_probe_ok():
    """Returns an async callable that simulates a successful DB probe."""

    async def probe():
        return True  # DB is reachable

    return probe


def _make_db_probe_fail():
    """Returns an async callable that simulates a failed DB probe."""

    async def probe():
        raise ConnectionRefusedError("Test: DB unreachable")

    return probe


def _make_broker_probe_ok():
    """Returns an async callable that simulates a successful broker (Redis) probe."""

    async def probe():
        return True  # Redis is reachable

    return probe


def _make_broker_probe_fail():
    """Returns an async callable that simulates a failed broker (Redis) probe."""

    async def probe():
        raise ConnectionRefusedError("Test: Redis unreachable")

    return probe


# ---------------------------------------------------------------------------
# C1 tests (DB probe only) — preserved unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_returns_200_when_db_is_available(api_client):
    """
    GET /healthz must return 200 when the DB probe succeeds.
    Confirms the endpoint is alive and the liveness check passes (C1 plan §5).
    """
    # Override DB dependency before the client is used
    from apps.api_core.health import get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_ok

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_healthz_response_body_contains_ok_status(api_client):
    """
    GET /healthz 200 response body must indicate an 'ok' status.
    Provides a machine-readable health signal beyond HTTP status code (C1 plan §5).
    """
    from apps.api_core.health import get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_ok

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    data = response.json()
    # The body must contain a status field with value "ok" (or equivalent)
    assert "status" in data
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_healthz_returns_non_200_when_db_is_unavailable(api_client):
    """
    GET /healthz must return a non-200 status (503 expected) when the DB probe
    raises an exception.
    Ensures healthcheck honestly signals dependency failure rather than lying
    about liveness (C1 plan §5, Q-C1-3 — foundation for deploy readiness probes).
    """
    from apps.api_core.health import get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_fail

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code != 200
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_healthz_db_unavailable_returns_json_not_stack_trace(api_client):
    """
    When DB is unavailable, the response body must be valid JSON and must not
    expose a Python stack trace or internal error details.
    Prevents information leakage and ensures structured error responses
    (conventions §5 — no stack traces exposed externally).
    """
    from apps.api_core.health import get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_fail

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    # Must parse as JSON — not HTML/plaintext traceback
    data = response.json()
    assert isinstance(data, dict)

    body_text = str(data)
    # Common Python traceback markers must not appear in the response
    assert "Traceback" not in body_text
    assert "ConnectionRefusedError" not in body_text


# ---------------------------------------------------------------------------
# C2 tests — broker probe: four state combinations (TДЕ-7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_returns_200_when_both_db_and_broker_are_available(api_client):
    """
    GET /healthz must return 200 only when BOTH the DB probe and the broker
    (Redis) probe succeed.
    api-core depends on both Postgres and Redis since C2; the healthcheck must
    reflect both dependencies (C2-T05, TДЕ-7, conventions §5).
    """
    from apps.api_core.health import get_broker_probe, get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_ok
    app.dependency_overrides[get_broker_probe] = _make_broker_probe_ok

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_healthz_returns_503_when_broker_fails_db_ok(api_client):
    """
    GET /healthz must return 503 when the broker probe fails even if the DB
    probe succeeds.
    Both dependencies must be healthy for the service to be considered ready;
    Redis failure alone must cause 503 (TДЕ-7, conventions §5).
    """
    from apps.api_core.health import get_broker_probe, get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_ok
    app.dependency_overrides[get_broker_probe] = _make_broker_probe_fail

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_healthz_returns_503_when_db_fails_broker_ok(api_client):
    """
    GET /healthz must return 503 when the DB probe fails even if the broker
    probe succeeds.
    DB failure alone must cause 503 regardless of broker state (TДЕ-7).
    """
    from apps.api_core.health import get_broker_probe, get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_fail
    app.dependency_overrides[get_broker_probe] = _make_broker_probe_ok

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_healthz_returns_503_when_both_db_and_broker_fail(api_client):
    """
    GET /healthz must return 503 when both DB and broker probes fail.
    The most degraded state must still return a structured 503, not crash
    (conventions §5 — no 500, no traceback) (TДЕ-7).
    """
    from apps.api_core.health import get_broker_probe, get_db_probe
    from apps.api_core.main import app

    app.dependency_overrides[get_db_probe] = _make_db_probe_fail
    app.dependency_overrides[get_broker_probe] = _make_broker_probe_fail

    response = await api_client.get("/healthz")

    app.dependency_overrides.clear()

    assert response.status_code == 503
    data = response.json()
    assert isinstance(data, dict)
    body_text = str(data)
    assert "Traceback" not in body_text
