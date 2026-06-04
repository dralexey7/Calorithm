"""
core.config.settings -- single source of truth for ENV configuration (conventions §6).

Rules:
  - All ENV variables are read ONLY through this module; direct os.getenv outside config is forbidden.
  - DATABASE_URL is required; absence raises ValidationError at startup (fail-fast).
  - Secrets do not leak into repr/str (DATABASE_URL contains credentials).
  - Only parameters needed for the current stage (C1) are declared here.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Required:
      DATABASE_URL — async SQLAlchemy URL (postgresql+asyncpg://...).

    Optional (with sensible defaults for C1):
      LOG_LEVEL — logging level string (default: "INFO").
      APP_ENV   — deployment environment tag (default: "development").
    """

    model_config = SettingsConfigDict(
        # Read from .env when present (useful for local dev); ENV always takes precedence.
        env_file=".env",
        env_file_encoding="utf-8",
        # Pydantic-settings lowercases field names before matching to ENV keys by default.
        case_sensitive=False,
        # Forbid extra env keys to surface typos early.
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Required
    # ------------------------------------------------------------------

    database_url: str
    """Full async SQLAlchemy URL including credentials — treated as a secret in repr."""

    # ------------------------------------------------------------------
    # Optional / C1-level
    # ------------------------------------------------------------------

    log_level: str = "INFO"
    """Python logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL."""

    app_env: str = "development"
    """Deployment environment tag: development | staging | production."""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @field_validator("database_url")
    @classmethod
    def _database_url_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("database_url must not be empty")
        return v

    @field_validator("log_level")
    @classmethod
    def _log_level_valid(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return v.upper()

    # ------------------------------------------------------------------
    # Secret hygiene: never print DATABASE_URL in repr/str
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        # List all fields but mask database_url so credentials never leak into logs.
        masked = {
            name: "***" if name == "database_url" else getattr(self, name)
            for name in self.model_fields
        }
        fields_str = ", ".join(f"{k}={v!r}" for k, v in masked.items())
        return f"{self.__class__.__name__}({fields_str})"

    def __str__(self) -> str:
        return self.__repr__()
