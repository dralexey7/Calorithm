"""
Shared fixtures for the C1 test suite.

Scope summary:
- `monkeypatch_env`: patches ENV variables in a test without touching the OS environment.
- `test_db_url`: ephemeral Postgres via testcontainers (session-scoped); honors a
  TEST_DATABASE_URL override; skips integration tests only if neither the override
  nor a usable Docker/testcontainers is available. Requires Docker running locally.
- `async_client`: an ASGI test client for api-core; mocks are passed in by individual tests.
"""

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# ENV helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def clean_env(monkeypatch):
    """
    Remove all Calorithm-relevant ENV variables so each config test starts
    from a clean slate — avoids leakage from a developer's real .env file.
    """
    keys = [
        "DATABASE_URL",
        "REDIS_URL",
        "TELEGRAM_BOT_TOKEN",
        "LLM_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_RPM",
        "LLM_TPM",
        "OFF_USER_AGENT",
        "OFF_CONTACT",
        "OFF_DAILY_LIMIT",
        "LOG_LEVEL",
        "APP_ENV",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Integration: real Postgres
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db_url():
    """
    Provides an async (asyncpg) Postgres URL for integration tests.

    Resolution order:
      1. TEST_DATABASE_URL — if set, use that DB as-is (CI/manual override).
      2. Otherwise spin up an ephemeral Postgres via testcontainers, kept alive
         for the whole test session and torn down at the end. Only requirement
         from the developer: a running Docker daemon.
      3. If neither is available, skip the integration tests (so unit tests can
         still run on a machine without Docker).

    Migration tests clean up after themselves (downgrade to base), so a single
    session-scoped container is safe to share across them.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        yield override
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip(
            "testcontainers not installed and TEST_DATABASE_URL not set — "
            "skipping Postgres integration test"
        )

    try:
        with PostgresContainer("postgres:16-alpine") as postgres:
            # testcontainers returns a psycopg2 URL; we need the asyncpg driver.
            url = postgres.get_connection_url().replace("+psycopg2", "+asyncpg")
            yield url
    except Exception as exc:  # Docker not running / image pull failed, etc.
        pytest.skip(f"Could not start Postgres testcontainer (is Docker running?): {exc}")


# ---------------------------------------------------------------------------
# ASGI client for api-core
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def api_client():
    """
    Thin ASGI test client for apps.api_core.main.app.
    Individual tests override dependencies (e.g. DB probe) via FastAPI's
    dependency_overrides before requesting this fixture.
    Importing here causes an ImportError while the module doesn't exist yet
    (correct red-phase behaviour).

    Unit tests (tests/unit/test_health.py) use this fixture with
    dependency_overrides — no real DB needed.

    Integration tests that need a real DB (tests/integration/test_health_db.py)
    do NOT use this fixture.  Instead they call `create_app(database_url=url)`
    directly, which is the interface required from apps.api_core.main for C1-T04.
    This keeps the mock path and the real-DB path completely separate.
    """
    from apps.api_core.main import app  # noqa: PLC0415 — intentional late import

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
