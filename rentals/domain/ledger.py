"""BookingLedger: what the reservation desk needs from wherever bookings are kept.

The desk decides *whether* a booking is allowed. A ledger makes that decision safe under
concurrency and durable. The desk depends on this abstraction and never on a database, so
the same rules run unchanged against PostgreSQL in production and against plain memory in
the unit tests. Both implementations are held to one behavioural test suite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractContextManager

from .booking import Booking, IdempotencyKey
from .car_type import CarType
from .period import RentalPeriod
from .schedule import FleetSchedule


class DuplicateIdempotencyKey(Exception):
    """The customer already has a booking under this key.

    Not a DomainError: it passes from ledger to desk, which turns it into a replay or an
    IdempotencyConflict. No caller of the desk ever sees it.
    """


class LockedFleet(ABC):
    """Exclusive access to the bookings of one car type.

    Only ever handed out by ``BookingLedger.exclusive``. That is the point of the type:
    ``add`` exists nowhere else, so a booking cannot be written without holding the lock
    that makes check-then-insert safe. The mistake is unrepresentable, not just discouraged.
    """

    @property
    @abstractmethod
    def size(self) -> int:
        """How many cars of this type the business owns."""

    @abstractmethod
    def booked_during(self, period: RentalPeriod) -> Sequence[RentalPeriod]:
        """The periods of active bookings that share any moment with ``period``."""

    @abstractmethod
    def add(self, *, customer_id: int, period: RentalPeriod, key: IdempotencyKey) -> Booking:
        """Record a new active booking. Raises DuplicateIdempotencyKey."""

    def schedule(self, period: RentalPeriod) -> FleetSchedule:
        return FleetSchedule(fleet_size=self.size, booked=self.booked_during(period))


class BookingLedger(ABC):
    @abstractmethod
    def exclusive(self, car_type: CarType) -> AbstractContextManager[LockedFleet]:
        """Everything inside the block happens atomically, and alone for this car type.

        Bookings of other types are not held up. Raises CarTypeNotFound if the business has
        no fleet of this type, and FleetBusy if exclusive access cannot be had in time.
        """

    @abstractmethod
    def schedule(self, car_type: CarType, period: RentalPeriod) -> FleetSchedule:
        """An unlocked look at the fleet. Advisory: it may be stale the moment it returns."""

    @abstractmethod
    def find_by_key(self, customer_id: int, key: IdempotencyKey) -> Booking | None:
        """The customer's booking under this key, if there is one."""

    @abstractmethod
    def exclusive_booking(self, booking_id: int) -> AbstractContextManager[Booking | None]:
        """Atomic, exclusive access to one booking, or None if there is no such booking."""

    @abstractmethod
    def save(self, booking: Booking) -> None:
        """Persist a state change made to a booking obtained from ``exclusive_booking``."""

    @abstractmethod
    def bookings_of(self, customer_id: int) -> Sequence[Booking]:
        """A customer's bookings, latest pick-up first, cancelled ones included."""
