"""
Unit tests for core.bus.InMemoryBus — C2-T02.

InMemoryBus is the test-time broker adapter: it must faithfully emulate the
at-least-once + explicit-ack + fan-out semantics of the MessageBus port so
that the entire async pipeline can be tested without a real Redis instance.

All tests are pure unit tests — no network, no DB, no real broker.
"""

from __future__ import annotations

import asyncio

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_simple_event(task_id: str = "t1") -> dict:
    """Minimal event dict accepted by InMemoryBus (schema_version etc optional)."""
    return {"task_id": task_id, "payload": "test"}


# ---------------------------------------------------------------------------
# Basic publish / subscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_bus_handler_receives_published_event():
    """
    A handler subscribed to a topic must receive an event that is published
    to that topic.
    Verifies the fundamental publish/subscribe contract of the MessageBus port
    (contracts.md §4).
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    received: list[dict] = []

    async def handler(event: dict) -> None:
        received.append(event)

    await bus.subscribe("tasks.processing", "group-a", handler)
    event = _make_simple_event()
    await bus.publish("tasks.processing", event)

    # Give the async loop a tick to process
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_inmemory_bus_handler_does_not_receive_other_topic_events():
    """
    A handler subscribed to topic A must NOT receive events published to
    topic B.
    Verifies topic isolation — events are routed strictly by topic name.
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    received: list[dict] = []

    async def handler(event: dict) -> None:
        received.append(event)

    await bus.subscribe("tasks.processing", "group-a", handler)
    await bus.publish("results.processing", _make_simple_event())
    await asyncio.sleep(0)

    assert len(received) == 0


# ---------------------------------------------------------------------------
# Ack semantics: successful delivery is not re-delivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_bus_acked_event_is_not_redelivered():
    """
    An event that is successfully processed (handler completes without raising)
    must be considered acked and must NOT be delivered again on subsequent
    consume calls.
    Ensures the explicit-ack semantic of the MessageBus port (contracts.md §4,
    conventions §4).
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    call_count = 0

    async def handler(event: dict) -> None:
        nonlocal call_count
        call_count += 1

    await bus.subscribe("tasks.processing", "group-a", handler)
    await bus.publish("tasks.processing", _make_simple_event())
    await asyncio.sleep(0)

    # Trigger a second consume pass
    await bus.consume_pending("tasks.processing", "group-a")
    await asyncio.sleep(0)

    assert call_count == 1, "Acked event must not be re-delivered"


# ---------------------------------------------------------------------------
# At-least-once: failed handler causes re-delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_bus_failed_handler_causes_redelivery():
    """
    If the handler raises an exception, the event must remain pending and be
    re-delivered on the next consume call (at-least-once semantics).
    This is the core guarantee that prevents message loss when a handler
    crashes before completing its work (contracts.md §4, conventions §4).
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    call_count = 0

    async def failing_then_succeeding_handler(event: dict) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Simulated handler failure")

    await bus.subscribe("tasks.processing", "group-a", failing_then_succeeding_handler)
    await bus.publish("tasks.processing", _make_simple_event())

    # First pass — handler fails, event stays pending
    await asyncio.sleep(0)

    # Second pass — event re-delivered, handler succeeds
    await bus.consume_pending("tasks.processing", "group-a")
    await asyncio.sleep(0)

    assert call_count == 2, (
        "Event must be re-delivered after handler failure (at-least-once)"
    )


# ---------------------------------------------------------------------------
# Fan-out across different groups (each group gets the event)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_bus_different_groups_each_receive_event():
    """
    Two subscriptions in DIFFERENT groups on the same topic must each receive
    the published event independently (fan-out by consumer group).
    This models Redis Streams consumer group semantics where each group has its
    own independent read pointer (contracts.md §4, ADR-0004).
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    received_a: list[dict] = []
    received_b: list[dict] = []

    async def handler_a(event: dict) -> None:
        received_a.append(event)

    async def handler_b(event: dict) -> None:
        received_b.append(event)

    await bus.subscribe("tasks.processing", "group-a", handler_a)
    await bus.subscribe("tasks.processing", "group-b", handler_b)
    await bus.publish("tasks.processing", _make_simple_event("fanout-task"))
    await asyncio.sleep(0)

    assert len(received_a) == 1, "group-a must receive the event"
    assert len(received_b) == 1, "group-b must receive the event independently"
    assert received_a[0]["task_id"] == "fanout-task"
    assert received_b[0]["task_id"] == "fanout-task"


# ---------------------------------------------------------------------------
# Competing consumers: same group, event delivered to only ONE handler (TДЕ-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_bus_same_group_event_delivered_to_one_handler():
    """
    Two subscriptions in the SAME group on the same topic must share the event
    — it is delivered to exactly one of the two handlers (competing consumers),
    not both.
    This models the load-balancing behaviour of a Redis Streams consumer group
    where multiple workers share the read pointer (TДЕ-2, ADR-0004).
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    total_received = 0

    async def handler_x(event: dict) -> None:
        nonlocal total_received
        total_received += 1

    async def handler_y(event: dict) -> None:
        nonlocal total_received
        total_received += 1

    await bus.subscribe("tasks.processing", "shared-group", handler_x)
    await bus.subscribe("tasks.processing", "shared-group", handler_y)
    await bus.publish("tasks.processing", _make_simple_event())
    await asyncio.sleep(0)

    assert total_received == 1, (
        "Within the same group, an event must be delivered to exactly one handler "
        "(competing consumers — not broadcast)"
    )


# ---------------------------------------------------------------------------
# Publish to topic without subscribers does not raise (TДЕ-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_bus_publish_to_unsubscribed_topic_does_not_raise():
    """
    Publishing to a topic that has no subscribers must complete silently without
    raising an exception.
    The port contract does not require a subscriber to exist before publish;
    fire-and-forget is valid (e.g. publishing results before the consumer-loop
    is attached in tests — TДЕ-3).
    """
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    # No subscriber registered for this topic
    await bus.publish("some.unsubscribed.topic", _make_simple_event())
    # If we reach here, no exception was raised — test passes
