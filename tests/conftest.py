"""
Shared fixtures for the test suite (C1 + C2).

Scope summary:
- `clean_env`: removes all Calorithm ENV variables so config tests start clean.
- `test_db_url`: ephemeral Postgres via testcontainers (session-scoped); honors
  TEST_DATABASE_URL override; skips if neither override nor Docker available.
- `redis_url`: ephemeral Redis via testcontainers (session-scoped); honors
  TEST_REDIS_URL override; skips integration Redis tests if neither is available.
  Added in C2 by analogy with test_db_url.
- `api_client`: ASGI test client for api-core (unit tests override dependencies).
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure apps.api_core.main.app can be imported in unit tests without a real
# database: set a dummy DATABASE_URL unless one is already provided. The engine
# is lazy and unit tests override the DB probe, so this URL is never connected.
# Config tests use the `clean_env` fixture to remove it and exercise fail-fast.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://placeholder:placeholder@localhost/placeholder",
)

# Similarly, set a dummy REDIS_URL so api-core (and processing-worker) can be
# imported in unit tests without a real Redis. The bus is never connected in
# unit tests because it is overridden via DI. Config tests use clean_env to
# remove this and exercise fail-fast on missing REDIS_URL.
os.environ.setdefault(
    "REDIS_URL",
    "redis://placeholder-redis:6379/0",
)

# ---------------------------------------------------------------------------
# ENV helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    """
    Remove all Calorithm-relevant ENV variables so each config test starts
    from a clean slate — avoids leakage from a developer's real .env file.

    Also redirects Pydantic-Settings' env_file to a non-existent path inside
    a temporary directory, so that a real `.env` file present in the repo root
    (created for local compose runs) does not bleed into unit tests.
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

    # Patch Settings so it reads env_file from a path that does not exist,
    # preventing any real .env file in the project root from being picked up.
    nonexistent_env = str(tmp_path / ".env.nonexistent")

    # Monkey-patch the model_config on the Settings class to point at the
    # nonexistent path.  We import lazily to avoid triggering Settings.__init__
    # at fixture-setup time (DATABASE_URL is not yet set).
    from core.config.settings import Settings

    original_config = Settings.model_config.copy()
    new_config = dict(original_config)
    new_config["env_file"] = nonexistent_env
    monkeypatch.setattr(Settings, "model_config", new_config)

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
# Integration: real Redis (C2)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_url():
    """
    Provides a Redis URL for integration tests against a real Redis broker.

    Resolution order (mirrors test_db_url logic for Postgres):
      1. TEST_REDIS_URL env var — use that URL as-is (CI or manual override).
      2. Spin up an ephemeral Redis:7-alpine via testcontainers (session-scoped,
         torn down automatically). Requires a running Docker daemon.
      3. If neither is available, skip the integration test.

    Integration tests that do NOT need real Redis (e.g. InMemoryBus loop tests)
    do not use this fixture and always run.
    """
    override = os.environ.get("TEST_REDIS_URL")
    if override:
        yield override
        return

    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        pytest.skip(
            "testcontainers[redis] not installed and TEST_REDIS_URL not set — "
            "skipping Redis integration test"
        )

    try:
        with RedisContainer("redis:7-alpine") as redis:
            host = redis.get_container_host_ip()
            port = redis.get_exposed_port(6379)
            yield f"redis://{host}:{port}/0"
    except Exception as exc:  # Docker not running / image pull failed, etc.
        pytest.skip(f"Could not start Redis testcontainer (is Docker running?): {exc}")


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
    from apps.api_core.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
