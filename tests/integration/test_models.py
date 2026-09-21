"""The rules the database enforces on its own, whatever the application code does."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from accounts.models import User
from rentals.models import CarType, CarTypeName, Reservation
from tests.support import NOW, at, key

pytestmark = pytest.mark.django_db


def make_reservation(user: User, car_type: CarType, **overrides: object) -> Reservation:
    fields: dict[str, object] = {
        "user": user,
        "car_type": car_type,
        "start_at": at(days=1),
        "end_at": at(days=4),
        "idempotency_key": key("model"),
    }
    fields.update(overrides)
    return Reservation.objects.create(**fields)


class TestCarType:
    def test_each_type_exists_at_most_once(self, sedan: CarType) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            CarType.objects.create(name=CarTypeName.SEDAN, fleet_size=1)

    @pytest.mark.parametrize("name", ["convertible", "Sedan", ""])
    def test_a_name_outside_the_known_set_is_refused_by_the_database(self, name: str) -> None:
        # create() skips model validation, so this proves the constraint and not choices=.
        with pytest.raises(IntegrityError), transaction.atomic():
            CarType.objects.create(name=name, fleet_size=1)

    def test_is_shown_by_its_label_not_its_stored_value(self) -> None:
        assert str(CarType(name=CarTypeName.SUV, fleet_size=1)) == "SUV"

    def test_fleet_size_cannot_be_negative(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            CarType.objects.create(name=CarTypeName.VAN, fleet_size=-1)

    def test_cannot_be_deleted_while_reservations_refer_to_it(
        self, sedan: CarType, alice: User
    ) -> None:
        make_reservation(alice, sedan)
        with pytest.raises(ProtectedError):
            sedan.delete()


class TestReservation:
    def test_the_return_must_be_after_the_pick_up(self, sedan: CarType, alice: User) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            make_reservation(alice, sedan, start_at=at(days=4), end_at=at(days=4))

    def test_one_user_cannot_hold_two_reservations_under_one_idempotency_key(
        self, sedan: CarType, alice: User
    ) -> None:
        make_reservation(alice, sedan)
        with pytest.raises(IntegrityError), transaction.atomic():
            make_reservation(alice, sedan, start_at=at(days=10), end_at=at(days=12))

    def test_different_users_may_happen_to_use_the_same_idempotency_key(
        self, sedan: CarType, alice: User, bob: User
    ) -> None:
        make_reservation(alice, sedan)
        make_reservation(bob, sedan)
        assert Reservation.objects.count() == 2

    def test_a_cancelled_reservation_must_record_when_it_was_cancelled(
        self, sedan: CarType, alice: User
    ) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            make_reservation(alice, sedan, status=Reservation.Status.CANCELLED)

    def test_an_active_reservation_cannot_carry_a_cancellation_time(
        self, sedan: CarType, alice: User
    ) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            make_reservation(alice, sedan, cancelled_at=NOW)

    def test_a_user_with_reservations_cannot_be_deleted(self, sedan: CarType, alice: User) -> None:
        make_reservation(alice, sedan)
        with pytest.raises(ProtectedError):
            alice.delete()

    def test_exposes_its_dates_as_a_rental_period(self, sedan: CarType, alice: User) -> None:
        reservation = make_reservation(alice, sedan)
        assert reservation.period.duration == timedelta(days=3)
