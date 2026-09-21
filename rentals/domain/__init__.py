"""Framework-free domain model. Nothing in this package may import Django."""

from .booking import Booking, BookingResult, BookingStatus, Customer, IdempotencyKey
from .car_type import CarType
from .desk import ReservationDesk
from .errors import (
    CarTypeNotFound,
    DomainError,
    FleetBusy,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    InvalidRentalPeriod,
    NoAvailability,
    ReservationNotCancellable,
    ReservationNotFound,
)
from .in_memory import InMemoryBookingLedger
from .ledger import BookingLedger, DuplicateIdempotencyKey, LockedFleet
from .period import RentalPeriod
from .policy import BookingPolicy
from .schedule import FleetSchedule

__all__ = [
    "Booking",
    "BookingLedger",
    "BookingPolicy",
    "BookingResult",
    "BookingStatus",
    "CarType",
    "CarTypeNotFound",
    "Customer",
    "DomainError",
    "DuplicateIdempotencyKey",
    "FleetBusy",
    "FleetSchedule",
    "IdempotencyConflict",
    "IdempotencyKey",
    "InMemoryBookingLedger",
    "InvalidIdempotencyKey",
    "InvalidRentalPeriod",
    "LockedFleet",
    "NoAvailability",
    "RentalPeriod",
    "ReservationDesk",
    "ReservationNotCancellable",
    "ReservationNotFound",
]
