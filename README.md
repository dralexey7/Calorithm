# Calorithm

Smart Telegram calorie counter: users write in free form ("200g fried chicken with buckwheat") — the system parses the text, fetches nutrition data (КБЖУ), stores it, and lets users track their intake.

**Stack:** Python 3.12 · FastAPI · PostgreSQL 16 · aiogram · LiteLLM · Redis · Docker · Alembic.

---

## Running tests

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended package manager — fast, lock-file based)

Install dependencies:

```bash
uv pip install -e ".[dev]"
```

### Unit tests (no Docker required)

Unit tests are pure Python — no database, no containers, no network.

```bash
uv run pytest -m "not integration" -v
```

### Integration tests (Docker required)

Integration tests spin up an ephemeral Postgres via [testcontainers](https://testcontainers-python.readthedocs.io/). You need a running Docker daemon.

```bash
# Docker must be running; testcontainers handles the rest automatically
uv run pytest -m integration -v
```

You can also point tests at an existing Postgres instance instead of spinning up a container:

```bash
TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/calorithm_test" \
    uv run pytest -m integration -v
```

### All tests

```bash
uv run pytest -v
```

### Lint, format, and type check

```bash
# Linter
uv run ruff check .

# Formatter (check only, no changes)
uv run ruff format --check .

# Formatter (apply changes)
uv run ruff format .

# Type checker
uv run pyright
```

---

## Local deployment (Docker Compose)

The full stack (PostgreSQL + one-shot migrations + api-core) starts with a single command.

### Prerequisites

- Docker 24+ and Docker Compose v2 (tested with Docker 27 / Compose v2.32).
- No other prerequisites — all Python dependencies are baked into the image.

### Start the stack

```bash
# 1. Copy the environment template and fill in values (change passwords in production).
cp .env.example .env

# 2. Build images and start all services in the background.
docker compose up -d --build
```

### What happens on startup

1. `postgres` starts and becomes healthy (`pg_isready` passes).
2. `migrate` (one-shot) runs `alembic upgrade head` — applies all pending migrations — then exits with code 0.
3. `api-core` starts only after `migrate` completes successfully.

### Verify the stack is running

```bash
# Health check — should return HTTP 200 with {"status": "ok"}.
curl http://localhost:8000/healthz

# Prometheus metrics endpoint.
curl http://localhost:8000/metrics

# Check all containers are up (migrate exits 0; postgres and api-core stay running).
docker compose ps
```

### Idempotent re-deploy

Running `docker compose up -d` again on an already-migrated database is safe.
The `migrate` service re-runs `alembic upgrade head`; Alembic detects no pending
revisions and exits cleanly without touching the schema. `api-core` restarts normally.

### Stop and clean up

```bash
# Stop and remove containers (keep postgres data volume).
docker compose down

# Stop and remove containers AND data volume (full reset).
docker compose down -v
```

### VPS deployment (sketch)

1. Provision a Linux VPS (Ubuntu 22.04 LTS or similar); install Docker + Docker Compose.
2. Clone the repository: `git clone <repo> /srv/calorithm && cd /srv/calorithm`.
3. Create `.env` from `.env.example`; set strong `POSTGRES_PASSWORD` and real `DATABASE_URL`.
4. Run `docker compose up -d --build`.
5. (Optional) point a reverse proxy (nginx / Caddy) at `localhost:8000` for TLS termination.
6. On subsequent deploys: `git pull && docker compose up -d --build` — migrations run automatically before api-core restarts.

> Full production hardening (TLS, firewall rules, log shipping, Prometheus/Grafana, backups)
> is scoped to later stages (C5+). The skeleton above is sufficient for a working MVP deploy.

---

## Project layout

```
core/          # Shared business logic and infrastructure modules
  config/      # Pydantic Settings — single source of truth for ENV
apps/
  api_core/    # FastAPI app: HTTP surface + sole DB owner (ADR-0015)
migrations/    # Alembic revisions
tests/
  unit/        # Pure unit tests — no external dependencies
  integration/ # Integration tests — require Postgres / Docker
docs/          # Architecture, ADRs, conventions, stage plans
```

See [`docs/conventions.md`](docs/conventions.md) for coding conventions and Definition of Done.
