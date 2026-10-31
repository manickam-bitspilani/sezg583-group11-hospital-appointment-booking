# Evidence: Notification Service tests

Raw output of the Notification Service unit tests and its end-to-end test,
recorded on 2026-10-31. Each claim below links to the file that shows it.

| File | What it is |
|---|---|
| [unit-tests.log](unit-tests.log) | `python -m pytest -v` in `services/notificationService` (in-memory SQLite) |
| [e2e-test.log](e2e-test.log) | `python scripts/run_e2e.py` against the running service on PostgreSQL (`notification_db`) |
| [service.log](service.log) | The service's own JSON log lines during that end-to-end run |
| `screenshots/` | Terminal screenshots of both runs (command, output and clock visible) |

## How to reproduce

```bash
cd services/notificationService
pip install -r requirements-dev.txt

python -m pytest -v              # unit tests, no PostgreSQL needed

python main.py                   # terminal 1: needs PostgreSQL with notification_db
python scripts/run_e2e.py        # terminal 2: fresh booking ids and keys each run
```

`run_e2e.py` changes the provider simulation (`PUT /api/v1/simulation`) and
resets it to a healthy provider at the end.

## Claims

| Claim | Evidence |
|---|---|
| All 9 unit tests pass | `unit-tests.log`: `9 passed` |
| **BR6** A patient is notified at most once per booking: the same `Idempotency-Key` is replayed, not sent again | `unit-tests.log`: `test_br6_same_key_notifies_only_once`; `e2e-test.log`: "same key is replayed" returns 200 with the same `id`, `attempts` stays 1, `Idempotent-Replayed: true` checked; "one notification stored" finds 1 row; `service.log`: one `"Mock SMS sent"` for that booking, then `"Idempotent replay, not sending again"` |
| **BR6** Reusing a key with a different payload is rejected | `unit-tests.log`: `test_br6_same_key_with_different_payload_is_rejected`; `e2e-test.log`: 422 `IDEMPOTENCY_KEY_REUSED` |
| A provider failure is a 503 the caller can retry with the same key | `unit-tests.log`: `test_provider_failure_returns_503_and_retry_with_same_key_succeeds`; `e2e-test.log`: 503 `PROVIDER_UNAVAILABLE`, then the retry is `SENT` on `attempts: 2` and the booking still has 1 row |
| `fail_next` fails an exact number of sends (repeatable demos for Challenge 3A) | `unit-tests.log`: `test_fail_next_fails_exactly_n_attempts_then_succeeds`; `e2e-test.log`: each `fail_next: 1` causes exactly one 503 |
| A failed send records why in `last_error` | `unit-tests.log`: `test_failed_send_records_last_error`; `e2e-test.log`: stored notification is `FAILED` with `last_error: "Simulated provider failure (fail_next)"`; `service.log`: `"Simulated provider failure"` (WARNING) |
| A second request while the first is still delivering gets 409 | `unit-tests.log`: `test_concurrent_request_while_pending_gets_409` |
| Invalid input gets one consistent error format (422) | `e2e-test.log`: missing `Idempotency-Key`, `notification_type: "FAX"`, and ids above the PostgreSQL INTEGER range all give 422 `VALIDATION_ERROR` with `details` |
| Unknown notification is 404 | `e2e-test.log`: `NOTIFICATION_NOT_FOUND` |
| `/health` reports database status, including failure | `unit-tests.log`: `test_health_is_ok_when_database_answers`, `test_health_is_503_when_database_is_down`; `e2e-test.log`: `database: up` |
| Structured logs carry a correlation ID per request | `service.log`: every request line has its own `correlation_id`; the failed send's `"Simulated provider failure"` and `"request completed"` lines share one ID |

## Failure cases shown

Not only the happy path: reused key with a different payload (422), provider
failure (503) and its retry, a send that stays `FAILED` with `last_error`,
missing header and invalid fields (422), ids above the INTEGER range (422
instead of a 500), unknown notification (404), and `/health` returning 503 when
the database is down.

## Notes

- A notification's `correlation_id` is the one from the request that created
  it, so a retry's response shows the first attempt's ID. The log lines of the
  retry itself carry the retry's own ID.
- Startup and shutdown log lines have `correlation_id: "-"` because they are
  not part of a request.
- How Booking Service uses these failures (timeout, retry with backoff,
  graceful failure) is shown in `evidence/challenge-3a-resilience/`.
