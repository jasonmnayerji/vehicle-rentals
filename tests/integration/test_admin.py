"""The staff interface. Reservations are read-only there apart from cancelling."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.contrib.auth.models import Permission
from django.test import Client

from accounts.models import User
from rentals.models import CarType, Reservation
from rentals.services import reservation_desk
from tests.support import NOW, at

pytestmark = pytest.mark.django_db

CHANGELIST = "/admin/rentals/reservation/"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rentals.admin.get_desk", lambda: reservation_desk(now=lambda: NOW))


@pytest.fixture
def booking(alice: User, sedan: CarType, reserve: Callable[..., Reservation]) -> Reservation:
    return reserve(alice, sedan, at(days=1), at(days=3), "admin")


@pytest.fixture
def staff_client(client: Client, make_user: Callable[..., User]) -> Callable[..., Client]:
    def _login(*codenames: str) -> Client:
        user = make_user("desk", is_staff=True)
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
        client.force_login(user)
        return client

    return _login


def cancel_in_admin(client: Client, reservation: Reservation) -> str:
    response = client.post(
        CHANGELIST,
        {"action": "cancel_selected", "_selected_action": [reservation.pk]},
        follow=True,
    )
    return response.content.decode()


def test_staff_who_may_change_reservations_can_cancel_one_through_the_desk(
    staff_client: Callable[..., Client], booking: Reservation
) -> None:
    page = cancel_in_admin(staff_client("view_reservation", "change_reservation"), booking)

    booking.refresh_from_db()
    assert booking.status == Reservation.Status.CANCELLED
    assert booking.cancelled_at == NOW  # the desk's clock: it went through the desk
    assert "Cancelled: 1." in page


def test_staff_who_may_only_view_reservations_cannot_cancel_them(
    staff_client: Callable[..., Client], booking: Reservation
) -> None:
    cancel_in_admin(staff_client("view_reservation"), booking)

    booking.refresh_from_db()
    assert booking.status == Reservation.Status.ACTIVE


def test_a_reservation_the_desk_refuses_to_cancel_is_reported_not_forced(
    staff_client: Callable[..., Client], booking: Reservation, monkeypatch: pytest.MonkeyPatch
) -> None:
    after_it_ended = reservation_desk(now=lambda: at(days=30))
    monkeypatch.setattr("rentals.admin.get_desk", lambda: after_it_ended)

    page = cancel_in_admin(staff_client("view_reservation", "change_reservation"), booking)

    booking.refresh_from_db()
    assert booking.status == Reservation.Status.ACTIVE
    assert "the rental period has already ended" in page


def test_reservations_still_cannot_be_edited_or_deleted_in_the_admin(
    staff_client: Callable[..., Client], booking: Reservation
) -> None:
    client = staff_client("view_reservation", "change_reservation", "delete_reservation")

    client.post(CHANGELIST, {"action": "delete_selected", "_selected_action": [booking.pk]})
    client.post(f"{CHANGELIST}{booking.pk}/change/", {"status": "cancelled"})

    booking.refresh_from_db()
    assert booking.status == Reservation.Status.ACTIVE
