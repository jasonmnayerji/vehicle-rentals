"""PostgresBookingLedger: the BookingLedger that production runs on.

This is the only module that opens transactions and takes locks. The domain decides whether
a booking is allowed; this makes that decision safe under concurrency and durable. It holds
no business rule, and is held to the same behavioural suite as the in-memory ledger.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone
from psycopg import errors as pg_errors

from . import models
from .domain import (
    Booking,
    BookingLedger,
    BookingStatus,
    CarType,
    CarTypeNotFound,
    DuplicateIdempotencyKey,
    FleetBusy,
    FleetSchedule,
    IdempotencyKey,
    LockedFleet,
    RentalPeriod,
)

logger = logging.getLogger(__name__)


def _to_booking(row: models.Reservation, car_type: CarType) -> Booking:
    return Booking(
        id=row.pk,
        customer_id=row.user_id,
        car_type=car_type,
        period=RentalPeriod(start=row.start_at, end=row.end_at),
        idempotency_key=IdempotencyKey(row.idempotency_key),
        status=BookingStatus(row.status),
        cancelled_at=row.cancelled_at,
    )


def _periods(rows: models.ReservationQuerySet) -> list[RentalPeriod]:
    # Only the two date columns of the rows that can matter, served by the partial index.
    return [
        RentalPeriod(start=start, end=end) for start, end in rows.values_list("start_at", "end_at")
    ]


class PostgresBookingLedger(BookingLedger):
    @contextmanager
    def exclusive(self, car_type: CarType) -> Iterator[LockedFleet]:
        with transaction.atomic():
            yield _LockedFleetRow(self._lock(car_type), car_type)

    def schedule(self, car_type: CarType, period: RentalPeriod) -> FleetSchedule:
        fleet = models.CarType.objects.filter(name=car_type.value).first()
        if fleet is None:
            raise CarTypeNotFound(f"there is no fleet of {car_type} cars")
        booked = models.Reservation.objects.active().for_car_type(fleet).overlapping(period)
        return FleetSchedule(fleet_size=fleet.fleet_size, booked=_periods(booked))

    def find_by_key(self, customer_id: int, key: IdempotencyKey) -> Booking | None:
        row = (
            models.Reservation.objects.filter(user_id=customer_id, idempotency_key=key.value)
            .select_related("car_type")
            .first()
        )
        return None if row is None else _to_booking(row, row.car_type.kind)

    @contextmanager
    def exclusive_booking(self, booking_id: int) -> Iterator[Booking | None]:
        with transaction.atomic():
            # of=("self",): lock the reservation only. The join is there to read the car type,
            # and locking that row too would queue every cancellation behind the bookings.
            row = (
                models.Reservation.objects.select_for_update(of=("self",))
                .select_related("car_type")
                .filter(pk=booking_id)
                .first()
            )
            yield None if row is None else _to_booking(row, row.car_type.kind)

    def save(self, booking: Booking) -> None:
        # The only state a booking can change after it is made. update() bypasses auto_now.
        models.Reservation.objects.filter(pk=booking.id).update(
            status=booking.status.value,
            cancelled_at=booking.cancelled_at,
            updated_at=timezone.now(),
        )

    def bookings_of(self, customer_id: int) -> Sequence[Booking]:
        rows = (
            models.Reservation.objects.filter(user_id=customer_id)
            .select_related("car_type")
            .order_by("-start_at")
        )
        return [_to_booking(row, row.car_type.kind) for row in rows]

    @staticmethod
    def _lock(car_type: CarType) -> models.CarType:
        # The lock is only a mutex at READ COMMITTED, which settings pin: every statement
        # after this one takes a fresh snapshot, so the availability read sees whatever the
        # previous holder committed. At REPEATABLE READ it would not, and the fleet overbooks.
        try:
            fleet = models.CarType.objects.select_for_update().filter(name=car_type.value).first()
        except OperationalError as error:
            if not isinstance(error.__cause__, pg_errors.LockNotAvailable):
                raise
            # lock_timeout expired. Nothing was written, so the caller may simply retry.
            logger.warning(
                "reservation rejected: timed out waiting for the fleet lock",
                extra={"car_type": car_type.value},
            )
            raise FleetBusy(
                "bookings for this car type are busy right now; please try again"
            ) from error
        if fleet is None:
            raise CarTypeNotFound(f"there is no fleet of {car_type} cars")
        return fleet


class _LockedFleetRow(LockedFleet):
    """Exists only inside ``PostgresBookingLedger.exclusive``, while the row lock is held."""

    def __init__(self, fleet: models.CarType, car_type: CarType) -> None:
        self._fleet = fleet
        self._car_type = car_type

    @property
    def size(self) -> int:
        return self._fleet.fleet_size

    def booked_during(self, period: RentalPeriod) -> Sequence[RentalPeriod]:
        return _periods(
            models.Reservation.objects.active().for_car_type(self._fleet).overlapping(period)
        )

    def add(self, *, customer_id: int, period: RentalPeriod, key: IdempotencyKey) -> Booking:
        try:
            # A savepoint of its own: after a failed INSERT PostgreSQL refuses everything
            # else in the transaction, and the caller still has to leave the block cleanly.
            with transaction.atomic():
                row = models.Reservation.objects.create(
                    user_id=customer_id,
                    car_type=self._fleet,
                    start_at=period.start,
                    end_at=period.end,
                    idempotency_key=key.value,
                )
        except IntegrityError as error:
            if _violated_constraint(error) == models.IDEMPOTENCY_CONSTRAINT:
                raise DuplicateIdempotencyKey from error
            raise  # anything else is a bug or bad data, never to be mistaken for a retry
        return _to_booking(row, self._car_type)


def _violated_constraint(error: IntegrityError) -> str | None:
    diagnostics = getattr(error.__cause__, "diag", None)
    return getattr(diagnostics, "constraint_name", None)
