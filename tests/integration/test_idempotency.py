"""
Integration tests for at-least-once idempotency — C2-T06.

Verifies that:
  1. Redelivering the same ProcessingTask (same task_id, twice) results in
     at most one Result in results.<channel> — no doubling of effects.
  2. Redelivering the same ProcessingResult (same task_id, twice) results in
     at most one final Result published — the orchestrator deduplicates.

No real Redis or Postgres needed: InMemoryBus is used for the broker, and the
DB probe is mocked. These tests do NOT require Docker.

These tests are marked integration because they exercise multi-step async
sequences; they can run without Docker.
"""

from __future__ import annotations

import asyncio

import pytest

# ---------------------------------------------------------------------------
# Helper: build a minimal valid ProcessingTask payload
# ---------------------------------------------------------------------------


def _task_payload(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "user_id": 1,
        "channel": "telegram",
        "channel_user_id": "u1",
        "kind": "ingest_message",
        "text": "100г риса",
        "reply_to": {"chat_id": 5, "message_id": 1},
        "confirm_enabled": False,
        "schema_version": 1,
        "event_id": "ev-1",
        "occurred_at": "2026-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Idempotency of ProcessingTask re-delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redelivered_processing_task_does_not_double_result():
    """
    Publishing the same ProcessingTask (identical task_id) twice must produce
    at most one Result in results.telegram.

    In C2 the echo pipeline is naturally idempotent (no side effects), but
    this test locks that guarantee so that future pipeline implementations
    are required to be idempotent too.

    At-least-once delivery means the same task can arrive multiple times;
    the handler must be idempotent by task_id (conventions §4, DoD §2,
    C2-T06 idempotency requirement).
    """
    from apps.api_core.orchestrator import handle_processing_result
    from apps.processing_worker.consumer import handle_processing_task
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    results_collected: list[dict] = []

    await bus.subscribe("tasks.processing", "processing-workers", handle_processing_task)
    await bus.subscribe("results.processing", "api-core-orchestrator", handle_processing_result)

    async def capture(event: dict) -> None:
        results_collected.append(event)

    await bus.subscribe("results.telegram", "test-capture", capture)

    task_id = "idem-task-001"
    task_event = _task_payload(task_id)

    # Publish the same task twice (simulating at-least-once re-delivery)
    await bus.publish("tasks.processing", task_event)
    await bus.publish("tasks.processing", task_event)
    await asyncio.sleep(0.1)

    # Count distinct results with this task_id
    matching = [r for r in results_collected if r.get("task_id") == task_id]

    assert len(matching) <= 1, (
        f"Redelivered ProcessingTask produced {len(matching)} Results — "
        "must produce at most 1 (idempotency guarantee, conventions §4)"
    )


# ---------------------------------------------------------------------------
# Idempotency of ProcessingResult re-delivery (orchestrator dedup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redelivered_processing_result_does_not_double_final_result():
    """
    Publishing the same ProcessingResult (identical task_id) twice to
    results.processing must produce at most one Result in results.telegram.

    The api-core orchestrator (consumer-loop) must deduplicate by task_id
    without relying on the DB (which does not exist for entries in C2).
    This is the Q-C2-4 dedup requirement.

    Guarantees: at-least-once for results.processing does not cause duplicate
    messages to the channel adapter (conventions §4, DoD §2, C2-T06).
    """
    from apps.api_core.orchestrator import handle_processing_result
    from core.bus import InMemoryBus
    from core.contracts import ProcessingResult

    bus = InMemoryBus()
    results_collected: list[dict] = []

    await bus.subscribe("results.processing", "api-core-orchestrator", handle_processing_result)

    async def capture(event: dict) -> None:
        results_collected.append(event)

    await bus.subscribe("results.telegram", "test-capture", capture)

    task_id = "idem-result-001"
    proc_result = ProcessingResult(
        task_id=task_id,
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 5, "message_id": 1},
    )

    # Publish the same ProcessingResult twice (simulating at-least-once re-delivery)
    await bus.publish("results.processing", proc_result.model_dump())
    await bus.publish("results.processing", proc_result.model_dump())
    await asyncio.sleep(0.1)

    matching = [r for r in results_collected if r.get("task_id") == task_id]

    assert len(matching) <= 1, (
        f"Redelivered ProcessingResult produced {len(matching)} final Results — "
        "orchestrator must deduplicate by task_id (Q-C2-4)"
    )


# ---------------------------------------------------------------------------
# Idempotency does not suppress DIFFERENT tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_task_ids_each_produce_a_result():
    """
    Two ProcessingResults with DIFFERENT task_ids must each produce one Result
    in results.telegram.
    Ensures the dedup logic does not incorrectly suppress distinct messages
    (idempotency must be scoped to task_id, not all messages).
    """
    from apps.api_core.orchestrator import handle_processing_result
    from core.bus import InMemoryBus
    from core.contracts import ProcessingResult

    bus = InMemoryBus()
    results_collected: list[dict] = []

    await bus.subscribe("results.processing", "api-core-orchestrator", handle_processing_result)

    async def capture(event: dict) -> None:
        results_collected.append(event)

    await bus.subscribe("results.telegram", "test-capture", capture)

    for task_id in ("task-A", "task-B"):
        result = ProcessingResult(
            task_id=task_id,
            user_id=1,
            channel="telegram",
            channel_user_id="u1",
            kind="ingest_message",
            intent="food",
            reply_to={"chat_id": 1},
        )
        await bus.publish("results.processing", result.model_dump())

    await asyncio.sleep(0.1)

    task_ids_in_results = {r.get("task_id") for r in results_collected}
    assert "task-A" in task_ids_in_results, "task-A must produce a Result"
    assert "task-B" in task_ids_in_results, "task-B must produce a Result"
