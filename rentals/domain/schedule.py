"""FleetSchedule: answers "is there a car free for this whole period?"."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .period import RentalPeriod

_RETURN = -1
_PICKUP = +1


class FleetSchedule:
    """The bookings held against a fleet of interchangeable cars of one type.

    Customers book a car *type*, not a specific car; a physical car is assigned at pick-up.
    A new booking therefore fits if, and only if, at no moment during the requested period
    are all cars already in use. That is a question about the *peak number of simultaneous
    bookings*, not about how many bookings touch the period.

    Counting the bookings that overlap the request is the common mistake. With a fleet of 2,
    bookings on days 1-3 and 5-7 both overlap a request for days 2-6, yet they never overlap
    each other, so only one car is ever in use and the request fits.

    Peak usage is sufficient as well as necessary: bookings are intervals, and for interval
    graphs the number of cars needed equals the largest number in use at once. Handing any
    free car to each customer at pick-up always works.
    """

    __slots__ = ("_booked", "_fleet_size")

    def __init__(self, fleet_size: int, booked: Iterable[RentalPeriod]) -> None:
        if fleet_size < 0:
            raise ValueError("fleet_size cannot be negative")
        self._fleet_size = fleet_size
        self._booked: tuple[RentalPeriod, ...] = tuple(booked)

    @property
    def fleet_size(self) -> int:
        return self._fleet_size

    def peak_usage(self, window: RentalPeriod) -> int:
        """Largest number of cars in use at any one time within ``window``.

        Sweep line: turn each overlapping booking into a +1 event at pick-up and a -1 event
        at return, clipped to the window, then walk the events in time order keeping a running
        total. O(k log k) for k overlapping bookings, independent of how long the window is.

        Returns sort before pick-ups at the same instant (-1 < +1), which is what makes the range
        half-open: a car returned at 10:00 is free for a pick-up at 10:00.
        """
        events: list[tuple[datetime, int]] = []
        for period in self._booked:
            if period.overlaps(window):
                events.append((max(period.start, window.start), _PICKUP))
                events.append((min(period.end, window.end), _RETURN))
        events.sort()

        in_use = peak = 0
        for _, delta in events:
            in_use += delta
            peak = max(peak, in_use)
        return peak

    def remaining(self, window: RentalPeriod) -> int:
        """Cars guaranteed free for the entire window. Never negative."""
        return max(0, self._fleet_size - self.peak_usage(window))

    def can_accommodate(self, window: RentalPeriod) -> bool:
        return self.peak_usage(window) < self._fleet_size
