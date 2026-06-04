# Calorithm api-core image.
#
# Build stages:
#   builder — installs Python dependencies into /app/.venv via uv (deps before code).
#   runtime — slim final image; copies only the venv and source; runs as non-root.
#
# Base image: python:3.12-slim-bookworm — Debian Bookworm slim, version-pinned.
# Package manager: uv (same as local dev, fast installs from lock file).
#
# Layer cache strategy:
#   1. Install uv.
#   2. Copy pyproject.toml + uv.lock (changes infrequently) → install deps.
#   3. Copy application source (changes often) → installed separately.
#
# Non-root: runtime image creates user `appuser` (uid 1001) and runs under it.
# Secrets: none hardcoded; all config from environment variables.

# ---------------------------------------------------------------------------
# Stage 1: builder — install dependencies
# ---------------------------------------------------------------------------
FROM python:3.12.10-slim-bookworm AS builder

# Install uv (pinned version for reproducibility).
# See https://docs.astral.sh/uv/guides/integration/docker/
COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy only the dependency manifest files first — maximises layer cache reuse.
# Source code changes do NOT bust the deps layer.
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev extras) into /app/.venv.
# --frozen: respect the lock file exactly (no resolution).
# --no-install-project: skip installing the project itself (source not copied yet).
RUN uv sync --frozen --no-install-project --no-dev

# Now copy the application source.
COPY core/ ./core/
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Install the project itself (editable or non-editable; uv picks the right mode).
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim final image
# ---------------------------------------------------------------------------
FROM python:3.12.10-slim-bookworm AS runtime

# Create a non-root user to run the application.
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid 1001 --no-create-home --shell /sbin/nologin appuser

WORKDIR /app

# Copy the fully-installed venv and application source from builder.
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appgroup /app/core /app/core
COPY --from=builder --chown=appuser:appgroup /app/apps /app/apps
COPY --from=builder --chown=appuser:appgroup /app/migrations /app/migrations
COPY --from=builder --chown=appuser:appgroup /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=appuser:appgroup /app/pyproject.toml /app/pyproject.toml

# Activate the venv by prepending it to PATH.
ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user.
USER appuser

# Expose the HTTP port (8000 inside container; mapped by compose).
EXPOSE 8000

# Run uvicorn with the api-core ASGI app.
# --host 0.0.0.0: listen on all interfaces inside the container network.
# --no-access-log: structured logging handled separately (stdout → Docker).
# ENV-driven config (DATABASE_URL, LOG_LEVEL, APP_ENV) is read by core.config.
CMD ["uvicorn", "apps.api_core.main:app", "--host", "0.0.0.0", "--port", "8000"]
