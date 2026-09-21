from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path(
        "reservations/<int:reservation_id>/cancel/",
        views.cancel_reservation,
        name="cancel-reservation",
    ),
]
