"""
Alembic environment script — synchronous mode.

Design notes
------------
* Synchronous env: Alembic's `command.upgrade` / `command.downgrade` API is
  synchronous.  The test suite (and the one-shot migrate container) call these
  commands directly, so we use a plain sync engine built from
  `engine_from_config`.  No async engine is needed here.
* target_metadata = None: C1 has no ORM models yet.  Autogenerate will be
  wired up in a later stage when models exist.
* URL resolution priority (highest → lowest):
    1. `AlembicConfig.set_main_option("sqlalchemy.url", ...)` — used by tests.
       This is set before `env.py` runs and is returned by
       `config.get_main_option("sqlalchemy.url")`.
    2. DATABASE_URL environment variable — used by the one-shot migrate
       container in docker-compose.  asyncpg/aiosqlite driver suffixes are
       stripped so Alembic gets a sync URL for psycopg2.
    3. `sqlalchemy.url` in alembic.ini — intentionally left blank; acts as
       a safety guard (migration refuses to run if neither 1 nor 2 is set).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Alembic Config — gives access to .ini values.
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file's logging section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# URL resolution: caller override → DATABASE_URL env → ini (blank = error).
# ---------------------------------------------------------------------------
_ini_url: str | None = config.get_main_option("sqlalchemy.url") or None

if not _ini_url:
    # No URL set via set_main_option (e.g. test path).  Try the DATABASE_URL
    # env var (docker-compose one-shot container sets this).
    _env_db_url = os.environ.get("DATABASE_URL", "")
    if _env_db_url:
        # Convert async driver suffix to sync for psycopg2.
        _sync_url = _env_db_url.replace("+asyncpg", "").replace("+aiosqlite", "")
        config.set_main_option("sqlalchemy.url", _sync_url)
    # If neither is set, engine_from_config will raise a clear error at runtime.

# ---------------------------------------------------------------------------
# Metadata for autogenerate — None until ORM models are introduced (C6/C11).
# ---------------------------------------------------------------------------
target_metadata = None


# ---------------------------------------------------------------------------
# Run migrations
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """
    Run migrations without an active DB connection (outputs SQL to stdout).
    Useful for generating SQL scripts for review; not used in the normal flow.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations against a live DB connection using a synchronous engine.

    The engine is built from the [alembic] section of alembic.ini (merged with
    any overrides set by the caller via AlembicConfig.set_main_option).
    NullPool is used so no connection is kept open after the migration run —
    important for the one-shot container use-case.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
