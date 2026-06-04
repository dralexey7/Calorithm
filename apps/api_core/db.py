"""
apps.api_core.db — async SQLAlchemy engine and DB probe (conventions §4, ADR-0015).

api-core is the sole owner of the database connection; no other component
imports from this module (ADR-0015).

The engine is created lazily per URL (via create_engine_for_url) so that
importing this module does not open any connections.  The DB probe is a
coroutine that executes SELECT 1 to verify connectivity.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_engine_for_url(database_url: str) -> AsyncEngine:
    """
    Build an async SQLAlchemy engine for the given URL.

    The engine uses a pool_pre_ping=True so stale connections are recycled.
    No connection is opened at construction time — the engine is lazy.

    Args:
        database_url: Full async SQLAlchemy URL (postgresql+asyncpg://...).

    Returns:
        Configured AsyncEngine instance.
    """
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        # Keep pool small for the C1 skeleton; tune in later stages.
        pool_size=5,
        max_overflow=10,
        # Explicit connection-establishment timeout (conventions §5): the
        # /healthz probe fails fast on an unreachable/black-holed DB host
        # instead of hanging on the default TCP timeout.
        connect_args={"timeout": 5},
    )


async def check_db(engine: AsyncEngine) -> None:
    """
    Execute a minimal probe query (SELECT 1) against the engine.

    Raises any SQLAlchemy / asyncpg exception on failure — callers
    (health router) are responsible for converting these to HTTP responses.

    Args:
        engine: The AsyncEngine to probe.

    Raises:
        Exception: Any connection or query failure propagates unmodified.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
