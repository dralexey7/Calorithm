# Модель данных — Calorithm MVP (авторитетный документ)

> Статус: **рабочий черновик, шаг 5 процесса** (`docs/development-workflow.md`). Требует ручной валидации автором перед стартом Стадии С1.
> Источники истины: `docs/architecture.md` §6, ADR-0001, ADR-0014, ADR-0015, ADR-0016, ADR-0017, ADR-0018, `docs/conventions.md` §3/§3a.
> Назначение: авторитетная детальная схема БД для MVP; `docs/architecture.md` §6 ссылается сюда и не дублирует DDL.
> Дата: 2026-06-03 · Язык: русский.

---

## 0. Принципы и ограничения

Все решения ниже следуют жёстким правилам:

- **`api-core` — единственный владелец БД** (ADR-0015). `processing-worker` и `scheduler` к Postgres не обращаются.
- **Одна схема — один владелец-модуль** (ADR-0001): схема `users` → модуль `users`; схема `diary` → модуль `diary` (включая `summary_dispatch`). У `scheduler` своей схемы нет.
- **Нет кросс-схемных FK** (ADR-0001). Ссылки между схемами — по значению `id` на уровне приложения. Например, `diary.entries.user_id` — это `bigint`, а не FK на `users.users.id`.
- **Все изменения схемы — только Alembic** (ADR-0014). Ручные `ALTER`/`CREATE`/`DROP` в продакшне запрещены.
- **Мягкое удаление** (ADR-0016): физический `DELETE` записей запрещён; удаление = смена `status` на `'deleted'`.
- **Инвариант сводок** (ADR-0016): сводки и дневные итоги учитывают только `status='confirmed'`. Фильтр — в единственном repository-методе модуля `diary`, покрыт тестом.

---

## 1. Первичные ключи: bigint GENERATED ALWAYS AS IDENTITY

**Решение: `bigint GENERATED ALWAYS AS IDENTITY` для всех первичных ключей** во всех таблицах (`users.users`, `users.channel_identities`, `diary.entries`, `diary.entry_items`, `diary.summary_dispatch`).

Обоснование (решение автора):
- Числовой PK удобен как видимый идентификатор для dev-пользователей при удалении (US-009): короткое читаемое число вместо громоздкой строки.
- Идемпотентность at-least-once по-прежнему держится на `entries.source_task_id text UNIQUE` — PK к этому не относится.
- Внешние ключи и ссылки между таблицами — соответственно `bigint`.

ORM: `Mapped[int]`; в SA 2.x — `mapped_column(BigInteger, primary_key=True)` с `Identity()`.

---

## 2. Типы статусов и источников: `text` + CHECK constraint

**Решение: `text` + `CHECK` для `entries.status` и `entry_items.source`.**

Область значений:
- `entries.status` ∈ {`pending`, `confirmed`, `rejected`, `deleted`} — `CHECK (status IN ('pending','confirmed','rejected','deleted'))`
- `entry_items.source` ∈ {`OFF`, `LLM`} — `CHECK (source IN ('OFF','LLM'))`

Преимущества перед PG native ENUM:
- Downgrade миграции тривиален: не нужен `DROP TYPE ... CASCADE` и нет риска потерять значения.
- Нет нужды в `ALTER TYPE ... ADD VALUE` при расширении области.
- Alembic не требует `create_type=True` и спецобработки при autogenerate.
- Отловить ошибку вставки CHECK умеет так же хорошо, как ENUM.

ORM: статус и источник — `Mapped[str]` с `CheckConstraint`; валидация констант на уровне Python-слоя (Enum или литеральные константы в модуле).

---

## 3. Схема `users` (владелец — модуль `users`, живёт в `api-core`)

### 3.1. Таблица `users.users`

Хранит канало-независимого пользователя и его per-user настройки.

| Колонка | Тип | Ограничения | По умолчанию | Назначение |
|---|---|---|---|---|
| `id` | `bigint` | PK, GENERATED ALWAYS AS IDENTITY, NOT NULL | автоинкремент | Внутренний идентификатор пользователя |
| `created_at` | `timestamptz` | NOT NULL | `now()` | Момент создания аккаунта |
| `timezone` | `text` | NOT NULL, CHECK (len > 0) | `'Europe/Moscow'` | TZ для local_date/сводок; смена TZ — после MVP (US-013) |
| `confirm_enabled` | `boolean` | NOT NULL | `false` | Включено ли per-user подтверждение распознанного (US-005, A7) |
| `auto_summary_enabled` | `boolean` | NOT NULL | `true` | Включена ли авто-сводка в 9:00 (US-008, ADR-0018) |
| `is_dev` | `boolean` | NOT NULL | `false` | Dev-пользователь: виден `entries.id`, доступно удаление по id (US-009, A10) |
| `updated_at` | `timestamptz` | NOT NULL | `now()` | Обновляется триггером при любом UPDATE настроек |

Ограничения:
- `timezone` — валидация формата TZ (IANA, напр. `Europe/Moscow`) на уровне приложения (ORM-валидатор); на уровне БД — `CHECK (length(timezone) > 0)`.

Индексы:
- PK по `id` — единственный нужный индекс (поиск всегда по `id`; поиск по другим полям не нужен в MVP).

ORM (SQLAlchemy 2.x, ориентировочно):
```python
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "users"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    timezone: Mapped[str] = mapped_column(Text, server_default=text("'Europe/Moscow'"))
    confirm_enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    auto_summary_enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    is_dev: Mapped[bool] = mapped_column(server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
```

### 3.2. Таблица `users.channel_identities`

Привязка внешних идентификаторов каналов к пользователю. Поддерживает мульти-канальность (A4): несколько каналов на одного пользователя, каждый активен независимо. Первый канал автоматически становится primary (US-008).

| Колонка | Тип | Ограничения | По умолчанию | Назначение |
|---|---|---|---|---|
| `id` | `bigint` | PK, GENERATED ALWAYS AS IDENTITY, NOT NULL | автоинкремент | Внутренний идентификатор строки |
| `user_id` | `bigint` | NOT NULL, FK → `users.users(id)` ON DELETE CASCADE | — | Пользователь-владелец (внутри той же схемы `users` — FK допустим) |
| `channel` | `text` | NOT NULL, CHECK (len > 0) | — | Канал (`'telegram'`, в будущем другие) |
| `channel_user_id` | `text` | NOT NULL, CHECK (len > 0) | — | Внешний id в канале (напр. Telegram user_id) |
| `is_active` | `boolean` | NOT NULL | `true` | Активна ли привязка; деактивация вместо удаления |
| `is_primary` | `boolean` | NOT NULL | `false` | Primary-канал для авто-сводки (ADR-0009) |
| `created_at` | `timestamptz` | NOT NULL | `now()` | Момент привязки |
| `last_seen_at` | `timestamptz` | NOT NULL | `now()` | Последнее сообщение из канала (обновляется при каждом запросе) |

Ограничения:
- `UNIQUE (channel, channel_user_id)` — один внешний id в канале привязан к одному пользователю.

**Инвариант «ровно один primary» — обеспечивается на двух уровнях:**

1. **БД-уровень (primary, решение автора):** partial UNIQUE index
   ```sql
   CREATE UNIQUE INDEX uq_channel_identities_primary_active
       ON users.channel_identities (user_id)
       WHERE is_primary AND is_active;
   ```
   Гарантирует, что у одного `user_id` не может быть двух строк с `is_primary=true AND is_active=true` одновременно — даже при конкурентных UPDATE. Правильный порядок операций в транзакции: сначала снять `is_primary=false` у текущего primary, затем установить `is_primary=true` у нового.

2. **Application-level:** транзакционный метод `set_primary_channel(user_id, identity_id)` в репозитории (`users`) — в пределах одной транзакции снимает `is_primary=false` у всех активных, затем устанавливает `is_primary=true` у нужной записи. Покрывается тестом.

Индексы:
- PK по `id`.
- `UNIQUE (channel, channel_user_id)` — основной lookup при регистрации/резолве.
- `INDEX (user_id)` — выборка всех каналов пользователя (для резолва primary, настроек, авто-сводки).
- `INDEX (user_id, is_primary, is_active)` — быстрый lookup primary-канала для авто-сводки (US-008).
- `UNIQUE INDEX (user_id) WHERE is_primary AND is_active` — инвариант «ровно один primary» на уровне БД (см. выше).

ORM (ориентировочно):
```python
class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint("channel", "channel_user_id"),
        Index(
            "uq_channel_identities_primary_active",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary AND is_active"),
        ),
        {"schema": "users"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.users.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    channel_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    is_primary: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
```

---

## 4. Схема `diary` (владелец — модуль `diary`, живёт в `api-core`)

### 4.1. Таблица `diary.entries`

Один залогированный приём пищи из одного пользовательского сообщения. Хранит исходный текст, статус, артефакты трассировки и ключ идемпотентности.

Семантика: одно входящее сообщение → одна запись `entry` → N записей `entry_items`. Отдельная таблица сообщений в MVP избыточна (1:1 с `entry`) — при будущей потребности выносится обратимо.

| Колонка | Тип | Ограничения | По умолчанию | Назначение |
|---|---|---|---|---|
| `id` | `bigint` | PK, GENERATED ALWAYS AS IDENTITY, NOT NULL | автоинкремент | Внутренний id записи; показывается dev-пользователям при удалении (US-009) — удобное короткое число |
| `user_id` | `bigint` | NOT NULL | — | Id пользователя (ссылка на `users.users.id` по значению, без FK — ADR-0001) |
| `created_at_utc` | `timestamptz` | NOT NULL | `now()` | Момент создания записи в UTC |
| `local_date` | `date` | NOT NULL | — | День в TZ пользователя (вычисляется в `api-core` при persist); ключ для группировки в сводках |
| `source_channel` | `text` | NOT NULL | — | Канал, из которого пришло сообщение (`'telegram'` и т.д.) |
| `source_task_id` | `text` | NOT NULL, UNIQUE | — | `task_id` задачи обработки; гарантирует идемпотентность при at-least-once (ADR-0015) |
| `raw_text` | `text` | NOT NULL, CHECK (len > 0) | — | Исходный текст пользовательского сообщения (трассируемость — ADR-0017, R4) |
| `status` | `text` | NOT NULL, CHECK (status IN ('pending','confirmed','rejected','deleted')) | `'pending'` | Статус записи (ADR-0016) |
| `status_reason` | `text` | NULL | `null` | Причина последнего перехода статуса (`user_rejected`, `user_deleted`, …) |
| `status_changed_at` | `timestamptz` | NULL | `null` | Время последнего перехода статуса (аудит, ADR-0016) |
| `intent_result` | `jsonb` | NULL | `null` | Артефакт классификации намерения (итог intent: label, confidence; ADR-0017) |
| `parse_artifact` | `jsonb` | NULL | `null` | Артефакт парсинга (распознанные позиции до нутриции; ADR-0017) |
| `model_meta` | `jsonb` | NULL | `null` | Метаданные LLM-вызовов: модель, версия промпта, usage (ADR-0017) |

**Статусы `entries.status`:**
- `pending` — сохранена, ожидает подтверждения
- `confirmed` — подтверждена или автоподтверждена (учитывается в сводках)
- `rejected` — пользователь отклонил превью
- `deleted` — мягко удалена (US-009)

**Решение по артефактам трассировки (ADR-0017):** хранятся как JSONB-поля в `entries`, а не в отдельной таблице `entry_traces`.

Обоснование: соотношение 1:1 с `entry` (один persist); JSONB позволяет гибкую структуру (intent-артефакт и parse-артефакт разные по форме); запрос артефакта всегда идёт вместе с записью, JOIN не нужен; в MVP (1–2 пользователя) размер JSONB незначим. Если в будущем артефакты вырастут или понадобится индексация по их содержимому — выделение в `entry_traces` обратимо (Alembic-миграция).

**Инвариант сводок** (ADR-0016): любая выборка для сводок/итогов ОБЯЗАНА фильтровать `WHERE status = 'confirmed'`. Фильтр вынесен в единственный repository-метод (`diary.repository.get_confirmed_entries_for_date`), покрывается тестом.

Индексы:
- PK по `id`.
- `UNIQUE (source_task_id)` — идемпотентность.
- **`INDEX (user_id, local_date, status)`** — основной рабочий индекс: выборка записей пользователя за день с фильтром по статусу (US-007, US-008, US-009). Покрывает паттерн `WHERE user_id = ? AND local_date = ? AND status = 'confirmed'`.
- `INDEX (user_id, status)` — поиск записей пользователя по статусу (например, pending для confirm-flow); создать при необходимости в Стадии С12.
- `INDEX (user_id, created_at_utc)` — для хронологических запросов и пагинации (создать при Стадии С13/С15, если понадобится).

ORM (ориентировочно):
```python
ENTRY_STATUS_VALUES = ('pending', 'confirmed', 'rejected', 'deleted')

class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint("status IN ('pending','confirmed','rejected','deleted')", name="ck_entries_status"),
        CheckConstraint("length(raw_text) > 0", name="ck_entries_raw_text"),
        {"schema": "diary"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # без FK — кросс-схемная ссылка
    created_at_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    local_date: Mapped[date] = mapped_column(nullable=False)
    source_channel: Mapped[str] = mapped_column(Text, nullable=False)
    source_task_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    intent_result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    parse_artifact: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    model_meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    items: Mapped[list["EntryItem"]] = relationship("EntryItem", back_populates="entry", cascade="all, delete-orphan")
```

### 4.2. Таблица `diary.entry_items`

Расшифровка по продуктам внутри одного приёма пищи. Один `entry` → N `entry_items`.

| Колонка | Тип | Ограничения | По умолчанию | Назначение |
|---|---|---|---|---|
| `id` | `bigint` | PK, GENERATED ALWAYS AS IDENTITY, NOT NULL | автоинкремент | Внутренний идентификатор позиции |
| `entry_id` | `bigint` | NOT NULL, FK → `diary.entries(id)` ON DELETE CASCADE | — | Запись-владелец (в пределах схемы `diary` — FK допустим) |
| `name` | `text` | NOT NULL, CHECK (len > 0) | — | Название продукта/блюда |
| `qty_grams` | `numeric(10, 2)` | NOT NULL, CHECK (> 0) | — | Количество в граммах (NUMERIC, не float) |
| `qty_is_estimated` | `boolean` | NOT NULL | `false` | True = вес оценочный (из порции «тарелка», «2 яйца» — US-003) |
| `kcal` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Калорийность за qty_grams граммов |
| `protein` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Белки за qty_grams граммов |
| `fat` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Жиры за qty_grams граммов |
| `carb` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Углеводы за qty_grams граммов |
| `kcal_per_100g` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Калорийность на 100 г (из источника; для отображения в ответе US-002) |
| `protein_per_100g` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Белки на 100 г |
| `fat_per_100g` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Жиры на 100 г |
| `carb_per_100g` | `numeric(8, 2)` | NOT NULL, CHECK (>= 0) | — | Углеводы на 100 г |
| `source` | `text` | NOT NULL, CHECK (source IN ('OFF','LLM')) | — | Источник КБЖУ: `OFF` или `LLM` per-item (US-004, A9) |

**Источник `entry_items.source`**: `OFF` или `LLM` — текстовое поле с CHECK. Позиции внутри одной записи могут иметь разные источники.

**Решение по хранению «КБЖУ на 100 г»:** хранятся явно как отдельные поля, а не вычисляются из `qty_grams`. Причина: источник (OFF/LLM) возвращает нутриенты именно в формате «на 100 г», пересчёт на количество делается в `api-core` при persist. Хранение «на 100 г» нужно для корректного отображения в ответе пользователю (US-002) и для будущей аналитики. Пересчитывать обратно из `kcal/qty_grams*100` — потеря точности из-за NUMERIC-арифметики.

**Почему NUMERIC, не float:** нутриентные значения имеют денежно-подобную точность (суммируются, сравниваются); `float` дал бы накапливающиеся ошибки округления при суммировании за день (US-007). `NUMERIC(8,2)` даёт 2 знака после запятой и избегает плавающей арифметики.

Ограничения:
- `CHECK (qty_grams > 0)` — нулевой вес невалиден.
- `CHECK (kcal >= 0)`, `CHECK (protein >= 0)`, `CHECK (fat >= 0)`, `CHECK (carb >= 0)` — нутриенты не отрицательны.
- `CHECK (kcal_per_100g >= 0)` и аналогично для остальных per-100g полей.
- `CHECK (source IN ('OFF','LLM'))` — область значений источника.

Индексы:
- PK по `id`.
- `INDEX (entry_id)` — выборка позиций по записи (неявно через FK, PostgreSQL создаёт автоматически при cascade, но явный индекс полезен).

ORM (ориентировочно):
```python
class EntryItem(Base):
    __tablename__ = "entry_items"
    __table_args__ = (
        CheckConstraint("source IN ('OFF','LLM')", name="ck_entry_items_source"),
        CheckConstraint("qty_grams > 0", name="ck_entry_items_qty_grams"),
        {"schema": "diary"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("diary.entries.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    qty_grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    qty_is_estimated: Mapped[bool] = mapped_column(server_default=text("false"))
    kcal: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    protein: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    fat: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    carb: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    kcal_per_100g: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    protein_per_100g: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    fat_per_100g: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    carb_per_100g: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    entry: Mapped["Entry"] = relationship("Entry", back_populates="items")
```

### 4.3. Таблица `diary.summary_dispatch`

Журнал отправки авто-сводок. Защищает от дублей при повторных тиках `scheduler` или рестартах `api-core` (ADR-0018). Пишет только модуль `diary` в составе `api-core`; `scheduler` к таблице не обращается (ADR-0001).

| Колонка | Тип | Ограничения | По умолчанию | Назначение |
|---|---|---|---|---|
| `id` | `bigint` | PK, GENERATED ALWAYS AS IDENTITY, NOT NULL | автоинкремент | Технический PK |
| `user_id` | `bigint` | NOT NULL | — | Пользователь (ссылка по значению, без FK — ADR-0001) |
| `local_date` | `date` | NOT NULL | — | Дата, за которую отправлена сводка (в TZ пользователя) |
| `created_at` | `timestamptz` | NOT NULL | `now()` | Момент создания записи (начало обработки триггера) |
| `sent_at` | `timestamptz` | NULL | `null` | Момент отправки; `null` = обработано, но пусто/не отправлено (A6) |
| `skipped_reason` | `text` | NULL | `null` | Причина не-отправки (`no_entries`, `flag_disabled`) |

Ограничения:
- `UNIQUE (user_id, local_date)` — гарантирует ровно одну запись на пользователя за дату; повторный `INSERT` при конкурентном тике → конфликт → upsert-игнор.

Индексы:
- PK по `id`.
- `UNIQUE (user_id, local_date)` — основной ограничитель и lookup.
- `INDEX (local_date)` — для обхода по дате при тике scheduler (выбрать записи за вчера при проверке).

ORM (ориентировочно):
```python
class SummaryDispatch(Base):
    __tablename__ = "summary_dispatch"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date"),
        {"schema": "diary"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    local_date: Mapped[date] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    sent_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    skipped_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

---

## 5. Repository-инвариант сводок (ADR-0016 — обязательно)

Единственный способ получить записи для сводок и итогов:

```python
# Единственная точка чтения confirmed-записей. Тест ОБЯЗАТЕЛЕН.
async def get_confirmed_entries_for_date(
    session: AsyncSession,
    user_id: int,
    local_date: date,
) -> list[Entry]:
    ...
    # WHERE user_id = :user_id AND local_date = :local_date AND status = 'confirmed'
```

Этот метод **единственный** обращается к `diary.entries` с целью формирования сводки. Любой другой запрос для вычисления КБЖУ-итогов — дефект, блокирует ревью.

---

## 6. Индексы — сводная таблица

| Схема | Таблица | Индекс | Паттерн запроса |
|---|---|---|---|
| `users` | `users` | PK `(id)` | Любой lookup по user_id |
| `users` | `channel_identities` | PK `(id)` | — |
| `users` | `channel_identities` | UNIQUE `(channel, channel_user_id)` | Регистрация/резолв пользователя при входящем сообщении |
| `users` | `channel_identities` | `(user_id)` | Все каналы пользователя |
| `users` | `channel_identities` | `(user_id, is_primary, is_active)` | Primary-канал для авто-сводки |
| `users` | `channel_identities` | UNIQUE `(user_id) WHERE is_primary AND is_active` | Инвариант «ровно один primary» на уровне БД |
| `diary` | `entries` | PK `(id)` | Выборка/удаление по id записи (видимый dev-id = entries.id) |
| `diary` | `entries` | UNIQUE `(source_task_id)` | Идемпотентность at-least-once |
| `diary` | `entries` | `(user_id, local_date, status)` | Сводка за день + фильтр по статусу (критический) |
| `diary` | `entry_items` | PK `(id)` | — |
| `diary` | `entry_items` | `(entry_id)` | Позиции по записи |
| `diary` | `summary_dispatch` | PK `(id)` | — |
| `diary` | `summary_dispatch` | UNIQUE `(user_id, local_date)` | Дедупликация авто-сводки |
| `diary` | `summary_dispatch` | `(local_date)` | Обход по дате при тике scheduler |

---

## 7. Последовательность миграций по стадиям roadmap

Каждая строка = один Alembic-revision (или несколько в рамках стадии, если схема меняется несколько раз). Ревизии обратимы, изолированы по схеме-владельцу (ADR-0014, ADR-0001).

| Стадия roadmap | Миграция (Alembic revision) | Содержимое |
|---|---|---|
| **С1** — Каркас репозитория + Postgres | `0001_alembic_init` | Инициализация Alembic; создание схем `users`, `diary` (`CREATE SCHEMA IF NOT EXISTS`); baseline без таблиц. Подтверждает, что Alembic работает и схемы созданы. |
| **С6** — Пользователи и регистрация | `0002_users_schema` | Создание `users.users` (bigint PK, GENERATED ALWAYS AS IDENTITY), `users.channel_identities` с полными ограничениями, индексами, включая partial UNIQUE index для инварианта primary. |
| **С11** — Логика diary: persist записей | `0003_diary_entries` | Создание `diary.entries` (bigint PK, text+CHECK для status) и `diary.entry_items` (bigint PK, text+CHECK для source) с полным набором колонок (включая JSONB-артефакты — `intent_result`, `parse_artifact`, `model_meta`); все индексы для обоих. PG ENUM не используется — downgrade тривиален (нет `DROP TYPE`). |
| **С14** — Авто-сводка в 9:00 | `0004_diary_summary_dispatch` | Создание `diary.summary_dispatch` (bigint PK) с UNIQUE-ограничением и индексами. |

Стадии С2–С5, С7–С10, С12–С13, С15–С16 **не вводят новых таблиц/колонок** — миграций схемы в эти стадии нет. Любая потребность изменить схему (например, добавить колонку) = новая ревизия Alembic, приходящая в соответствующей стадии.

**Правило CI (ADR-0014):** для каждой ревизии в CI проверяется прогон `up → down → up` на чистой БД.

**Примечание о downgrade (text+CHECK vs PG ENUM):** использование `text` + `CHECK` вместо PG ENUM упрощает downgrade: не требуется `DROP TYPE ... CASCADE` и нет риска потери данных при откате. Откат ревизии `0003` — это `DROP TABLE diary.entry_items; DROP TABLE diary.entries;` без дополнительных DDL-шагов.

---

## 8. Что не входит в MVP (но схема готова)

- Поле `timezone` в `users.users` есть; смена TZ через UI — US-013, после MVP.
- `channel_identities.is_active` есть; деактивация канала — после MVP.
- Артефакты трассировки (`intent_result`, `parse_artifact`, `model_meta`) — JSONB NULL до Стадии С11; заполняются при persist.
- Физический `DELETE` записей запрещён навсегда (ADR-0016); чистка `rejected`/`deleted` строк — архивация вне scope MVP.
