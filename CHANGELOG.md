# Changelog

Все значимые изменения Calorithm фиксируются здесь.
Формат — Keep a Changelog; semver-теги ставятся на значимых релизах (скелет/MVP),
промежуточные стадии помечаются лёгкими тегами-чекпойнтами (`c1`, `c2`, …).

## [Unreleased]

### Стадия C1 — шагающий скелет: каркас БД/API (тег `c1`)
- Скелет репозитория и тулинг: uv, ruff (lint+format), pyright, pytest; CI (lint, typecheck, unit, integration).
- `core/config`: Pydantic Settings (`DATABASE_URL` обязателен, fail-fast; секрет маскируется в `repr/str`); `.env.example`.
- `api-core` (FastAPI): фабрика `create_app()` + модульный `app`; `GET /healthz` (реальная проба БД, 200/503, без утечки трейсов); `GET /metrics` (Prometheus).
- Alembic + миграция `0001`: создание схем `users`, `diary` (baseline без таблиц; обратимая, `up→down→up`).
- `docker-compose`: postgres (только внутренняя сеть) → one-shot миграции → api-core; образ non-root, базовые образы запинены; деплой одной командой.
- Тесты (TDD): unit (config, health, metrics, smoke) + integration через testcontainers (миграции, real-DB healthz). Все гейты зелёные; security-пасс — clear.
