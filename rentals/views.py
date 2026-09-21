"""HTML views. Thin on purpose: parse the request, call the ReservationDesk, render the result.

No business rule lives here. A rule that appeared in a view would have to be repeated in the
REST API, the admin and every other caller, and would drift.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .domain import (
    DomainError,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    RentalPeriod,
    ReservationDesk,
    ReservationNotCancellable,
    ReservationNotFound,
)
from .forms import BookingForm, new_idempotency_key
from .services import customer_from, reservation_desk


def get_desk() -> ReservationDesk:
    """The seam tests use to substitute a desk with a fixed clock."""
    return reservation_desk()


def _blank_form() -> BookingForm:
    # A sensible default so the form can be submitted as it stands: tomorrow 10:00 for 2 days.
    tomorrow_at_ten = (timezone.localtime() + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    return BookingForm(
        initial={
            "start": tomorrow_at_ten,
            "days": 2,
            "idempotency_key": new_idempotency_key(),
        }
    )


def _with_fresh_key(request: HttpRequest) -> BookingForm:
    """The submitted form again, but carrying a new idempotency key."""
    data = request.POST.copy()
    data["idempotency_key"] = new_idempotency_key()
    form = BookingForm(data)
    form.is_valid()
    return form


@login_required
@require_http_methods(["GET", "POST"])
def dashboard(request: HttpRequest) -> HttpResponse:
    desk, customer = get_desk(), customer_from(request.user)
    form = _blank_form()
    available: int | None = None

    if request.method == "POST":
        form = BookingForm(request.POST)
        if form.is_valid():
            booking = form.cleaned_data
            try:
                car_type = booking["car_type"].kind
                period = RentalPeriod.for_days(booking["start"], booking["days"])
                if request.POST.get("action") == "check":
                    available = desk.availability(car_type=car_type, period=period)
                else:
                    result = desk.reserve(
                        customer=customer,
                        car_type=car_type,
                        period=period,
                        idempotency_key=booking["idempotency_key"],
                    )
                    if result.replayed and result.booking.is_cancelled:
                        # The back button after a cancellation. The replay is correct (same
                        # attempt, same result), but "already made" would leave the customer
                        # believing they still hold a car.
                        messages.warning(
                            request,
                            "That booking was cancelled, so nothing is booked. "
                            "Submit the form again to make a new booking.",
                        )
                    elif result.replayed:
                        messages.info(request, "That booking was already made. Nothing was added.")
                    else:
                        messages.success(request, f"Booked: {booking['car_type']}.")
                    # Post/Redirect/Get, so refreshing the result page cannot re-submit.
                    return redirect("dashboard")
            except (IdempotencyConflict, InvalidIdempotencyKey):
                # The key on this page has already been spent on a different booking (the
                # back button after a success) or was tampered with. Issue a new one.
                form = _with_fresh_key(request)
                form.add_error(None, "This form was already used. Please submit it again.")
            except DomainError as error:
                form.add_error(None, str(error))

    return render(
        request,
        "rentals/dashboard.html",
        {
            "form": form,
            "available": available,
            "reservations": desk.reservations_for(customer),
            "now": desk.now(),
            "time_zone": timezone.get_current_timezone_name(),
        },
    )


@login_required
@require_POST
def cancel_reservation(request: HttpRequest, reservation_id: int) -> HttpResponse:
    try:
        get_desk().cancel(booking_id=reservation_id, actor=customer_from(request.user))
    except ReservationNotFound:
        messages.error(request, "Reservation not found.")
    except ReservationNotCancellable as error:
        messages.error(request, f"Cannot cancel: {error}.")
    else:
        messages.success(request, "Reservation cancelled.")
    return redirect("dashboard")
