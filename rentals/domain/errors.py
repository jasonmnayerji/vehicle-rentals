"""Domain errors.

Every business-rule failure is a subclass of DomainError, so callers (a future API layer,
a management command, a test) can translate them to their own vocabulary in one place
without catching framework or database exceptions.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all business-rule violations."""


class InvalidRentalPeriod(DomainError):
    """The requested period is structurally or commercially invalid."""


class CarTypeNotFound(DomainError):
    """The requested car type does not exist."""


class NoAvailability(DomainError):
    """Every car of the requested type is already booked at some point in the period."""


class FleetBusy(DomainError):
    """Bookings for this car type are queued too deep to answer in time. Safe to retry."""


class IdempotencyConflict(DomainError):
    """An idempotency key was reused with different booking parameters."""


class ReservationNotFound(DomainError):
    """The reservation does not exist."""


class InvalidIdempotencyKey(DomainError):
    """The idempotency key is missing or malformed."""


class ReservationNotCancellable(DomainError):
    """The reservation can no longer be cancelled, e.g. the rental has already ended."""
