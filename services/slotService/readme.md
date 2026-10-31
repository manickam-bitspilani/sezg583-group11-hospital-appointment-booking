# Hospital Appointment Booking System

A microservices-based hospital appointment booking system built with **Python, FastAPI, SQLAlchemy, and PostgreSQL**. It separates patient management, appointment-slot management, booking orchestration, and notification handling into independently runnable services.

> **Status note:** Before submitting, re-run the tests and verify that all database tables exist. The notification service currently simulates delivery; it does not send real SMS or email.

## Services

| Service | Default URL | Responsibility |
|---|---|---|
| Slot Service | `http://127.0.0.1:8001` | Creates appointment slots and manages reservation capacity and idempotency |
| Patient Service | `http://127.0.0.1:8002` | Registers patients and retrieves patient records |
| Booking Service | `http://127.0.0.1:8003` | Coordinates patient validation, slot reservation, and notification requests |
| Notification Service | `http://127.0.0.1:8004` | Accepts mock SMS/email notification requests |

Each service exposes `/health` and `/docs` when running.

## Technology Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Pydantic
- HTTPX
- Pytest
- Uvicorn

## Project Structure

```text
hospital-slot-booking/
├── slot-service/
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── reservation_schemas.py
│   ├── reservation_service.py
│   ├── requirements.txt
│   ├── test_concurrency.py
│   └── test_business_rules.py
├── patient-service/
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   └── requirements.txt
├── booking-service/
│   ├── main.py
│   └── requirements.txt
├── notification-service/
│   ├── main.py
│   └── requirements.txt
├── test_end_to_end.py
└── README.md
```

Adjust this tree to match the files actually committed to your repository.

## Prerequisites

- Python version supported by the service dependencies
- PostgreSQL
- Git
- VS Code (optional)

## Database Configuration

The current code is configured to use these local PostgreSQL databases:

- `slot_service_db`
- `patient_service_db`

Expected tables:
- `slot_service_db`: `appointment_slots`, `reservations`
- `patient_service_db`: `patients`

Separate databases for Booking Service and Notification Service have not been confirmed in the current implementation. If the assignment requires a database per service, configure and document those databases before submission.

Set the PostgreSQL password in each PowerShell terminal that runs a database-connected service:

```powershell
$env:DB_PASSWORD = Read-Host "Enter PostgreSQL password"
```

Never commit passwords, tokens, or other secrets. Add `.env` to `.gitignore`; any `.env.example` should contain placeholders only.

## Installation and Running

Run each service in a separate PowerShell terminal. These commands assume the project is located at `E:\Smitha\Projects\Assignment\hospital-slot-booking`. Update the path if yours differs.

### 1. Slot Service

```powershell
cd E:\Smitha\Projects\Assignment\hospital-slot-booking\slot-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DB_PASSWORD = Read-Host "Enter PostgreSQL password"
uvicorn main:app --reload --port 8001
```

- Swagger UI: http://127.0.0.1:8001/docs
- Health: http://127.0.0.1:8001/health

### 2. Patient Service

In a new terminal:

```powershell
cd E:\Smitha\Projects\Assignment\hospital-slot-booking\patient-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DB_PASSWORD = Read-Host "Enter PostgreSQL password"
uvicorn main:app --reload --port 8002
```

- Swagger UI: http://127.0.0.1:8002/docs
- Health: http://127.0.0.1:8002/health

### 3. Booking Service

In a new terminal:

```powershell
cd E:\Smitha\Projects\Assignment\hospital-slot-booking\booking-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8003
```

- Swagger UI: http://127.0.0.1:8003/docs
- Health: http://127.0.0.1:8003/health

### 4. Notification Service

In a new terminal:

```powershell
cd E:\Smitha\Projects\Assignment\hospital-slot-booking\notification-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8004
```

- Swagger UI: http://127.0.0.1:8004/docs
- Health: http://127.0.0.1:8004/health

Keep all four terminals running for integrated tests.

## API Overview

### Slot Service — port 8001

- `GET /health` — checks service health.
- `POST /api/v1/slots` — creates an appointment slot.
- `GET /api/v1/slots` — lists slots, if this route exists in the current code.
- `POST /api/v1/reservations` — attempts to reserve capacity for a patient.

Check `slot-service/main.py` and Swagger UI for the exact routes, including any implemented cancellation or retrieval endpoints.

### Patient Service — port 8002

- `GET /health` — checks service health.
- `POST /api/v1/patients` — registers a patient.
- `GET /api/v1/patients` — lists patients.
- `GET /api/v1/patients/{patient_id}` — retrieves one patient by ID.

### Booking Service — port 8003

- `GET /health` — checks service health.
- `POST /api/v1/bookings` — checks the patient, requests a slot reservation, and then requests a mock notification.

The booking request requires an `Idempotency-Key` header.

### Notification Service — port 8004

- `GET /health` — checks service health.
- `POST /api/v1/notifications` — accepts a simulated SMS or email notification request.

Use each service's Swagger UI to confirm exact request schemas and routes.

## Slot Booking: Key Design Points

- **Validation:** Reject invalid slot values, such as non-positive doctor IDs or capacity, and an end time that is not after the start time.
- **Capacity protection:** A conditional database update increments `reserved_count` only when it is below `capacity`.
- **Idempotency:** Repeating the same key with the same slot and patient should return the existing reservation; reusing the key with a different payload should be rejected.
- **Duplicate prevention:** Database uniqueness constraints protect against duplicate idempotency keys and repeated reservations for the same patient and slot.
- **Cancellation:** If cancellation is implemented, verify that capacity is freed exactly once before describing it as a completed feature.
- **Concurrency:** A previous test run reported 20 parallel requests against capacity 5, with 5 successful and 15 rejected. Re-run it against the current code before using this result in a final submission.

## Testing

Run tests from the `slot-service` folder with its virtual environment activated:

```powershell
python -m pytest test_business_rules.py -v
python -m pytest test_concurrency.py -v
```

From the project root, run the end-to-end test:

```powershell
python -m pytest test_end_to_end.py -v
```

The end-to-end test needs valid patient and slot records and all required services running. If it fails, inspect the service logs, database tables, and test IDs.

## Troubleshooting

### `relation "reservations" does not exist`

The PostgreSQL database used by Slot Service does not currently contain the `reservations` table.

1. Check that `DB_PASSWORD` is set in the Slot Service terminal.
2. Confirm `slot_service_db` is the database configured in `slot-service/database.py`.
3. Verify the `reservations` table exists in that database.
4. Ensure table creation or database migrations are configured.
5. Restart the service and retry the reservation.

`Base.metadata.create_all()` can create missing tables if all models are imported, but it does not update existing tables to match changed models. Use migrations for schema changes.

### Booking returns `Patient not found`

Call `GET http://127.0.0.1:8002/api/v1/patients` and use an existing patient ID.

### A port is already in use

Stop the process using that port or select another port and update downstream service URLs accordingly.

## Security and Git Hygiene

- Never commit passwords, access tokens, private keys, or real patient data.
- Add `.venv/`, `__pycache__/`, `.pytest_cache/`, `.env`, and local log files to `.gitignore`.
- Use placeholders in `.env.example`.
- Use synthetic data for demo evidence.

## Current Limitations

- Notifications are simulated; no real SMS/email is sent.
- Separate databases for Booking Service and Notification Service have not been verified.
- The reservation endpoint and end-to-end test should be revalidated against the current database schema before submission.
- Keep the API list and project tree aligned with the routes and files actually in Git.

## Contributors

Replace this table with your team's actual names and verified contributions.

| Team member | Contribution |
|---|---|
| Your name | Slot creation, reservation capacity/concurrency, idempotency, cancellation, and Slot Service tests — edit to reflect only completed and tested work |
| Team member 2 | Add verified contribution |
| Team member 3 | Add verified contribution |

## AI Use Declaration

Complete this section according to your institution's AI-use policy. Describe any assistance used and verify all assisted code before submission.

## License

Add the license required by your team or institution. Do not imply an open-source license unless one has been selected.
