"""The reservation desk against the in-memory ledger: the whole system, no infrastructure."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from itertools import count

import pytest

from rentals.domain import (
    BookingLedger,
    CarType,
    Customer,
    DomainError,
    IdempotencyConflict,
    InMemoryBookingLedger,
    NoAvailability,
    RentalPeriod,
    ReservationDesk,
)
from tests.desk_contract import MakeCustomer, MakeLedger, ReservationDeskContract, days
from tests.support import NOW, key

CONTENDERS = 12


class TestInMemoryDesk(ReservationDeskContract):
    @pytest.fixture
    def make_ledger(self) -> MakeLedger:
        def _make(fleet: Mapping[CarType, int]) -> BookingLedger:
            return InMemoryBookingLedger(fleet)

        return _make

    @pytest.fixture
    def make_customer(self) -> MakeCustomer:
        ids = count(1)

        def _make(name: str, *, is_staff: bool = False) -> Customer:
            return Customer(id=next(ids), is_staff=is_staff)

        return _make


class UnhurriedLedger(InMemoryBookingLedger):
    """Pauses between reading the schedule and writing the booking, where a real system does
    its I/O.

    In memory that gap is a few bytecodes wide, too narrow for threads to race through, so a
    test without this passes whether or not the fleet lock exists and proves nothing. With
    it, removing the lock lets every contender read "a car is free" during someone else's
    pause, and the fleet overbooks. (Checked by removing the lock: 12 bookings out of 3.)
    """

    def booked_during(self, car_type: CarType, period: RentalPeriod) -> list[RentalPeriod]:
        booked = super().booked_during(car_type, period)
        time.sleep(0.02)
        return booked


def run_simultaneously(attempts: list[Callable[[], object]]) -> list[object]:
    """Run every attempt on its own thread, released at the same instant."""
    barrier = threading.Barrier(len(attempts))

    def contend(attempt: Callable[[], object]) -> object:
        barrier.wait(timeout=10)
        try:
            return attempt()
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
        return list(pool.map(contend, attempts))


def test_simultaneous_bookings_never_exceed_the_fleet() -> None:
    desk = ReservationDesk(UnhurriedLedger({CarType.SUV: 3}), now=lambda: NOW)

    outcomes = run_simultaneously(
        [
            lambda n=n: desk.reserve(
                customer=Customer(id=n),
                car_type=CarType.SUV,
                period=days(1, 5),
                idempotency_key=key(f"race-{n}"),
            )
            for n in range(CONTENDERS)
        ]
    )

    rejected = [o for o in outcomes if isinstance(o, NoAvailability)]
    assert len(outcomes) - len(rejected) == 3
    assert len(rejected) == CONTENDERS - 3
    assert desk.availability(car_type=CarType.SUV, period=days(1, 5)) == 0


def test_one_key_raced_across_two_car_types_books_exactly_one() -> None:
    # The two requests hold *different* fleet locks, so both get past the desk's lookup. The
    # ledger's uniqueness rule is all that stands between this and a double booking.
    confused = Customer(id=1)

    for _ in range(25):  # a race: repeat to give it a fair chance of interleaving
        desk = ReservationDesk(
            InMemoryBookingLedger({CarType.SUV: 5, CarType.VAN: 5}), now=lambda: NOW
        )
        outcomes = run_simultaneously(
            [
                lambda desk=desk, car_type=car_type: desk.reserve(
                    customer=confused,
                    car_type=car_type,
                    period=days(1, 5),
                    idempotency_key=key("one-key-two-types"),
                )
                for car_type in (CarType.SUV, CarType.VAN)
            ]
        )

        assert len(desk.reservations_for(confused)) == 1
        assert sum(isinstance(o, IdempotencyConflict) for o in outcomes) == 1


def test_a_fleet_cannot_have_a_negative_number_of_cars() -> None:
    with pytest.raises(ValueError, match="negative"):
        InMemoryBookingLedger({CarType.VAN: -1})


def test_a_ledger_that_leaves_part_of_the_contract_out_cannot_be_created() -> None:
    class HalfALedger(BookingLedger):
        pass

    with pytest.raises(TypeError, match="abstract"):
        HalfALedger()  # type: ignore[abstract]
