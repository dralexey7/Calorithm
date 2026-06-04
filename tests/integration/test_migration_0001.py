"""
Integration tests for Alembic migration 0001 — C1-T03.

REQUIRES A REAL POSTGRES INSTANCE.
Marked with @pytest.mark.integration; skipped automatically when
TEST_DATABASE_URL is not set (see conftest.py `test_db_url` fixture).

Why a real Postgres:
  `CREATE SCHEMA` is a Postgres-specific DDL statement and cannot be
  reproduced on SQLite.  These tests are therefore inherently integration
  tests that need an actual Postgres process.

Run locally:
    TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/test_calorithm" \
        pytest tests/integration/test_migration_0001.py -v

In CI the job should spin up a postgres service container and export
TEST_DATABASE_URL before running pytest.

What is verified and why:
  - Migration applies cleanly on a fresh DB (§3a p.5 conventions).
  - Schemas `users` and `diary` exist after upgrade (data-model §7, C1 goal).
  - No business tables exist — C1 is schema-only, baseline (data-model §7).
  - Full up → down → up cycle completes without error (reversibility §3a p.3/p.5).
  - After downgrade the schemas are removed (downgrade is a real rollback).
"""

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alembic_cfg(db_url: str) -> AlembicConfig:
    """
    Build an Alembic Config pointing at our alembic.ini and overriding
    sqlalchemy.url to the test database.  Keeps the test DB isolated from
    the developer's real DB.
    """
    import os

    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cfg = AlembicConfig(os.path.join(repo_root, "alembic.ini"))
    # Alembic sync URL (psycopg2 / asyncpg URL needs stripping for sync ops)
    sync_url = db_url.replace("+asyncpg", "").replace("+aiosqlite", "")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


async def _schema_exists(conn: AsyncConnection, schema_name: str) -> bool:
    """Returns True if the given schema exists in the connected Postgres DB."""
    result = await conn.execute(
        sa.text("SELECT 1 FROM information_schema.schemata " "WHERE schema_name = :schema"),
        {"schema": schema_name},
    )
    return result.scalar() is not None


async def _tables_in_schema(conn: AsyncConnection, schema_name: str) -> list[str]:
    """Returns all table names in the given schema."""
    result = await conn.execute(
        sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
        ),
        {"schema": schema_name},
    )
    return [row[0] for row in result.fetchall()]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def migrated_engine(test_db_url: str):
    """
    Applies `alembic upgrade head` on the test DB, yields an async engine for
    assertions, then applies `alembic downgrade base` to leave the DB clean.
    """
    # Run upgrade (sync Alembic API; fine to call from async test via blocking)
    cfg = _make_alembic_cfg(test_db_url)
    alembic_command.upgrade(cfg, "head")

    engine = create_async_engine(test_db_url, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()
        # Always attempt downgrade to leave the DB clean for subsequent runs
        alembic_command.downgrade(cfg, "base")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_0001_upgrade_applies_without_error(test_db_url: str):
    """
    Running `alembic upgrade head` on a clean DB must complete without raising.
    Verifies that the migration file is syntactically correct and can be applied
    to a fresh Postgres instance (conventions §3a p.5).
    """
    cfg = _make_alembic_cfg(test_db_url)
    # Should not raise
    alembic_command.upgrade(cfg, "head")
    # Cleanup
    alembic_command.downgrade(cfg, "base")


@pytest.mark.asyncio
async def test_migration_0001_creates_users_schema(migrated_engine):
    """
    After upgrade the schema `users` must exist in the database.
    C1 goal: `CREATE SCHEMA users` (data-model §7, §3 conventions).
    """
    async with migrated_engine.connect() as conn:
        assert await _schema_exists(
            conn, "users"
        ), "Schema 'users' was not created by migration 0001"


@pytest.mark.asyncio
async def test_migration_0001_creates_diary_schema(migrated_engine):
    """
    After upgrade the schema `diary` must exist in the database.
    C1 goal: `CREATE SCHEMA diary` (data-model §7, §3 conventions).
    """
    async with migrated_engine.connect() as conn:
        assert await _schema_exists(
            conn, "diary"
        ), "Schema 'diary' was not created by migration 0001"


@pytest.mark.asyncio
async def test_migration_0001_no_business_tables_in_users_schema(migrated_engine):
    """
    After upgrade there must be no tables in the `users` schema.
    C1 is a baseline migration — only schemas are created, no business tables
    (data-model §7: tables arrive in С6/С11).
    """
    async with migrated_engine.connect() as conn:
        tables = await _tables_in_schema(conn, "users")
        assert (
            tables == []
        ), f"Unexpected business tables in schema 'users' after migration 0001: {tables}"


@pytest.mark.asyncio
async def test_migration_0001_no_business_tables_in_diary_schema(migrated_engine):
    """
    After upgrade there must be no tables in the `diary` schema.
    C1 is baseline-only; diary tables arrive in С11 (data-model §7).
    """
    async with migrated_engine.connect() as conn:
        tables = await _tables_in_schema(conn, "diary")
        assert (
            tables == []
        ), f"Unexpected business tables in schema 'diary' after migration 0001: {tables}"


@pytest.mark.asyncio
async def test_migration_0001_up_down_up_cycle(test_db_url: str):
    """
    Full up → down → up cycle must complete without error.
    Validates that downgrade is a real rollback (schemas removed) and that
    re-upgrade works on a DB that previously had the migration applied and
    rolled back (conventions §3a p.3/p.5 — reversibility gate in CI).
    """
    cfg = _make_alembic_cfg(test_db_url)

    alembic_command.upgrade(cfg, "head")
    alembic_command.downgrade(cfg, "base")
    alembic_command.upgrade(cfg, "head")  # must not raise

    # Verify schemas exist after second upgrade
    engine = create_async_engine(test_db_url, echo=False)
    try:
        async with engine.connect() as conn:
            assert await _schema_exists(conn, "users")
            assert await _schema_exists(conn, "diary")
    finally:
        await engine.dispose()
        alembic_command.downgrade(cfg, "base")


@pytest.mark.asyncio
async def test_migration_0001_downgrade_removes_schemas(test_db_url: str):
    """
    After downgrade to base the schemas `users` and `diary` must be gone.
    Confirms that `downgrade` is a genuine rollback, not a no-op (§3a p.3).
    """
    cfg = _make_alembic_cfg(test_db_url)
    alembic_command.upgrade(cfg, "head")
    alembic_command.downgrade(cfg, "base")

    engine = create_async_engine(test_db_url, echo=False)
    try:
        async with engine.connect() as conn:
            assert not await _schema_exists(
                conn, "users"
            ), "Schema 'users' still exists after downgrade"
            assert not await _schema_exists(
                conn, "diary"
            ), "Schema 'diary' still exists after downgrade"
    finally:
        await engine.dispose()
