# Контракты компонентов — Calorithm (ЧИСТОВИК)

> Статус: чистовик, шаг 2 процесса. Дополняет `docs/architecture.md`.
> Сигнатуры — типизированные Pydantic-границы (ADR-0001: компоненты обмениваются валидированными типизированными payload). Точные имена полей и форматы валидации уточняются на этапе реализации, но **состав полей и семантика — зафиксированы**.
> Язык: русский. Дата: 2026-06-02.

---

## 1. Эндпоинты `api-core` (FastAPI)

Все эндпоинты канало-независимы: принимают `(channel, channel_user_id)` для резолва пользователя. **Канонический вход в ядро — только через эти HTTP-эндпоинты** (адаптер не публикует задачи в брокер напрямую — ADR-0012/0008). Имя сервиса ядра — `api-core` (исторический префикс `core-api` — тот же компонент).

Принцип «async только там, где есть внешний/медленный/лимитируемый вызов» (ADR-0015, уточняет ADR-0012): **синхронны в HTTP-ответе** резолв/регистрация (US-001), чтение/смена настроек (US-005), смена primary, **сводка по запросу (US-007)**, **мягкое удаление (US-009)** и **enqueue еды** (быстрый non-blocking возврат). Асинхронен **только** лог еды (US-002): он ставится в `tasks.processing`, обрабатывается `processing-worker` (без БД), результат возвращается в `api-core`, тот персистит и доставляет через `results.<channel>`.

Общие конвенции: путь с префиксом `/v1`; тело — JSON; ответы — Pydantic-модели; ошибки — структурированный JSON (см. `conventions.md`).

### 1.1. `POST /v1/users/resolve` — регистрация/резолв пользователя (US-001)

Идемпотентен: создаёт пользователя при первом обращении (`/start`), иначе возвращает существующего. Регистрирует/обновляет `channel_identity`.

Request `ResolveUserRequest`:
- `channel: str` — напр. `"telegram"`
- `channel_user_id: str` — внешний id в канале
- `is_start: bool = False` — пришёл по `/start` (для приветствия)

Response `ResolveUserResponse`:
- `user_id: UUID`
- `created: bool` — был ли создан новый аккаунт
- `timezone: str`
- `confirm_enabled: bool`
- `auto_summary_enabled: bool` — ADR-0018
- `is_dev: bool`
- `is_primary_channel: bool` — является ли текущий канал primary

### 1.2. `POST /v1/messages` — приём сообщения о еде → постановка в очередь (US-002/US-017)

Тонкая ручка: валидирует, резолвит пользователя, публикует `ProcessingTask` в очередь `tasks.processing`, отвечает сразу (индикация на стороне адаптера опциональна — ADR-0003). Воркер вернёт `ProcessingResult` в `api-core` (`results.processing`), `api-core` персистит запись и доставит результат через `results.<channel>` (§4.2).

Request `IngestMessageRequest`:
- `channel: str`
- `channel_user_id: str`
- `text: str`
- `reply_to: ReplyTo` — куда вернуть результат (см. §3.4)

Response `IngestMessageResponse`:
- `task_id: str` — для трассировки/идемпотентности
- `status: Literal["queued"]`

> Канонический путь (ADR-0012, под вето автора): вход — **только** через этот эндпоинт; адаптер **не** публикует `Task` в брокер напрямую (не знает топиков задач, для брокера он — только подписчик `results.*`). Обоснование: чистая версионируемая граница; enqueue быстрый и non-blocking, не противоречит «минимуму синхронного».

### 1.3. `GET /v1/summary` — сводка по запросу (US-007, **синхронная** — ADR-0015)

Query: `channel`, `channel_user_id`, `local_date` (опц.; по умолчанию — сегодня в TZ пользователя).

Считает сводку **в HTTP-ответе**: резолвит пользователя, читает записи дня (только `status='confirmed'` — ADR-0016) и форматирует. Без LLM/OFF и без очереди.

Response `SummaryResponse`:
- `local_date: date`
- `items: list[SummaryItem]` — пусто, если записей нет
- `totals: Nutrition` — суммарные КБЖУ за день (нули, если пусто)
- `is_empty: bool` — для понятного сообщения «записей нет»

`SummaryItem`: `entry_id`, `name`, `qty_grams`, `qty_is_estimated`, `nutrition: Nutrition`, `source: Literal["OFF","LLM"]`, `entry_id_visible: str | None` (виден только dev-пользователю, US-009).

### 1.4. `DELETE /v1/entries/{entry_id}` — мягкое удаление записи (US-009, ADR-0016)

Только своя запись; видимый id показывается dev-пользователям. Удаление только командой. **Синхронно** в `api-core`: переводит `status` в `deleted` (без физического `DELETE`); идемпотентно (повтор на уже `deleted` → `deleted=true`). Удалённая запись не учитывается в сводках.

Path: `entry_id`. Body `DeleteEntryRequest`: `channel`, `channel_user_id`.

Response `DeleteEntryResponse`:
- `deleted: bool`
- `entry_id: str`
- `reason: str | None` — напр. `"not_found"`, `"not_owner"`

### 1.5. `PATCH /v1/users/settings` — смена per-user настроек (US-005, A7)

Request `UpdateSettingsRequest` (все поля опциональны):
- `channel`, `channel_user_id` (обязательны для резолва)
- `confirm_enabled: bool | None`
- `auto_summary_enabled: bool | None` — отключаемая авто-сводка (US-008, ADR-0018)
- *(timezone/is_dev — поля модели есть, но смена в MVP вне scope: TZ — US-013 вне MVP; is_dev меняется вне пользовательского пути)*

Response `UserSettingsResponse`: `confirm_enabled`, `auto_summary_enabled`, `timezone`, `is_dev`.

### 1.6. `POST /v1/users/primary-channel` — смена primary-канала (US-008, ADR-0009)

Делает указанный канal пользователя primary; снимает флаг с прежнего (инвариант: ровно один primary среди активных).

Request `SetPrimaryChannelRequest`: `channel`, `channel_user_id`.

Response `SetPrimaryChannelResponse`:
- `user_id: UUID`
- `primary_channel: str`
- `primary_channel_user_id: str`

### 1.7. Внутренний — триггер авто-сводки (US-008, ADR-0018)

`POST /v1/internal/auto-summary` — дёргается **только `scheduler`** изнутри compose-сети (не публичный, адаптер его не вызывает). `api-core` синхронно для пользователей с локальным 09:00: проверяет `auto_summary_enabled`, читает confirmed-записи за вчера, дедуплицирует через `summary_dispatch`, публикует `Result{kind="daily_summary"}` в primary-канал. Тело/гранулярность (все пользователи vs список) — свобода реализации. Идемпотентно (дубль режет `summary_dispatch`).

Response: счётчики обработанных/отправленных/пропущенных (для метрик; точная форма — свобода реализации).

### 1.8. Служебные

- `GET /metrics` — Prometheus (текстовый формат), не `/v1`.
- `GET /healthz` — liveness (свобода реализации деталей).

---

## 2. Общие типы (Pydantic DTO)

- **`Nutrition`**: `kcal: float`, `protein: float`, `fat: float`, `carb: float` — на указанное количество (для `/100г` используется тот же тип в контексте item).
- **`ParsedItem`**: `name: str`, `qty_grams: float`, `qty_is_estimated: bool`.
- **`NutritionedItem`**: `name`, `qty_grams`, `qty_is_estimated`, `nutrition_per_100g: Nutrition`, `nutrition_total: Nutrition`, `source: Literal["OFF","LLM"]`.
- **`ReplyTo`** (Telegram): `chat_id: int`, `message_id: int | None`. Канало-специфичная адресация ответа; для других каналов поля иные, но роль та же.
- **`EntryStatus`**: `Literal["pending","confirmed","rejected","deleted"]` — статусная модель записи (ADR-0016); в сводках учитываются только `confirmed`.
- **`ProcessingArtifacts`** (ADR-0017): `intent_result`, `parse_artifact`, `model_meta` — трасса обработки для persist/разбора; точная структура — свобода реализации.
- **`ProcessingError`**: `message: str`, `retryable: bool`.

---

## 3. Схемы сообщений брокера

Транспорт — Redis Streams за портом `MessageBus` (ADR-0004). Семантика: at-least-once, идемпотентный handler, явный ack. Все сообщения — Pydantic-события, сериализуемые в поля stream.

Общие поля каждого сообщения (envelope): `schema_version: int`, `event_id: str`, `occurred_at: datetime`.

Один топик задач (ADR-0015): **`tasks.processing`** (пайплайн обработки сообщения о еде — единственная async/лимитируемая работа). Обратный топик **`results.processing`** несёт результат воркера в `api-core`. Публикует `tasks.processing` **только `api-core`**; адаптер задачи в брокер **не публикует** (ADR-0012/0008). `tasks.diary`/`build_summary`/`diary-worker` удалены (ADR-0015): сводки теперь синхронны в `api-core`.

### 3.1. `ProcessingTask` — задача обработки сообщения (топик `tasks.processing`)

Публикует `api-core`; потребляет `processing-worker` (stateless, без БД, consumer group `processing-workers`).

- `task_id: str` — ключ идемпотентности (→ `entries.source_task_id`)
- `channel: str`
- `channel_user_id: str`
- `user_id: UUID` — резолвится `api-core` до публикации
- `kind: Literal["ingest_message", "confirm"]` — обычный лог либо подтверждение превью (US-005; confirm может быть отдельной задачей или переходом статуса — свобода реализации, ADR-0016)
- `text: str | None` — текст еды (для `ingest_message`)
- `confirm_payload: ConfirmPayload | None` — для `kind="confirm"`
- `confirm_enabled: bool` — нужно ли превью вместо автосейва (US-005) — `api-core` прокидывает из настроек
- `reply_to: ReplyTo`

### 3.1a. `ProcessingResult` — результат воркера в `api-core` (топик `results.processing`)

Публикует `processing-worker`; потребляет **`api-core`** (consumer group `api-core-orchestrator`). Воркер **не пишет в БД** — он возвращает структурный результат, persist делает `api-core` (ADR-0015). Артефакты трассировки — ADR-0017.

- `task_id: str` — корреляция с `ProcessingTask` и идемпотентность persist
- `channel: str`, `channel_user_id: str`, `user_id: UUID`, `reply_to: ReplyTo` — для последующего `Result`
- `kind: Literal["ingest_message", "confirm"]`
- `intent: Literal["food", "not_food"]` — итог классификации (US-017)
- `items: list[NutritionedItem] | None` — распознанные позиции + КБЖУ + `source` per-item (если `intent=food`)
- `totals: Nutrition | None`
- `artifacts: ProcessingArtifacts` — трасса для persist/разбора (ADR-0017): `intent_result`, `parse_artifact`, `model_meta`
- `error: ProcessingError | None` — `message`, `retryable` (если пайплайн упал)

### 3.2. `Result` — результат доставки в адаптер (топик `results.telegram`, на будущее `results.<channel>`)

Публикует **только `api-core`** (после persist для еды; синхронно для авто-сводки); потребляет адаптер канала (consumer group на адаптер-инстанс). Партиционирование по каналу (ADR-0008). Воркер и `scheduler` в `results.<channel>` **не публикуют**.

- `task_id: str | None` — для авто-сводки идемпотентность дубля — по `summary_dispatch`; для еды — по `task_id`
- `channel: str`
- `channel_user_id: str`
- `reply_to: ReplyTo`
- `kind: Literal["not_food", "preview", "logged", "daily_summary", "error"]`
- `payload: ResultPayload` — варьируется по `kind`:
  - `not_food` — текст «не распознано как еда» (US-017)
  - `preview` — `items: list[NutritionedItem]` + признак ожидания подтверждения (US-005); запись уже сохранена как `pending` (ADR-0016)
  - `logged` — `entry_id`, `items: list[NutritionedItem]`, `totals: Nutrition` (US-002; запись `confirmed`)
  - `daily_summary` — `local_date`, `items`, `totals`, `is_empty` (US-008 авто; для US-007 сводка идёт синхронным HTTP, а не через топик)
  - `error` — `message: str`, `retryable: bool`

### 3.3. Ключевые внутренние события (шина модулей, топики `events.*`)

Межмодульное общение, не через общие таблицы (ADR-0001). Минимальный набор MVP:

- **`EntryLogged`** (топик `events.diary`): `user_id`, `entry_id`, `local_date`, `task_id`, `items_count`, `totals: Nutrition`. Публикует `diary` (в `api-core`) после сохранения `confirmed`-записи; потребители — метрики/будущие модули (напр. цели US-011).
- **`UserRegistered`** (топик `events.users`): `user_id`, `channel`, `channel_user_id`, `created: bool`. Публикует `users`; для аналитики/онбординга.
- **`SummaryDispatched`** (топик `events.diary`): `user_id`, `local_date`, `sent: bool` (false = пусто/выключено, не слали — A6/ADR-0018). Публикует `api-core` (он строит/доставляет сводку и владеет `summary_dispatch` — схема `diary`); для метрик доставки.

> Свобода реализации: набор внутренних событий минимален для MVP и расширяется по мере появления потребителей; envelope и принцип «через `bus`, не через таблицы» — обязательны. Точные имена топиков (`tasks.processing`, `results.processing`, `results.<channel>`, `events.*`) фиксированы как контракт.

### 3.4. Маршрутизация и адресация ответа

- Ответ на конкретное сообщение идёт в **канал-источник** — `channel` + `reply_to` протягиваются `ProcessingTask` → `ProcessingResult` → `Result` (ADR-0008). Публикует `Result` всегда `api-core`.
- Авто-сводка идёт в **primary-канал** пользователя — `api-core` (по триггеру `scheduler`, ADR-0018) резолвит primary через `users` и формирует `Result{kind="daily_summary"}` с соответствующими `channel`/`reply_to` (ADR-0009). `scheduler` только дёргает триггер.

---

## 4. Порт `MessageBus` (контракт, не реализация)

```python
class MessageBus(Protocol):
    async def publish(self, topic: str, event: Event) -> None: ...
    async def subscribe(
        self, topic: str, group: str, handler: Callable[[Event], Awaitable[None]]
    ) -> None: ...
    # семантика: at-least-once; handler ОБЯЗАН быть идемпотентным;
    # ack — после успешной обработки; при ошибке — повторная доставка.
```

- Реализации: `RedisStreamsBus` (MVP), `KafkaBus` (позже, по триггерам ADR-0004), `InMemoryBus` (тесты).
- `topic`/`group` — абстрактные имена; маппинг на stream/consumer-group живёт в адаптере.
- Модули НИКОГДА не импортируют клиент брокера напрямую — только через этот порт.
