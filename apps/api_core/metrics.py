"""
apps.api_core.metrics — GET /metrics endpoint (C1-T04, ADR-0010, conventions §7).

Exposes the default prometheus_client registry in Prometheus text exposition
format.  The default registry includes process and Python runtime metrics.

Content-Type is set to the standard Prometheus text format header so that
a Prometheus scraper (introduced in C5) can parse it correctly.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/metrics")
async def metrics() -> Response:
    """
    Expose Prometheus metrics from the default registry.

    Returns a plain-text Prometheus exposition format response.
    Content-Type is set to CONTENT_TYPE_LATEST (text/plain; version=0.0.4; charset=utf-8).
    """
    data = generate_latest()
    return Response(
        content=data,
        media_type=CONTENT_TYPE_LATEST,
    )
