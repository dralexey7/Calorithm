# Контракты компонентов — Calorithm (ЧИСТОВИК)

> Статус: чистовик, шаг 2 процесса. Дополняет `docs/architecture.md`.
> Сигнатуры — типизированные Pydantic-границы (ADR-0001: компоненты обмениваются валидированными типизированными payload). Точные имена полей и форматы валидации уточняются на этапе реализации, но **состав полей и семантика — зафиксированы**.
> Язык: русский. Дата: 2026-06-02.

---

## 1. Эндпоинты `core-api` (FastAPI)

Все эндпоинты канало-независимы: принимают `(channel, channel_user_id)` для резолва пользователя. **Канонический вход в ядро — только через эти HTTP-эндпоинты** (адаптер не публикует задачи в брокер напрямую — ADR-0012/0008).

Принцип «минимум синхронного» (ADR-0012): **синхронны только** резолв/регистрация (US-001), чтение/смена настроек (US-005), смена primary-канала и сам **enqueue** (быстрый non-blocking возврат). Тяжёлые/отложенные операции (лог еды, сводка по запросу US-007, удаление US-009) **ставятся в очередь**, результат приходит через топик `results.<channel>`.

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
- `is_dev: bool`
- `is_primary_channel: bool` — является ли текущий канал primary

### 1.2. `POST /v1/messages` — приём сообщения о еде → постановка в очередь (US-002/US-017)

Тонкая ручка: валидирует, резолвит пользователя, публикует `Task{kind="ingest_message"}` в очередь `tasks.llm`, отвечает сразу (индикация на стороне адаптера опциональна — ADR-0003). Результат придёт асинхронно через топик (§4.2).

Request `IngestMessageRequest`:
- `channel: str`
- `channel_user_id: str`
- `text: str`
- `reply_to: ReplyTo` — куда вернуть результат (см. §3.4)

Response `IngestMessageResponse`:
- `task_id: str` — для трассировки/идемпотентности
- `status: Literal["queued"]`

> Канонический путь (ADR-0012, под вето автора): вход — **только** через этот эндпоинт; адаптер **не** публикует `Task` в брокер напрямую (не знает топиков задач, для брокера он — только подписчик `results.*`). Обоснование: чистая версионируемая граница; enqueue быстрый и non-blocking, не противоречит «минимуму синхронного».

### 1.3. `GET /v1/summary` — сводка по запросу (US-007, **асинхронная** — ADR-0012)

Query: `channel`, `channel_user_id`, `local_date` (опц.; по умолчанию — сегодня в TZ пользователя), плюс `reply_to` (для адресации результата; в Telegram-адаптере извлекается из апдейта).

Не считает сводку в HTTP-ответе: резолвит пользователя и ставит `Task{kind="build_summary", origin="request"}` в очередь `tasks.diary`; строит и доставляет non-LLM обработчик `diary-worker` (§4.4).

Response `EnqueueSummaryResponse`:
- `task_id: str`
- `status: Literal["queued"]`

Результат приходит асинхронно через топик как `Result{kind="daily_summary"}` (§3.2) с payload, эквивалентным прежнему `SummaryResponse`:
- `local_date: date`
- `items: list[SummaryItem]` — пусто, если записей нет
- `totals: Nutrition` — суммарные КБЖУ за день (нули, если пусто)
- `is_empty: bool` — для понятного сообщения «записей нет»

`SummaryItem`: `entry_id`, `name`, `qty_grams`, `qty_is_estimated`, `nutrition: Nutrition`, `source: Literal["OFF","LLM"]`, `entry_id_visible: str | None` (виден только dev-пользователю, US-009).

### 1.4. `DELETE /v1/entries/{entry_id}` — удаление записи (US-009)

Только своя запись; видимый id показывается dev-пользователям. Удаление только командой.

Path: `entry_id`. Body `DeleteEntryRequest`: `channel`, `channel_user_id`.

Response `DeleteEntryResponse`:
- `deleted: bool`
- `entry_id: str`
- `reason: str | None` — напр. `"not_found"`, `"not_owner"`

### 1.5. `PATCH /v1/users/settings` — смена per-user настроек (US-005, A7)

Request `UpdateSettingsRequest` (все поля опциональны):
- `channel`, `channel_user_id` (обязательны для резолва)
- `confirm_enabled: bool | None`
- *(timezone/is_dev — поля модели есть, но смена в MVP вне scope: TZ — US-013 вне MVP; is_dev меняется вне пользовательского пути)*

Response `UserSettingsResponse`: `confirm_enabled`, `timezone`, `is_dev`.

### 1.6. `POST /v1/users/primary-channel` — смена primary-канала (US-008, ADR-0009)

Делает указанный канal пользователя primary; снимает флаг с прежнего (инвариант: ровно один primary среди активных).

Request `SetPrimaryChannelRequest`: `channel`, `channel_user_id`.

Response `SetPrimaryChannelResponse`:
- `user_id: UUID`
- `primary_channel: str`
- `primary_channel_user_id: str`

### 1.7. Служебные

- `GET /metrics` — Prometheus (текстовый формат), не `/v1`.
- `GET /healthz` — liveness (свобода реализации деталей).

---

## 2. Общие типы (Pydantic DTO)

- **`Nutrition`**: `kcal: float`, `protein: float`, `fat: float`, `carb: float` — на указанное количество (для `/100г` используется тот же тип в контексте item).
- **`ParsedItem`**: `name: str`, `qty_grams: float`, `qty_is_estimated: bool`.
- **`NutritionedItem`**: `name`, `qty_grams`, `qty_is_estimated`, `nutrition_per_100g: Nutrition`, `nutrition_total: Nutrition`, `source: Literal["OFF","LLM"]`.
- **`ReplyTo`** (Telegram): `chat_id: int`, `message_id: int | None`. Канало-специфичная адресация ответа; для других каналов поля иные, но роль та же.

---

## 3. Схемы сообщений брокера

Транспорт — Redis Streams за портом `MessageBus` (ADR-0004). Семантика: at-least-once, идемпотентный handler, явный ack. Все сообщения — Pydantic-события, сериализуемые в поля stream.

Общие поля каждого сообщения (envelope): `schema_version: int`, `event_id: str`, `occurred_at: datetime`.

Топики задач разделены по природе работы (ADR-0013): **`tasks.llm`** (LLM-работа) и **`tasks.diary`** (non-LLM сводки). Публикует **только `core-api`** (для триггера авто-сводки — `scheduler`); адаптер задачи в брокер **не публикует** (ADR-0012/0008).

### 3.1. `LlmTask` — LLM-задача обработки (топик `tasks.llm`)

Публикует `core-api`; потребляет `core-worker` (LLM-only, consumer group `llm-workers`).

- `task_id: str` — ключ идемпотентности (→ `entries.source_task_id`)
- `channel: str`
- `channel_user_id: str`
- `user_id: UUID` — резолвится до публикации (или воркером — свобода реализации)
- `kind: Literal["ingest_message", "confirm"]` — обычный лог либо подтверждение превью (US-005)
- `text: str | None` — текст еды (для `ingest_message`)
- `confirm_payload: ConfirmPayload | None` — для `kind="confirm"` (ссылка на превью/позиции)
- `reply_to: ReplyTo`

### 3.1a. `DiaryTask` — non-LLM задача (топик `tasks.diary`)

Публикует `core-api` (сводка по запросу US-007, `origin="request"`) и `scheduler` (авто-сводка US-008, `origin="auto"`); потребляет `diary-worker` (non-LLM, consumer group `diary-workers`).

- `task_id: str` — ключ идемпотентности
- `kind: Literal["build_summary", "delete_entry"]` — `delete_entry` — опционально, если удаление переведено в async (US-009)
- `user_id: UUID`
- `channel: str` — канал доставки результата (для авто-сводки = primary)
- `channel_user_id: str`
- `local_date: date | None` — день сводки в TZ пользователя (`build_summary`)
- `origin: Literal["request", "auto"]` — источник (для `build_summary`)
- `entry_id: str | None` — для `delete_entry`
- `reply_to: ReplyTo`

### 3.2. `Result` — результат обработки (топик `results.telegram`, на будущее `results.<channel>`)

Публикует `core-worker` (LLM-результаты) и `diary-worker` (сводки US-007/US-008); потребляет адаптер канала (consumer group на адаптер-инстанс). Партиционирование по каналу (ADR-0008). `scheduler` результаты **не публикует** — он только ставит `build_summary` в `tasks.diary` (ADR-0013).

- `task_id: str | None` — для авто-сводки соответствует `task_id` задачи `build_summary` (origin=auto); идемпотентность доставки — по `task_id`, дубля авто-сводки — по `summary_dispatch`
- `channel: str`
- `channel_user_id: str`
- `reply_to: ReplyTo`
- `kind: Literal["not_food", "preview", "logged", "daily_summary", "error"]`
- `payload: ResultPayload` — варьируется по `kind`:
  - `not_food` — текст «не распознано как еда» (US-017)
  - `preview` — `items: list[NutritionedItem]` + признак ожидания подтверждения (US-005)
  - `logged` — `entry_id`, `items: list[NutritionedItem]`, `totals: Nutrition` (US-002)
  - `daily_summary` — `local_date`, `items`, `totals`, `is_empty` (US-007 по запросу и US-008 авто)
  - `error` — `message: str`, `retryable: bool`

### 3.3. Ключевые внутренние события (шина модулей, топики `events.*`)

Межмодульное общение, не через общие таблицы (ADR-0001). Минимальный набор MVP:

- **`EntryLogged`** (топик `events.diary`): `user_id`, `entry_id`, `local_date`, `task_id`, `items_count`, `totals: Nutrition`. Публикует `diary` после сохранения; потребители — метрики/будущие модули (напр. цели US-011). Развязывает `diary` от потребителей факта записи.
- **`UserRegistered`** (топик `events.users`): `user_id`, `channel`, `channel_user_id`, `created: bool`. Публикует `users`; для аналитики/онбординга.
- **`SummaryDispatched`** (топик `events.diary`): `user_id`, `local_date`, `sent: bool` (false = пусто, не слали — A6). Публикует `diary-worker` (он строит/доставляет сводку и владеет записью в `summary_dispatch` — схема `diary`); для метрик доставки.

> Свобода реализации: набор внутренних событий минимален для MVP и расширяется по мере появления потребителей; envelope и принцип «через `bus`, не через таблицы» — обязательны. Точные имена топиков (`tasks.llm`, `tasks.diary`, `results.<channel>`, `events.*`) фиксированы как контракт.

### 3.4. Маршрутизация и адресация ответа

- Ответ на конкретное сообщение идёт в **канал-источник** — `channel` + `reply_to` берутся из исходного `Task` (ADR-0008).
- Авто-сводка идёт в **primary-канал** пользователя — `diary-worker` (при обработке `build_summary` с `origin="auto"`) резолвит primary через `users` и формирует `Result{kind="daily_summary"}` с соответствующими `channel`/`reply_to` (ADR-0009, ADR-0013). `scheduler` только ставит задачу.

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
