# Calorithm — Субагенты для Claude Code

10 специализированных субагентов для разработки **Calorithm** — умного счётчика калорий в Telegram. Пользователь пишет в свободной форме о съеденном ("грамм 200 жареной курицы с гречкой", "тарелка куриного супа"); система парсит текст, переводит его в структурированный вид, получает КБЖУ и сохраняет, а пользователь отслеживает потребление.

## Подтверждённый стек

Python · FastAPI · PostgreSQL · Telegram (aiogram) · LiteLLM · Redis · Alembic · Docker · Prometheus + Grafana

> Архитектура спроектирована. Источник правды для агентов: `docs/architecture.md`, `docs/contracts.md`, `docs/conventions.md` (+ Definition of Done), `docs/adr/`, `docs/prd.md`, `docs/development-workflow.md`. Агенты ссылаются на эти документы, а не дублируют их — так контекст не расходится. Процесс — **TDD** (тесты раньше кода).

## Где лежат файлы

Проектные агенты — работают внутри репозитория и хранятся в git, чтобы вся команда использовала одних и тех же агентов:

```
Calorithm/
└── .claude/
    └── agents/
        ├── product-manager.md
        ├── tech-lead.md
        ├── system-architect.md
        ├── database-architect.md
        ├── fastapi-developer.md
        ├── telegram-bot-developer.md
        ├── code-reviewer.md
        ├── test-engineer.md
        ├── security-auditor.md
        └── devops-engineer.md
```

(Те же файлы можно положить в `~/.claude/agents/`, если нужны глобально во всех проектах.)

## Как пользоваться

Claude Code сам решает, кого подключить, на основе поля `description` в каждом файле. Просто пиши задачи естественным языком:

```
"Напиши user story для логирования еды через текст"   → product-manager
"Спроектируй, как фича вписывается в архитектуру"      → system-architect
"Спроектируй схему хранения приёмов пищи"              → database-architect
"Реализуй парсинг описания еды через LiteLLM"          → fastapi-developer
"Сделай хендлер для текстовых сообщений"               → telegram-bot-developer
"Покрой парсер тестами"                                → test-engineer
"Проверь этот код перед мержем"                        → code-reviewer
```

Принудительный вызов:
```
"Используй database-architect, чтобы добавить таблицу для целей по калориям"
```

## Рекомендуемый порядок работы над фичей (TDD)

Соответствует `docs/development-workflow.md` (Фазы 2…N). Ключевое: **тесты пишутся раньше кода**.

```
1. product-manager        → user story + acceptance criteria (если новые)
2. tech-lead              → план стадии, разбивка на срезы
3. system-architect       → если нужны изменения архитектуры/ADR
4. database-architect     → схема + Alembic-миграция (если нужна БД)
   ── далее TDD-цикл по каждому срезу ──
5. test-engineer          → ТЕСТЫ первыми (красные), автор валидирует глазами
6. fastapi-developer /    → реализация под зелёное
   telegram-bot-developer
7. code-reviewer          → ревью (+ security-auditor, если секреты/ввод/инфра)
8. (правки по ревью, повтор по срезам)
9. devops-engineer        → инфра/деплой/миграции при необходимости
```

## Агенты и модели

Модель выбрана по характеру задачи. Используются псевдонимы (`opus`/`sonnet`) — они резолвятся в актуальную версию и не устаревают.

| Агент | Роль | Модель |
|---|---|---|
| `product-manager` | PRD, user stories, скоуп | **opus** |
| `tech-lead` | Планирование, разбивка задач, оркестрация | **opus** |
| `system-architect` | Архитектура, ADR, контракты компонентов | **opus** |
| `code-reviewer` | Ревью кода, quality gate | **opus** |
| `security-auditor` | Секреты, инъекции, Docker-безопасность | **opus** |
| `database-architect` | PostgreSQL схема, ORM, миграции | **sonnet** |
| `fastapi-developer` | Backend, API, парсинг через LiteLLM | **sonnet** |
| `telegram-bot-developer` | Telegram-хендлеры, форматирование | **sonnet** |
| `test-engineer` | pytest, моки, покрытие | **sonnet** |
| `devops-engineer` | Docker, Compose, деплой | **sonnet** |

Логика: глубокое рассуждение (планирование, архитектура, ревью, безопасность) → **opus**; генерация кода и конфигов в ограниченном домене → **sonnet**.

## Проверка, что агенты загрузились

В Claude Code выполни `/agents` — должны появиться все 10.
