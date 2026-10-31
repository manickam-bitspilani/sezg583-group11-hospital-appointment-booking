# Evidence: Patient Service tests

Raw output of the Patient Service unit tests and its end-to-end test, recorded
on 2026-10-31. Each claim below links to the file that shows it.

| File | What it is |
|---|---|
| [unit-tests.log](unit-tests.log) | `python -m pytest -v` in `services/patientService` (in-memory SQLite) |
| [e2e-test.log](e2e-test.log) | `python scripts/run_e2e.py` against the running service on PostgreSQL 17 (`patient_db`) |
| [service.log](service.log) | The service's own JSON log lines during that end-to-end run |
| `screenshots/` | Terminal screenshots of both runs (command, output and clock visible) |

## How to reproduce

```bash
cd services/patientService
pip install -r requirements-dev.txt

python -m pytest -v              # unit tests, no PostgreSQL needed

python main.py                   # terminal 1: needs PostgreSQL with patient_db
python scripts/run_e2e.py        # terminal 2: creates a fresh patient each run
```

## Claims

| Claim | Evidence |
|---|---|
| All 16 unit tests pass, one or more per business rule | `unit-tests.log`: `16 passed` |
| **BR11** A phone number belongs to one patient | `unit-tests.log`: `test_br11_*`; `e2e-test.log`: "same phone number is rejected", 409 `PATIENT_PHONE_EXISTS` |
| **BR4** An unknown patient is 404 | `unit-tests.log`: `test_br4_*`; `e2e-test.log`: "Unknown patient is 404", `PATIENT_NOT_FOUND` |
| Invalid input gets one consistent error format (422) | `unit-tests.log`: `test_invalid_patient_is_rejected_with_uniform_error[*]` (5 cases); `e2e-test.log`: age 130 → 422 `VALIDATION_ERROR` with `details` |
| **BR12** The patient is blocked automatically at the 3rd no-show | `e2e-test.log`: no-shows 1–3, `status` goes `ACTIVE` → `BLOCKED`; `service.log`: `"Patient blocked: no-show limit reached"` (WARNING) |
| **BR12** Reporting the same no-show twice counts once | `unit-tests.log`: `test_br12_same_no_show_reported_twice_counts_once`; `e2e-test.log`: replay returns 200, `no_show_count` stays 3, `Idempotent-Replayed: true` checked |
| **BR12** A blocked patient may not book; unblocking restores eligibility | `e2e-test.log`: eligibility `PATIENT_BLOCKED`, then unblock (count reset to 0), then eligible again |
| Invalid state transitions are rejected | `e2e-test.log`: unblocking an `ACTIVE` patient → 409 `INVALID_STATE_TRANSITION` |
| `/health` reports database status, including failure | `unit-tests.log`: `test_health_is_ok_when_database_answers`, `test_health_is_503_when_database_is_down`; `e2e-test.log`: `database: up` |
| Structured logs carry a correlation ID per request | `service.log`: every request line has its own `correlation_id`; the 3rd no-show's "No-show recorded" and "Patient blocked" lines share one ID |

## Failure cases shown

Not only the happy path: duplicate phone (409), invalid age (422), unknown
patient (404), blocked patient refused (`PATIENT_BLOCKED`), invalid unblock
(409), and `/health` returning 503 when the database is down.

## Notes

- `test_health_is_503_when_database_is_down` waits for a database connection
  timeout, which is why the unit-test run takes about 2 minutes.
- Startup log lines have `correlation_id: "-"` because they are not part of a
  request.
