from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from .domain import DomainError, ReservationDesk
from .models import CarType, Reservation
from .services import customer_from, reservation_desk


def get_desk() -> ReservationDesk:
    """The seam tests use to substitute a desk with a fixed clock."""
    return reservation_desk()


@admin.register(CarType)
class CarTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "fleet_size")
    search_fields = ("name",)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    """Read-only on purpose.

    Creating or editing a reservation here would bypass ReservationDesk, and with it the
    availability check and the lock that prevents overbooking. Staff can inspect bookings;
    the one change they can make, cancelling, is an action that goes through the desk.
    """

    list_display = ("id", "car_type", "user", "start_at", "end_at", "status", "created_at")
    list_filter = ("status", "car_type")
    date_hierarchy = "start_at"
    list_select_related = ("car_type", "user")
    actions = ["cancel_selected"]

    @admin.action(description="Cancel selected reservations", permissions=["cancel"])
    def cancel_selected(self, request: HttpRequest, queryset: QuerySet[Reservation]) -> None:
        desk, actor = get_desk(), customer_from(request.user)
        cancelled = 0
        for reservation_id in queryset.values_list("pk", flat=True):
            try:
                desk.cancel(booking_id=reservation_id, actor=actor)
            except DomainError as error:
                self.message_user(request, f"#{reservation_id}: {error}", messages.ERROR)
            else:
                cancelled += 1
        if cancelled:
            self.message_user(request, f"Cancelled: {cancelled}.", messages.SUCCESS)

    def has_cancel_permission(self, request: HttpRequest) -> bool:
        # Seeing every customer's bookings and being able to cancel them are different
        # privileges. The form-level change permission below stays off for everyone; the
        # model's change permission is reused to mean "may cancel".
        return request.user.has_perm("rentals.change_reservation")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Reservation | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Reservation | None = None) -> bool:
        return False
