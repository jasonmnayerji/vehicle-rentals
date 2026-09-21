"""The brief, requirement by requirement, as executable statements.

    3. The system should allow reservation of a car of a given type at a desired date and
       time for a given number of days.
    4. There are 3 types of cars (sedan, SUV and van).
    5. The number of cars of each type is limited.
    6. Use unit tests to prove the system satisfies the requirements.

These run against the real ReservationDesk with its in-memory ledger: the same rules
production runs, with no database. That ledger is held to the same behavioural suite as the
PostgreSQL one (tests/desk_contract.py), which is what entitles a unit test to call itself
proof. Run them with nothing installed but Python and pytest:

    pytest -c tests/unit/pytest.ini tests/unit
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rentals.domain import (
    CarType,
    CarTypeNotFound,
    Customer,
    InMemoryBookingLedger,
    NoAvailability,
    RentalPeriod,
    ReservationDesk,
)
from tests.support import NOW, key

FLEET = {CarType.SEDAN: 3, CarType.SUV: 2, CarType.VAN: 1}
ALICE, BOB = Customer(id=1), Customer(id=2)

MONDAY_10AM = datetime(2026, 1, 12, 10, 0, tzinfo=UTC)
WEDNESDAY_10AM = datetime(2026, 1, 14, 10, 0, tzinfo=UTC)
THURSDAY_10AM = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)


@pytest.fixture
def desk() -> ReservationDesk:
    return ReservationDesk(InMemoryBookingLedger(FLEET), now=lambda: NOW)


def reserve(
    desk: ReservationDesk,
    car_type: CarType,
    pick_up: datetime,
    days: int,
    attempt: str,
    customer: Customer = ALICE,
) -> RentalPeriod:
    return desk.reserve(
        customer=customer,
        car_type=car_type,
        period=RentalPeriod.for_days(pick_up, days),
        idempotency_key=key(attempt),
    ).booking.period


class TestRequirement3ReserveATypeAtADateAndTimeForANumberOfDays:
    def test_a_customer_reserves_a_car_of_a_type_from_a_date_and_time_for_some_days(
        self, desk: ReservationDesk
    ) -> None:
        result = desk.reserve(
            customer=ALICE,
            car_type=CarType.SUV,
            period=RentalPeriod.for_days(MONDAY_10AM, 3),
            idempotency_key=key("monday"),
        )

        booking = result.booking
        assert booking.car_type is CarType.SUV
        assert booking.period.start == MONDAY_10AM
        assert booking.period.end == THURSDAY_10AM  # three days later, to the minute
        assert booking.customer_id == ALICE.id
        assert desk.reservations_for(ALICE) == [booking]

    def test_the_time_of_day_matters_not_just_the_date(self, desk: ReservationDesk) -> None:
        # The only van goes out Monday 10:00 for two days, so it is back Wednesday 10:00.
        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "first")

        with pytest.raises(NoAvailability):
            reserve(desk, CarType.VAN, datetime(2026, 1, 14, 9, 59, tzinfo=UTC), 1, "early")

        reserve(desk, CarType.VAN, WEDNESDAY_10AM, 1, "on-the-dot")

    @pytest.mark.parametrize("days", [1, 7, 90])
    def test_any_whole_number_of_days_the_policy_allows_can_be_reserved(
        self, desk: ReservationDesk, days: int
    ) -> None:
        period = reserve(desk, CarType.SEDAN, MONDAY_10AM, days, f"{days}-days")

        assert (period.end - period.start).days == days

    def test_a_reservation_holds_a_car_for_every_day_of_it(self, desk: ReservationDesk) -> None:
        reserve(desk, CarType.VAN, MONDAY_10AM, 3, "held")

        for day_of_rental in (12, 13, 14):
            with pytest.raises(NoAvailability):
                reserve(
                    desk,
                    CarType.VAN,
                    datetime(2026, 1, day_of_rental, 12, 0, tzinfo=UTC),
                    1,
                    f"clash-on-{day_of_rental}",
                )


class TestRequirement4ThereAreThreeTypesOfCar:
    def test_the_types_are_sedan_suv_and_van_and_nothing_else(self) -> None:
        assert [car_type.label for car_type in CarType] == ["Sedan", "SUV", "Van"]

    @pytest.mark.parametrize("car_type", list(CarType))
    def test_each_type_can_be_reserved(self, desk: ReservationDesk, car_type: CarType) -> None:
        reserve(desk, car_type, MONDAY_10AM, 2, f"a-{car_type.value}")

        (booking,) = desk.reservations_for(ALICE)
        assert booking.car_type is car_type

    def test_there_is_no_way_to_name_a_fourth_type(self) -> None:
        with pytest.raises(ValueError, match="convertible"):
            CarType("convertible")

    def test_each_type_has_its_own_cars(self, desk: ReservationDesk) -> None:
        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "the-only-van")

        # The vans are gone for those days. The sedans and SUVs are untouched.
        assert desk.availability(car_type=CarType.VAN, period=_two_days()) == 0
        assert desk.availability(car_type=CarType.SEDAN, period=_two_days()) == 3
        assert desk.availability(car_type=CarType.SUV, period=_two_days()) == 2


class TestRequirement5TheNumberOfCarsOfEachTypeIsLimited:
    @pytest.mark.parametrize(("car_type", "fleet_size"), list(FLEET.items()))
    def test_a_type_can_be_reserved_until_its_cars_run_out_and_not_once_more(
        self, desk: ReservationDesk, car_type: CarType, fleet_size: int
    ) -> None:
        for n in range(fleet_size):
            reserve(desk, car_type, MONDAY_10AM, 2, f"car-{n}")

        with pytest.raises(NoAvailability):
            reserve(desk, car_type, MONDAY_10AM, 2, "one-too-many", customer=BOB)

        assert desk.availability(car_type=car_type, period=_two_days()) == 0

    def test_the_limit_applies_to_days_that_overlap_not_to_bookings_ever_made(
        self, desk: ReservationDesk
    ) -> None:
        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "this-week")

        reserve(desk, CarType.VAN, datetime(2026, 1, 19, 10, 0, tzinfo=UTC), 2, "next-week")

    def test_a_car_is_free_again_once_its_reservation_is_cancelled(
        self, desk: ReservationDesk
    ) -> None:
        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "changed-my-mind")
        (mine,) = desk.reservations_for(ALICE)

        desk.cancel(booking_id=mine.id, actor=ALICE)

        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "lucky", customer=BOB)

    def test_a_retried_request_does_not_use_up_a_second_car(self, desk: ReservationDesk) -> None:
        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "double-click")
        reserve(desk, CarType.VAN, MONDAY_10AM, 2, "double-click")

        assert len(desk.reservations_for(ALICE)) == 1

    def test_a_business_with_no_cars_of_a_type_cannot_rent_one(self) -> None:
        sedans_only = ReservationDesk(InMemoryBookingLedger({CarType.SEDAN: 3}), now=lambda: NOW)

        with pytest.raises(CarTypeNotFound):
            reserve(sedans_only, CarType.VAN, MONDAY_10AM, 2, "no-vans-here")


def _two_days() -> RentalPeriod:
    return RentalPeriod.for_days(MONDAY_10AM, 2)
