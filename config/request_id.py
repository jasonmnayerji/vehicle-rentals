"""Correlation ids: tag every log line written while serving a request with that request's id."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from django.http import HttpRequest, HttpResponse

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# An upstream proxy may supply the id so a request can be traced across services. It is
# client-controlled input that lands in logs, so anything unexpected is discarded rather
# than echoed: no newlines, no oversized values, no log injection.
_ACCEPTABLE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
HEADER = "X-Request-ID"


def current_request_id() -> str | None:
    return _request_id.get()


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self._get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.headers.get(HEADER, "")
        request_id = supplied if _ACCEPTABLE.fullmatch(supplied) else uuid.uuid4().hex
        token = _request_id.set(request_id)
        try:
            response = self._get_response(request)
        finally:
            _request_id.reset(token)
        response[HEADER] = request_id
        return response


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True
