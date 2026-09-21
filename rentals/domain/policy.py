"""Commercial rules on what may be booked, separate from what is structurally valid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .errors import InvalidRentalPeriod
from .period import RentalPeriod

# A rental of N calendar days that spans the end of daylight saving lasts N days *and one
# hour* of elapsed time. It is still an N-day rental, and refusing a customer who asked for
# exactly the maximum because the clocks went back would be a bug, not a policy.
_DAYLIGHT_SAVING_ALLOWANCE = timedelta(hours=1)


def _describe(span: timedelta) -> str:
    hours = span / timedelta(hours=1)
    if hours >= 48 and hours % 24 == 0:
        return f"{int(hours // 24)} days"
    if hours == int(hours):
        return f"{int(hours)} hour" + ("" if hours == 1 else "s")
    return f"{int(span / timedelta(minutes=1))} minutes"


@dataclass(frozen=True, slots=True)
class BookingPolicy:
    """Limits the business places on a new booking.

    Kept apart from RentalPeriod on purpose: a period in the past is a perfectly valid
    period (history is full of them), it just cannot be *newly booked*. The caps also bound
    the work the availability check can be asked to do.
    """

    min_duration: timedelta = timedelta(hours=1)
    max_duration: timedelta = timedelta(days=90)
    max_advance: timedelta = timedelta(days=365)
    # A customer at the desk books "from now". By the time the request is handled, "now" is a
    # few seconds in the past; without some tolerance every walk-up booking would be refused.
    start_grace: timedelta = timedelta(minutes=5)

    def validate(self, period: RentalPeriod, now: datetime) -> None:
        if period.start < now - self.start_grace:
            raise InvalidRentalPeriod("a rental cannot start in the past")
        if period.duration < self.min_duration:
            raise InvalidRentalPeriod(f"a rental must last at least {_describe(self.min_duration)}")
        if period.duration > self.max_duration + _DAYLIGHT_SAVING_ALLOWANCE:
            raise InvalidRentalPeriod(f"a rental cannot exceed {_describe(self.max_duration)}")
        if period.start - now > self.max_advance:
            raise InvalidRentalPeriod(
                f"a rental cannot start more than {_describe(self.max_advance)} ahead"
            )
