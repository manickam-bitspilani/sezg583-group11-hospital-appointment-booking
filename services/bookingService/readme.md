# Booking Service

Part of the Hospital Slot Booking system. This service runs the booking
transaction: it checks the patient may book, reserves a place in the slot and
sends the confirmation SMS. It also cancels bookings and makes both operations
safe to repeat (idempotency).

| | |
|---|---|
| Port | `8003` |
| Database / role | `booking_db` / `booking_svc` (database-per-service) |
| Called by | clients (front desk, patient app) |
| Calls | Patient Service `:8002`, Slot Service `:8001`, Notification Service `:8004` (REST only) |

```
client ──POST /api/v1/bookings──▶ booking-service
                                   ├─ 1. GET  /api/v1/patients/{id}/eligibility ─▶ patient-service
                                   ├─ 2. POST /api/v1/reservations              ─▶ slot-service
                                   └─ 3. POST /api/v1/notifications             ─▶ notification-service
```

Booking stores only the IDs of patients, slots and reservations. It never reads
another service's database.

**Booking needs the other three services running** for real bookings. Its unit
tests replace them with fakes, so `pytest` works without them.

## Business rules

| Rule | Where | Test |
|---|---|---|
| **BR3** Same `Idempotency-Key` returns the original result with no new side effects; same key with a different payload → `422` | `booking_service.py` (key + request fingerprint + stored response) | `test_br3_*` |
| **BR4** A booking requires an existing patient (`404 PATIENT_NOT_FOUND`) | `booking_service.py` (`_run_booking`, step 1) | `test_br4_*` |
| **BR5** A confirmed booking stays confirmed if the notification fails (`notification_status: FAILED`) | `booking_service.py` (`_notify`) | `test_br5_*` |
| **BR10** Only a `CONFIRMED` booking can be cancelled, only before the slot starts | `booking_service.py` (`cancel_booking`) | `test_br10_*` |
| **BR12** A blocked patient can't book (`409 PATIENT_BLOCKED`); the rule lives in Patient Service | `booking_service.py` (`_run_booking`, step 1) | `test_br12_*` |

Booking states: `PENDING → CONFIRMED | FAILED`, `CONFIRMED → CANCELLED`. Every
transition is a conditional `UPDATE ... WHERE status = <from>`.

**Idempotency (BR3).** Before doing any work, the service inserts the
`Idempotency-Key` and a SHA-256 fingerprint of the request as a `PENDING`
booking. The unique key means only one of several identical simultaneous
requests wins. The others wait (up to `IDEMPOTENCY_WAIT_SECONDS`) and then
return the stored response with `Idempotent-Replayed: true`.
- Business failures (patient missing, slot full) are stored and replayed.
- Transient failures (timeout, service down) release the key, so the client
  can retry with the same key. That is safe because Slot Service is idempotent
  on the same key and Notification Service dedupes on `booking-<id>-confirmation`.

**Notification retries.** Each attempt has a 2 s timeout, with up to 4 attempts.
Retries happen on 409 (attempt still in flight), 502, 503, 504, timeouts and
refused connections, never on other 4xx. The wait between attempts is
`uniform(0, min(2 s, 0.2 s × 2^(attempt-1)))` (exponential backoff with full
jitter). Every attempt sends the same `Idempotency-Key`, so the patient gets at
most one SMS. If all attempts fail, the booking stays `CONFIRMED` (BR5).

**Cancellation (BR10).** Booking calls Slot Service's
`POST /api/v1/reservations/{id}/cancel`, which frees the place. It then
changes its own record to `CANCELLED` and sends a cancellation SMS
(`booking-<id>-cancellation`, same retry policy). Repeating a cancel returns
`200` with `Idempotent-Replayed: true`.

## Database

`booking_db` holds one table. The service creates it on startup.

**`bookings`**: one row per booking request. It is both the booking record and
the idempotency record.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `idempotency_key` | varchar(255) | unique (BR3) |
| `request_fingerprint` | varchar(64) | SHA-256 of the request body |
| `patient_id`, `slot_id`, `reservation_id` | integer | external IDs; no cross-database foreign keys |
| `status` | varchar(20) | `PENDING`, `CONFIRMED`, `FAILED` or `CANCELLED` |
| `notification_status` | varchar(20) | confirmation SMS: `SENT` or `FAILED` |
| `cancel_notification_status` | varchar(20) | cancellation SMS: `SENT` or `FAILED` |
| `response_status_code`, `response_body` | integer, JSON | stored answer, replayed for a repeated key |
| `correlation_id` | varchar(64) | |
| `created_at`, `updated_at`, `cancelled_at` | timestamptz | |

### Create the database (once)

Run as a PostgreSQL superuser:

```sql
CREATE ROLE booking_svc LOGIN PASSWORD '<your password>';
CREATE DATABASE booking_db OWNER booking_svc;
REVOKE CONNECT ON DATABASE booking_db FROM PUBLIC;
```

macOS / Linux: `psql -U postgres`, then paste the lines.
Windows: `& "C:\Program Files\PostgreSQL\15\bin\psql.exe" -U postgres` (change `15` to your version).
Use the same password as `DB_PASSWORD` in this service's `.env`. The root
`./scripts/setup_local.sh` does all of this for every service.
If it says the role or database already exists, skip this step.

### Look at the data

```bash
psql -h localhost -U booking_svc -d booking_db      # password: DB_PASSWORD in .env
```

```sql
SELECT id, idempotency_key, patient_id, slot_id, status, notification_status, cancel_notification_status
FROM bookings ORDER BY id;
```

Or connect a GUI (VS Code PostgreSQL extension, pgAdmin, DBeaver) to
`localhost:5432`, database `booking_db`, user `booking_svc`, password from `DB_PASSWORD` in `.env`.

### Upgrade an older `booking_db`

Only needed if your `booking_db` was created before cancellation was added. A
new database doesn't need it. Safe to run more than once:

```sql
\connect booking_db
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS check_booking_status;
ALTER TABLE bookings ADD CONSTRAINT check_booking_status
    CHECK (status IN ('PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED'));
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancel_notification_status VARCHAR(20);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS check_booking_cancel_notification_status;
ALTER TABLE bookings ADD CONSTRAINT check_booking_cancel_notification_status
    CHECK (cancel_notification_status IS NULL OR cancel_notification_status IN ('SENT', 'FAILED'));
```

## API

Interactive docs: http://localhost:8003/docs

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/health` | `200` (database answers) | `503` (database down) |
| `POST` | `/api/v1/bookings` (header `Idempotency-Key` required) | `201`; repeated key → stored response with `Idempotent-Replayed: true` | see below |
| `GET` | `/api/v1/bookings/{id}` | `200` | `404 BOOKING_NOT_FOUND` |
| `POST` | `/api/v1/bookings/{id}/cancel` | `200`; already cancelled → `200` with `Idempotent-Replayed: true` | `404`, `409 INVALID_STATE_TRANSITION`, `409 SLOT_ALREADY_STARTED`, `503`, `504` |

Booking errors:

| Status | Code | Meaning |
|---|---|---|
| `404` | `PATIENT_NOT_FOUND`, `SLOT_NOT_FOUND` | Unknown patient or slot |
| `409` | `PATIENT_BLOCKED` | Patient blocked after too many no-shows |
| `409` | `SLOT_FULL`, `DUPLICATE_BOOKING`, `SLOT_ALREADY_STARTED` | Passed through from Slot Service |
| `409` | `REQUEST_IN_PROGRESS` | Same key still being processed; retry shortly |
| `422` | `IDEMPOTENCY_KEY_REUSED` | Same key with a different body |
| `502` | `PATIENT_SERVICE_ERROR`, `SLOT_SERVICE_ERROR` | Unexpected downstream answer |
| `503` / `504` | `DOWNSTREAM_UNAVAILABLE` / `DOWNSTREAM_TIMEOUT` | Retry with the **same** `Idempotency-Key` |

Request body for `POST /api/v1/bookings`:

```json
{ "patient_id": 1, "slot_id": 1 }
```

Every error uses the same body:

```json
{ "error": { "code": "SLOT_FULL", "message": "Appointment slot is full.", "correlation_id": "..." } }
```

Send `X-Correlation-ID` to trace a request. Booking forwards it to every
downstream call, so one ID appears in all four services' logs.

## Configuration

Environment variables. For local runs, copy `.env.example` to `.env`. Real
environment variables take precedence over `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8003` | Bind address and port |
| `LOG_LEVEL` | `INFO` | Log level |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | `localhost`, `5432`, `booking_db`, `booking_svc` | PostgreSQL connection |
| `DB_PASSWORD` | (required) | Password for `DB_USER` |
| `DATABASE_URL` | | Optional; overrides the `DB_*` settings |
| `PATIENT_SERVICE_URL` | `http://127.0.0.1:8002` | Patient Service base URL |
| `SLOT_SERVICE_URL` | `http://127.0.0.1:8001` | Slot Service base URL |
| `NOTIFICATION_SERVICE_URL` | `http://127.0.0.1:8004` | Notification Service base URL |
| `DOWNSTREAM_TIMEOUT_SECONDS` | `5` | Timeout for Patient and Slot calls |
| `NOTIFICATION_TIMEOUT_SECONDS` | `2` | Timeout per notification attempt |
| `NOTIFICATION_MAX_ATTEMPTS` | `4` | Notification attempts in total |
| `NOTIFICATION_BACKOFF_BASE_SECONDS`, `NOTIFICATION_BACKOFF_MAX_SECONDS` | `0.2`, `2` | Backoff base and cap |
| `IDEMPOTENCY_WAIT_SECONDS` | `10` | How long a duplicate in-flight request waits for the first one |

If the other services run on another machine or port, change the three `*_SERVICE_URL` values.

## Install and run

Prerequisites: Python 3.12+ and PostgreSQL 14+ on `localhost:5432`, with the
database created as described above. Start Patient, Slot and Notification
services first.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

### Windows (PowerShell)

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

Command Prompt: activate with `.venv\Scripts\activate.bat` and copy with
`copy .env.example .env`.

The service listens on http://127.0.0.1:8003 and creates its table on first start.

### Try it

This needs a patient (Patient Service) and a slot (Slot Service) to exist first:

```bash
curl -X POST localhost:8002/api/v1/patients -H 'content-type: application/json' \
  -d '{"full_name":"Asha Rao","age":30,"phone":"9876543210"}'
curl -X POST localhost:8001/api/v1/slots -H 'content-type: application/json' \
  -d '{"doctor_id":101,"start_time":"2027-03-01T10:00:00Z","end_time":"2027-03-01T10:30:00Z","capacity":2}'

# book; run it twice with the same key: the second answer has Idempotent-Replayed: true
curl -i -X POST localhost:8003/api/v1/bookings -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-1' -d '{"patient_id":1,"slot_id":1}'

curl localhost:8003/api/v1/bookings/1
curl -i -X POST localhost:8003/api/v1/bookings/1/cancel
```

On Windows PowerShell, use `curl.exe` and escape the inner quotes (`\"`), or use
the Swagger page at `/docs`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Unit tests use an in-memory SQLite database and a fake for the three downstream
services, so neither PostgreSQL nor the other services need to run. They cover
BR3, BR4, BR5, BR10 and BR12, the retry and backoff policy, the uniform error
format and the health check. End-to-end, idempotency and resilience tests
(Challenges 2 and 3) need all four services and live in the system's shared
`scripts/` folder.

## Files

| File | Purpose |
|---|---|
| `main.py` | HTTP endpoints |
| `booking_service.py` | Booking transaction, idempotency record, notification retries, cancellation |
| `models.py` | `bookings` table and constraints |
| `schemas.py` | Request/response models and input validation |
| `database.py` | Engine and session; reads the `DB_*` settings |
| `common.py` | JSON logging, correlation IDs, uniform errors, health check (each service has its own copy) |
