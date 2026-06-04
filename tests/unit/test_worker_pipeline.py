"""
Unit tests for apps.processing_worker pipeline — C2-T04.

The pipeline in C2 is an echo stub: it receives a ProcessingTask and returns
a ProcessingResult without any LLM/OFF calls. Tests verify the stub's
correctness, task_id propagation, idempotency, and that the worker module
never imports the DB driver (ADR-0015 decoupling check).

All tests are pure unit tests — no network, no DB, no broker.
"""

from __future__ import annotations

import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**kwargs):  # type: ignore[return]  # ProcessingTask not yet importable
    from core.contracts import ProcessingTask

    defaults: dict = {
        "task_id": "test-task-42",
        "user_id": 1,
        "channel": "telegram",
        "channel_user_id": "u1",
        "kind": "ingest_message",
        "text": "100г гречки",
        "reply_to": {"chat_id": 10, "message_id": 5},
        "confirm_enabled": False,
    }
    defaults.update(kwargs)
    return ProcessingTask(**defaults)


# ---------------------------------------------------------------------------
# Echo pipeline: correct ProcessingResult shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_returns_processing_result_for_ingest_message():
    """
    The echo pipeline stub must accept a ProcessingTask with kind='ingest_message'
    and return a ProcessingResult instance.
    Verifies that the pipeline produces the correct DTO type for the
    results.processing topic (contracts.md §3.1a).
    """
    from apps.processing_worker.pipeline import run_pipeline
    from core.contracts import ProcessingResult

    task = _make_task()
    result = await run_pipeline(task)

    assert isinstance(result, ProcessingResult)


@pytest.mark.asyncio
async def test_pipeline_echo_sets_intent_food():
    """
    The C2 echo stub must set intent='food' on the ProcessingResult.
    In C2 there is no real classification; the stub unconditionally echoes
    'food' so the full loop exercises the happy-path branch (C2 plan §2).
    """
    from apps.processing_worker.pipeline import run_pipeline

    task = _make_task(text="200г риса с курицей")
    result = await run_pipeline(task)

    assert result.intent == "food"


@pytest.mark.asyncio
async def test_pipeline_propagates_task_id_unchanged():
    """
    The task_id from ProcessingTask must appear unchanged in ProcessingResult.
    This is the correlation key that api-core uses to route the result back
    to the original request (ADR-0017 traceability, contracts.md §3.1a).
    """
    from apps.processing_worker.pipeline import run_pipeline

    original_task_id = "immutable-task-id-99"
    task = _make_task(task_id=original_task_id)
    result = await run_pipeline(task)

    assert result.task_id == original_task_id, (
        f"task_id must be propagated unchanged; got {result.task_id!r}"
    )


@pytest.mark.asyncio
async def test_pipeline_propagates_channel_and_reply_to():
    """
    channel, channel_user_id, and reply_to must pass through the pipeline
    unchanged so api-core can route the Result to the correct channel/user.
    These fields are part of the routing envelope (contracts.md §3.4).
    """
    from apps.processing_worker.pipeline import run_pipeline

    task = _make_task(channel="telegram", channel_user_id="user-77")
    result = await run_pipeline(task)

    assert result.channel == "telegram"
    assert result.channel_user_id == "user-77"


# ---------------------------------------------------------------------------
# Idempotency: running the same task twice produces equivalent results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_is_idempotent_for_same_task():
    """
    Running the pipeline with the same ProcessingTask twice must produce
    equivalent results without any side effects or divergence.
    At-least-once delivery means a handler can be called multiple times for
    the same task_id; the handler must be idempotent (conventions §4).
    """
    from apps.processing_worker.pipeline import run_pipeline

    task = _make_task(task_id="idempotent-task")

    result_first = await run_pipeline(task)
    result_second = await run_pipeline(task)

    # Both invocations must yield results with the same observable fields
    assert result_first.task_id == result_second.task_id
    assert result_first.intent == result_second.intent
    assert result_first.channel == result_second.channel


# ---------------------------------------------------------------------------
# DB isolation: processing_worker must NOT import the DB driver (TДЕ-5 / ADR-0015)
# ---------------------------------------------------------------------------


def test_processing_worker_does_not_import_db_driver():
    """
    Importing apps.processing_worker must not pull in any DB driver
    (asyncpg, sqlalchemy, psycopg2) or ORM/repository modules.
    ADR-0015: the worker is stateless and has zero DB dependency.
    Any import of a DB driver here is a decoupling defect that blocks review
    (conventions §3.7).
    """
    # Ensure the module is imported (may already be from earlier tests)
    import apps.processing_worker  # noqa: F401

    forbidden_modules = {"asyncpg", "sqlalchemy", "psycopg2", "psycopg2_binary"}

    # The worker's own namespace must not import these; we check that no
    # processing_worker sub-module imports them directly.
    worker_imports = {
        name: mod
        for name, mod in sys.modules.items()
        if name.startswith("apps.processing_worker")
    }
    for mod_name, mod in worker_imports.items():
        for forbidden in forbidden_modules:
            # Check the module's globals for direct imports
            mod_dict = getattr(mod, "__dict__", {})
            for attr_val in mod_dict.values():
                attr_module = getattr(attr_val, "__module__", "") or ""
                assert forbidden not in attr_module, (
                    f"apps.processing_worker.{mod_name} imports {forbidden} — "
                    f"DB driver must not be imported in the worker (ADR-0015)"
                )
