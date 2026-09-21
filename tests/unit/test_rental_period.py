from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from rentals.domain import InvalidRentalPeriod, RentalPeriod


def utc(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=UTC)


def period(start_day: int, end_day: int) -> RentalPeriod:
    return RentalPeriod(start=utc(start_day), end=utc(end_day))


class TestConstruction:
    def test_duration_is_the_time_the_car_is_occupied(self) -> None:
        rental = RentalPeriod(start=utc(1, 9, 30), end=utc(3, 17, 0))
        assert rental.duration == timedelta(days=2, hours=7, minutes=30)

    def test_rejects_a_return_at_the_moment_of_pick_up(self) -> None:
        with pytest.raises(InvalidRentalPeriod, match="end after it starts"):
            RentalPeriod(start=utc(5, 10), end=utc(5, 10))

    def test_rejects_a_return_before_pick_up(self) -> None:
        with pytest.raises(InvalidRentalPeriod, match="end after it starts"):
            RentalPeriod(start=utc(5, 10), end=utc(5, 9, 59))

    def test_rejects_a_time_without_a_zone_because_it_names_no_particular_instant(self) -> None:
        naive = datetime(2026, 3, 1, 9, 0)
        with pytest.raises(InvalidRentalPeriod, match="time zone"):
            RentalPeriod(start=naive, end=utc(2))
        with pytest.raises(InvalidRentalPeriod, match="time zone"):
            RentalPeriod(start=utc(1), end=naive + timedelta(days=1))

    @pytest.mark.parametrize("bad", [date(2026, 3, 1), "2026-03-01T09:00:00Z", 1_772_355_600, None])
    def test_rejects_anything_that_is_not_a_date_and_time(self, bad: object) -> None:
        with pytest.raises(InvalidRentalPeriod, match="must be a date and time"):
            RentalPeriod(start=bad, end=utc(2))  # type: ignore[arg-type]

    def test_is_immutable(self) -> None:
        with pytest.raises(FrozenInstanceError):
            period(1, 4).start = utc(2)  # type: ignore[misc]


class TestTimeZones:
    def test_the_same_instants_written_in_different_zones_are_the_same_period(self) -> None:
        in_utc = RentalPeriod(start=utc(1, 9), end=utc(2, 9))
        tokyo = ZoneInfo("Asia/Tokyo")
        in_tokyo = RentalPeriod(
            start=datetime(2026, 3, 1, 18, 0, tzinfo=tokyo),
            end=datetime(2026, 3, 2, 18, 0, tzinfo=tokyo),
        )

        assert in_tokyo == in_utc
        assert hash(in_tokyo) == hash(in_utc)

    def test_fixed_offsets_are_understood_too(self) -> None:
        minus_five = timezone(timedelta(hours=-5))
        rental = RentalPeriod(
            start=datetime(2026, 3, 1, 4, 0, tzinfo=minus_five),
            end=datetime(2026, 3, 1, 8, 0, tzinfo=minus_five),
        )

        assert rental.start == utc(1, 9)

    def test_duration_is_real_elapsed_time_across_a_daylight_saving_change(self) -> None:
        # New York springs forward at 02:00 on 8 March 2026. Noon on the 7th to noon on the
        # 8th reads as 24 hours on the wall clock but only 23 hours actually pass. Python's
        # own subtraction of two datetimes sharing a tzinfo would say 24.
        new_york = ZoneInfo("America/New_York")
        start = datetime(2026, 3, 7, 12, 0, tzinfo=new_york)
        end = datetime(2026, 3, 8, 12, 0, tzinfo=new_york)
        assert end - start == timedelta(hours=24)  # the trap

        assert RentalPeriod(start=start, end=end).duration == timedelta(hours=23)


class TestOverlaps:
    @pytest.mark.parametrize(
        ("other", "expected"),
        [
            pytest.param(period(1, 5), False, id="ends the moment this one starts"),
            pytest.param(period(10, 12), False, id="starts the moment this one ends"),
            pytest.param(
                RentalPeriod(start=utc(1), end=utc(5, 0, 1)), True, id="runs one minute into it"
            ),
            pytest.param(
                RentalPeriod(start=utc(9, 23, 59), end=utc(12)),
                True,
                id="starts in its last minute",
            ),
            pytest.param(period(6, 8), True, id="sits entirely inside"),
            pytest.param(period(1, 20), True, id="entirely contains"),
            pytest.param(period(5, 10), True, id="is identical"),
            pytest.param(period(20, 25), False, id="is well clear"),
        ],
    )
    def test_periods_overlap_only_when_they_share_a_moment(
        self, other: RentalPeriod, expected: bool
    ) -> None:
        this = period(5, 10)
        assert this.overlaps(other) is expected
        assert other.overlaps(this) is expected  # symmetric


class TestForDays:
    """Requirement 3: a car is reserved at a date and time *for a given number of days*."""

    def test_runs_from_the_pick_up_time_for_that_many_days(self) -> None:
        pick_up = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)

        period = RentalPeriod.for_days(pick_up, 3)

        assert period.start == pick_up
        assert period.end == datetime(2026, 6, 4, 10, 30, tzinfo=UTC)
        assert period.duration == timedelta(days=3)

    def test_is_the_same_period_as_one_written_out_in_full(self) -> None:
        pick_up = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)

        assert RentalPeriod.for_days(pick_up, 2) == RentalPeriod(
            start=pick_up, end=datetime(2026, 6, 3, 10, 30, tzinfo=UTC)
        )

    @pytest.mark.parametrize(
        ("pick_up", "elapsed_hours"),
        [
            pytest.param(datetime(2026, 3, 7, 10, 0), 47, id="clocks go forward: a 23-hour day"),
            pytest.param(datetime(2026, 10, 31, 10, 0), 49, id="clocks go back: a 25-hour day"),
        ],
    )
    def test_the_car_comes_back_at_the_same_time_of_day_across_a_daylight_saving_change(
        self, pick_up: datetime, elapsed_hours: int
    ) -> None:
        new_york = ZoneInfo("America/New_York")

        period = RentalPeriod.for_days(pick_up.replace(tzinfo=new_york), 2)

        returned = period.end.astimezone(new_york)
        assert (returned.hour, returned.minute) == (10, 0)  # not 09:00 or 11:00
        assert period.duration == timedelta(hours=elapsed_hours)  # and not a flat 48

    @pytest.mark.parametrize("days", [0, -1])
    def test_a_rental_lasts_at_least_one_day(self, days: int) -> None:
        with pytest.raises(InvalidRentalPeriod, match="at least 1 day"):
            RentalPeriod.for_days(datetime(2026, 6, 1, 10, 0, tzinfo=UTC), days)

    @pytest.mark.parametrize("days", [1.5, "3", None, True])
    def test_the_number_of_days_must_be_a_whole_number(self, days: object) -> None:
        with pytest.raises(InvalidRentalPeriod, match="whole number"):
            RentalPeriod.for_days(datetime(2026, 6, 1, 10, 0, tzinfo=UTC), days)  # type: ignore[arg-type]

    def test_rejects_a_pick_up_time_without_a_zone(self) -> None:
        with pytest.raises(InvalidRentalPeriod, match="time zone"):
            RentalPeriod.for_days(datetime(2026, 6, 1, 10, 0), 3)

    def test_rejects_a_pick_up_that_is_not_a_date_and_time(self) -> None:
        with pytest.raises(InvalidRentalPeriod, match="date and time"):
            RentalPeriod.for_days(date(2026, 6, 1), 3)  # type: ignore[arg-type]
