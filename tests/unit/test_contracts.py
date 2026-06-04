"""
Unit tests for core.contracts — C2-T01.

Covers: round-trip serialisation of DTO types, user_id integer constraint,
envelope field presence, kind literal constraints, and task_id stability
across all three DTO classes in the processing pipeline.

All tests are pure unit tests — no network, no DB, no bus.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


def test_processing_task_round_trips_without_loss():
    """
    ProcessingTask must serialise to dict/JSON and deserialise back to an
    identical object without losing any field values.
    Verifies that the Pydantic boundary is stable for broker transport
    (contracts.md §3.1).
    """
    from core.contracts import ProcessingTask

    task = ProcessingTask(
        task_id="task-abc-123",
        user_id=42,
        channel="telegram",
        channel_user_id="12345",
        kind="ingest_message",
        text="200г куриной грудки",
        reply_to={"chat_id": 99, "message_id": 7},
        confirm_enabled=False,
    )
    data = task.model_dump()
    restored = ProcessingTask.model_validate(data)

    assert restored.task_id == task.task_id
    assert restored.user_id == task.user_id
    assert restored.channel == task.channel
    assert restored.text == task.text
    assert restored.kind == task.kind


def test_processing_result_round_trips_without_loss():
    """
    ProcessingResult must serialise and deserialise without data loss.
    This is the return payload from processing-worker back to api-core
    (contracts.md §3.1a).
    """
    from core.contracts import ProcessingResult

    result = ProcessingResult(
        task_id="task-abc-123",
        user_id=42,
        channel="telegram",
        channel_user_id="12345",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 99, "message_id": 7},
    )
    data = result.model_dump()
    restored = ProcessingResult.model_validate(data)

    assert restored.task_id == result.task_id
    assert restored.intent == result.intent
    assert restored.user_id == result.user_id


def test_result_round_trips_without_loss():
    """
    Result (the final delivery payload to the channel adapter) must serialise
    and deserialise without data loss (contracts.md §3.2).
    """
    from core.contracts import Result

    result = Result(
        task_id="task-abc-123",
        user_id=42,
        channel="telegram",
        channel_user_id="12345",
        kind="logged",
        reply_to={"chat_id": 99, "message_id": 7},
    )
    data = result.model_dump()
    restored = Result.model_validate(data)

    assert restored.task_id == result.task_id
    assert restored.kind == result.kind
    assert restored.user_id == result.user_id


# ---------------------------------------------------------------------------
# user_id is int in ALL three DTO types (not UUID — data-model.md §1)
# ---------------------------------------------------------------------------


def test_processing_task_user_id_is_int():
    """
    ProcessingTask.user_id must accept int and reject a UUID string.
    Enforces the bigint primary key decision from data-model.md §1 and
    removes the UUID drift from contracts.md (C2-T07).
    """
    from core.contracts import ProcessingTask

    task = ProcessingTask(
        task_id="t1",
        user_id=100,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        text="тест",
        reply_to={"chat_id": 1},
        confirm_enabled=False,
    )
    assert isinstance(task.user_id, int)
    assert task.user_id == 100


def test_processing_task_user_id_rejects_uuid_string():
    """
    ProcessingTask.user_id must not accept a UUID string — Pydantic must raise
    ValidationError (or coerce to int and fail).
    Prevents accidental UUID usage drifting back in (data-model.md §1).
    """
    from pydantic import ValidationError

    from core.contracts import ProcessingTask

    with pytest.raises((ValidationError, ValueError)):
        ProcessingTask(
            task_id="t1",
            user_id="550e8400-e29b-41d4-a716-446655440000",  # UUID string — must fail
            channel="telegram",
            channel_user_id="u1",
            kind="ingest_message",
            text="тест",
            reply_to={"chat_id": 1},
            confirm_enabled=False,
        )


def test_processing_result_user_id_is_int():
    """
    ProcessingResult.user_id must be int, not UUID.
    contracts.md §3.1a listed UUID; this test locks the corrected type
    (data-model.md §1 bigint).
    """
    from core.contracts import ProcessingResult

    result = ProcessingResult(
        task_id="t1",
        user_id=77,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 1},
    )
    assert isinstance(result.user_id, int)


def test_result_user_id_is_int():
    """
    Result.user_id must be int, not UUID.
    The final delivery DTO carries user_id for routing; it must match the
    bigint type used across the entire pipeline (data-model.md §1).
    """
    from core.contracts import Result

    r = Result(
        task_id="t1",
        user_id=55,
        channel="telegram",
        channel_user_id="u1",
        kind="not_food",
        reply_to={"chat_id": 1},
    )
    assert isinstance(r.user_id, int)


# ---------------------------------------------------------------------------
# Envelope fields on broker events (contracts.md §3)
# ---------------------------------------------------------------------------


def test_processing_task_has_envelope_fields():
    """
    ProcessingTask (broker event) must carry the standard envelope fields:
    schema_version (int), event_id (str), occurred_at (datetime).
    These allow consumers to detect schema drift and correlate events
    (contracts.md §3 — common envelope).
    """
    from core.contracts import ProcessingTask

    task = ProcessingTask(
        task_id="t1",
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        text="тест",
        reply_to={"chat_id": 1},
        confirm_enabled=False,
    )
    assert hasattr(task, "schema_version"), "ProcessingTask must have schema_version"
    assert hasattr(task, "event_id"), "ProcessingTask must have event_id"
    assert hasattr(task, "occurred_at"), "ProcessingTask must have occurred_at"
    assert isinstance(task.schema_version, int)


def test_processing_result_has_envelope_fields():
    """
    ProcessingResult (broker event) must carry envelope fields.
    Ensures both sides of the task/result pair are envelope-aware
    (contracts.md §3 — all broker messages share the envelope).
    """
    from core.contracts import ProcessingResult

    result = ProcessingResult(
        task_id="t1",
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 1},
    )
    assert hasattr(result, "schema_version")
    assert hasattr(result, "event_id")
    assert hasattr(result, "occurred_at")


# ---------------------------------------------------------------------------
# Kind literal constraints
# ---------------------------------------------------------------------------


def test_processing_task_kind_valid_values():
    """
    ProcessingTask.kind must accept only 'ingest_message' and 'confirm'.
    Locks the enumerated domain defined in contracts.md §3.1.
    """
    from core.contracts import ProcessingTask

    for kind in ("ingest_message", "confirm"):
        task = ProcessingTask(
            task_id="t1",
            user_id=1,
            channel="telegram",
            channel_user_id="u1",
            kind=kind,  # type: ignore[arg-type]
            reply_to={"chat_id": 1},
            confirm_enabled=False,
        )
        assert task.kind == kind


def test_processing_task_kind_rejects_invalid():
    """
    ProcessingTask.kind must reject values outside the Literal constraint.
    Prevents unknown task types silently flowing into the pipeline.
    """
    from pydantic import ValidationError

    from core.contracts import ProcessingTask

    with pytest.raises(ValidationError):
        ProcessingTask(
            task_id="t1",
            user_id=1,
            channel="telegram",
            channel_user_id="u1",
            kind="unknown_kind",  # type: ignore[arg-type]
            reply_to={"chat_id": 1},
            confirm_enabled=False,
        )


def test_result_kind_valid_values():
    """
    Result.kind must accept the full set from contracts.md §3.2:
    not_food, preview, logged, daily_summary, error.
    In C2 only logged/not_food are used, but all must be accepted now.
    """
    from core.contracts import Result

    for kind in ("not_food", "preview", "logged", "daily_summary", "error"):
        r = Result(
            task_id="t1",
            user_id=1,
            channel="telegram",
            channel_user_id="u1",
            kind=kind,  # type: ignore[arg-type]
            reply_to={"chat_id": 1},
        )
        assert r.kind == kind


def test_result_kind_rejects_invalid():
    """
    Result.kind must reject values outside its Literal domain.
    Prevents unknown result kinds from reaching channel adapters silently.
    """
    from pydantic import ValidationError

    from core.contracts import Result

    with pytest.raises(ValidationError):
        Result(
            task_id="t1",
            user_id=1,
            channel="telegram",
            channel_user_id="u1",
            kind="bogus",  # type: ignore[arg-type]
            reply_to={"chat_id": 1},
        )


# ---------------------------------------------------------------------------
# task_id stability across the processing pipeline (TДЕ-1)
# ---------------------------------------------------------------------------


def test_task_id_survives_serialisation_round_trip_across_all_dtos():
    """
    task_id must be preserved identically through serialisation of
    ProcessingTask, ProcessingResult, and Result.
    This is the correlation key that links the entire pipeline end-to-end
    and must never be modified or regenerated (ADR-0017 traceability).
    """
    from core.contracts import ProcessingResult, ProcessingTask, Result

    task_id = "stable-task-id-xyz"

    task = ProcessingTask(
        task_id=task_id,
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        text="тест",
        reply_to={"chat_id": 1},
        confirm_enabled=False,
    )
    proc_result = ProcessingResult(
        task_id=task_id,
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="ingest_message",
        intent="food",
        reply_to={"chat_id": 1},
    )
    result = Result(
        task_id=task_id,
        user_id=1,
        channel="telegram",
        channel_user_id="u1",
        kind="logged",
        reply_to={"chat_id": 1},
    )

    # All three, after round-trip, must carry the same task_id
    for dto in (task, proc_result, result):
        cls = type(dto)
        restored = cls.model_validate(dto.model_dump())
        assert restored.task_id == task_id, (
            f"{cls.__name__}.task_id changed after round-trip: "
            f"{restored.task_id!r} != {task_id!r}"
        )


# ---------------------------------------------------------------------------
# IngestMessageRequest / IngestMessageResponse
# ---------------------------------------------------------------------------


def test_ingest_message_request_round_trips():
    """
    IngestMessageRequest (HTTP boundary DTO for POST /v1/messages) must
    serialise and deserialise without data loss (contracts.md §1.2).
    """
    from core.contracts import IngestMessageRequest

    req = IngestMessageRequest(
        channel="telegram",
        channel_user_id="99",
        text="тарелка борща",
        reply_to={"chat_id": 10, "message_id": 2},
    )
    restored = IngestMessageRequest.model_validate(req.model_dump())
    assert restored.text == req.text
    assert restored.channel == req.channel


def test_ingest_message_response_contains_task_id_and_status():
    """
    IngestMessageResponse must carry task_id (str) and status='queued'.
    The HTTP client uses these to trace the request and confirm enqueue
    (contracts.md §1.2).
    """
    from core.contracts import IngestMessageResponse

    resp = IngestMessageResponse(task_id="abc-123", status="queued")
    assert resp.task_id == "abc-123"
    assert resp.status == "queued"
