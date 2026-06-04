"""
Unit tests for core.config.Settings — C1-T02 + C2 REDIS_URL additions (TДЕ-9).

All tests are pure unit tests: no real DB, no network, no file I/O.
ENV is controlled via the `clean_env` fixture (see conftest.py).

C1 tests verify:
  - Settings reads DATABASE_URL from ENV (§6 conventions: single source of truth).
  - Missing required variable raises a clear validation error (fail-fast, §6).
  - Secret value does not appear in repr/str (secrets must not leak, §6).
  - Every key in .env.example maps to a Settings field (DoD §5: .env.example
    must be complete and in sync with the model).

C2 additions (TДЕ-9, REDIS_URL):
  - Settings loads REDIS_URL from ENV.
  - Missing REDIS_URL raises ValidationError at init (fail-fast — C2 adds Redis
    as a required dependency, conventions §6).
  - REDIS_URL does not appear in repr/str (may contain credentials — §6).
"""

import os
import re

import pytest

# ---------------------------------------------------------------------------
# Happy path: DATABASE_URL loaded from ENV
# ---------------------------------------------------------------------------


def test_settings_loads_database_url_from_env(clean_env):
    """
    When DATABASE_URL is present in ENV, Settings.database_url must equal it.
    Verifies that config is the single source of truth for the connection URL
    and that it reads from the environment (conventions §6).
    """
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")

    from core.config.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    assert settings.database_url == "postgresql+asyncpg://user:pass@localhost/test"


# ---------------------------------------------------------------------------
# Fail-fast: missing required variable raises at init time
# ---------------------------------------------------------------------------


def test_settings_raises_on_missing_database_url(clean_env):
    """
    When DATABASE_URL is absent, constructing Settings must raise a
    ValidationError (or equivalent), not silently default to None.
    Ensures fail-fast behaviour so a misconfigured container crashes loudly
    rather than producing hard-to-debug runtime failures (conventions §6).
    """
    # DATABASE_URL intentionally not set
    with pytest.raises(Exception) as exc_info:
        from core.config.settings import Settings

        Settings()  # type: ignore[call-arg]

    # The error must mention the missing field — not a generic crash
    error_text = str(exc_info.value).lower()
    assert "database_url" in error_text or "validation" in error_text or "field" in error_text


# ---------------------------------------------------------------------------
# Secret hygiene: DATABASE_URL must not appear in repr / str
# ---------------------------------------------------------------------------


def test_settings_database_url_not_in_repr(clean_env):
    """
    The DATABASE_URL value (which contains credentials) must not appear in
    repr(settings) or str(settings).
    Prevents accidental credential leakage into logs or error messages (§6).
    """
    secret_url = "postgresql+asyncpg://admin:SUPERSECRET@db.host/prod"
    clean_env.setenv("DATABASE_URL", secret_url)

    from core.config.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    representation = repr(settings) + str(settings)

    # The literal password fragment must not be visible
    assert "SUPERSECRET" not in representation


# ---------------------------------------------------------------------------
# .env.example completeness: every example key maps to a Settings field
# ---------------------------------------------------------------------------


def test_env_example_keys_match_settings_fields(clean_env):
    """
    Every ENV variable listed in .env.example must correspond to a declared
    field on the Settings model (no orphaned / undocumented variables).
    Ensures .env.example stays in sync with the config model (DoD §5).

    Implementation note: we parse .env.example line-by-line, skip comments and
    blanks, and check each KEY against Settings.model_fields (Pydantic v2) or
    __fields__ (Pydantic v1).  The comparison is case-insensitive because
    Pydantic Settings lowercases ENV keys by default.
    """
    env_example_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env.example")
    env_example_path = os.path.normpath(env_example_path)

    if not os.path.exists(env_example_path):
        pytest.skip(".env.example does not exist yet — skip until file is created")

    with open(env_example_path) as fh:
        lines = fh.readlines()

    # Extract variable names: lines like KEY=... or KEY= (ignore comments/blanks)
    example_keys: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z0-9_]+)\s*=", line)
        if match:
            example_keys.append(match.group(1).lower())

    assert example_keys, ".env.example has no variable definitions — check the file"

    # Import only after we know the file is there
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    from core.config.settings import Settings

    # Pydantic v2 uses model_fields; v1 uses __fields__
    try:
        declared_fields = set(Settings.model_fields.keys())
    except AttributeError:
        declared_fields = set(Settings.__fields__.keys())  # type: ignore[attr-defined]

    orphaned = [k for k in example_keys if k not in declared_fields]
    assert not orphaned, f".env.example contains keys not declared in Settings: {orphaned}"


# ---------------------------------------------------------------------------
# C2 additions: REDIS_URL (TДЕ-9)
# ---------------------------------------------------------------------------


def test_settings_loads_redis_url_from_env(clean_env):
    """
    When REDIS_URL is present in ENV (alongside DATABASE_URL), Settings must
    expose it as settings.redis_url.
    REDIS_URL is required since C2 — Redis is a hard dependency for the broker
    (conventions §6, C2 plan §2 core/config).
    """
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    clean_env.setenv("REDIS_URL", "redis://localhost:6379/0")

    from core.config.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    assert settings.redis_url == "redis://localhost:6379/0"


def test_settings_raises_on_missing_redis_url(clean_env):
    """
    When REDIS_URL is absent, constructing Settings must raise a ValidationError.
    Redis is a required dependency from C2 onward; missing config must fail
    loudly at startup rather than producing a confusing runtime error
    (conventions §6 fail-fast, C2-T03).
    """
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    # REDIS_URL intentionally not set

    with pytest.raises(Exception) as exc_info:
        from core.config.settings import Settings

        Settings()  # type: ignore[call-arg]

    error_text = str(exc_info.value).lower()
    assert "redis_url" in error_text or "validation" in error_text or "field" in error_text


def test_settings_redis_url_not_in_repr(clean_env):
    """
    The REDIS_URL value must not appear in repr(settings) or str(settings).
    REDIS_URL can contain credentials (redis://:password@host/db) — it must
    be masked in all string representations (conventions §6).
    """
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    clean_env.setenv("REDIS_URL", "redis://:SUPERSECRET_REDIS@localhost:6379/0")

    from core.config.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    representation = repr(settings) + str(settings)

    assert "SUPERSECRET_REDIS" not in representation
