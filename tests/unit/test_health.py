"""
Unit tests for GET /healthz — C1-T04.

Tests use the ASGI test client (no real network).
DB availability is controlled by overriding the FastAPI dependency that
performs the DB probe — mock at the boundary, never inside the unit under test.

What is verified and why:
  - 200 response when DB is reachable (liveness + dependency probe, C1 plan §5).
  - Response body signals "ok" (structured health response).
  - Non-200 (503 expected) when DB probe raises (healthcheck honestly reflects
    DB unavailability — C1 plan §5, Q-C1-3).
  - Service does not crash or return a 500 stack trace on DB failure
    (structured error, conventions §5).
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_healthz_returns_200_when_db_is_available(api_client):
    """
    GET /healthz must return 200 when the DB probe succeeds.
    Confirms the endpoint is alive and the liveness check passes (C1 plan §5).
    """
    # Override DB dependency before the client is used
    from apps.api_core.main import app  # noqa: PLC0415
    from apps.api_core.health import get_db_probe  # noqa: PLC0415

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
    from apps.api_core.main import app  # noqa: PLC0415
    from apps.api_core.health import get_db_probe  # noqa: PLC0415

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
    from apps.api_core.main import app  # noqa: PLC0415
    from apps.api_core.health import get_db_probe  # noqa: PLC0415

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
    from apps.api_core.main import app  # noqa: PLC0415
    from apps.api_core.health import get_db_probe  # noqa: PLC0415

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
