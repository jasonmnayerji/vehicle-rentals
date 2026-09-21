from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from accounts.models import User
from rentals.domain import RentalPeriod, ReservationDesk
from rentals.models import CarType, CarTypeName, Reservation
from rentals.services import customer_from, reservation_desk
from tests.support import NOW


@pytest.fixture(autouse=True)
def static_files_without_a_manifest(settings: object) -> None:
    # Production resolves {% static %} through the manifest that collectstatic writes at image
    # build. A page that uses it (the admin) would pass inside the image and fail anywhere
    # else, so the suite would depend on a build artefact. Plain storage needs none.
    settings.STORAGES = {  # type: ignore[attr-defined]
        **settings.STORAGES,  # type: ignore[attr-defined]
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def desk() -> ReservationDesk:
    return reservation_desk(now=lambda: NOW)


@pytest.fixture
def reserve(desk: ReservationDesk) -> Callable[..., Reservation]:
    """Book through the real desk, and hand back the stored row for the test to inspect."""

    def _reserve(
        user: User, car_type: CarType, start: datetime, end: datetime, label: str
    ) -> Reservation:
        booking = desk.reserve(
            customer=customer_from(user),
            car_type=car_type.kind,
            period=RentalPeriod(start=start, end=end),
            idempotency_key=f"test-key-{label}",
        ).booking
        return Reservation.objects.get(pk=booking.id)

    return _reserve


@pytest.fixture
def make_user(db: None) -> Callable[..., User]:
    def _make(username: str, **extra: object) -> User:
        # password=None stores an unusable password and skips the (deliberately slow) hasher.
        return User.objects.create_user(username=username, password=None, **extra)

    return _make


@pytest.fixture
def alice(make_user: Callable[..., User]) -> User:
    return make_user("alice")


@pytest.fixture
def bob(make_user: Callable[..., User]) -> User:
    return make_user("bob")


@pytest.fixture
def staff(make_user: Callable[..., User]) -> User:
    return make_user("staff", is_staff=True)


@pytest.fixture
def sedan(db: None) -> CarType:
    return CarType.objects.create(name=CarTypeName.SEDAN, fleet_size=2)
