"""Booking (an entity) and the small value objects that travel with it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .car_type import CarType
from .errors import InvalidIdempotencyKey, ReservationNotCancellable
from .period import RentalPeriod

# Conservative on purpose: the key is client-supplied and ends up in a unique index.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """A client-chosen name for one booking *attempt*, carried unchanged across retries.

    A value object that validates itself, like RentalPeriod: a malformed key cannot exist,
    so nothing downstream has to remember to check it.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _KEY_PATTERN.fullmatch(self.value):
            raise InvalidIdempotencyKey(
                "idempotency key must be 8-64 characters of letters, digits, '-' or '_'"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Customer:
    """Whoever is asking: just enough to decide what they may do. No name, no email."""

    id: int
    is_staff: bool = False


class BookingStatus(Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


@dataclass(eq=False, slots=True)
class Booking:
    """One reservation of one car of a type, for a period.

    An entity, not a value object: it has an identity and a lifecycle. Two bookings with the
    same customer, type and period are still two bookings, and a booking that has been
    cancelled is still the same booking. Equality is therefore by ``id`` alone, where
    RentalPeriod and IdempotencyKey compare by value.

    The rules about its own state live here, next to the state they guard.
    """

    id: int
    customer_id: int
    car_type: CarType
    period: RentalPeriod
    idempotency_key: IdempotencyKey
    status: BookingStatus = BookingStatus.ACTIVE
    cancelled_at: datetime | None = None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Booking) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    @property
    def is_cancelled(self) -> bool:
        return self.status is BookingStatus.CANCELLED

    def is_for(self, car_type: CarType, period: RentalPeriod) -> bool:
        """True if this booking was made with exactly these parameters."""
        return self.car_type is car_type and self.period == period

    def may_be_managed_by(self, actor: Customer) -> bool:
        return actor.is_staff or actor.id == self.customer_id

    def cancel(self, *, now: datetime) -> bool:
        """Apply the cancellation. Returns False if it was already cancelled, so cancelling
        twice is a harmless no-op rather than an error."""
        if self.is_cancelled:
            return False
        if self.period.end <= now:
            raise ReservationNotCancellable("the rental period has already ended")
        self.status = BookingStatus.CANCELLED
        self.cancelled_at = now
        return True


@dataclass(frozen=True, slots=True)
class BookingResult:
    booking: Booking
    replayed: bool
    """True when this call matched an earlier one and no new booking was created."""
