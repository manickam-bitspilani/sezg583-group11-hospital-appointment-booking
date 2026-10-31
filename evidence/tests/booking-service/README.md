# Evidence: Booking Service tests

Raw output of the Booking Service unit tests and its end-to-end test,
recorded on 2026-10-31. Each claim below links to the file that shows it.

| File | What it is |
|---|---|
| [unit-tests.log](unit-tests.log) | `python -m pytest -v` in `services/bookingService` (in-memory SQLite, Patient/Slot/Notification replaced by a fake `httpx.MockTransport`) |
| [e2e-test.log](e2e-test.log) | `python scripts/run_e2e.py` against the running Booking Service on PostgreSQL (`booking_db`), calling the real Patient, Slot and Notification services |
| [service.log](service.log) | Booking Service's own JSON log lines during that end-to-end run |
| `screenshots/` | Terminal screenshots of both runs (command, output and clock visible) |

## How to reproduce

```bash
cd services/bookingService
pip install -r requirements-dev.txt

python -m pytest -v              # unit tests, no PostgreSQL or other services needed

# terminal 1-4: Slot (8001), Patient (8002), Booking (8003), Notification (8004),
# each with `python main.py` in its own folder (PostgreSQL with all four databases)
python scripts/run_e2e.py        # creates fresh patients and slots each run
```

`run_e2e.py` changes Notification Service's provider simulation
(`PUT /api/v1/simulation`) and resets it to a healthy provider before the
cancellation checks.

## Claims

| Claim | Evidence |
|---|---|
| All 16 unit tests pass | `unit-tests.log`: `16 passed` |
| **BR3** One `Idempotency-Key` is one booking: a repeat is replayed, not booked again | `unit-tests.log`: `test_br3_repeated_request_is_replayed_without_new_side_effects`; `e2e-test.log`: "same key is replayed" returns the same `booking_id` and `reservation_id` with `Idempotent-Replayed: true`, and "slot reserved only once" shows `reserved_count: 1`; `service.log`: `"Idempotent replay, returning stored response"` for booking 561 |
| **BR3** Reusing a key with a different payload is rejected | `unit-tests.log`: `test_br3_same_key_with_different_payload_is_rejected`; `e2e-test.log`: 422 `IDEMPOTENCY_KEY_REUSED` |
| A full slot is rejected | `e2e-test.log`: 409 `SLOT_FULL`; `service.log`: `"Booking failed"` with `reason: SLOT_FULL` |
| **BR4** Booking needs an existing patient (and slot) | `unit-tests.log`: `test_br4_booking_requires_existing_patient`; `e2e-test.log`: 404 `PATIENT_NOT_FOUND` and 404 `SLOT_NOT_FOUND` |
| **BR12** A blocked patient cannot book, and nothing is reserved | `unit-tests.log`: `test_br12_blocked_patient_cannot_book`; `e2e-test.log`: after 3 no-shows the patient is `BLOCKED`, booking gets 409 `PATIENT_BLOCKED`, slot keeps `reserved_count: 0` |
| A failed notification is retried with exponential backoff and jitter | `unit-tests.log`: `test_notification_is_retried_until_it_succeeds`, `test_backoff_grows_exponentially_and_is_capped`; `e2e-test.log`: with `fail_next: 1` the booking still has `notification_status: SENT`, and Notification Service holds one SMS with `attempts: 2`; `service.log`: `"Notification attempt failed, retrying"` (503) then `"Notification sent after retry"` for booking 568 |
| **BR5** The booking stays confirmed when the notification fails | `unit-tests.log`: `test_br5_booking_stays_confirmed_when_notification_fails`; `e2e-test.log`: with `fail_next: 4` the booking is `CONFIRMED` with `notification_status: FAILED`; `service.log`: 3 retries for booking 569, then `"Notification failed after all retries; booking remains confirmed"` |
| A non-retryable notification error is not retried | `unit-tests.log`: `test_non_retryable_notification_error_is_not_retried` |
| **BR10** Cancelling releases the slot place and sends a cancellation SMS | `unit-tests.log`: `test_br10_cancel_releases_reservation_and_notifies`; `e2e-test.log`: `CANCELLED` with `cancel_notification_status: SENT`, slot back to `reserved_count: 0`; `service.log`: `"Booking cancelled"` for booking 561 |
| **BR10** Cancelling twice has no new side effects | `unit-tests.log`: `test_br10_cancelling_twice_has_no_new_side_effects`; `e2e-test.log`: second cancel returns the same booking with `Idempotent-Replayed: true` |
| **BR10** Only a confirmed booking, before its slot starts, can be cancelled | `unit-tests.log`: `test_br10_only_confirmed_bookings_can_be_cancelled`, `test_br10_cancel_after_slot_started_is_rejected` |
| Invalid input gets one consistent error format (422) | `unit-tests.log`: `test_validation_errors_use_uniform_error_format`; `e2e-test.log`: missing `Idempotency-Key`, `patient_id` and `booking_id` above the PostgreSQL INTEGER range all give 422 `VALIDATION_ERROR` with `details` |
| Unknown booking is 404 (read and cancel) | `unit-tests.log`: `test_cancel_unknown_booking_is_404`; `e2e-test.log`: `BOOKING_NOT_FOUND` |
| `/health` reports database status, including failure | `unit-tests.log`: `test_health_is_ok_when_database_answers`, `test_health_is_503_when_database_is_down`; `e2e-test.log`: all four services `database: up` |
| Structured logs carry a correlation ID per request | `service.log`: every request line has its own `correlation_id`; booking 561's `"Booking completed"` and `"request completed"` lines share one ID, which is also the `correlation_id` in the booking's response |

## Failure cases shown

Not only the happy path: reused key with a different payload (422), full slot
(409), unknown patient and slot (404), blocked patient (409), a notification
that fails once and is retried, a notification that fails on every retry while
the booking stays confirmed, repeated cancel, missing header and ids above the
INTEGER range (422 instead of a 500), unknown booking (404), and `/health`
returning 503 when the database is down.

## Notes

- A rejected booking (slot full, unknown patient ...) is stored as `FAILED`
  with its error response, so a retry with the same key gets the same answer.
  That is why `service.log` shows `"Booking failed"` with a `booking_id` while
  the client only sees the error.
- A replayed booking returns the stored response, so its `correlation_id` is
  the one from the original request. The replay's own log lines carry the
  replay's ID.
- Startup and shutdown log lines have `correlation_id: "-"` because they are
  not part of a request.
- Timeouts and a service being down (504 / 503 with the key released for a
  retry) are shown in `evidence/challenge-3a-resilience/`.
