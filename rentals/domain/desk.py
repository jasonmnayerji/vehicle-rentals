"""ReservationDesk: the use cases. Reserve a car, cancel a booking, ask what is free."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from .booking import Booking, BookingResult, Customer, IdempotencyKey
from .car_type import CarType
from .errors import IdempotencyConflict, NoAvailability, ReservationNotFound
from .ledger import BookingLedger, DuplicateIdempotencyKey
from .period import RentalPeriod
from .policy import BookingPolicy

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ReservationDesk:
    """Everything that creates or cancels a booking goes through here.

    Its collaborators are handed in, not reached for: the ledger that stores bookings, the
    policy that limits them, and the clock. So the rules can be exercised in memory in
    milliseconds, the clock in a test never moves, and none of this code knows whether it is
    talking to PostgreSQL.
    """

    def __init__(
        self,
        ledger: BookingLedger,
        *,
        policy: BookingPolicy | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._ledger = ledger
        self._policy = policy or BookingPolicy()
        self._now = now

    # -- queries ---------------------------------------------------------------------------

    def now(self) -> datetime:
        """The desk's clock. Callers that compare against "now" use this, not their own, so
        the whole request agrees on what time it is."""
        return self._now()

    def availability(self, *, car_type: CarType, period: RentalPeriod) -> int:
        """Cars of this type free for the whole period. Advisory only: it takes no lock, so
        the answer can be stale by the time the caller acts on it. ``reserve`` re-checks.

        Held to the same policy as ``reserve``. "2 cars free" for a period that could never
        be booked (in the past, too long) would be an answer nobody can act on.
        """
        self._policy.validate(period, now=self._now())
        return self._ledger.schedule(car_type, period).remaining(period)

    def reservations_for(self, customer: Customer) -> Sequence[Booking]:
        """A customer's own bookings, latest pick-up first, cancelled ones included."""
        return self._ledger.bookings_of(customer.id)

    # -- commands --------------------------------------------------------------------------

    def reserve(
        self,
        *,
        customer: Customer,
        car_type: CarType,
        period: RentalPeriod,
        idempotency_key: str,
    ) -> BookingResult:
        """Book one car of the given type, or raise a DomainError explaining why not.

        Safe against overbooking: the check and the insert happen inside the ledger's
        exclusive block, so concurrent bookings of one type run one at a time and each sees
        the ones before it.

        Safe against client retries: a repeated call with the same customer, key and
        parameters returns the original booking instead of creating a second one.
        """
        key = IdempotencyKey(idempotency_key)

        try:
            with self._ledger.exclusive(car_type) as fleet:
                # Before the policy check, so that a retry of a request that was valid when
                # first made still returns its booking once its start time has passed.
                existing = self._ledger.find_by_key(customer.id, key)
                if existing is not None:
                    return self._replay(existing, car_type, period)

                self._policy.validate(period, now=self._now())

                if not fleet.schedule(period).can_accommodate(period):
                    logger.warning(
                        "reservation rejected: no availability",
                        extra={
                            "user_id": customer.id,
                            "car_type": car_type.value,
                            "start_at": period.start.isoformat(),
                            "end_at": period.end.isoformat(),
                        },
                    )
                    raise NoAvailability(
                        f"no {car_type} available for the whole of "
                        f"{period.start:%Y-%m-%d %H:%M} to {period.end:%Y-%m-%d %H:%M} UTC"
                    )

                booking = fleet.add(customer_id=customer.id, period=period, key=key)
        except DuplicateIdempotencyKey:
            # Two retries carrying one key but naming *different* car types hold different
            # locks, so both get past the lookup above. The ledger lets only one in. Handled
            # out here, after the exclusive block has been abandoned and rolled back.
            winner = self._ledger.find_by_key(customer.id, key)
            if winner is None:
                raise
            return self._replay(winner, car_type, period)

        logger.info(
            "reservation created",
            extra={
                "reservation_id": booking.id,
                "user_id": customer.id,
                "car_type": car_type.value,
                "start_at": period.start.isoformat(),
                "end_at": period.end.isoformat(),
            },
        )
        return BookingResult(booking=booking, replayed=False)

    def cancel(self, *, booking_id: int, actor: Customer) -> Booking:
        """Cancel a booking. Owners cancel their own; staff may cancel any.

        Idempotent: cancelling an already-cancelled booking succeeds and changes nothing.

        Deliberately takes no fleet lock. Cancelling only ever frees capacity, so the worst
        a concurrent booking can do is miss the freed car and be rejected. It can never be
        wrongly accepted.
        """
        with self._ledger.exclusive_booking(booking_id) as booking:
            if booking is None or not booking.may_be_managed_by(actor):
                if booking is not None:
                    logger.warning(
                        "cancellation denied: actor does not own reservation",
                        extra={"reservation_id": booking_id, "actor_id": actor.id},
                    )
                # One error for "missing" and "not yours": ids are sequential, and telling
                # the two apart would let anyone probe which exist.
                raise ReservationNotFound(f"reservation {booking_id} does not exist")

            if booking.cancel(now=self._now()):
                self._ledger.save(booking)
                logger.info(
                    "reservation cancelled",
                    extra={"reservation_id": booking.id, "actor_id": actor.id},
                )
            return booking

    # -- internals -------------------------------------------------------------------------

    @staticmethod
    def _replay(existing: Booking, car_type: CarType, period: RentalPeriod) -> BookingResult:
        if not existing.is_for(car_type, period):
            logger.warning(
                "idempotency key reused with different parameters",
                extra={"reservation_id": existing.id, "user_id": existing.customer_id},
            )
            raise IdempotencyConflict(
                "this idempotency key was already used for a different booking"
            )
        logger.info(
            "reservation replayed from idempotency key",
            extra={"reservation_id": existing.id, "user_id": existing.customer_id},
        )
        return BookingResult(booking=existing, replayed=True)
