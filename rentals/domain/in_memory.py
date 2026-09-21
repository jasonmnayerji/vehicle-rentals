"""InMemoryBookingLedger: the whole system with no infrastructure at all.

This is what lets unit tests prove the requirements. It is not a mock: it keeps real
bookings, enforces the same uniqueness rule as the database, and serialises bookings of one
car type with a real lock. It is held to the same behavioural suite as the PostgreSQL ledger,
so what passes here is known to hold there.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from itertools import count

from .booking import Booking, IdempotencyKey
from .car_type import CarType
from .errors import CarTypeNotFound
from .ledger import BookingLedger, DuplicateIdempotencyKey, LockedFleet
from .period import RentalPeriod
from .schedule import FleetSchedule


class InMemoryBookingLedger(BookingLedger):
    def __init__(self, fleet_sizes: Mapping[CarType, int]) -> None:
        if any(size < 0 for size in fleet_sizes.values()):
            raise ValueError("a fleet cannot have a negative number of cars")
        self._sizes = dict(fleet_sizes)
        self._bookings: dict[int, Booking] = {}
        self._ids = count(1)
        # One lock per car type, as the database has one lockable row per car type.
        self._fleet_locks = {car_type: threading.Lock() for car_type in self._sizes}
        # Guards the dictionary itself, and stands in for the database's unique index.
        self._table = threading.RLock()

    @contextmanager
    def exclusive(self, car_type: CarType) -> Iterator[LockedFleet]:
        with self._fleet_locks[self._known(car_type)]:
            yield _LockedMemoryFleet(self, car_type)

    def schedule(self, car_type: CarType, period: RentalPeriod) -> FleetSchedule:
        self._known(car_type)
        return FleetSchedule(self._sizes[car_type], self.booked_during(car_type, period))

    def find_by_key(self, customer_id: int, key: IdempotencyKey) -> Booking | None:
        with self._table:
            for booking in self._bookings.values():
                if booking.customer_id == customer_id and booking.idempotency_key == key:
                    return replace(booking)
        return None

    @contextmanager
    def exclusive_booking(self, booking_id: int) -> Iterator[Booking | None]:
        with self._table:
            stored = self._bookings.get(booking_id)
            # A copy, as a database row would be: changes count only once saved.
            yield None if stored is None else replace(stored)

    def save(self, booking: Booking) -> None:
        with self._table:
            self._bookings[booking.id] = replace(booking)

    def bookings_of(self, customer_id: int) -> Sequence[Booking]:
        with self._table:
            mine = [replace(b) for b in self._bookings.values() if b.customer_id == customer_id]
        return sorted(mine, key=lambda booking: booking.period.start, reverse=True)

    # -- shared with the locked fleet ------------------------------------------------------

    def fleet_size(self, car_type: CarType) -> int:
        return self._sizes[self._known(car_type)]

    def booked_during(self, car_type: CarType, period: RentalPeriod) -> list[RentalPeriod]:
        with self._table:
            return [
                booking.period
                for booking in self._bookings.values()
                if booking.car_type is car_type
                and not booking.is_cancelled
                and booking.period.overlaps(period)
            ]

    def add(
        self, car_type: CarType, customer_id: int, period: RentalPeriod, key: IdempotencyKey
    ) -> Booking:
        """Reached only through a LockedFleet, which is what holds the car type's lock."""
        with self._table:
            if self.find_by_key(customer_id, key) is not None:
                raise DuplicateIdempotencyKey
            booking = Booking(
                id=next(self._ids),
                customer_id=customer_id,
                car_type=car_type,
                period=period,
                idempotency_key=key,
            )
            self._bookings[booking.id] = booking
            return replace(booking)

    def _known(self, car_type: CarType) -> CarType:
        if car_type not in self._sizes:
            raise CarTypeNotFound(f"there is no fleet of {car_type} cars")
        return car_type


class _LockedMemoryFleet(LockedFleet):
    def __init__(self, ledger: InMemoryBookingLedger, car_type: CarType) -> None:
        self._ledger = ledger
        self._car_type = car_type

    @property
    def size(self) -> int:
        return self._ledger.fleet_size(self._car_type)

    def booked_during(self, period: RentalPeriod) -> Sequence[RentalPeriod]:
        return self._ledger.booked_during(self._car_type, period)

    def add(self, *, customer_id: int, period: RentalPeriod, key: IdempotencyKey) -> Booking:
        return self._ledger.add(self._car_type, customer_id, period, key)
