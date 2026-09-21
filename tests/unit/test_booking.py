from __future__ import annotations

from dataclasses import replace

import pytest

from rentals.domain import (
    Booking,
    CarType,
    Customer,
    IdempotencyKey,
    InvalidIdempotencyKey,
    RentalPeriod,
    ReservationNotCancellable,
)
from tests.support import NOW, at, key

PERIOD = RentalPeriod(start=at(days=1), end=at(days=3))


def a_booking(**changes: object) -> Booking:
    booking = Booking(
        id=1,
        customer_id=7,
        car_type=CarType.SEDAN,
        period=PERIOD,
        idempotency_key=IdempotencyKey(key("a")),
    )
    return replace(booking, **changes)  # type: ignore[arg-type]


class TestCarType:
    def test_there_are_exactly_three_types_of_car(self) -> None:
        assert {car_type.value for car_type in CarType} == {"sedan", "suv", "van"}

    def test_a_type_is_looked_up_by_the_value_that_gets_stored(self) -> None:
        assert CarType("suv") is CarType.SUV

    def test_a_fourth_type_cannot_be_conjured_up(self) -> None:
        with pytest.raises(ValueError, match="convertible"):
            CarType("convertible")

    def test_is_shown_to_people_by_its_label(self) -> None:
        assert str(CarType.SUV) == "SUV"
        assert [car_type.label for car_type in CarType] == ["Sedan", "SUV", "Van"]


class TestIdempotencyKey:
    def test_compares_by_value(self) -> None:
        assert IdempotencyKey("retry-0001") == IdempotencyKey("retry-0001")
        assert str(IdempotencyKey("retry-0001")) == "retry-0001"

    @pytest.mark.parametrize(
        "malformed", ["", "short", "k" * 65, "with spaces", "semi;colon", 12345678]
    )
    def test_a_malformed_key_cannot_exist(self, malformed: object) -> None:
        with pytest.raises(InvalidIdempotencyKey):
            IdempotencyKey(malformed)  # type: ignore[arg-type]


class TestBookingIdentity:
    def test_is_the_same_booking_after_its_state_changes(self) -> None:
        before, after = a_booking(), a_booking()
        after.cancel(now=NOW)

        assert after == before
        assert len({before, after}) == 1

    def test_two_bookings_with_identical_details_are_still_two_bookings(self) -> None:
        assert a_booking(id=1) != a_booking(id=2)

    def test_is_not_equal_to_something_that_is_not_a_booking(self) -> None:
        assert a_booking(id=1) != 1


class TestBookingRules:
    def test_knows_whether_it_was_made_with_these_parameters(self) -> None:
        booking = a_booking()

        assert booking.is_for(CarType.SEDAN, PERIOD)
        assert not booking.is_for(CarType.VAN, PERIOD)
        assert not booking.is_for(CarType.SEDAN, RentalPeriod(start=at(days=1), end=at(days=4)))

    def test_its_owner_and_staff_may_manage_it_and_nobody_else(self) -> None:
        booking = a_booking(customer_id=7)

        assert booking.may_be_managed_by(Customer(id=7))
        assert booking.may_be_managed_by(Customer(id=99, is_staff=True))
        assert not booking.may_be_managed_by(Customer(id=8))

    def test_cancelling_records_when_and_reports_that_something_changed(self) -> None:
        booking = a_booking()

        assert booking.cancel(now=NOW) is True
        assert booking.is_cancelled
        assert booking.cancelled_at == NOW

    def test_cancelling_again_changes_nothing_and_says_so(self) -> None:
        booking = a_booking()
        booking.cancel(now=NOW)

        assert booking.cancel(now=at(hours=5)) is False
        assert booking.cancelled_at == NOW

    def test_cannot_be_cancelled_once_the_rental_has_ended(self) -> None:
        booking = a_booking()

        with pytest.raises(ReservationNotCancellable):
            booking.cancel(now=PERIOD.end)

        assert not booking.is_cancelled
