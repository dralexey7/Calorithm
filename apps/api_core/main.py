"""
apps.api_core.main — FastAPI application factory and module-level app (C1-T04).

Exports:
  create_app(*, database_url: str) -> FastAPI
      App factory used by integration tests and docker-compose.  Accepts the
      database URL directly so callers can wire any URL without monkey-patching
      global state.

  app — module-level FastAPI instance.
      Built from Settings().database_url (single source of truth, §6;
      fail-fast if DATABASE_URL is missing in a real deployment).
      Unit tests set a dummy DATABASE_URL and override get_db_probe, so the
      engine is never actually used to connect.

ADR-0015: api-core is the sole DB owner.
Conventions §4: async everywhere on the hot path.
Conventions §5: no stack traces in error responses (handled in health.py).
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.api_core import health, metrics
from apps.api_core.db import check_db, create_engine_for_url
from apps.api_core.health import get_db_probe
from core.config.settings import Settings


def create_app(*, database_url: str) -> FastAPI:
    """
    Build and return a fully wired FastAPI application.

    The DB probe dependency is overridden with a closure over the async engine
    created from `database_url`.  No connection is opened at construction time
    (engine is lazy); the probe runs only when /healthz is called.

    Args:
        database_url: Full async SQLAlchemy URL (postgresql+asyncpg://...).

    Returns:
        Configured FastAPI instance ready to serve traffic.
    """
    engine = create_engine_for_url(database_url)

    application = FastAPI(
        title="Calorithm api-core",
        version="0.1.0",
    )

    # Wire the real DB probe as a dependency override.
    # health.get_db_probe is the sentinel; the override returns a closure
    # that calls check_db with our engine.
    async def _real_probe() -> None:
        await check_db(engine)

    def _probe_dependency() -> health._DbProbeCallable:  # type: ignore[name-defined]
        return _real_probe

    application.dependency_overrides[get_db_probe] = _probe_dependency

    # Register routers.
    application.include_router(health.router)
    application.include_router(metrics.router)

    return application


# ---------------------------------------------------------------------------
# Module-level app (served by uvicorn in the container).
#
# Built from Settings — the single source of truth for config (conventions §6).
# Settings reads DATABASE_URL from the environment and fails fast if it is
# missing, so a misconfigured deployment crashes loudly at startup.
# Unit tests set a dummy DATABASE_URL (tests/conftest.py) and override the DB
# probe, so the lazy engine is never actually connected during unit tests.
# ---------------------------------------------------------------------------

app: FastAPI = create_app(database_url=Settings().database_url)  # type: ignore[call-arg]
