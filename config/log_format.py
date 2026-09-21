"""Structured logging: one JSON object per line, standard library only."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has. Anything else on a record was passed by the caller via
# ``extra=`` and belongs in the output.
_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # default=str: a log call must never raise because a value was not JSON-serialisable.
        return json.dumps(payload, default=str)
