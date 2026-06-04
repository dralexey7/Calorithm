"""
Integration tests for the full echo loop using InMemoryBus — C2-T06.

Verifies the complete processing pipeline end-to-end:
  POST /v1/messages
    → ProcessingTask in tasks.processing
    → processing-worker echo handler
    → ProcessingResult in results.processing
    → api-core consumer-loop
    → Result in results.<channel>

No real Redis or Postgres is needed: InMemoryBus replaces the broker; the
DB probe is mocked. LLM/OFF are absent in C2 (echo only).

Tests in this file are NOT marked integration because they don't require
Docker — InMemoryBus is fully in-process. They run in the standard unit/
integration matrix.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def full_loop_setup():
    """
    Wires the complete echo loop on an InMemoryBus:
    - api-core uses InMemoryBus (DI override)
    - processing-worker handler is subscribed to tasks.processing
    - api-core consumer-loop is subscribed to results.processing
    - a test subscriber is attached to results.telegram to capture Results

    Returns (client, bus, results_received) where results_received is a
    list that the test subscriber appends to.
    """
    from apps.api_core.bus_provider import get_bus
    from apps.api_core.health import get_db_probe
    from apps.api_core.main import app
    from apps.api_core.orchestrator import handle_processing_result
    from apps.processing_worker.consumer import handle_processing_task
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    results_received: list[dict] = []

    # Attach processing-worker handler
    await bus.subscribe("tasks.processing", "processing-workers", handle_processing_task)

    # Attach api-core orchestrator consumer-loop handler
    await bus.subscribe("results.processing", "api-core-orchestrator", handle_processing_result)

    # Attach test subscriber to capture final Results
    async def capture_result(event: dict) -> None:
        results_received.append(event)

    await bus.subscribe("results.telegram", "test-capture", capture_result)

    # Wire DI overrides for api-core app
    async def _noop_db_probe() -> None:
        return None

    def _db_probe_dep():
        return _noop_db_probe

    def _bus_dep():
        return bus

    app.dependency_overrides[get_db_probe] = _db_probe_dep
    app.dependency_overrides[get_bus] = _bus_dep

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, bus, results_received

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Full loop: food intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_echo_loop_food_intent_produces_logged_result(full_loop_setup):
    """
    POST /v1/messages → echo worker (intent=food) → api-core orchestrator →
    Result{kind='logged'} in results.telegram.

    Verifies the full async pipeline on InMemoryBus: the task flows from HTTP
    to the worker, back to api-core, and finally a Result reaches the channel
    topic (C2-T06, architecture §4.2).
    """
    client, bus, results_received = full_loop_setup

    payload = {
        "channel": "telegram",
        "channel_user_id": "u1",
        "text": "200г гречки",
        "reply_to": {"chat_id": 10, "message_id": 1},
    }
    response = await client.post("/v1/messages", json=payload)
    assert response.status_code in (200, 202)

    # Allow the async chain to run
    await asyncio.sleep(0.1)

    assert len(results_received) >= 1
    result = results_received[0]
    assert result.get("kind") == "logged", (
        f"Expected Result kind='logged' for food intent, got {result.get('kind')!r}"
    )


@pytest.mark.asyncio
async def test_echo_loop_result_carries_same_task_id(full_loop_setup):
    """
    The Result that arrives in results.telegram must carry the same task_id
    as the one returned by POST /v1/messages.
    Verifies end-to-end task_id correlation across all hops of the pipeline
    (ADR-0017 traceability; C2-T06).
    """
    client, bus, results_received = full_loop_setup

    payload = {
        "channel": "telegram",
        "channel_user_id": "u1",
        "text": "яблоко",
        "reply_to": {"chat_id": 5},
    }
    response = await client.post("/v1/messages", json=payload)
    response_task_id = response.json()["task_id"]

    await asyncio.sleep(0.1)

    assert results_received, "Expected at least one Result in results.telegram"
    assert results_received[0].get("task_id") == response_task_id, (
        "task_id in Result must match the one from POST response (ADR-0017)"
    )


@pytest.mark.asyncio
async def test_echo_loop_result_carries_channel_and_reply_to(full_loop_setup):
    """
    The Result in results.telegram must carry the channel and reply_to from
    the original request so the adapter can route the answer to the correct
    Telegram chat.
    Verifies routing metadata propagation through the full pipeline
    (contracts.md §3.4, architecture §4.2).
    """
    client, bus, results_received = full_loop_setup

    payload = {
        "channel": "telegram",
        "channel_user_id": "u99",
        "text": "тарелка борща",
        "reply_to": {"chat_id": 77, "message_id": 3},
    }
    await client.post("/v1/messages", json=payload)
    await asyncio.sleep(0.1)

    assert results_received
    result = results_received[0]
    assert result.get("channel") == "telegram"
    assert result.get("reply_to", {}).get("chat_id") == 77


# ---------------------------------------------------------------------------
# Full loop: not_food intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_echo_loop_not_food_intent_produces_not_food_result(full_loop_setup):
    """
    When the processing-worker returns intent='not_food', the api-core
    orchestrator must map it to Result{kind='not_food'} in results.telegram.
    Verifies the not-food branch of the orchestrator mapping (C2-T06,
    contracts.md §3.2).
    """
    client, bus, results_received = full_loop_setup

    # The echo pipeline must support triggering not_food — either by a special
    # text prefix or by an explicit test hook. The pipeline stub should expose
    # a way to produce not_food for testing; we use a sentinel text.
    payload = {
        "channel": "telegram",
        "channel_user_id": "u1",
        "text": "not_food:привет как дела",  # sentinel: echo stub returns not_food
        "reply_to": {"chat_id": 1},
    }
    await client.post("/v1/messages", json=payload)
    await asyncio.sleep(0.1)

    assert results_received, "Expected a Result for not_food intent"
    result = results_received[0]
    assert result.get("kind") == "not_food", (
        f"Expected Result kind='not_food', got {result.get('kind')!r}"
    )


# ---------------------------------------------------------------------------
# Consumer-loop isolation: handler error does not crash the loop (TДЕ-8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_loop_continues_after_handler_error():
    """
    If the orchestrator handler raises an exception for one ProcessingResult,
    the consumer-loop must continue processing subsequent events without
    crashing.

    This verifies isolation of handler errors: a bad message must not kill
    the loop and prevent all future results from being processed
    (Q-C2-1, TДЕ-8, conventions §5).
    """
    from apps.api_core.orchestrator import handle_processing_result
    from core.bus import InMemoryBus

    bus = InMemoryBus()
    results_received: list[dict] = []

    # Register the real orchestrator handler
    await bus.subscribe("results.processing", "api-core-orchestrator", handle_processing_result)

    # Capture what ends up in results.telegram
    async def capture_result(event: dict) -> None:
        results_received.append(event)

    await bus.subscribe("results.telegram", "test", capture_result)

    # Publish a malformed ProcessingResult that should cause the handler to error
    malformed_event = {"this_is": "completely_invalid", "missing": "all_required_fields"}
    await bus.publish("results.processing", malformed_event)
    await asyncio.sleep(0)

    # Now publish a valid ProcessingResult — the loop must handle it
    from core.contracts import ProcessingResult

    valid_result = ProcessingResult(
        task_id="t-after-error",
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 1},
    )
    await bus.publish("results.processing", valid_result.model_dump())
    await asyncio.sleep(0)

    # The loop must have processed the valid event even after the malformed one
    assert any(r.get("task_id") == "t-after-error" for r in results_received), (
        "Consumer-loop must process subsequent events after a handler error "
        "(loop isolation — TДЕ-8)"
    )


# ---------------------------------------------------------------------------
# Decoupling: consumer-loop does NOT access the DB on C2 (ADR-0015)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_loop_does_not_call_db(monkeypatch):
    """
    The api-core consumer-loop (orchestrator) must not access the database
    in C2 — there is no persist step yet (persist arrives in C11, ADR-0015).
    We verify this by ensuring no SQLAlchemy session is opened during a
    full-loop run.
    """
    import sqlalchemy.ext.asyncio as sa_async

    sessions_opened: list = []
    original_begin = sa_async.AsyncSession.begin

    def patched_begin(self, *args, **kwargs):
        sessions_opened.append(self)
        return original_begin(self, *args, **kwargs)

    monkeypatch.setattr(sa_async.AsyncSession, "begin", patched_begin, raising=False)

    from apps.api_core.orchestrator import handle_processing_result
    from core.bus import InMemoryBus
    from core.contracts import ProcessingResult

    bus = InMemoryBus()
    await bus.subscribe("results.processing", "api-core-orchestrator", handle_processing_result)

    result = ProcessingResult(
        task_id="no-db-task",
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 1},
    )
    await bus.publish("results.processing", result.model_dump())
    await asyncio.sleep(0)

    assert len(sessions_opened) == 0, (
        "Consumer-loop must not open a DB session in C2 — persist is deferred to C11 (ADR-0015)"
    )
