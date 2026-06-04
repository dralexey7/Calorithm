"""
apps.api_core.health — GET /healthz endpoint (C1-T04).

Liveness + DB probe.  Returns:
  200  {"status": "ok"}        — probe succeeded
  503  {"status": "error", "detail": "db_unavailable"}  — probe failed

The dependency `get_db_probe` is a FastAPI Depends-compatible callable that,
when called, performs (or raises on) the DB check.  Unit tests override it
via app.dependency_overrides to control the outcome without a real DB.

Conventions §5: no stack traces or internal details in error responses.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency: DB probe
# ---------------------------------------------------------------------------

# Type alias for the probe callable injected via Depends.
_DbProbeCallable = Callable[[], Awaitable[None]]


def get_db_probe() -> _DbProbeCallable:
    """
    FastAPI dependency that returns the DB probe coroutine factory.

    This function is overridden in unit tests via dependency_overrides to
    inject a mock probe (success or failure) without touching a real DB.

    The actual probe is set at app-construction time by `create_app` in
    main.py, which replaces this default with a closure over the real engine.

    Returns:
        An async callable that performs the DB probe.
    """

    # Default: raise NotImplementedError so accidental use without override is visible.
    # In practice, create_app always overrides this before the app serves traffic.
    async def _unset_probe() -> None:
        raise NotImplementedError(
            "get_db_probe was not configured — did create_app forget to override it?"
        )

    return _unset_probe


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/healthz")
async def healthz(
    probe: _DbProbeCallable = Depends(get_db_probe),  # noqa: B008
) -> JSONResponse:
    """
    Liveness + DB readiness check.

    Calls the injected probe.  On success returns 200 {"status": "ok"}.
    On any exception returns 503 {"status": "error", "detail": "db_unavailable"}
    without exposing stack traces or driver internals (conventions §5).
    """
    try:
        await probe()
    except Exception:
        # Log server-side with traceback for diagnosis (ADR-0017, §7); the
        # response body never exposes internal details (conventions §5).
        logger.warning("healthz db probe failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": "db_unavailable"},
        )

    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
