"""Where the domain meets Django: the one place that decides what the desk is wired to.

Views, the admin and management commands get their ReservationDesk here and nowhere else, so
"which ledger, which clock, which policy" has a single answer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from django.utils import timezone

from .domain import BookingPolicy, Customer, ReservationDesk
from .ledger import PostgresBookingLedger

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser


def reservation_desk(
    *,
    now: Callable[[], datetime] = timezone.now,
    policy: BookingPolicy | None = None,
) -> ReservationDesk:
    return ReservationDesk(PostgresBookingLedger(), policy=policy, now=now)


def customer_from(user: AbstractBaseUser | AnonymousUser) -> Customer:
    """All the domain needs to know about whoever is signed in. No name, no email."""
    return Customer(id=user.pk, is_staff=bool(getattr(user, "is_staff", False)))
