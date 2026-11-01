# Hospital Slot Booking — Microservices

Patients book doctor appointment slots. A booking validates the patient, reserves
a slot (a contended resource with limited capacity) and sends a confirmation
SMS through a mock provider that can be made slow or failing.

| Service | Port | Owns | Database / role |
|---|---|---|---|
| slot-service | 8001 | Doctor slots, capacity, reservations | `slot_db` / `slot_svc` |
| patient-service | 8002 | Patient registry, booking eligibility, no-show policy | `patient_db` / `patient_svc` |
| booking-service | 8003 | Booking transaction, idempotency records | `booking_db` / `booking_svc` |
| notification-service | 8004 | Mock SMS/e-mail provider, delivery log | `notification_db` / `notification_svc` |

Services talk only over REST (`/api/v1/...`). No service reads another's
database; each role can only `CONNECT` to its own database.

```
client ──POST /api/v1/bookings──▶ booking-service
                                   ├─ GET  /api/v1/patients/{id}/eligibility ─▶ patient-service
                                   ├─ POST /api/v1/reservations    ─▶ slot-service
                                   └─ POST /api/v1/notifications   ─▶ notification-service
```

## Prerequisites

- Python 3.12+ (tested with 3.13)
- PostgreSQL 14+ running on `localhost:5432`

## 1. Create the databases and configure (once)

```bash
./scripts/setup_local.sh        # add psql flags if needed, e.g. -h localhost -U postgres
```

This generates random passwords in `secrets/*.txt` (gitignored, shared with
Docker Compose), creates one database and one login role per service with
[scripts/setup_databases.sql](scripts/setup_databases.sql), and writes each
service's `.env` from its `.env.example` with the password filled in. No
password is stored in the repository. Existing databases and `.env` files are
left untouched, so it is safe to run again. If the databases already existed,
any new `.env` gets the placeholder and the script tells you to set
`DB_PASSWORD` to that role's existing password.

Without bash (e.g. Windows PowerShell), pass the passwords to psql yourself and
put the same value in each service's `.env` as `DB_PASSWORD`:

```bash
psql -U postgres -v patient_pw=... -v slot_pw=... -v booking_pw=... -v notification_pw=... -f scripts/setup_databases.sql
```

**Already have the databases from an earlier version?** Run the migrations
once (both are safe to repeat):

```bash
psql -U postgres -f scripts/migrate_patient_status.sql
psql -U postgres -f scripts/migrate_notification.sql
```

## 2. Configure

Each service reads its configuration from environment variables. For local runs
they come from the service's `.env`, created in step 1 (`.env.example` holds
placeholders only). Real environment variables (Docker, Kubernetes) take
precedence over `.env`.

| Variable | Used by | Meaning |
|---|---|---|
| `HOST`, `PORT` | all | Bind address and port |
| `LOG_LEVEL` | all | `INFO`, `DEBUG`, ... |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | all | Own PostgreSQL database |
| `DB_PASSWORD_FILE` | all | Optional; read the password from this file instead of `DB_PASSWORD` (Docker secrets) |
| `DATABASE_URL` | all | Optional; overrides the `DB_*` settings |
| `PATIENT_SERVICE_URL`, `SLOT_SERVICE_URL`, `NOTIFICATION_SERVICE_URL` | booking | Downstream base URLs |
| `DOWNSTREAM_TIMEOUT_SECONDS`, `NOTIFICATION_TIMEOUT_SECONDS` | booking | HTTP timeouts |
| `NOTIFICATION_MAX_ATTEMPTS`, `NOTIFICATION_BACKOFF_BASE_SECONDS`, `NOTIFICATION_BACKOFF_MAX_SECONDS` | booking | Notification retry policy |
| `NOTIFICATION_IN_PROGRESS_WAIT_SECONDS` | notification | How long a retry waits for an in-flight delivery of the same key |
| `IDEMPOTENCY_WAIT_SECONDS` | booking | How long a duplicate in-flight request waits for the first one |
| `NOTIFICATION_FAILURE_RATE` (0–1), `NOTIFICATION_LATENCY_MS` | notification | Mock provider behaviour |
| `NOTIFICATION_FAIL_NEXT` | notification | Fail exactly this many upcoming sends, then fall back to the failure rate (default 0) |
| `NO_SHOW_LIMIT` | patient | No-shows after which a patient is blocked from booking (default 3) |

## 3. Install and run

Each service has its own virtual environment and `requirements.txt` (runtime
dependencies only; `requirements-dev.txt` adds the test tools). In four
terminals:

```bash
cd services/slotService     # then services/patientService, services/bookingService, services/notificationService
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Tables are created automatically on startup. Check health:

```bash
curl localhost:8001/health localhost:8002/health localhost:8003/health localhost:8004/health
```

`/health` runs `SELECT 1` against the service's own database: `200 {"status":"healthy","database":"up"}`
when it answers, `503 {"status":"unhealthy","database":"down"}` when it doesn't (e.g. PostgreSQL
stopped). It recovers by itself once the database is back.

Swagger UI: `http://localhost:<port>/docs`. Exported specs are in [docs/openapi/](docs/openapi/).

## 4. Try it

```bash
curl -X POST localhost:8002/api/v1/patients -H 'content-type: application/json' \
  -d '{"full_name":"Asha Rao","age":30,"phone":"9876543210"}'

curl -X POST localhost:8001/api/v1/slots -H 'content-type: application/json' \
  -d '{"doctor_id":101,"start_time":"2027-03-01T10:00:00Z","end_time":"2027-03-01T10:30:00Z","capacity":2}'

curl -i -X POST localhost:8003/api/v1/bookings -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-1' -d '{"patient_id":1,"slot_id":1}'

# Cancel (frees the place, sends a cancellation SMS; repeating it changes nothing)
curl -i -X POST localhost:8003/api/v1/bookings/1/cancel
```

Record no-shows (front desk); the third one blocks the patient, and a blocked
patient's booking gets `409 PATIENT_BLOCKED` until they are unblocked:

```bash
curl -X POST localhost:8002/api/v1/patients/1/no-shows -H 'content-type: application/json' -d '{"booking_id":1}'
curl localhost:8002/api/v1/patients/1/eligibility
curl -X POST localhost:8002/api/v1/patients/1/unblock
```

Make the SMS provider fail (or slow) without restarting:

```bash
curl -X PUT localhost:8004/api/v1/simulation -H 'content-type: application/json' \
  -d '{"failure_rate":1.0,"latency_ms":0}'
```

For repeatable demos, fail an exact number of sends instead of a random share.
Here the next 2 attempts fail and the 3rd succeeds:

```bash
curl -X PUT localhost:8004/api/v1/simulation -H 'content-type: application/json' -d '{"fail_next":2}'
```

`PUT` replaces all the settings, so any field left out goes back to 0. A failed
attempt's reason is stored in the notification's `last_error`, and every
successful send logs a `"Mock SMS sent"` line with the SMS text (`sms_text`).

## 5. Tests

Install the test tools once per service, in that service's virtual environment:

```bash
pip install -r requirements-dev.txt
```

**Unit tests**: one per business rule. They use in-memory SQLite, so you
don't need PostgreSQL or the other services running:

```bash
cd services/slotService && pytest      # likewise services/patientService, services/bookingService, services/notificationService
```

**Integration / challenge scripts**: these need all four services running.
Run them from the repo root with any service's dev environment activated
(they only need `pytest` and `httpx`):

```bash
pytest -s scripts/test_end_to_end.py    # main transaction + failure paths
pytest -s scripts/test_concurrency.py   # Challenge 1 (CAPACITY=1 REQUESTS=20 to vary) + parallel cancels
pytest -s scripts/test_idempotency.py   # Challenge 2 (sequential + simultaneous)
pytest -s scripts/test_resilience.py    # Challenge 3A: retries with exact failures (fail_next) + slow provider
# stop notification-service, then:
NOTIFICATION_DOWN=1 pytest -s scripts/test_resilience.py -k down
```

## 6. Run with Docker Compose

Needs Docker with Compose v2. This replaces steps 1–3: PostgreSQL and the four
services all run as containers. Stop any locally running services first, because
the containers publish the same ports (8001–8004). A local service bound to
`127.0.0.1` silently takes the traffic meant for the container, so check first
(this was our Task 3 deployment problem; see
[evidence/assignment-2/task3-port-conflict/](evidence/assignment-2/task3-port-conflict/)):

```bash
./scripts/docker/check_ports.sh        # every port must say "ok"
```

```bash
./scripts/docker/generate_secrets.sh   # once: random passwords in secrets/*.txt (gitignored)
docker compose up --build              # builds the 4 images, starts postgres + 4 services
```

In another terminal:

```bash
docker compose ps                      # all five should show (healthy)
docker compose logs -f booking-service # JSON logs; follow one X-Correlation-ID across services
```

The `curl` commands in [4. Try it](#4-try-it) and the scripts in [5. Tests](#5-tests)
work unchanged against `localhost:8001–8004`.

For a demo, `./scripts/docker/demo.sh` walks through images, health, the main
workflow, the cross-service trace, tests, logs, secrets and a restart, printing
each command before running it (pass a step name, e.g. `demo.sh workflow`, to run
one step).

| Container | Image | Notes |
|---|---|---|
| `postgres` | `postgres:16-alpine` | Not published to the host. On first start `scripts/docker/init-databases.sh` creates one database and role per service. Data is kept in the `pgdata` volume |
| `slot-service`, `patient-service`, `notification-service` | `hospital-booking/<name>:1.0` | Start once `postgres` is healthy |
| `booking-service` | `hospital-booking/booking-service:1.0` | Starts once the other three are healthy; calls them by Compose DNS name (`http://patient-service:8002`, ...) |

- **Configuration** comes from environment variables in `docker-compose.yml`.
  Non-secret settings (`LOG_LEVEL`, `NO_SHOW_LIMIT`, mock SMS behaviour) can be
  overridden by copying `.env.example` to `.env`.
- **Passwords** are Docker secrets mounted at `/run/secrets/*` and read through
  `DB_PASSWORD_FILE`. They are never in an image, `.env` or the repository.
- **Health**: each image has a `HEALTHCHECK` on `/health`, which also checks the
  service's own database, so a container is only `healthy` when its database answers.
- Images contain only the runtime code (`.dockerignore` excludes `.env`, tests
  and virtual environments) and run as a non-root user.

Stop and restart:

```bash
docker compose down        # stop and remove containers; data stays in the pgdata volume
docker compose up -d       # start again; earlier bookings are still there
docker compose down -v     # also delete the data (next start re-creates the databases)
```

`init-databases.sh` runs only when the `pgdata` volume is empty. After changing
`secrets/*.txt`, run `docker compose down -v` so the roles are re-created with
the new passwords.

## Business rules

| Rule | Enforced in | Test |
|---|---|---|
| **BR1** A slot is never booked beyond its capacity | `services/slotService/reservation_service.py` (atomic conditional `UPDATE`), `models.py` (`CHECK reserved_count <= capacity`) | `services/slotService/test_business_rules.py::test_br1_*`, `scripts/test_concurrency.py` |
| **BR2** A patient holds at most one reservation per slot | `services/slotService/models.py` (unique `slot_id, patient_id`), `reservation_service.py` | `services/slotService/test_business_rules.py::test_br2_*` |
| **BR3** Same Idempotency-Key returns the original result with no new side effects; same key + different payload is rejected (422) | `services/bookingService/booking_service.py` (key + fingerprint + stored response), `services/slotService/reservation_service.py` | `services/bookingService/test_business_rules.py::test_br3_*`, `services/slotService/...::test_br3_*`, `scripts/test_idempotency.py` |
| **BR4** A booking requires an existing patient | `services/bookingService/booking_service.py` (`_run_booking`, step 1), `services/patientService/main.py` (`get_eligibility` → 404) | `services/bookingService/test_business_rules.py::test_br4_*`, `services/patientService/test_business_rules.py::test_br4_*` |
| **BR5** A confirmed booking stays confirmed if the notification fails | `services/bookingService/booking_service.py` (`_notify`) | `services/bookingService/test_business_rules.py::test_br5_*`, `scripts/test_end_to_end.py` |
| **BR6** A patient is notified at most once per booking | `services/notificationService/notification_service.py` (`claim_notification`) | `services/notificationService/test_business_rules.py::test_br6_*` |
| **BR7** A slot has positive capacity and ends after it starts | `services/slotService/schemas.py`, `models.py` | `services/slotService/test_business_rules.py::test_br7_*` |
| **BR8** A slot must start in the future, and a slot that has started can't be reserved | `services/slotService/slot_service.py`, `reservation_service.py` (`start_time > now` in the capacity `UPDATE`) | `services/slotService/...::test_br8_*`, `scripts/test_end_to_end.py::test_slot_rules_past_and_overlap` |
| **BR9** A doctor can't have overlapping slots (back-to-back is fine) | `services/slotService/slot_service.py` (per-doctor advisory lock + overlap query) | `services/slotService/...::test_br9_*`, `scripts/test_end_to_end.py::test_slot_rules_past_and_overlap` |
| **BR10** Only a `CONFIRMED` booking can be cancelled, only before the slot starts; cancelling frees the place exactly once | `services/slotService/reservation_service.py` (`ALLOWED_TRANSITIONS`, `cancel_reservation`), `services/bookingService/booking_service.py` (`cancel_booking`) | `services/slotService/...::test_br10_*`, `services/bookingService/...::test_br10_*`, `scripts/test_concurrency.py::test_concurrent_cancels_*` |
| **BR11** A phone number belongs to one patient | `services/patientService/models.py` (unique `phone`), `main.py` (`create_patient` → 409 `PATIENT_PHONE_EXISTS`) | `services/patientService/test_business_rules.py::test_br11_*` |
| **BR12** Only an `ACTIVE` patient can book; a patient is `BLOCKED` automatically after `NO_SHOW_LIMIT` no-shows (each booking's no-show counts once) and booking is refused until staff unblock them | `services/patientService/patient_service.py` (`check_eligibility`, `record_no_show`, `unblock`), `services/bookingService/booking_service.py` (`_run_booking`, step 1) | `services/patientService/test_business_rules.py::test_br12_*`, `services/bookingService/...::test_br12_*`, `scripts/test_end_to_end.py::test_blocked_patient_*` |

## Design notes

**Concurrency (BR1).** Capacity is claimed with one statement,
`UPDATE appointment_slots SET reserved_count = reserved_count + 1 WHERE id = :id AND reserved_count < capacity`,
in the same transaction as the reservation insert. PostgreSQL row-locks the slot
during the update, so concurrent requests serialise on it, and `rowcount = 0`
means the slot is full. A check-then-update in Python (read `reserved_count`,
compare, write) would let several requests read the same value before any of
them writes, which over-allocates the slot.

**Idempotency (BR3).** booking-service inserts `(idempotency_key, sha256(request))`
as a `PENDING` booking before doing any work. The unique constraint means only
one of several simultaneous identical requests wins. The others wait for that
booking to reach `CONFIRMED` or `FAILED` and then return the stored response
with `Idempotent-Replayed: true`. Business failures (patient missing, slot
full) are stored and replayed. Transient failures (timeout, service down)
release the key so the client can retry it. That retry is safe because
slot-service is also idempotent on the same key, and notification-service
dedupes on `booking-<id>-confirmation`.

**Notification resilience.** Booking calls Notification with a 2 s timeout and
up to 4 attempts. It retries on 409 (attempt still in flight), 502, 503, 504, timeouts and
refused connections, and never on other 4xx errors. Between attempts it waits
`uniform(0, min(2 s, 0.2 s × 2^(attempt-1)))`: exponential backoff with full
jitter, so many clients don't retry in lockstep. Every attempt carries the
same `Idempotency-Key: booking-<id>-confirmation`. If an attempt times out on
the caller's side but the provider finishes anyway, the retry waits briefly
for that delivery and returns its result, so it never sends a second SMS. If
all attempts fail, the booking stays `CONFIRMED` with
`notification_status: FAILED` (BR5).

**State machines.** Every transition is a conditional `UPDATE ... WHERE status = <from>`,
so of several concurrent requests only one performs it; anything not listed is rejected
with `409 INVALID_STATE_TRANSITION`.

| Entity | Transitions |
|---|---|
| Booking | `PENDING → CONFIRMED \| FAILED`, `CONFIRMED → CANCELLED` |
| Reservation | `CONFIRMED → CANCELLED` (`PENDING` is reserved for future time-bound holds) |
| Notification | `PENDING → SENT \| FAILED`, `FAILED → PENDING` on a retry with the same key |
| Patient | `ACTIVE → BLOCKED` (automatic at the no-show limit), `BLOCKED → ACTIVE` (unblock) |

**Cancellation (BR10).** `POST /api/v1/bookings/{id}/cancel` calls Slot Service's
`POST /api/v1/reservations/{id}/cancel`. That flips the reservation to `CANCELLED` and
decrements `reserved_count` in one transaction, and only the request whose
`UPDATE` matched `status = 'CONFIRMED'` decrements. Booking then flips its own
record and sends a cancellation SMS (`Idempotency-Key: booking-<id>-cancellation`,
same retry policy). Repeating a cancel returns `200` with `Idempotent-Replayed: true`.
BR2's uniqueness is a partial index (`WHERE status <> 'CANCELLED'`), so a patient
can book the same slot again after cancelling.

**Observability.** Every service logs JSON lines to stdout. Each line carries
the `X-Correlation-ID`, which is taken from the incoming request (or generated),
passed to downstream calls and returned in the response header.

**Errors.** Every error, validation errors included, has one shape:

```json
{"error": {"code": "SLOT_FULL", "message": "Appointment slot is full.", "correlation_id": "…", "details": [...]}}
```

`common.py` is copied into every service on purpose. Each service has to build
and deploy on its own, so there is no shared library.

## Known limitations

- Retries happen inside the booking request (adding up to about 2 s of backoff
  plus timeouts). A notification that still fails after 4 attempts is not
  re-sent later; an outbox or background re-sender would cover long outages.
- No circuit breaker: during a long outage every booking still makes 4 attempts.
- A process crash while a booking is `PENDING` leaves that key blocked
  (requests get `409 REQUEST_IN_PROGRESS`). A stale-claim timeout would fix this.
- Reservations are confirmed immediately; there are no time-bound holds.
- Cancellation is two local transactions (Slot, then Booking). If Booking
  crashes in between, the reservation is cancelled while the booking still
  shows `CONFIRMED`; repeating the cancel repairs it, because Slot's cancel
  is idempotent.
- **A timed-out booking can leave a place held with no booking.** If Slot
  commits the reservation but Booking times out waiting for the reply,
  Booking returns `504` and asks the client to retry with the same
  `Idempotency-Key` (a retry completes the booking safely). If the client
  never retries, the reservation stays active with no booking: the place is
  lost to others, and the patient gets `409 DUPLICATE_BOOKING` on a new key.
  Nothing repairs this automatically. Fix: time-bound holds that expire
  (Challenge 3B) or a compensating cancel (saga, Challenge 3C).
