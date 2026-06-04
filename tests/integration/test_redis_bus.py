"""
Integration tests for core.bus.RedisStreamsBus against a real Redis — C2-T03.

REQUIRES a running Redis instance (or Docker for testcontainers).
Marked @pytest.mark.integration; skipped automatically when neither
TEST_REDIS_URL nor a running Docker daemon is available (see conftest.py,
redis_url fixture).

What is verified:
  - publish → subscribe delivers the event to the handler in the consumer group.
  - XACK after success: acknowledged message leaves the PEL.
  - at-least-once: unacked (failed) handler causes the message to stay in PEL
    and be re-delivered on the next consume call.
  - Explicit read/connect timeout: no infinite blocking.
  - Fan-out: two different groups both receive the same published event (TДЕ-4).

The RedisStreamsBus must satisfy the same MessageBus port contract as
InMemoryBus — the adapter is interchangeable (ADR-0004).
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Basic publish → subscribe delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_bus_handler_receives_published_event(redis_url: str):
    """
    A handler subscribed to a topic must receive an event published to that
    topic on a real Redis Streams backend.
    Verifies the basic MessageBus port contract is satisfied by
    RedisStreamsBus (contracts.md §4, ADR-0004).
    """
    from core.bus import RedisStreamsBus

    bus = RedisStreamsBus(redis_url)
    received: list[dict] = []

    async def handler(event: dict) -> None:
        received.append(event)

    await bus.subscribe("tasks.processing", "test-group-basic", handler)
    await bus.publish("tasks.processing", {"task_id": "redis-test-1", "payload": "hello"})

    # Drive the consumer for a short window
    await bus.consume_once("tasks.processing", "test-group-basic", timeout_ms=500)
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].get("task_id") == "redis-test-1"

    await bus.close()


# ---------------------------------------------------------------------------
# XACK after success: message leaves PEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_bus_successful_handler_acks_message(redis_url: str):
    """
    After a handler completes successfully, the message must be acknowledged
    (XACK) and must not appear in the Pending Entries List (PEL).
    Verifies correct at-least-once ack semantics on the real broker
    (contracts.md §4, ADR-0004 — ack removes from PEL).
    """
    from core.bus import RedisStreamsBus

    bus = RedisStreamsBus(redis_url)
    handled = []

    async def handler(event: dict) -> None:
        handled.append(event)

    await bus.subscribe("test.ack", "ack-group", handler)
    await bus.publish("test.ack", {"task_id": "ack-test"})

    # Consume and ack
    await bus.consume_once("test.ack", "ack-group", timeout_ms=500)

    # Inspect PEL — it must be empty after successful ack
    pending_count = await bus.pending_count("test.ack", "ack-group")
    assert pending_count == 0, (
        f"PEL must be empty after successful handler; got {pending_count} pending"
    )

    await bus.close()


# ---------------------------------------------------------------------------
# at-least-once: failed handler leaves message in PEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_bus_failed_handler_leaves_message_in_pel(redis_url: str):
    """
    When the handler raises an exception, the message must NOT be XACK'd and
    must remain in the PEL for re-delivery.
    This is the at-least-once guarantee on the real broker: a crash before ack
    does not lose the message (contracts.md §4, ADR-0004).
    """
    from core.bus import RedisStreamsBus

    bus = RedisStreamsBus(redis_url)

    async def failing_handler(event: dict) -> None:
        raise RuntimeError("Simulated failure — do not ack")

    await bus.subscribe("test.pel", "pel-group", failing_handler)
    await bus.publish("test.pel", {"task_id": "pel-test"})

    # Consume — handler will fail, message stays in PEL
    try:
        await bus.consume_once("test.pel", "pel-group", timeout_ms=500)
    except Exception:
        pass  # Expected: handler failed

    pending_count = await bus.pending_count("test.pel", "pel-group")
    assert pending_count >= 1, (
        "Message must remain in PEL when handler fails (at-least-once — not acked)"
    )

    await bus.close()


# ---------------------------------------------------------------------------
# at-least-once: re-delivery from PEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_bus_redelivers_from_pel_after_failure(redis_url: str):
    """
    After a handler failure (message in PEL), the next consume call must
    re-deliver the message to the handler (from the PEL via XAUTOCLAIM or
    XPENDING + XCLAIM).
    Verifies the full at-least-once retry cycle on the real broker
    (ADR-0004, conventions §5 error handling).
    """
    from core.bus import RedisStreamsBus

    bus = RedisStreamsBus(redis_url)
    call_count = 0

    async def failing_then_ok_handler(event: dict) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("First attempt fails")
        # Second attempt succeeds — no exception

    await bus.subscribe("test.redeliver", "redeliver-group", failing_then_ok_handler)
    await bus.publish("test.redeliver", {"task_id": "redeliver-test"})

    # First pass — fails, stays in PEL
    try:
        await bus.consume_once("test.redeliver", "redeliver-group", timeout_ms=500)
    except Exception:
        pass

    # Second pass — re-delivered from PEL, succeeds
    await bus.consume_pending("test.redeliver", "redeliver-group")
    await asyncio.sleep(0)

    assert call_count == 2, (
        f"Expected handler called twice (fail then succeed); got {call_count}"
    )

    # PEL must now be empty (successfully acked on second attempt)
    pending_count = await bus.pending_count("test.redeliver", "redeliver-group")
    assert pending_count == 0

    await bus.close()


# ---------------------------------------------------------------------------
# Explicit read timeout (no infinite blocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_bus_consume_returns_within_timeout(redis_url: str):
    """
    consume_once with a small timeout on an empty stream must return (not block
    indefinitely) within a reasonable wall-clock time.
    Verifies that the bus enforces explicit read timeouts (conventions §5:
    explicit timeouts on all external calls — no infinite waiting).
    """
    import time

    from core.bus import RedisStreamsBus

    bus = RedisStreamsBus(redis_url)
    handled = []

    async def handler(event: dict) -> None:
        handled.append(event)

    await bus.subscribe("test.timeout", "timeout-group", handler)
    # Do NOT publish anything — the stream is empty

    start = time.monotonic()
    await bus.consume_once("test.timeout", "timeout-group", timeout_ms=300)
    elapsed = time.monotonic() - start

    # Must return within 2x the timeout (generous bound to avoid flakiness)
    assert elapsed < 2.0, (
        f"consume_once blocked for {elapsed:.2f}s — must respect timeout (conventions §5)"
    )
    assert handled == [], "No event was published — handler must not have been called"

    await bus.close()


# ---------------------------------------------------------------------------
# Fan-out: different consumer groups each receive the event (TДЕ-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_bus_fan_out_to_different_groups(redis_url: str):
    """
    Two different consumer groups subscribed to the same topic must each
    independently receive the same published event (fan-out).
    Verifies that Redis Streams consumer-group semantics are correctly
    implemented: each group has its own read pointer (TДЕ-4, ADR-0004).
    """
    from core.bus import RedisStreamsBus

    bus = RedisStreamsBus(redis_url)
    received_group_a: list[dict] = []
    received_group_b: list[dict] = []

    async def handler_a(event: dict) -> None:
        received_group_a.append(event)

    async def handler_b(event: dict) -> None:
        received_group_b.append(event)

    await bus.subscribe("test.fanout", "group-alpha", handler_a)
    await bus.subscribe("test.fanout", "group-beta", handler_b)
    await bus.publish("test.fanout", {"task_id": "fanout-redis"})

    # Both groups must consume independently
    await bus.consume_once("test.fanout", "group-alpha", timeout_ms=500)
    await bus.consume_once("test.fanout", "group-beta", timeout_ms=500)
    await asyncio.sleep(0)

    assert len(received_group_a) == 1, "group-alpha must receive the event"
    assert len(received_group_b) == 1, "group-beta must receive the event independently"

    await bus.close()
