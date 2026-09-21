from __future__ import annotations

import json
import logging
import sys
from datetime import date

from config.log_format import JsonFormatter
from config.request_id import RequestIdFilter


def make_record(level: int, message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("rentals.services", level, __file__, 1, message, (), None)
    for name, value in extra.items():
        setattr(record, name, value)
    return record


def test_each_line_is_one_json_object_with_level_logger_and_message() -> None:
    line = JsonFormatter().format(make_record(logging.WARNING, "reservation rejected"))

    assert "\n" not in line
    payload = json.loads(line)
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "rentals.services"
    assert payload["message"] == "reservation rejected"
    assert payload["timestamp"].endswith("+00:00")


def test_context_passed_by_the_caller_becomes_top_level_fields() -> None:
    record = make_record(logging.INFO, "reservation created", reservation_id=42, user_id=7)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["reservation_id"] == 42
    assert payload["user_id"] == 7


def test_values_that_are_not_json_native_never_break_logging() -> None:
    record = make_record(logging.INFO, "reservation created", start_date=date(2026, 3, 1))

    assert json.loads(JsonFormatter().format(record))["start_date"] == "2026-03-01"


def test_errors_carry_their_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "ERROR"
    assert "RuntimeError: boom" in payload["exception"]


def test_lines_logged_outside_a_request_have_a_null_request_id() -> None:
    record = make_record(logging.INFO, "started")
    RequestIdFilter().filter(record)

    assert json.loads(JsonFormatter().format(record))["request_id"] is None
