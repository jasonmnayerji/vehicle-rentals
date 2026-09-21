# vehicle-rentals

A simulated car rental system. A customer reserves a **type** of car (sedan, SUV or van) at a
date and time, for a number of days. The reservation is accepted only if a car of that type is
free for the whole period, and the system stays correct when many customers book at once.

## The brief, and where each requirement is met

| # | Requirement | Where it lives | Proved by (unit tests, no database) |
|---|---|---|---|
| 3 | Reserve a car of a given type at a date and time for a number of days | `ReservationDesk.reserve`, `RentalPeriod.for_days` | `test_requirements.py::TestRequirement3…`, `test_rental_period.py::TestForDays` |
| 4 | Three types of car: sedan, SUV, van | `CarType` (a closed enum) | `test_requirements.py::TestRequirement4…`, `test_booking.py::TestCarType` |
| 5 | The number of cars of each type is limited | `FleetSchedule`, `LockedFleet` | `test_requirements.py::TestRequirement5…`, `test_fleet_schedule.py`, `test_reservation_desk.py` |
| 6 | Unit tests prove the system satisfies the requirements | `tests/unit/` | 145 tests, under a second, nothing to install but pytest |
| – | Object-oriented principles | `rentals/domain/` | [Where the object-oriented design lives](#where-the-object-oriented-design-lives) |

**What is the answer, and what is extra.** The answer is `rentals/domain/` and `tests/unit/`:
plain Python, no framework. Everything else (PostgreSQL, a web page, the admin, Docker, CI)
shows the same domain running unchanged behind a real database under real concurrency. It
can be ignored without losing the answer.

## Test it

**Unit tests: no Docker, no database, no configuration.**

```sh
pip install pytest          # and tzdata on Windows, which has no time-zone database
pytest -c tests/unit/pytest.ini tests/unit
```

The `ReservationDesk` runs against an in-memory `BookingLedger`. That ledger is held to the
same behavioural suite (`tests/desk_contract.py`) as the PostgreSQL one, so what passes in
memory is known to hold against the database. CI runs these tests with Django not installed.

**Everything, against real PostgreSQL.** 269 tests in a few seconds. 124 need the database:
the shared suite again, the concurrency tests (real row locks, so no SQLite shortcut), the
database constraints, the web page and the admin.

```sh
cp .env.example .env
docker compose --profile test run --rm tests

# lint, format and types, as CI runs them
docker compose --profile test run --rm tests sh -c "ruff check . && ruff format --check . && mypy"
```

## Run it

Requires Docker only.

```sh
cp .env.example .env
docker compose up --build
```

- http://127.0.0.1:8000/ is a plain page for trying it by hand: sign up, book, check
  availability, cancel. A demo fleet (Sedan x3, SUV x2, Van x1) is created on first start.
- http://127.0.0.1:8000/admin/ Sign in as `admin` with the `DEV_SUPERUSER_PASSWORD` from your
  `.env`. The account exists only because `.env.example` sets `DJANGO_ENV=dev`; see
  [Security](#security).
- http://127.0.0.1:8000/healthz

Without Docker (optional): install [uv](https://docs.astral.sh/uv/), start the database with
`docker compose up -d db`, export the `.env` values with `POSTGRES_HOST=localhost`, then
`uv sync --locked` and `uv run pytest`.

## Try it

The whole system in a Python prompt, with no database and nothing running:

```python
from datetime import UTC, datetime

from rentals.domain import CarType, Customer, InMemoryBookingLedger, RentalPeriod, ReservationDesk

desk = ReservationDesk(InMemoryBookingLedger({CarType.SEDAN: 3, CarType.SUV: 1, CarType.VAN: 1}))
alice = Customer(id=1)
three_days = RentalPeriod.for_days(datetime(2027, 6, 1, 10, 0, tzinfo=UTC), 3)  # from 10:00

desk.availability(car_type=CarType.SUV, period=three_days)  # 1
booked = desk.reserve(
    customer=alice, car_type=CarType.SUV, period=three_days, idempotency_key="demo-key-0001"
)
# Same key again: the original booking comes back, nothing new is created.
desk.reserve(
    customer=alice, car_type=CarType.SUV, period=three_days, idempotency_key="demo-key-0001"
).replayed  # True
# New key, but the only SUV is taken: raises NoAvailability.
desk.reserve(
    customer=alice, car_type=CarType.SUV, period=three_days, idempotency_key="demo-key-0002"
)
desk.cancel(booking_id=booked.booking.id, actor=alice)  # frees the car
```

Against PostgreSQL only the `desk =` line changes, to `rentals.services.reservation_desk()`,
inside `docker compose exec web python manage.py shell`.

## Design

### Where the object-oriented design lives

```
rentals/domain/      Plain Python. No Django imports. Strictly type-checked. The answer.
  car_type.py        CarType          the closed set of three kinds of car
  period.py          RentalPeriod     value object, half-open [start, end); for_days()
  booking.py         Booking          entity: identity, lifecycle, its own rules
                     IdempotencyKey, Customer   self-validating value objects
  schedule.py        FleetSchedule    "is a car free for this whole period?"
  policy.py          BookingPolicy    commercial limits (no past dates, max length, horizon)
  ledger.py          BookingLedger    ABSTRACT: what the desk needs from storage
                     LockedFleet      ABSTRACT: exclusive access to one type's bookings
  desk.py            ReservationDesk  the use cases: reserve, cancel, availability
  in_memory.py       InMemoryBookingLedger   one implementation of the ledger
rentals/ledger.py    PostgresBookingLedger   the other: transactions, row locks, constraints
rentals/models.py    How bookings are stored. No rules.
rentals/services.py  Wires the desk to PostgreSQL. The one composition root.
rentals/views.py     Parse the form, call the desk, render. No rules.
```

The domain decides whether a booking is allowed. A ledger makes that decision safe and
durable.

- **Abstraction and dependency inversion.** `ReservationDesk` depends on the abstract
  `BookingLedger`. Django imports the domain; the domain imports nothing.
- **Polymorphism where behaviour varies.** Two real ledgers store and lock in different ways
  behind one interface, and one test suite holds both to the same contract.
- **No inheritance where it does not.** There is no `Sedan(Car)`, `SUV(Car)`, `Van(Car)`: the
  three types differ in data (a name, a fleet size), so those would be three empty classes.
  `CarType` is an enum. If vans ever need a licence check, that rule gets its own type.
- **Encapsulation.** `Booking` owns who may manage it and whether it can still be cancelled.
  `RentalPeriod` and `IdempotencyKey` cannot be constructed invalid.
- **Entities and value objects.** A `Booking` is equal by `id`. A `RentalPeriod` is equal by
  value: the same instants written in two time zones are one period.
- **Mistakes made unrepresentable.** `add()` exists only on `LockedFleet`, which can only be
  obtained from `ledger.exclusive(car_type)`. A booking cannot be written without the lock.
- **Composition.** The desk is handed a ledger, a policy and a clock. A different policy or a
  frozen clock is a constructor argument.

Car types are rows as well as an enum. The enum is the business fact; the row holds the fleet
size and is what a booking locks. A new type needs a code change and a migration, which is
acceptable for a set the brief declares fixed.

### Data model

- **CarType**: `name` (unique, one of three, both enforced by the database) and `fleet_size`.
  Customers book a class and get a specific car at pick-up, so a count is the honest model.
- **User**: Django's `AbstractUser`, subclassed with no extra fields because Django cannot swap
  the user model after the first migration.
- **Reservation**: car type and user (both `PROTECT`, so history never vanishes), `start_at`,
  `end_at` (UTC, half-open), `status`, `idempotency_key`, timestamps.

Database constraints enforce what must never be false: the return is after the pick-up, one
reservation per user per idempotency key, and `cancelled_at` set exactly when cancelled.

### Availability: peak usage

A booking fits if at no moment in the period are all cars of that type in use. That is the
peak number of simultaneous bookings. Counting the bookings that overlap the period is wrong:

```
fleet of 2      A: days 1-3      B: days 5-7      request: days 2-6
overlap count:  A and B both overlap the request -> 2 >= 2 -> reject   (wrong)
peak usage:     A and B never overlap each other  -> 1 <  2 -> accept   (right)
```

`FleetSchedule.peak_usage` is a sweep line: +1 at each pick-up, -1 at each return, with a
running maximum. It is O(k log k) in the k overlapping bookings, which are fetched through a
partial index on active reservations. A randomised test checks it against an hour-by-hour
count on 300 schedules. Periods are half-open, so a car returned at 10:00 can go out at 10:00.

### Concurrency

The hazard is check-then-insert: two requests both see the last car free and both book it.

`PostgresBookingLedger.exclusive` locks the car type row (`SELECT ... FOR UPDATE`), and the
availability check happens after the lock is held. Bookings for one type run one at a time;
different types never wait for each other. Cancelling takes no fleet lock, because freeing
capacity cannot cause an overbooking.

`tests/integration/test_concurrency.py` releases 12 threads at once against a fleet of 3 and
requires exactly 3 successes. With the lock removed it fails with 12.

**The isolation level is pinned to READ COMMITTED in settings.** There, the availability read
takes a fresh snapshot and sees what the previous lock holder committed. At REPEATABLE READ
the snapshot is taken before the lock is granted, and the same test books 12 cars out of 3
with the lock still in place. A test asserts the level the connection runs at.

Waits are bounded: `lock_timeout` 5 s and `statement_timeout` 15 s, from the environment. A
booking that cannot get the lock raises `FleetBusy`, a retryable error, instead of a 500.

Alternatives considered: one row per physical car with an exclusion constraint (pins a car at
booking time, which fragments the fleet and refuses requests that would fit); SERIALIZABLE or
an optimistic version column (retry storms on the busiest rows); a Redis lock (a new
component to protect data that already lives in the database).

### Idempotency

Clients retry when a response is lost, so every booking carries a client-generated key.

- Same user, key and parameters: the original reservation is returned, flagged `replayed`,
  even if the fleet has since sold out or the start time has passed.
- Same key, different parameters: refused with `IdempotencyConflict`.
- Keys are scoped per user and backed by a unique constraint. Two retries naming different
  car types hold different locks and can both pass the lookup; the constraint stops the
  second. That race is tested against both ledgers.

### Manual QA page

A harness for checking by hand what a customer would see. Django templates, no JavaScript, no
external assets, and no business rules in the views.

- Each rendered form carries a fresh idempotency key, so a double click or a refresh replays
  the original booking. A spent form reused for different dates is refused and reissued.
- `datetime-local` inputs have no zone. They are read in the site's zone, which the page
  states, and Django rejects wall-clock times that do not exist or are ambiguous.
- CSRF on every form, POST-only cancel and logout, auto-escaped output, Django's password
  hashing and validators.

### Security

- No secret has a default. A missing `DJANGO_SECRET_KEY` or database password stops start-up.
- The test superuser exists only where `DJANGO_ENV=dev`. Unset means production, and a typo
  stops start-up. The password comes from the environment, and the command never overwrites
  or promotes an existing account.
- Cancelling someone else's reservation returns "not found", identical to a missing id.
- Reservations are read-only in the admin, because editing them would bypass the availability
  check. The one exception is a cancel action that calls the desk and needs its own permission.
- Dependencies are locked with hashes in `uv.lock` and installed with `uv sync --locked`
  everywhere. CI fails on a known-vulnerable dependency (`pip-audit`), lints with Bandit's
  rules, and pins actions, base images and the `uv` installer to commits or digests.
- `DJANGO_BEHIND_TLS=true` switches on HSTS, secure cookies and the HTTPS redirect.

### Operations

- **Logging.** One JSON object per line on stdout, standard library only. Every line carries
  the request id (an upstream `X-Request-ID` is accepted only if well-formed). Ids only, never
  names or emails. The access line omits the query string and skips healthy probes.
- **Container.** `python:3.13-slim` pinned by digest (Alpine's musl breaks wheels; distroless
  makes migrations awkward). Multi-stage, with an explicit allowlist of paths in the runtime
  image and tests in a separate stage. Non-root numeric user, read-only root filesystem, all
  capabilities dropped. Ports bind to 127.0.0.1. Migrations run once in their own service.

## Assumptions

- A rental is a pick-up time plus a whole number of days, returned at the time of day it went
  out. Days are counted on the customer's clock: across a daylight-saving change two days are
  47 or 49 elapsed hours and the car is still due at 10:00.
- Times must be timezone-aware and are normalised to UTC. A time without a zone is rejected.
- Bookings last at most 90 days and start at most 365 days ahead, or "now" with 5 minutes of
  grace. These are `BookingPolicy` constructor arguments.
- No turnaround time between rentals. A rental can be cancelled until the moment it ends.

## Out of scope

- **Pricing and payments.** The brief does not ask for them, and a token `daily_rate` column
  would be worse than nothing. The seam is ready: a payment step slots into `reserve` as a
  `pending` status with an expiring hold, and the idempotency key providers require exists.
- **REST API.** `ReservationDesk` is the complete interface. An API would map HTTP to desk
  calls and `DomainError` subclasses to status codes.

## Taking it to production

What I would do with more time, roughly in this order.

- **Secrets.** A managed secret store (Azure Key Vault or equivalent) read through a managed
  identity. No code change: every secret already comes from the environment.
- **Database.** Managed PostgreSQL with backups and a standby. A least-privilege role for the
  application; the compose user is a superuser only because the test runner creates its own
  database. The row-lock design does not change.
- **Runtime.** A container platform (AKS or Container Apps). Split `/healthz` in two: readiness
  checks the database, liveness must not, or a database blip restarts healthy containers.
- **Identity.** Single sign-on (Entra ID / OIDC) instead of open sign-up, rate limiting, and a
  Content-Security-Policy header.
- **Caching.** Cache what is displayed (the car types, the availability count for a few
  seconds), never what is decided. A booking always re-reads availability under the lock.
- **Observability.** The JSON logs ship as they are. Add metrics on lock wait time and
  refusals, which are the first signs of contention, and tracing.
- **Delivery.** CI already tests, audits and builds the image. Add a registry push, staged
  deploys, and migrations as a pre-deploy job.
- **Tests.** Browser tests (Playwright) once the page has client-side logic, and a load test
  of the booking path to find the real ceiling per car type. If one type became a bottleneck:
  partition inventory by branch, then per-day capacity counters.
- **Product.** The REST API with token auth, per-branch inventory, an audit trail of state
  changes, a turnaround buffer (one more policy setting), and a time-to-live for idempotency
  keys.

## Known trade-offs

- Reducing a `fleet_size` below existing bookings is allowed and simply stops new bookings. A
  real system would warn staff or block the change.
- mypy is strict on the domain only. The Django layers would need `django-stubs`.
- Idempotency keys never expire. They live on the reservation row, so the cost is a column
  and an index.
