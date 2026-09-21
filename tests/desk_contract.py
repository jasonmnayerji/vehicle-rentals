"""What a reservation desk must do, whatever it keeps its bookings in.

One behavioural suite, run twice: in tests/unit against the in-memory ledger (no database, no
configuration), and in tests/integration against PostgreSQL. That is what lets the unit tests
*prove* the requirements rather than merely exercise a stand-in: the stand-in is held to
exactly the behaviour of the real thing.

A subclass supplies two fixtures:

    make_ledger(fleet)            a BookingLedger holding a fleet of {CarType: size}
    make_customer(name, **extra)  a Customer the ledger will accept bookings from
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rentals.domain import (
    Booking,
    BookingLedger,
    BookingPolicy,
    CarType,
    CarTypeNotFound,
    Customer,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    InvalidRentalPeriod,
    NoAvailability,
    RentalPeriod,
    ReservationDesk,
    ReservationNotCancellable,
    ReservationNotFound,
)
from tests.support import NOW, at, key

MakeLedger = Callable[[Mapping[CarType, int]], BookingLedger]
MakeCustomer = Callable[..., Customer]

DEMO_FLEET = {CarType.SEDAN: 2, CarType.SUV: 1}


def days(start: float, length: float) -> RentalPeriod:
    """From ``start`` days after NOW, for ``length`` days. Fractions give hours."""
    return RentalPeriod(start=at(days=start), end=at(days=start + length))


def book(
    desk: ReservationDesk,
    customer: Customer,
    car_type: CarType,
    start: float,
    length: float,
    label: str,
) -> Booking:
    return desk.reserve(
        customer=customer,
        car_type=car_type,
        period=days(start, length),
        idempotency_key=key(label),
    ).booking


class ReservationDeskContract:
    # -- supplied by the subclass ----------------------------------------------------------

    @pytest.fixture
    def make_ledger(self) -> MakeLedger:
        raise NotImplementedError

    @pytest.fixture
    def make_customer(self) -> MakeCustomer:
        raise NotImplementedError

    # -- shared ----------------------------------------------------------------------------

    @pytest.fixture
    def ledger(self, make_ledger: MakeLedger) -> BookingLedger:
        return make_ledger(DEMO_FLEET)

    @pytest.fixture
    def desk(self, ledger: BookingLedger) -> ReservationDesk:
        return ReservationDesk(ledger, now=lambda: NOW)

    @pytest.fixture
    def alice(self, make_customer: MakeCustomer) -> Customer:
        return make_customer("alice")

    @pytest.fixture
    def bob(self, make_customer: MakeCustomer) -> Customer:
        return make_customer("bob")

    @pytest.fixture
    def staff(self, make_customer: MakeCustomer) -> Customer:
        return make_customer("staff", is_staff=True)

    # -- reserving -------------------------------------------------------------------------

    def test_creates_an_active_booking_for_the_requested_type_and_period(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        result = desk.reserve(
            customer=alice,
            car_type=CarType.SEDAN,
            period=days(1, 3),
            idempotency_key=key("a"),
        )

        assert result.replayed is False
        assert desk.reservations_for(alice) == [result.booking]
        (stored,) = desk.reservations_for(alice)
        assert stored.customer_id == alice.id
        assert stored.car_type is CarType.SEDAN
        assert stored.period == days(1, 3)
        assert not stored.is_cancelled
        assert stored.cancelled_at is None

    def test_accepts_bookings_until_the_fleet_is_exhausted_then_rejects(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SEDAN, 1, 3, "a")
        book(desk, alice, CarType.SEDAN, 2, 3, "b")

        with pytest.raises(NoAvailability):
            book(desk, alice, CarType.SEDAN, 3, 1, "c")

        assert len(desk.reservations_for(alice)) == 2

    def test_accepts_a_booking_that_overlaps_many_others_which_never_coincide(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        # Fleet of 2. Days 1-3 and 5-7 both overlap 2-6, yet never overlap each other, so
        # only one car is ever out. Counting overlaps would refuse this; peak usage does not.
        book(desk, alice, CarType.SEDAN, 1, 2, "a")
        book(desk, alice, CarType.SEDAN, 5, 2, "b")

        book(desk, alice, CarType.SEDAN, 2, 4, "c")

        with pytest.raises(NoAvailability):
            book(desk, alice, CarType.SEDAN, 2, 4, "d")

    def test_a_car_can_be_booked_from_the_moment_the_previous_rental_ends(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SUV, 1, 2, "a")

        book(desk, alice, CarType.SUV, 3, 2, "back-to-back")

        with pytest.raises(NoAvailability):
            desk.reserve(
                customer=alice,
                car_type=CarType.SUV,
                period=RentalPeriod(
                    start=at(days=1) - timedelta(hours=2), end=at(days=1, hours=0.02)
                ),
                idempotency_key=key("returns-a-minute-late"),
            )

    def test_car_types_have_independent_availability(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SUV, 1, 3, "the-only-suv")

        book(desk, alice, CarType.SEDAN, 1, 3, "a-sedan-is-still-free")

    def test_a_cancelled_booking_gives_its_car_back(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        held = book(desk, alice, CarType.SUV, 1, 3, "a")
        desk.cancel(booking_id=held.id, actor=alice)

        book(desk, alice, CarType.SUV, 1, 3, "b")

    def test_rejects_a_car_type_the_business_has_no_fleet_of(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        with pytest.raises(CarTypeNotFound):
            book(desk, alice, CarType.VAN, 1, 3, "a")

    @pytest.mark.parametrize(
        ("start", "length", "reason"),
        [
            pytest.param(-2, 3, "in the past", id="starts in the past"),
            pytest.param(1, 0.01, "at least 1 hour", id="too short"),
            pytest.param(1, 91, "cannot exceed 90 days", id="too long"),
            pytest.param(400, 2, "more than 365 days ahead", id="too far ahead"),
        ],
    )
    def test_rejects_a_period_the_policy_forbids_without_creating_anything(
        self, desk: ReservationDesk, alice: Customer, start: float, length: float, reason: str
    ) -> None:
        with pytest.raises(InvalidRentalPeriod, match=reason):
            book(desk, alice, CarType.SEDAN, start, length, "a")

        assert desk.reservations_for(alice) == []

    def test_a_walk_up_customer_can_book_starting_now(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SEDAN, 0, 1, "walk-up")

    def test_the_limits_are_the_policy_handed_to_the_desk(
        self, ledger: BookingLedger, alice: Customer
    ) -> None:
        weekend_only = ReservationDesk(
            ledger, policy=BookingPolicy(max_duration=timedelta(days=2)), now=lambda: NOW
        )

        with pytest.raises(InvalidRentalPeriod, match="cannot exceed 2 days"):
            book(weekend_only, alice, CarType.SEDAN, 1, 3, "a")

    def test_availability_is_judged_on_instants_whatever_zone_the_customer_used(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        tokyo, new_york = ZoneInfo("Asia/Tokyo"), ZoneInfo("America/New_York")
        # The only SUV, 12 Jan 09:00-17:00 in Tokyo, which is 00:00-08:00 UTC.
        desk.reserve(
            customer=alice,
            car_type=CarType.SUV,
            period=RentalPeriod(
                start=datetime(2026, 1, 12, 9, 0, tzinfo=tokyo),
                end=datetime(2026, 1, 12, 17, 0, tzinfo=tokyo),
            ),
            idempotency_key=key("tokyo"),
        )

        # 11 Jan 20:00 in New York *is* 12 Jan 01:00 UTC: the same hours, another wall clock.
        with pytest.raises(NoAvailability):
            desk.reserve(
                customer=alice,
                car_type=CarType.SUV,
                period=RentalPeriod(
                    start=datetime(2026, 1, 11, 20, 0, tzinfo=new_york),
                    end=datetime(2026, 1, 11, 22, 0, tzinfo=new_york),
                ),
                idempotency_key=key("new-york"),
            )

    # -- idempotency -----------------------------------------------------------------------

    def test_a_retried_request_returns_the_original_booking(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        first = desk.reserve(
            customer=alice, car_type=CarType.SEDAN, period=days(1, 3), idempotency_key=key("a")
        )
        second = desk.reserve(
            customer=alice, car_type=CarType.SEDAN, period=days(1, 3), idempotency_key=key("a")
        )

        assert (first.replayed, second.replayed) == (False, True)
        assert second.booking == first.booking
        assert len(desk.reservations_for(alice)) == 1

    def test_a_retry_succeeds_even_when_the_fleet_has_since_sold_out(
        self, desk: ReservationDesk, alice: Customer, bob: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SUV, 1, 3, "a")
        with pytest.raises(NoAvailability):
            book(desk, bob, CarType.SUV, 1, 3, "sold-out")

        assert book(desk, alice, CarType.SUV, 1, 3, "a") == mine

    def test_a_retry_after_the_start_time_has_passed_still_returns_the_original(
        self, ledger: BookingLedger, desk: ReservationDesk, alice: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SEDAN, 0, 2, "a")
        ten_minutes_later = ReservationDesk(ledger, now=lambda: NOW + timedelta(minutes=10))

        result = ten_minutes_later.reserve(
            customer=alice, car_type=CarType.SEDAN, period=days(0, 2), idempotency_key=key("a")
        )

        assert result.replayed
        assert result.booking == mine

    def test_a_retry_that_writes_the_same_instants_in_another_zone_is_the_same_request(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        utc = days(1, 2)
        tokyo = ZoneInfo("Asia/Tokyo")
        same_instants = RentalPeriod(
            start=utc.start.astimezone(tokyo), end=utc.end.astimezone(tokyo)
        )

        first = desk.reserve(
            customer=alice, car_type=CarType.SEDAN, period=utc, idempotency_key=key("a")
        )
        second = desk.reserve(
            customer=alice, car_type=CarType.SEDAN, period=same_instants, idempotency_key=key("a")
        )

        assert second.replayed
        assert second.booking == first.booking

    def test_a_retry_of_a_since_cancelled_booking_does_not_rebook_it(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SEDAN, 1, 3, "a")
        desk.cancel(booking_id=mine.id, actor=alice)

        result = desk.reserve(
            customer=alice, car_type=CarType.SEDAN, period=days(1, 3), idempotency_key=key("a")
        )

        assert result.replayed
        assert result.booking.is_cancelled
        assert len(desk.reservations_for(alice)) == 1

    def test_reusing_a_key_for_a_different_period_is_refused(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SEDAN, 1, 3, "a")

        with pytest.raises(IdempotencyConflict):
            book(desk, alice, CarType.SEDAN, 10, 3, "a")

        assert len(desk.reservations_for(alice)) == 1

    def test_reusing_a_key_for_a_different_car_type_is_refused(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SEDAN, 1, 3, "a")

        with pytest.raises(IdempotencyConflict):
            book(desk, alice, CarType.SUV, 1, 3, "a")

    def test_keys_are_scoped_to_the_customer(
        self, desk: ReservationDesk, alice: Customer, bob: Customer
    ) -> None:
        hers = book(desk, alice, CarType.SEDAN, 1, 3, "same-key")
        his = book(desk, bob, CarType.SEDAN, 1, 3, "same-key")

        assert hers != his
        assert desk.reservations_for(bob) == [his]

    @pytest.mark.parametrize(
        "malformed",
        [
            pytest.param("", id="empty"),
            pytest.param("short", id="too short"),
            pytest.param("k" * 65, id="too long"),
            pytest.param("has spaces in it", id="spaces"),
            pytest.param("semi;colon-drop-table", id="punctuation"),
        ],
    )
    def test_malformed_keys_are_rejected(
        self, desk: ReservationDesk, alice: Customer, malformed: str
    ) -> None:
        with pytest.raises(InvalidIdempotencyKey):
            desk.reserve(
                customer=alice,
                car_type=CarType.SEDAN,
                period=days(1, 3),
                idempotency_key=malformed,
            )

        assert desk.reservations_for(alice) == []

    # -- cancelling ------------------------------------------------------------------------

    def test_an_owner_can_cancel_their_booking(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SEDAN, 1, 3, "a")

        desk.cancel(booking_id=mine.id, actor=alice)

        (stored,) = desk.reservations_for(alice)
        assert stored.is_cancelled
        assert stored.cancelled_at == NOW

    def test_staff_can_cancel_any_booking(
        self, desk: ReservationDesk, alice: Customer, staff: Customer
    ) -> None:
        hers = book(desk, alice, CarType.SEDAN, 1, 3, "a")

        desk.cancel(booking_id=hers.id, actor=staff)

        assert desk.reservations_for(alice)[0].is_cancelled

    def test_another_customer_cannot_cancel_it_or_learn_that_it_exists(
        self, desk: ReservationDesk, alice: Customer, bob: Customer
    ) -> None:
        hers = book(desk, alice, CarType.SEDAN, 1, 3, "a")

        with pytest.raises(ReservationNotFound) as not_his:
            desk.cancel(booking_id=hers.id, actor=bob)
        with pytest.raises(ReservationNotFound) as missing:
            desk.cancel(booking_id=hers.id + 999, actor=bob)

        assert not desk.reservations_for(alice)[0].is_cancelled
        # Word for word the same apart from the id, so ids cannot be probed for existence.
        assert str(not_his.value).replace(str(hers.id), "N") == str(missing.value).replace(
            str(hers.id + 999), "N"
        )

    def test_cancelling_twice_is_harmless_and_keeps_the_original_time(
        self, ledger: BookingLedger, desk: ReservationDesk, alice: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SEDAN, 1, 3, "a")
        desk.cancel(booking_id=mine.id, actor=alice)
        an_hour_later = ReservationDesk(ledger, now=lambda: at(hours=1))

        again = an_hour_later.cancel(booking_id=mine.id, actor=alice)

        assert again.is_cancelled
        assert desk.reservations_for(alice)[0].cancelled_at == NOW

    def test_a_rental_that_has_already_ended_cannot_be_cancelled(
        self, ledger: BookingLedger, desk: ReservationDesk, alice: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SEDAN, 1, 3, "a")
        after_it_ended = ReservationDesk(ledger, now=lambda: at(days=30))

        with pytest.raises(ReservationNotCancellable):
            after_it_ended.cancel(booking_id=mine.id, actor=alice)

        assert not desk.reservations_for(alice)[0].is_cancelled

    def test_a_rental_in_progress_can_still_be_cancelled(
        self, ledger: BookingLedger, desk: ReservationDesk, alice: Customer
    ) -> None:
        mine = book(desk, alice, CarType.SEDAN, 1, 3, "a")
        halfway = ReservationDesk(ledger, now=lambda: at(days=2))

        halfway.cancel(booking_id=mine.id, actor=alice)

        assert desk.reservations_for(alice)[0].cancelled_at == at(days=2)

    # -- availability and listing ----------------------------------------------------------

    def test_reports_cars_free_for_the_whole_period(
        self, desk: ReservationDesk, alice: Customer
    ) -> None:
        book(desk, alice, CarType.SEDAN, 3, 2, "a")

        assert desk.availability(car_type=CarType.SEDAN, period=days(1, 10)) == 1
        assert desk.availability(car_type=CarType.SEDAN, period=days(5, 10)) == 2

    def test_availability_of_a_type_with_no_fleet_is_an_error_not_zero(
        self, desk: ReservationDesk
    ) -> None:
        with pytest.raises(CarTypeNotFound):
            desk.availability(car_type=CarType.VAN, period=days(1, 1))

    def test_never_reports_cars_free_for_a_period_that_could_not_be_booked(
        self, desk: ReservationDesk
    ) -> None:
        with pytest.raises(InvalidRentalPeriod, match="in the past"):
            desk.availability(car_type=CarType.SEDAN, period=days(-2, 3))

    def test_a_customer_sees_only_their_own_bookings_latest_pick_up_first(
        self, desk: ReservationDesk, alice: Customer, bob: Customer
    ) -> None:
        soon = book(desk, alice, CarType.SEDAN, 1, 1, "soon")
        later = book(desk, alice, CarType.SEDAN, 20, 1, "later")
        book(desk, bob, CarType.SUV, 1, 1, "his")

        assert desk.reservations_for(alice) == [later, soon]

    # -- logging ---------------------------------------------------------------------------

    def test_a_successful_booking_is_logged_at_info_with_ids_but_no_personal_data(
        self,
        desk: ReservationDesk,
        make_customer: MakeCustomer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        carol = make_customer("carol-whose-name-must-not-be-logged")

        with caplog.at_level(logging.INFO, logger="rentals"):
            mine = book(desk, carol, CarType.SEDAN, 1, 3, "a")

        (record,) = [r for r in caplog.records if r.message == "reservation created"]
        assert record.levelno == logging.INFO
        assert record.reservation_id == mine.id  # type: ignore[attr-defined]
        assert record.user_id == carol.id  # type: ignore[attr-defined]
        assert "carol" not in str(vars(record))

    def test_a_rejected_booking_is_logged_at_warning(
        self, desk: ReservationDesk, alice: Customer, caplog: pytest.LogCaptureFixture
    ) -> None:
        book(desk, alice, CarType.SUV, 1, 3, "a")
        caplog.clear()

        with caplog.at_level(logging.INFO, logger="rentals"), pytest.raises(NoAvailability):
            book(desk, alice, CarType.SUV, 1, 3, "b")

        (record,) = [r for r in caplog.records if r.name.startswith("rentals")]
        assert record.levelno == logging.WARNING
        assert "no availability" in record.message

    def test_an_attempt_to_cancel_someone_elses_booking_is_logged_at_warning(
        self,
        desk: ReservationDesk,
        alice: Customer,
        bob: Customer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        hers = book(desk, alice, CarType.SEDAN, 1, 3, "a")
        caplog.clear()  # the booking above logs too; this test is about the cancellation

        with caplog.at_level(logging.WARNING, logger="rentals"), pytest.raises(ReservationNotFound):
            desk.cancel(booking_id=hers.id, actor=bob)

        (record,) = [r for r in caplog.records if r.name.startswith("rentals")]
        assert record.levelno == logging.WARNING
        assert record.actor_id == bob.id  # type: ignore[attr-defined]
