# Patient Service

Part of the Hospital Appointment Booking system (SEZG583, Group 11).

Owns the patient registry and decides whether a patient may book. It has its
own PostgreSQL database (`patient_db`); no other service reads it directly.

**Stack:** Python 3.13 · FastAPI · SQLAlchemy · PostgreSQL · pytest
**Port:** `8002`

## Business rules

| Rule | What it means | How it is enforced |
| --- | --- | --- |
| BR11 | A phone number belongs to one patient | Unique constraint on `patients.phone`; a duplicate returns `409 PATIENT_PHONE_EXISTS` |
| BR12 | A patient is blocked from booking after 3 no-shows | The no-show count and status change in one conditional `UPDATE`; at the limit the status becomes `BLOCKED` |
| BR12 | Each missed appointment counts once | Unique `booking_id` on `patient_no_shows`; a repeat report returns `200` with `Idempotent-Replayed: true` |
| BR4 | Bookings for unknown patients are rejected | Every patient lookup returns `404 PATIENT_NOT_FOUND` for an unknown id |

Patient status: `ACTIVE → BLOCKED` automatically at the no-show limit,
`BLOCKED → ACTIVE` when staff unblock (the count resets to 0; the no-show
history is kept).

## API

Base URL: `http://localhost:8002`. Interactive docs: `http://localhost:8002/docs`.

| Method | Path | Purpose | Success |
| --- | --- | --- | --- |
| GET | `/health` | Is the service and its database up? | `200` (`503` if the database is down) |
| POST | `/api/v1/patients` | Register a patient | `201` |
| GET | `/api/v1/patients` | List patients | `200` |
| GET | `/api/v1/patients/{id}` | Get one patient | `200` |
| GET | `/api/v1/patients/{id}/eligibility` | May this patient book? (for Booking Service) | `200` |
| POST | `/api/v1/patients/{id}/no-shows` | Record a missed appointment | `201` (`200` if already recorded) |
| POST | `/api/v1/patients/{id}/unblock` | Unblock a blocked patient | `200` |

### Register a patient

```http
POST /api/v1/patients
Content-Type: application/json

{
  "full_name": "test patient",
  "age": 30,
  "phone": "9876543210"
}
```

```json
{
  "id": 1,
  "full_name": "test patient",
  "age": 30,
  "phone": "9876543210",
  "status": "ACTIVE",
  "no_show_count": 0
}
```

Validation: `full_name` 2–100 characters, `age` 0–120, `phone` 10–14 digits
with an optional leading `+`.

### Check eligibility

```http
GET /api/v1/patients/1/eligibility
```

```json
{ "patient_id": 1, "eligible": false, "reason": "PATIENT_BLOCKED",
  "message": "Patient is blocked from booking after 3 no-shows." }
```

### Record a no-show

```http
POST /api/v1/patients/1/no-shows
Content-Type: application/json

{ "booking_id": 42 }
```

Returns the updated patient. Reporting the same `booking_id` again returns
`200` with header `Idempotent-Replayed: true` and does not change the count.
Reporting it for a different patient returns `409 NO_SHOW_OTHER_PATIENT`.

### Unblock

```http
POST /api/v1/patients/1/unblock
```

Returns the patient as `ACTIVE` with `no_show_count: 0`. Unblocking a patient
who is not blocked returns `409 INVALID_STATE_TRANSITION`.

### Errors

Every error has the same shape:

```json
{
  "error": {
    "code": "PATIENT_NOT_FOUND",
    "message": "Patient not found.",
    "correlation_id": "6f1c…"
  }
}
```

Validation errors (`422 VALIDATION_ERROR`) also list the failing fields in
`details`. Send an `X-Correlation-ID` header to trace a request across
services; if you don't, one is generated. It is returned in the response
header and written to every log line (logs are JSON on stdout).

## Run locally (Windows / PowerShell)

Needs Python 3.13 and PostgreSQL running on `localhost:5432` with a
`patient_svc` user and a `patient_db` database (see `.env.example`). The
tables are created automatically on startup.

```powershell
cd services\patientService

# create and activate a virtual environment
py -m venv venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1

# install dependencies
pip install -r requirements.txt

# create your local config (only the first time)
Copy-Item .env.example .env

# start the service
python main.py
```

Then open `http://localhost:8002/health` or `http://localhost:8002/docs`.

### Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOST` / `PORT` | `127.0.0.1` / `8002` | Where the service listens |
| `LOG_LEVEL` | `INFO` | Log level |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` | PostgreSQL server |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `patient_db` / `patient_svc` / *(required)* | Database credentials |
| `NO_SHOW_LIMIT` | `3` | No-shows before a patient is blocked |

Environment variables already set (for example by Docker) take priority over `.env`.

## Tests

The tests use an in-memory SQLite database, so PostgreSQL is not needed.

```powershell
pip install -r requirements-dev.txt
pytest -v
```

They cover every business rule above, input validation, the error format and
the health check (16 tests).

## Files

| File | Contents |
| --- | --- |
| `main.py` | HTTP routes; maps requests to the business rules |
| `patient_service.py` | Business rules: eligibility, no-shows, unblock |
| `models.py` | Database tables (`patients`, `patient_no_shows`) |
| `schemas.py` | Request and response models with validation |
| `database.py` | Database connection |
| `common.py` | JSON logging, correlation IDs, error format, health check |
| `test_business_rules.py`, `test_health.py` | Tests |
