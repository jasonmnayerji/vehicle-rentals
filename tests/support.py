"""Shared, fixture-free helpers for tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Every test runs against this fixed instant, never the wall clock.
NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def at(days: float = 0, hours: float = 0) -> datetime:
    """The instant ``days`` and ``hours`` after the fixed NOW."""
    return NOW + timedelta(days=days, hours=hours)


def key(label: str) -> str:
    """A well-formed idempotency key that is readable in test output."""
    return f"test-key-{label}"
