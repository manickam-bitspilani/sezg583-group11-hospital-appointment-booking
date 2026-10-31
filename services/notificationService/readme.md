# Notification Service

Part of the Hospital Slot Booking system. This service sends appointment SMS and
e-mail messages through a **mock provider** and keeps a delivery log. It sends
each message at most once, even when the caller retries. You can make the
provider slow or failing at runtime to test how callers handle failure.

| | |
|---|---|
| Port | `8004` |
| Database / role | `notification_db` / `notification_svc` (database-per-service) |
| Called by | Booking Service (booking confirmation and cancellation) |
| Calls | nothing — the "provider" is simulated in-process |

There is no real SMS gateway. A successful "send" writes a JSON log line
`"Mock SMS sent"` with the text in `sms_text`, and stores the row as `SENT`.
In production, `deliver()` in `notification_service.py` would call a real
provider (Twilio, SES, ...).

## Business rules

| Rule | Where | Test |
|---|---|---|
| **BR6** A patient is notified at most once per `Idempotency-Key`; same key with a different payload → `422` | `notification_service.py` (`claim_notification`), `models.py` (unique `idempotency_key`) | `test_br6_*` |

Notification states: `PENDING → SENT | FAILED`, `FAILED → PENDING` on a retry
with the same key.

How "at most once" works:
- The first request inserts a `PENDING` row for its key. The unique constraint
  means only one request can do that.
- A repeated key whose row is `SENT` gets the stored row back (`200`,
  `Idempotent-Replayed: true`). Nothing is sent again.
- A repeated key whose row is `FAILED` is claimed again with a conditional
  `UPDATE ... WHERE status = 'FAILED'` and re-sent. This is how the caller's
  retry eventually succeeds.
- A repeated key whose row is still `PENDING` (first attempt in flight) waits up
  to `NOTIFICATION_IN_PROGRESS_WAIT_SECONDS` for it to finish, then returns
  its result. If it is still running, the answer is `409 REQUEST_IN_PROGRESS`
  and the caller retries.

The retry policy itself (attempts, timeout, backoff with jitter) is in Booking
Service. This service makes those retries safe.

## Database

`notification_db` holds one table. The service creates it on startup.

**`notifications`**: one row per `Idempotency-Key`.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `idempotency_key` | varchar(255) | unique (BR6); Booking uses `booking-<id>-confirmation` and `booking-<id>-cancellation` |
| `request_fingerprint` | varchar(64) | SHA-256 of the request body |
| `patient_id`, `booking_id` | integer | external IDs; no cross-database foreign keys |
| `notification_type` | varchar(10) | `SMS` or `EMAIL` |
| `message` | varchar(500) | |
| `status` | varchar(20) | `PENDING`, `SENT` or `FAILED` |
| `attempts` | integer | provider attempts made for this key |
| `last_error` | varchar(255) | why the latest attempt failed; cleared on success |
| `correlation_id` | varchar(64) | |
| `created_at`, `sent_at` | timestamptz | |

### Create the database (once)

Run as a PostgreSQL superuser:

```sql
CREATE ROLE notification_svc LOGIN PASSWORD '<your password>';
CREATE DATABASE notification_db OWNER notification_svc;
REVOKE CONNECT ON DATABASE notification_db FROM PUBLIC;
```

macOS / Linux: `psql -U postgres`, then paste the lines.
Windows: `& "C:\Program Files\PostgreSQL\15\bin\psql.exe" -U postgres` (change `15` to your version).
Use the same password as `DB_PASSWORD` in this service's `.env`. The root
`./scripts/setup_local.sh` does all of this for every service.
If it says the role or database already exists, skip this step.

### Look at the data

```bash
psql -h localhost -U notification_svc -d notification_db      # password: DB_PASSWORD in .env
```

```sql
SELECT id, idempotency_key, booking_id, status, attempts, last_error, message
FROM notifications ORDER BY id;
```

Or connect a GUI (VS Code PostgreSQL extension, pgAdmin, DBeaver) to
`localhost:5432`, database `notification_db`, user `notification_svc`, password
from `DB_PASSWORD` in `.env`.

### Upgrade an older `notification_db`

Only needed if your `notification_db` was created before `last_error` was added.
A new database doesn't need it. Safe to run more than once:

```sql
\connect notification_db
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS last_error VARCHAR(255);
```

## API

Interactive docs: http://localhost:8004/docs

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/health` | `200` (database answers) | `503` (database down) |
| `POST` | `/api/v1/notifications` (header `Idempotency-Key` required) | `202` sent; `200` replay with `Idempotent-Replayed: true` | `503 PROVIDER_UNAVAILABLE`, `409 REQUEST_IN_PROGRESS`, `422 IDEMPOTENCY_KEY_REUSED`, `422` validation |
| `GET` | `/api/v1/notifications?patient_id=&booking_id=` | `200` (both filters optional) | |
| `GET` | `/api/v1/notifications/{id}` | `200` | `404 NOTIFICATION_NOT_FOUND` |
| `GET` | `/api/v1/simulation` | `200` current provider settings | |
| `PUT` | `/api/v1/simulation` | `200` | `422` validation |

Request body for `POST /api/v1/notifications`:

```json
{ "patient_id": 1, "booking_id": 1, "message": "Appointment confirmed.", "notification_type": "SMS" }
```

`booking_id` is optional, `message` is 1–500 characters, and `notification_type` is `SMS` or `EMAIL`.

Every error uses the same body:

```json
{ "error": { "code": "PROVIDER_UNAVAILABLE", "message": "...", "correlation_id": "..." } }
```

Send `X-Correlation-ID` to trace a request across services. If you don't send
one, the service generates it. It is returned in the response and included in
every JSON log line.

### Simulating provider failures

`PUT /api/v1/simulation` changes the mock provider without a restart:

| Field | Range | Effect |
|---|---|---|
| `failure_rate` | 0–1 | Probability that an attempt fails |
| `latency_ms` | 0–60000 | Delay added to every attempt (use above 2000 to trigger Booking's timeout) |
| `fail_next` | 0–100 | Fail exactly this many upcoming attempts, then fall back to `failure_rate` (repeatable demos) |

`PUT` replaces all settings, so any field you leave out goes back to `0`.

```bash
# the next 2 attempts fail, the 3rd succeeds
curl -X PUT localhost:8004/api/v1/simulation -H 'content-type: application/json' -d '{"fail_next":2}'
# every attempt fails
curl -X PUT localhost:8004/api/v1/simulation -H 'content-type: application/json' -d '{"failure_rate":1.0}'
# back to normal
curl -X PUT localhost:8004/api/v1/simulation -H 'content-type: application/json' -d '{}'
```

## Configuration

Environment variables. For local runs, copy `.env.example` to `.env`. Real
environment variables take precedence over `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8004` | Bind address and port |
| `LOG_LEVEL` | `INFO` | Log level |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | `localhost`, `5432`, `notification_db`, `notification_svc` | PostgreSQL connection |
| `DB_PASSWORD` | (required) | Password for `DB_USER` |
| `DATABASE_URL` | | Optional; overrides the `DB_*` settings |
| `NOTIFICATION_FAILURE_RATE` | `0` | Start-up value of `failure_rate` |
| `NOTIFICATION_LATENCY_MS` | `0` | Start-up value of `latency_ms` |
| `NOTIFICATION_FAIL_NEXT` | `0` | Start-up value of `fail_next` |
| `NOTIFICATION_IN_PROGRESS_WAIT_SECONDS` | `1.5` | How long a retry waits for an in-flight delivery of the same key |

## Install and run

Prerequisites: Python 3.12+ and PostgreSQL 14+ on `localhost:5432`, with the
database created as described above.

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

The service listens on http://127.0.0.1:8004 and creates its table on first start.

### Try it

```bash
curl localhost:8004/health

# send twice with the same key: the second answer is 200 with Idempotent-Replayed: true
curl -i -X POST localhost:8004/api/v1/notifications -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-sms-1' \
  -d '{"patient_id":1,"booking_id":1,"message":"Appointment confirmed.","notification_type":"SMS"}'

curl 'localhost:8004/api/v1/notifications?booking_id=1'
```

On Windows PowerShell, use `curl.exe` and escape the inner quotes (`\"`), or use
the Swagger page at `/docs`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Unit tests run against an in-memory SQLite database, so PostgreSQL is not needed.
They cover BR6, a failed send followed by a successful retry with the same key,
`fail_next`, `last_error`, the in-flight `409` and the health check. The
resilience tests (Challenge 3A: retries, slow provider, service down) need all
four services and live in the system's shared `scripts/` folder.

## Files

| File | Purpose |
|---|---|
| `main.py` | HTTP endpoints and the simulation API |
| `notification_service.py` | BR6: claim a key, deliver via the mock provider, wait for an in-flight send |
| `models.py` | `notifications` table and constraints |
| `schemas.py` | Request/response models, simulation settings, input validation |
| `database.py` | Engine and session; reads the `DB_*` settings |
| `common.py` | JSON logging, correlation IDs, uniform errors, health check (each service has its own copy) |
