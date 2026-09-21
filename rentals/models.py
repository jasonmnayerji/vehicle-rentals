from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from . import domain
from .domain import RentalPeriod

IDEMPOTENCY_CONSTRAINT = "reservation_idempotency_key_unique_per_user"

# Built from the domain's CarType, never restated: the set of car types is a business fact
# and has one home. This is only its database spelling (choices and the check constraint).
CarTypeName = models.TextChoices(  # type: ignore[misc]
    "CarTypeName", [(kind.name, (kind.value, kind.label)) for kind in domain.CarType]
)


class CarType(models.Model):
    """A class of interchangeable cars and how many of them the fleet has.

    Car types are rows, not subclasses. Sedan, SUV and van differ only in data, so an
    inheritance hierarchy would add classes without adding behaviour. The row is also what
    holds the fleet size and what a booking locks, so it exists whatever the names are.

    The set of names is closed on purpose: the brief fixes it at three, so a typo cannot
    create a fourth. The price is that a new type needs a code change and a migration.
    """

    name = models.CharField(max_length=16, choices=CarTypeName.choices, unique=True)
    fleet_size = models.PositiveIntegerField(
        help_text="Number of cars of this type available to rent."
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            # choices= only guards forms; this guards every other way of writing the row.
            models.CheckConstraint(
                condition=Q(name__in=CarTypeName.values), name="car_type_name_is_known"
            ),
        ]

    def __str__(self) -> str:
        return self.get_name_display()

    @property
    def kind(self) -> domain.CarType:
        """This row's car type, as the domain knows it."""
        return domain.CarType(self.name)


class ReservationQuerySet(models.QuerySet["Reservation"]):
    def active(self) -> ReservationQuerySet:
        return self.filter(status=Reservation.Status.ACTIVE)

    def for_car_type(self, car_type: CarType) -> ReservationQuerySet:
        return self.filter(car_type=car_type)

    def overlapping(self, period: RentalPeriod) -> ReservationQuerySet:
        """Reservations sharing any moment with ``period`` (half-open ranges)."""
        return self.filter(start_at__lt=period.end, end_at__gt=period.start)


class Reservation(models.Model):
    """How a booking is stored. Persistence only.

    The rules (who may cancel, until when, what counts as the same request) belong to
    ``domain.Booking``. PostgresBookingLedger maps between the two.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    # PROTECT, never CASCADE: a booking is a business record. Deleting a car type or a user
    # must not silently erase the history of who rented what.
    car_type = models.ForeignKey(CarType, on_delete=models.PROTECT, related_name="reservations")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reservations"
    )
    start_at = models.DateTimeField(help_text="Pick-up time (inclusive).")
    end_at = models.DateTimeField(
        help_text="Return time (exclusive): the car is free again from this moment."
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    idempotency_key = models.CharField(
        max_length=64,
        help_text="Client-generated key identifying one booking attempt across retries.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = ReservationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")),
                name="reservation_end_after_start",
            ),
            # The last line of defence for idempotency: even if two retries race past the
            # application-level lookup, the database admits only one row per (user, key).
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                name=IDEMPOTENCY_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=Q(status="cancelled", cancelled_at__isnull=False)
                | Q(status="active", cancelled_at__isnull=True),
                name="reservation_cancelled_at_matches_status",
            ),
        ]
        indexes = [
            # Serves the availability lookup. Partial: cancelled rows never take part in it,
            # so they should not bloat the index.
            models.Index(
                fields=["car_type", "start_at", "end_at"],
                condition=Q(status="active"),
                name="reservation_active_lookup",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"#{self.pk} {self.car_type_id} "
            f"{self.start_at:%Y-%m-%d %H:%M}..{self.end_at:%Y-%m-%d %H:%M} ({self.status})"
        )

    @property
    def period(self) -> RentalPeriod:
        return RentalPeriod(start=self.start_at, end=self.end_at)
