from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rentals.domain import BookingPolicy, InvalidRentalPeriod, RentalPeriod

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
POLICY = BookingPolicy(
    min_duration=timedelta(hours=1),
    max_duration=timedelta(days=30),
    max_advance=timedelta(days=100),
    start_grace=timedelta(minutes=5),
)


def rental(starts_in: timedelta, lasts: timedelta = timedelta(days=3)) -> RentalPeriod:
    start = NOW + starts_in
    return RentalPeriod(start=start, end=start + lasts)


def test_a_rental_may_start_right_now() -> None:
    POLICY.validate(rental(timedelta(0)), now=NOW)


def test_a_walk_up_booking_is_not_refused_for_the_seconds_the_request_took() -> None:
    POLICY.validate(rental(-timedelta(minutes=5)), now=NOW)


def test_a_rental_may_not_start_in_the_past() -> None:
    with pytest.raises(InvalidRentalPeriod, match="in the past"):
        POLICY.validate(rental(-timedelta(minutes=5, seconds=1)), now=NOW)


def test_a_rental_may_last_exactly_the_minimum() -> None:
    POLICY.validate(rental(timedelta(hours=1), lasts=timedelta(hours=1)), now=NOW)


def test_a_rental_may_not_be_shorter_than_the_minimum() -> None:
    with pytest.raises(InvalidRentalPeriod, match="at least 1 hour"):
        POLICY.validate(rental(timedelta(hours=1), lasts=timedelta(minutes=59)), now=NOW)


def test_a_rental_may_last_exactly_the_maximum() -> None:
    POLICY.validate(rental(timedelta(days=1), lasts=timedelta(days=30)), now=NOW)


def test_a_rental_may_not_exceed_the_maximum_length() -> None:
    with pytest.raises(InvalidRentalPeriod, match="cannot exceed 30 days"):
        POLICY.validate(rental(timedelta(days=1), lasts=timedelta(days=31)), now=NOW)


def test_a_rental_of_exactly_the_maximum_days_is_allowed_even_when_the_clocks_go_back() -> None:
    # Sydney leaves daylight saving on 5 April 2026. Thirty calendar days across that night
    # are thirty days and one hour of elapsed time, and still a thirty-day rental.
    sydney = ZoneInfo("Australia/Sydney")
    thirty_days = RentalPeriod.for_days(datetime(2026, 3, 20, 10, 0, tzinfo=sydney), 30)
    assert thirty_days.duration == timedelta(days=30, hours=1)

    POLICY.validate(thirty_days, now=NOW)


def test_the_daylight_saving_allowance_is_one_hour_and_no_more() -> None:
    with pytest.raises(InvalidRentalPeriod, match="cannot exceed 30 days"):
        POLICY.validate(rental(timedelta(days=1), lasts=timedelta(days=30, minutes=61)), now=NOW)


def test_a_rental_may_start_exactly_at_the_booking_horizon() -> None:
    POLICY.validate(rental(timedelta(days=100)), now=NOW)


def test_a_rental_may_not_start_beyond_the_booking_horizon() -> None:
    with pytest.raises(InvalidRentalPeriod, match="more than 100 days ahead"):
        POLICY.validate(rental(timedelta(days=100, minutes=1)), now=NOW)


def test_limits_are_described_in_the_unit_a_person_would_use() -> None:
    policy = BookingPolicy(min_duration=timedelta(minutes=30), max_duration=timedelta(hours=36))
    with pytest.raises(InvalidRentalPeriod, match="at least 30 minutes"):
        policy.validate(rental(timedelta(hours=1), lasts=timedelta(minutes=10)), now=NOW)
    with pytest.raises(InvalidRentalPeriod, match="cannot exceed 36 hours"):
        policy.validate(rental(timedelta(hours=1), lasts=timedelta(hours=40)), now=NOW)
