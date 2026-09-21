from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rentals.domain import FleetSchedule, RentalPeriod

BASE = datetime(2026, 3, 1, tzinfo=UTC)


def period(start_day: int, end_day: int) -> RentalPeriod:
    """Midnight to midnight. Days are 1-based offsets from BASE; ``end_day`` is exclusive."""
    return RentalPeriod(
        start=BASE + timedelta(days=start_day - 1), end=BASE + timedelta(days=end_day - 1)
    )


def hours(start_hour: float, end_hour: float) -> RentalPeriod:
    """A period given in hours after BASE, for the cases that turn on the time of day."""
    return RentalPeriod(
        start=BASE + timedelta(hours=start_hour), end=BASE + timedelta(hours=end_hour)
    )


class TestCanAccommodate:
    def test_an_empty_schedule_accepts_a_booking(self) -> None:
        assert FleetSchedule(1, []).can_accommodate(period(1, 5))

    def test_a_fleet_with_no_cars_accepts_nothing(self) -> None:
        assert not FleetSchedule(0, []).can_accommodate(period(1, 5))

    def test_rejects_when_every_car_is_out_for_part_of_the_period(self) -> None:
        schedule = FleetSchedule(2, [period(1, 10), period(4, 6)])
        assert not schedule.can_accommodate(period(5, 8))

    def test_accepts_when_the_overlapping_bookings_are_never_simultaneous(self) -> None:
        # The case that separates peak usage from a naive overlap count. Both existing
        # bookings overlap the request, and a fleet of 2 "has 2 overlaps", yet they never
        # coincide, so one car is always free.
        schedule = FleetSchedule(2, [period(1, 3), period(5, 7)])
        assert schedule.can_accommodate(period(2, 6))

    def test_a_single_fully_booked_hour_anywhere_in_the_period_is_enough_to_reject(self) -> None:
        one_busy_hour = hours(240, 241)
        schedule = FleetSchedule(2, [one_busy_hour, one_busy_hour])
        assert not schedule.can_accommodate(period(1, 30))
        assert schedule.can_accommodate(hours(0, 240))
        assert schedule.can_accommodate(hours(241, 700))

    def test_a_car_returned_at_ten_can_go_out_again_at_ten(self) -> None:
        schedule = FleetSchedule(1, [hours(8, 10)])
        assert schedule.can_accommodate(hours(10, 12))

    def test_a_car_cannot_go_out_one_minute_before_it_is_returned(self) -> None:
        schedule = FleetSchedule(1, [hours(8, 10)])
        a_minute_early = RentalPeriod(
            start=BASE + timedelta(hours=9, minutes=59), end=BASE + timedelta(hours=12)
        )
        assert not schedule.can_accommodate(a_minute_early)

    def test_a_car_can_be_booked_right_up_to_the_next_pick_up(self) -> None:
        schedule = FleetSchedule(1, [hours(10, 12)])
        assert schedule.can_accommodate(hours(8, 10))

    def test_several_short_rentals_in_one_day_need_only_one_car(self) -> None:
        schedule = FleetSchedule(1, [hours(8, 10), hours(10, 13), hours(15, 18)])
        assert schedule.can_accommodate(hours(13, 15))
        assert not schedule.can_accommodate(hours(12, 15))


class TestPeakUsage:
    def test_is_zero_when_nothing_is_booked(self) -> None:
        assert FleetSchedule(3, []).peak_usage(period(1, 5)) == 0

    def test_ignores_bookings_outside_the_window(self) -> None:
        schedule = FleetSchedule(3, [period(1, 3), period(20, 25)])
        assert schedule.peak_usage(period(5, 10)) == 0

    def test_ignores_how_busy_the_fleet_is_outside_the_window(self) -> None:
        # Three cars are out on day 2, but by the window only one booking is still running.
        schedule = FleetSchedule(3, [period(1, 4), period(1, 4), period(1, 8)])
        assert schedule.peak_usage(period(5, 7)) == 1

    def test_counts_identical_bookings_separately(self) -> None:
        schedule = FleetSchedule(5, [period(1, 5)] * 3)
        assert schedule.peak_usage(period(1, 5)) == 3

    def test_is_independent_of_the_order_bookings_are_supplied_in(self) -> None:
        booked = [period(1, 6), period(3, 9), period(5, 7), period(8, 12)]
        expected = FleetSchedule(9, booked).peak_usage(period(1, 12))
        assert expected == 3
        assert FleetSchedule(9, reversed(booked)).peak_usage(period(1, 12)) == expected

    def test_compares_instants_not_wall_clocks_across_time_zones(self) -> None:
        # 09:00-11:00 in Tokyo is 00:00-02:00 UTC, which does not touch 08:00-10:00 UTC even
        # though the wall-clock numbers look as if they overlap.
        tokyo = ZoneInfo("Asia/Tokyo")
        in_tokyo = RentalPeriod(
            start=datetime(2026, 3, 1, 9, 0, tzinfo=tokyo),
            end=datetime(2026, 3, 1, 11, 0, tzinfo=tokyo),
        )
        assert FleetSchedule(1, [in_tokyo]).can_accommodate(hours(8, 10))
        assert not FleetSchedule(1, [in_tokyo]).can_accommodate(hours(1, 3))

    def test_agrees_with_an_hour_by_hour_count_on_random_schedules(self) -> None:
        """The sweep line must match the obviously-correct (but slow) definition."""
        rng = random.Random(20260310)  # noqa: S311 - seeded on purpose: deterministic, not security
        for _ in range(300):
            booked = []
            for _ in range(rng.randint(0, 12)):
                start = rng.randint(0, 200)
                booked.append(hours(start, start + rng.randint(1, 72)))
            start = rng.randint(0, 200)
            window = hours(start, start + rng.randint(1, 72))

            # Every boundary is on the hour, so usage can only change on the hour and sampling
            # the start of each hour in the window sees every level it reaches.
            sample_points = (
                window.start + timedelta(hours=i)
                for i in range(int(window.duration / timedelta(hours=1)))
            )
            brute_force = max(
                sum(1 for b in booked if b.start <= instant < b.end) for instant in sample_points
            )
            assert FleetSchedule(99, booked).peak_usage(window) == brute_force


class TestRemaining:
    def test_is_the_number_of_cars_free_for_the_entire_window(self) -> None:
        schedule = FleetSchedule(5, [period(1, 5), period(2, 6), period(8, 9)])
        assert schedule.remaining(period(1, 10)) == 3

    def test_never_goes_negative_when_the_fleet_was_shrunk_below_existing_bookings(self) -> None:
        schedule = FleetSchedule(1, [period(1, 5), period(1, 5), period(1, 5)])
        assert schedule.remaining(period(1, 5)) == 0
        assert not schedule.can_accommodate(period(1, 5))


def test_a_negative_fleet_size_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="negative"):
        FleetSchedule(-1, [])
