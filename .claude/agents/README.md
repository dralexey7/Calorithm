# Calorithm — Субагенты для Claude Code

10 специализированных субагентов для разработки **Calorithm** — умного счётчика калорий в Telegram. Пользователь пишет в свободной форме о съеденном ("грамм 200 жареной курицы с гречкой", "тарелка куриного супа"); система парсит текст, переводит его в структурированный вид, получает КБЖУ и сохраняет, а пользователь отслеживает потребление.

## Подтверждённый стек

Python · FastAPI · PostgreSQL · Telegram (aiogram) · LiteLLM (оркестрация LLM) · Docker

> Остальное (источник данных КБЖУ, фоновая обработка, схема БД, структура проекта, тулинг) ещё проектируется. Агенты намеренно держатся **общими**: они знают продукт и ядро стека, но не зашивают решения, которые ещё не приняты. Конкретику добавим в файлы агентов после проектирования системы.

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

## Рекомендуемый порядок работы над фичей

```
1. product-manager       → user story + acceptance criteria
2. tech-lead             → план задач и порядок выполнения
3. system-architect      → как фича вписывается в архитектуру (если нужно)
4. database-architect    → схема / миграция (если нужна БД)
5. fastapi-developer     → бизнес-логика и API
6. telegram-bot-developer→ бот-хендлеры
7. test-engineer         → тесты
8. security-auditor      → проверка безопасности
9. code-reviewer         → финальный ревью
10. devops-engineer      → если нужны изменения в инфре
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
