"""
Integration tests for GET /healthz against a real Postgres — C1-T04.

REQUIRES A REAL POSTGRES INSTANCE (or Docker for testcontainers).
Marked with @pytest.mark.integration; skipped automatically when neither
TEST_DATABASE_URL nor a running Docker daemon is available (see conftest.py).

Why real Postgres here (not a mock):
  The unit tests in tests/unit/test_health.py already cover mock-based
  scenarios via FastAPI dependency_overrides.  These tests exist to verify
  that the end-to-end wiring — app factory → config → real asyncpg driver →
  real Postgres — actually works: no mocks, no hidden bypass.

  Two mandatory scenarios:
    1. Positive: api-core pointed at a live testcontainers Postgres returns 200.
       Proves that the real DB probe (SELECT 1 or equivalent) succeeds and that
       the app is correctly config-driven.
    2. Negative: api-core pointed at an unreachable URL returns 503.
       Proves the healthcheck is fail-closed — it does NOT return 200 when the
       DB is genuinely unreachable (not just mocked to raise).

Implied interface requirement fixed here for C1-T04 implementation:
  apps.api_core.main MUST expose a `create_app()` factory (or
  `create_app(settings)`) that accepts a DATABASE_URL / Settings object so
  tests (and compose) can wire the correct URL without monkey-patching globals.

Run locally with Docker:
    pytest tests/integration/test_health_db.py -v -m integration

With an explicit DB:
    TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/test" \
        pytest tests/integration/test_health_db.py -v -m integration
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def api_client_with_real_db(test_db_url: str):
    """
    Builds an ASGI test client for api-core with DATABASE_URL wired to the
    testcontainers (or override) Postgres.

    Uses `create_app()` from apps.api_core.main so the DB URL is injected
    through the settings/config layer — the same path used in production.
    This is the fixture that pins the `create_app()` factory requirement.

    If create_app() does not exist yet, this will ImportError (correct red
    phase: implementation missing, tests failing for the right reason).
    """
    from apps.api_core.main import create_app

    app = create_app(database_url=test_db_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def api_client_with_unreachable_db():
    """
    Builds an ASGI test client for api-core configured with a DATABASE_URL
    that points to a closed port (localhost:1).

    localhost:1 is a real network address but has no listener; the driver
    will get a genuine connection-refused or timeout — this is NOT a mock.
    The URL uses port 1 which is reserved and always closed on dev machines.

    Verifies fail-closed behaviour: the healthcheck must return 503, not 200,
    when the DB is genuinely unreachable.
    """
    from apps.api_core.main import create_app

    # Port 1 is reserved/always-closed; asyncpg will fail to connect for real.
    unreachable_url = "postgresql+asyncpg://nobody:nopass@localhost:1/nonexistent"
    app = create_app(database_url=unreachable_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Positive path — real Postgres reachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_real_db_returns_200(api_client_with_real_db):
    """
    GET /healthz must return HTTP 200 when api-core is configured with a
    reachable Postgres URL.

    End-to-end connectivity check: app factory → config (DATABASE_URL) →
    asyncpg driver → real testcontainers Postgres → probe (SELECT 1 or
    equivalent) → response.  No mock anywhere in the path.
    Proves the real DB wiring works, not just the mock dependency path
    (C1-T04, C1 exit criterion: '/healthz ok при доступной БД').
    """
    response = await api_client_with_real_db.get("/healthz")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_healthz_real_db_response_body_status_ok(api_client_with_real_db):
    """
    GET /healthz with a reachable Postgres must return a JSON body with
    status == "ok".

    Validates the structured response contract beyond just HTTP status code
    so that monitoring tools can parse the body reliably (C1 plan §5,
    Q-C1-3 — structured health signal).
    """
    response = await api_client_with_real_db.get("/healthz")
    data = response.json()
    assert "status" in data, "Response body must contain a 'status' field"
    assert (
        data["status"] == "ok"
    ), f"Expected status 'ok' with real DB connected, got: {data['status']!r}"


# ---------------------------------------------------------------------------
# Negative path — unreachable DB (real failure, not a mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_unreachable_db_returns_503(api_client_with_unreachable_db):
    """
    GET /healthz must return HTTP 503 when the configured DATABASE_URL is
    genuinely unreachable (localhost port 1 — always closed).

    This is NOT a mocked failure: the asyncpg driver attempts a real TCP
    connection and receives a real connection-refused / timeout.  Verifies
    that the healthcheck is fail-closed and does not return 200 when the DB
    is down — essential for deploy readiness probes to function correctly
    (C1 plan §5, Q-C1-3; conventions §5 — honest error propagation).
    """
    response = await api_client_with_unreachable_db.get("/healthz")
    assert (
        response.status_code == 503
    ), f"Expected 503 when DB is unreachable, got {response.status_code}"


@pytest.mark.asyncio
async def test_healthz_unreachable_db_returns_json_not_traceback(api_client_with_unreachable_db):
    """
    GET /healthz must return valid JSON when the DB is unreachable — not an
    HTML error page or a raw Python traceback.

    Verifies structured error responses (conventions §5: no stack traces
    exposed externally) even under a real, not mocked, connection failure.
    """
    response = await api_client_with_unreachable_db.get("/healthz")

    # Must parse as JSON without raising
    data = response.json()
    assert isinstance(data, dict), "Response body must be a JSON object"

    body_text = str(data)
    # Python traceback markers must not leak into the HTTP response body
    assert "Traceback" not in body_text, "Stack trace must not be exposed in response"
    assert (
        "asyncpg" not in body_text
    ), "Internal driver details (asyncpg) must not be exposed in response"
