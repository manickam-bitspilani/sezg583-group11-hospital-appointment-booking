# Evidence index

Every claim in the report and the README links to a file in this folder.
Each sub-folder has its own README that maps each claim to a file; every log
starts with the exact command that produced it, followed by the unedited
output.

## By assignment requirement

| Requirement (brief) | Folder | Script |
|---|---|---|
| **Challenge 1** Concurrency control | [challenge-1-concurrency/](challenge-1-concurrency/) | `scripts/test_concurrency.py` |
| **Challenge 2** Idempotency | [challenge-2-idempotency/](challenge-2-idempotency/) | `scripts/test_idempotency.py` |
| **Challenge 3A** Resilience | [challenge-3a-resilience/](challenge-3a-resilience/) | `scripts/test_resilience.py` |
| Unit test per business rule, per service | [tests/](tests/) `<service>/unit-tests.log` | `python -m pytest -v` in each service |
| Scripted end-to-end test | [tests/](tests/) `<service>/e2e-test.log` | `services/<service>/scripts/run_e2e.py` |
| **Assignment 2, Tasks 1–2** Docker images and Compose deployment | [assignment-2/compose/](assignment-2/compose/) | `docker compose up --build` |
| **Assignment 2, Task 3** Deployment troubleshooting (port conflict) | [assignment-2/task3-port-conflict/](assignment-2/task3-port-conflict/) | `scripts/docker/check_ports.sh` |
| Structured logs with a correlation ID | `service.log` in each `tests/<service>/` folder, plus the challenge logs | – |

## By service

| Service | Unit tests | End-to-end | Service log | Folder |
|---|---|---|---|---|
| Slot Service (8001) | `unit-tests.log` | `e2e-test.log` | `service.log` | [tests/slot-service/](tests/slot-service/) |
| Patient Service (8002) | `unit-tests.log` | `e2e-test.log` | `service.log` | [tests/patient-service/](tests/patient-service/) |
| Booking Service (8003) | `unit-tests.log` | `e2e-test.log` | `service.log` | [tests/booking-service/](tests/booking-service/) |
| Notification Service (8004) | `unit-tests.log` | `e2e-test.log` | `service.log` | [tests/notification-service/](tests/notification-service/) |

Each folder also has a `README.md` (claims mapped to files) and a
`screenshots/` folder with the terminal runs.

## Failure cases

The brief asks for failures as well as successes. Each folder shows them;
the main ones:

| Failure | Where |
|---|---|
| Slot full under 20–25 parallel requests (409 `SLOT_FULL`) | `challenge-1-concurrency/` |
| Same key with a different payload (422 `IDEMPOTENCY_KEY_REUSED`) | `challenge-2-idempotency/` |
| SMS provider failing, slow or stopped; booking stays `CONFIRMED` | `challenge-3a-resilience/` |
| Blocked patient refused (409 `PATIENT_BLOCKED`), unknown patient or slot (404) | `tests/patient-service/`, `tests/booking-service/` |
| Invalid input and ids above the INTEGER range (422 instead of a 500) | `tests/patient-service/`, `tests/booking-service/`, `tests/notification-service/` |
| Overlapping or past slots (409 `SLOT_OVERLAP`, 422 `SLOT_IN_PAST`) | `tests/slot-service/` |
| `/health` returns 503 when the database is down | every `tests/<service>/unit-tests.log` (`test_health_is_503_when_database_is_down`) |

## How to reproduce everything

```bash
psql -U postgres -f scripts/setup_databases.sql      # once: 4 databases, 4 roles

# four terminals, one per service (each with its own venv and .env):
cd services/<service> && python main.py

# per-service tests (unit tests need no database):
cd services/<service> && python -m pytest -v && python scripts/run_e2e.py

# challenges (from the repo root, all four services running):
pytest -s scripts/test_concurrency.py
pytest -s scripts/test_idempotency.py
pytest -s scripts/test_resilience.py
```
