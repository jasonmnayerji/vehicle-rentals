"""The reservation desk against PostgreSQL: the same behaviour the unit tests prove in memory.

Everything here is inherited from tests/desk_contract.py. If a behaviour holds in memory but
not in the database (or the reverse), exactly one of the two suites fails, and the in-memory
ledger can no longer be trusted as evidence about production.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from accounts.models import User
from rentals import models
from rentals.domain import BookingLedger, CarType, Customer
from rentals.ledger import PostgresBookingLedger
from rentals.services import customer_from
from tests.desk_contract import MakeCustomer, MakeLedger, ReservationDeskContract

pytestmark = pytest.mark.django_db


class TestPostgresDesk(ReservationDeskContract):
    @pytest.fixture
    def make_ledger(self) -> MakeLedger:
        def _make(fleet: Mapping[CarType, int]) -> BookingLedger:
            for car_type, size in fleet.items():
                models.CarType.objects.create(name=car_type.value, fleet_size=size)
            return PostgresBookingLedger()

        return _make

    @pytest.fixture
    def make_customer(self, make_user: Callable[..., User]) -> MakeCustomer:
        def _make(name: str, *, is_staff: bool = False) -> Customer:
            return customer_from(make_user(name, is_staff=is_staff))

        return _make
