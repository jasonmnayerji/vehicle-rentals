"""RentalPeriod value object."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .errors import InvalidRentalPeriod


@dataclass(frozen=True, slots=True)
class RentalPeriod:
    """A half-open span of time: ``[start, end)``, from pick-up to return.

    The car is occupied from ``start`` up to but excluding ``end``, so a car returned at
    10:00 can be picked up by someone else at 10:00. Half-open ranges make back-to-back
    rentals work without special cases: two periods overlap only if each starts before the
    other ends.

    Both ends must be timezone-aware and are normalised to UTC on construction. A naive
    datetime is ambiguous about which instant it means, and Python compares and subtracts two
    datetimes that share a tzinfo by *wall clock*, which silently gets durations wrong across
    a daylight-saving change. After normalisation, equality, ordering and duration are all
    about real elapsed time, and the same instant written in two time zones is one value.

    Immutable and self-validating, so an invalid period can never exist.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for name in ("start", "end"):
            value = getattr(self, name)
            if not isinstance(value, datetime):
                raise InvalidRentalPeriod(
                    f"{name} must be a date and time, got {type(value).__name__}"
                )
            if value.tzinfo is None or value.utcoffset() is None:
                raise InvalidRentalPeriod(f"{name} must include a time zone")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.end <= self.start:
            raise InvalidRentalPeriod("a rental must end after it starts")

    @classmethod
    def for_days(cls, start: datetime, days: int) -> RentalPeriod:
        """From ``start`` for a whole number of days: the car comes back at the time of day
        it went out.

        "Three days from 10:00" means 10:00 three days later *on the customer's clock*. Across
        a daylight-saving change that is 71 or 73 elapsed hours, not 72, so the days are
        added in the zone ``start`` was given in, and only then is the result normalised to
        UTC. Adding 72 hours to the UTC instant instead would hand the car back at 09:00 or
        11:00 and quietly collide with, or leave a gap before, the next booking.
        """
        # bool is an int in Python; "True days" is a bug at the call site, not one day.
        if isinstance(days, bool) or not isinstance(days, int):
            raise InvalidRentalPeriod(f"days must be a whole number, got {type(days).__name__}")
        if days < 1:
            raise InvalidRentalPeriod("a rental must last at least 1 day")
        if not isinstance(start, datetime):
            raise InvalidRentalPeriod(f"start must be a date and time, got {type(start).__name__}")
        if start.tzinfo is None or start.utcoffset() is None:
            raise InvalidRentalPeriod("start must include a time zone")
        # Aware datetime + timedelta is wall-clock arithmetic in that datetime's own zone.
        return cls(start=start, end=start + timedelta(days=days))

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: RentalPeriod) -> bool:
        return self.start < other.end and other.start < self.end
