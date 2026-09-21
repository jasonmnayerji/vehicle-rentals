from __future__ import annotations

import uuid

from django import forms

from .models import CarType

# What <input type="datetime-local"> submits, with and without seconds.
_DATETIME_LOCAL_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]


def new_idempotency_key() -> str:
    return uuid.uuid4().hex


def _datetime_local() -> forms.DateTimeInput:
    return forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class BookingForm(forms.Form):
    """Parses and type-checks the request. It deliberately knows no business rules.

    Whether the period is allowed and whether a car is free are decided by the domain, via
    ReservationDesk, so the rules exist in exactly one place whichever interface is used.

    A browser's datetime-local input sends a wall-clock time with no zone. Django's
    DateTimeField interprets it in the current time zone and returns an aware datetime, and
    rejects wall-clock times that do not exist or are ambiguous across a daylight-saving
    change. The page tells the customer which zone that is.
    """

    car_type = forms.ModelChoiceField(queryset=CarType.objects.all(), empty_label=None)
    start = forms.DateTimeField(
        label="Pick-up", widget=_datetime_local(), input_formats=_DATETIME_LOCAL_FORMATS
    )
    # "At a desired date and time for a given number of days". Positive and whole is all the
    # form insists on, because anything else is not a number of days at all. How many days
    # are allowed is a business rule, and BookingPolicy's to decide.
    days = forms.IntegerField(label="Number of days", min_value=1)
    # Issued when the form is rendered and sent back with it. A double click, a refresh of
    # the POST, or a retry after a timeout all carry the same key, so they cannot double-book.
    idempotency_key = forms.CharField(widget=forms.HiddenInput, max_length=64)
