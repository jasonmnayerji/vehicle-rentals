"""Proof, not argument, that simultaneous bookings cannot overbook a fleet.

These tests need real transactions and real row locks, so they use ``transaction=True``
(each thread gets its own database connection and its own transaction) and only mean
anything on PostgreSQL.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections, transaction

from accounts.models import User
from rentals.domain import (
    CarType,
    DomainError,
    FleetBusy,
    IdempotencyConflict,
    NoAvailability,
    RentalPeriod,
)
from rentals.models import CarType as Fleet
from rentals.models import Reservation
from rentals.services import customer_from, reservation_desk
from tests.support import NOW, at, key

FIVE_DAYS = RentalPeriod(start=at(days=1), end=at(days=6))

pytestmark = pytest.mark.django_db(transaction=True)

CONTENDERS = 12


def run_simultaneously(attempts: list[Callable[[], object]]) -> list[object]:
    """Run every attempt on its own thread, released at the same instant.

    Returns each attempt's result, or the DomainError it raised. Anything else (a deadlock,
    an integrity error leaking out of the desk) propagates and fails the test.
    """
    barrier = threading.Barrier(len(attempts))

    def contend(attempt: Callable[[], object]) -> object:
        try:
            barrier.wait(timeout=10)
            return attempt()
        except DomainError as error:
            return error
        finally:
            connections.close_all()  # this thread's connection; pytest cannot clean it up

    with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
        return list(pool.map(contend, attempts))


def test_simultaneous_bookings_never_exceed_the_fleet(make_user: Callable[..., User]) -> None:
    suv = Fleet.objects.create(name=CarType.SUV.value, fleet_size=3)
    desk = reservation_desk(now=lambda: NOW)
    customers = [customer_from(make_user(f"customer-{n}")) for n in range(CONTENDERS)]

    outcomes = run_simultaneously(
        [
            lambda customer=customer: desk.reserve(
                customer=customer,
                car_type=CarType.SUV,
                period=FIVE_DAYS,
                idempotency_key=key(f"race-{customer.id}"),
            )
            for customer in customers
        ]
    )

    rejected = [o for o in outcomes if isinstance(o, NoAvailability)]
    assert len(outcomes) - len(rejected) == 3
    assert len(rejected) == CONTENDERS - 3
    assert Reservation.objects.active().for_car_type(suv).count() == 3


def test_simultaneous_retries_of_one_request_create_one_reservation(
    make_user: Callable[..., User],
) -> None:
    Fleet.objects.create(name=CarType.SUV.value, fleet_size=CONTENDERS)  # room for duplicates
    desk = reservation_desk(now=lambda: NOW)
    impatient = customer_from(make_user("impatient"))

    outcomes = run_simultaneously(
        [
            lambda: desk.reserve(
                customer=impatient,
                car_type=CarType.SUV,
                period=FIVE_DAYS,
                idempotency_key=key("double-click"),
            )
        ]
        * CONTENDERS
    )

    assert Reservation.objects.count() == 1
    only = Reservation.objects.get()
    assert [o.booking.id for o in outcomes] == [only.pk] * CONTENDERS  # type: ignore[attr-defined]
    assert sum(1 for o in outcomes if not o.replayed) == 1  # type: ignore[attr-defined]


def test_one_key_raced_across_two_car_types_books_exactly_one(
    make_user: Callable[..., User],
) -> None:
    # The two requests lock *different* car type rows, so neither waits for the other and
    # both get past the application-level lookup. Only the unique constraint stands between
    # this and a double booking; the loser must get a clean domain error, not a 500.
    Fleet.objects.create(name=CarType.SUV.value, fleet_size=5)
    Fleet.objects.create(name=CarType.VAN.value, fleet_size=5)
    desk = reservation_desk(now=lambda: NOW)
    confused = customer_from(make_user("confused"))

    def attempt(car_type: CarType) -> Callable[[], object]:
        return lambda: desk.reserve(
            customer=confused,
            car_type=car_type,
            period=FIVE_DAYS,
            idempotency_key=key("one-key-two-types"),
        )

    for _ in range(5):  # a race: repeat to give it a fair chance of interleaving
        Reservation.objects.all().delete()
        outcomes = run_simultaneously([attempt(CarType.SUV), attempt(CarType.VAN)])

        assert Reservation.objects.count() == 1
        assert sum(isinstance(o, IdempotencyConflict) for o in outcomes) == 1


def test_bookings_run_at_read_committed_whatever_the_server_default() -> None:
    # The row lock is only a mutex at READ COMMITTED. At REPEATABLE READ the availability
    # read uses a snapshot taken before the lock was granted, misses the previous holder's
    # insert, and test_simultaneous_bookings_never_exceed_the_fleet books 12 cars out of 3.
    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        assert cursor.fetchone() == ("read committed",)


def test_a_booking_stuck_behind_the_fleet_lock_gives_up_with_a_retryable_error(
    make_user: Callable[..., User],
) -> None:
    suv = Fleet.objects.create(name=CarType.SUV.value, fleet_size=3)
    desk = reservation_desk(now=lambda: NOW)
    waiting = customer_from(make_user("waiting"))
    lock_held, booking_refused = threading.Event(), threading.Event()

    def hold_the_lock() -> None:
        try:
            with transaction.atomic():
                Fleet.objects.select_for_update().get(pk=suv.pk)
                lock_held.set()
                booking_refused.wait(timeout=10)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_the_lock)
        assert lock_held.wait(timeout=10)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '200ms'")  # not the configured seconds
            with pytest.raises(FleetBusy):
                desk.reserve(
                    customer=waiting,
                    car_type=CarType.SUV,
                    period=FIVE_DAYS,
                    idempotency_key=key("stuck"),
                )
        finally:
            booking_refused.set()
            holder.result(timeout=10)
            connection.close()  # drop the session-level lock_timeout with the connection

    assert Reservation.objects.count() == 0
    # Nothing was written and the transaction was rolled back cleanly, so a retry succeeds.
    assert not desk.reserve(
        customer=waiting,
        car_type=CarType.SUV,
        period=FIVE_DAYS,
        idempotency_key=key("stuck"),
    ).replayed
