"""Access log: one structured line per request, tagged with that request's id.

Gunicorn can write an access log too, but in its own text format and with no knowledge of
the request id, so its lines would neither parse as JSON nor correlate with anything else.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)

_HEALTH_PATH = "/healthz"


class AccessLogMiddleware:
    """Must sit inside RequestIdMiddleware, so the id is set by the time the line is written."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self._get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        response = self._get_response(request)

        # The container probe calls every few seconds. A healthy answer is noise; an
        # unhealthy one is worth a line.
        if request.path == _HEALTH_PATH and response.status_code == 200:
            return response

        logger.info(
            "request handled",
            extra={
                "method": request.method,
                # The path only. Query strings carry whatever a client or a redirect put
                # there (?next=..., tokens), and none of it belongs in a log.
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response
