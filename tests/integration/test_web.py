"""The HTML interface, driven through Django's test client the way a browser would."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from django.core.management import call_command
from django.test import Client

from accounts.models import User
from rentals.models import CarType, CarTypeName, Reservation
from rentals.services import reservation_desk
from tests.support import NOW, at, key

pytestmark = pytest.mark.django_db

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rentals.views.get_desk", lambda: reservation_desk(now=lambda: NOW))


@pytest.fixture
def as_alice(client: Client, alice: User) -> Client:
    client.force_login(alice)
    return client


def booking_post(
    car_type: CarType,
    start: datetime,
    days: int,
    label: str = "web",
    action: str = "book",
) -> dict[str, object]:
    """What the browser submits: a zone-less wall-clock string, as datetime-local sends it,
    and a number of days."""
    return {
        "car_type": car_type.pk,
        "start": start.strftime(DATETIME_LOCAL),
        "days": days,
        "idempotency_key": key(label),
        "action": action,
    }


class TestAccess:
    @pytest.mark.parametrize("path", ["/", "/reservations/1/cancel/"])
    def test_visitors_who_are_not_logged_in_are_sent_to_the_login_page(
        self, client: Client, path: str
    ) -> None:
        response = client.post(path) if "cancel" in path else client.get(path)

        assert response.status_code == 302
        assert response["Location"].startswith("/accounts/login/")

    def test_signing_up_creates_the_account_and_logs_in(self, client: Client) -> None:
        response = client.post(
            "/accounts/signup/",
            {
                "username": "dana",
                "first_name": "Dana",
                "last_name": "Scully",
                "password1": "correct-horse-battery",
                "password2": "correct-horse-battery",
            },
        )

        assert response.status_code == 302
        dana = User.objects.get(username="dana")
        assert dana.first_name == "Dana"
        assert dana.check_password("correct-horse-battery")
        assert not dana.is_staff
        assert client.get("/").status_code == 200

    def test_a_weak_password_is_refused_by_the_configured_validators(self, client: Client) -> None:
        response = client.post(
            "/accounts/signup/",
            {"username": "dana", "password1": "password", "password2": "password"},
        )

        assert response.status_code == 200
        assert "too common" in response.content.decode()
        assert not User.objects.filter(username="dana").exists()

    def test_forms_cannot_be_submitted_without_a_csrf_token(
        self, alice: User, sedan: CarType
    ) -> None:
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(alice)

        response = strict.post("/", booking_post(sedan, at(days=1), 2))

        assert response.status_code == 403
        assert not Reservation.objects.exists()


class TestBooking:
    def test_the_page_offers_the_car_types_and_says_which_time_zone_applies(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        page = as_alice.get("/").content.decode()

        assert "Sedan" in page
        assert "Times are in UTC." in page
        assert "You have no reservations yet." in page

    def test_booking_creates_a_reservation_and_shows_it(
        self, as_alice: Client, alice: User, sedan: CarType
    ) -> None:
        response = as_alice.post("/", booking_post(sedan, at(days=1, hours=6), 2), follow=True)

        reservation = Reservation.objects.get()
        assert reservation.user == alice
        # Picked up at a date and time, for a number of days: back at the same time of day.
        assert (reservation.start_at, reservation.end_at) == (
            at(days=1, hours=6),
            at(days=3, hours=6),
        )
        page = response.content.decode()
        assert "Booked: Sedan." in page
        assert "Sun 11 Jan 2026, 18:00" in page
        assert "Tue 13 Jan 2026, 18:00" in page

    def test_a_successful_booking_redirects_so_a_refresh_cannot_resubmit(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        response = as_alice.post("/", booking_post(sedan, at(days=1), 2))

        assert response.status_code == 302
        assert response["Location"] == "/"

    def test_submitting_the_same_form_twice_books_once(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        form = booking_post(sedan, at(days=1), 2)
        as_alice.post("/", form)

        response = as_alice.post("/", form, follow=True)

        assert Reservation.objects.count() == 1
        assert "already made" in response.content.decode()

    def test_resubmitting_the_form_of_a_cancelled_booking_does_not_claim_it_is_still_booked(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        # Book, cancel, press back, submit again: the page still carries the spent key.
        form = booking_post(sedan, at(days=1), 2)
        as_alice.post("/", form)
        as_alice.post(f"/reservations/{Reservation.objects.get().pk}/cancel/")

        page = as_alice.post("/", form, follow=True).content.decode()

        assert Reservation.objects.active().count() == 0
        assert "already made" not in page
        assert "nothing is booked" in page

    def test_a_spent_form_reused_for_a_different_booking_is_refused_and_reissued(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        # The back button after a success, then different dates: the page still carries the
        # old key. The customer must not silently get the old booking back.
        as_alice.post("/", booking_post(sedan, at(days=1), 2))

        response = as_alice.post("/", booking_post(sedan, at(days=5), 1))

        assert Reservation.objects.count() == 1
        page = response.content.decode()
        assert "already used" in page
        assert key("web") not in page  # a fresh key was issued, so the next submit works

    def test_a_sold_out_period_is_explained_and_books_nothing(
        self, as_alice: Client, bob: User, reserve: Callable[..., Reservation]
    ) -> None:
        van = CarType.objects.create(name=CarTypeName.VAN, fleet_size=1)
        reserve(bob, van, at(days=1), at(days=3), "bob")

        response = as_alice.post("/", booking_post(van, at(days=2), 2))

        assert response.status_code == 200
        assert "no Van available" in response.content.decode()
        assert Reservation.objects.count() == 1

    @pytest.mark.parametrize(
        ("start", "days", "message"),
        [
            pytest.param(at(days=-2), 3, "in the past", id="starts in the past"),
            pytest.param(at(days=1), 91, "cannot exceed 90 days", id="too many days"),
            pytest.param(at(days=400), 2, "more than 365 days ahead", id="too far ahead"),
        ],
    )
    def test_the_domain_rules_are_reported_on_the_form(
        self, as_alice: Client, sedan: CarType, start: datetime, days: int, message: str
    ) -> None:
        response = as_alice.post("/", booking_post(sedan, start, days))

        assert response.status_code == 200
        assert message in response.content.decode()
        assert not Reservation.objects.exists()

    def test_garbage_input_is_rejected_by_the_form(self, as_alice: Client, sedan: CarType) -> None:
        response = as_alice.post(
            "/",
            {
                "car_type": 999_999,
                "start": "not-a-date",
                "days": "a few",
                "idempotency_key": key("web"),
                "action": "book",
            },
        )

        assert response.status_code == 200
        page = response.content.decode()
        assert "Enter a valid date/time." in page
        assert "Enter a whole number." in page
        assert not Reservation.objects.exists()

    def test_checking_availability_reports_free_cars_and_books_nothing(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        response = as_alice.post("/", booking_post(sedan, at(days=1), 2, action="check"))

        assert "2 cars free for that whole period." in response.content.decode()
        assert not Reservation.objects.exists()

    def test_checking_a_period_that_cannot_be_booked_explains_why_instead_of_counting_cars(
        self, as_alice: Client, sedan: CarType
    ) -> None:
        response = as_alice.post("/", booking_post(sedan, at(days=-2), 3, action="check"))

        page = response.content.decode()
        assert "a rental cannot start in the past" in page
        assert "cars free" not in page

    @pytest.mark.parametrize("days", [0, -3, "2.5"])
    def test_the_number_of_days_must_be_a_positive_whole_number(
        self, as_alice: Client, sedan: CarType, days: object
    ) -> None:
        response = as_alice.post("/", booking_post(sedan, at(days=1), days))  # type: ignore[arg-type]

        assert response.status_code == 200
        assert not Reservation.objects.exists()

    def test_a_rental_in_days_is_returned_at_the_time_of_day_it_was_picked_up(
        self, as_alice: Client, sedan: CarType, settings: object
    ) -> None:
        # New York's clocks go forward on 8 March 2026, so these two days hold 47 hours.
        settings.TIME_ZONE = "America/New_York"  # type: ignore[attr-defined]

        response = as_alice.post(
            "/", booking_post(sedan, datetime(2026, 3, 7, 10, 0), 2), follow=True
        )

        reservation = Reservation.objects.get()
        assert reservation.start_at == datetime(2026, 3, 7, 15, 0, tzinfo=UTC)  # 10:00 EST
        assert reservation.end_at == datetime(2026, 3, 9, 14, 0, tzinfo=UTC)  # 10:00 EDT
        page = response.content.decode()
        assert "Sat 7 Mar 2026, 10:00" in page
        assert "Mon 9 Mar 2026, 10:00" in page  # not 11:00

    def test_times_typed_into_the_form_are_read_in_the_site_time_zone(
        self, as_alice: Client, sedan: CarType, settings: object
    ) -> None:
        settings.TIME_ZONE = "Asia/Tokyo"  # type: ignore[attr-defined]
        nine_am = datetime(2026, 1, 12, 9, 0)

        response = as_alice.post("/", booking_post(sedan, nine_am, 1), follow=True)

        reservation = Reservation.objects.get()
        assert reservation.start_at == datetime(2026, 1, 12, 0, 0, tzinfo=UTC)
        page = response.content.decode()
        assert "Times are in Asia/Tokyo." in page
        assert "Mon 12 Jan 2026, 09:00" in page  # shown back as the customer typed it

    def test_a_customer_name_cannot_inject_markup_into_the_page(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        # The name typed at sign-up is the one free-text value the pages echo back.
        client.force_login(make_user("mallory", first_name="<script>alert(1)</script>"))

        page = client.get("/").content.decode()

        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


class TestCancelling:
    @pytest.fixture
    def reservation(
        self, alice: User, sedan: CarType, reserve: Callable[..., Reservation]
    ) -> Reservation:
        return reserve(alice, sedan, at(days=1), at(days=3), "to-cancel")

    def test_a_customer_can_cancel_their_own_reservation(
        self, as_alice: Client, reservation: Reservation
    ) -> None:
        assert f"/reservations/{reservation.pk}/cancel/" in as_alice.get("/").content.decode()

        response = as_alice.post(f"/reservations/{reservation.pk}/cancel/", follow=True)

        reservation.refresh_from_db()
        assert reservation.status == Reservation.Status.CANCELLED
        page = response.content.decode()
        assert "Reservation cancelled." in page
        assert f"/reservations/{reservation.pk}/cancel/" not in page  # no button once cancelled

    def test_a_customer_cannot_cancel_or_see_someone_elses_reservation(
        self, client: Client, bob: User, reservation: Reservation
    ) -> None:
        client.force_login(bob)

        response = client.post(f"/reservations/{reservation.pk}/cancel/", follow=True)

        reservation.refresh_from_db()
        assert reservation.status == Reservation.Status.ACTIVE
        page = response.content.decode()
        assert "Reservation not found." in page
        assert "You have no reservations yet." in page

    def test_cancelling_needs_a_post_so_a_link_cannot_do_it(
        self, as_alice: Client, reservation: Reservation
    ) -> None:
        response = as_alice.get(f"/reservations/{reservation.pk}/cancel/")

        assert response.status_code == 405
        reservation.refresh_from_db()
        assert reservation.status == Reservation.Status.ACTIVE


def test_logging_out_needs_a_post(as_alice: Client) -> None:
    assert as_alice.get("/accounts/logout/").status_code == 405
    assert as_alice.post("/accounts/logout/").status_code == 302
    assert as_alice.get("/").status_code == 302  # logged out: back to the login page


def test_seeding_creates_the_demo_fleet_once_and_respects_later_edits() -> None:
    call_command("seed_demo")
    CarType.objects.filter(name=CarTypeName.VAN).update(fleet_size=7)

    call_command("seed_demo")

    assert CarType.objects.count() == 3
    assert CarType.objects.get(name=CarTypeName.VAN).fleet_size == 7
