# Evidence: Challenge 2, idempotency (BR3)

Repeating `POST /api/v1/bookings` with the same `Idempotency-Key` returns the
same result and has no duplicate effect: no second reservation, no second SMS.
This holds for sequential repeats and for identical requests arriving at the
same moment. Recorded on 2026-11-01 with all four services running on
PostgreSQL 15.

| File | What it is |
|---|---|
| [01-test-idempotency.log](01-test-idempotency.log) | `pytest -s scripts/test_idempotency.py`: sequential repeat, reused key with a different payload, 20 simultaneous identical requests |
| [02-curl-first-vs-repeat.txt](02-curl-first-vs-repeat.txt) | Raw HTTP: first call, two repeats and a different payload under the same key, with status lines, headers and bodies, then the side effects |
| [03-db-state.txt](03-db-state.txt) | `booking_db`, `slot_db` and `notification_db` afterwards: one record per key |
| [04-booking-service.log](04-booking-service.log) | Booking Service's JSON log for both runs, including every replay |
| [screenshots/test-idempotency.png](screenshots/test-idempotency.png) | Terminal screenshot of the pytest run (3 passed, 20 simultaneous summary) |
| [screenshots/curl-first-vs-repeat.png](screenshots/curl-first-vs-repeat.png) | Terminal screenshot of the curl run (first call vs repeats vs different payload) |

## The problem

A client that times out does not know whether its booking went through, so
it retries; proxies and mobile networks retry too. Without protection, each
retry would reserve another place and send another SMS. Two identical
requests can also arrive at the same moment (a double click, or a retry that
overtakes a slow first attempt), so checking "have I seen this key?" before
doing the work is not enough: both requests would pass that check.

## The design

`services/bookingService/booking_service.py` (`claim_booking`, `place_booking`, `settle`):

1. **Claim before any work.** Booking inserts `(idempotency_key,
   sha256(request body))` as a `PENDING` booking. `idempotency_key` is
   `UNIQUE`, so of several simultaneous identical requests exactly one insert
   succeeds; that request runs the transaction (eligibility → reserve → notify).
2. **Store the response with the key.** When the booking settles, its status
   code and response body are saved in the same row (`response_status_code`,
   `response_body`).
3. **Replay.** A request whose insert hits the unique key reads that row. If
   it is still `PENDING`, it waits (up to `IDEMPOTENCY_WAIT_SECONDS`, 10 s) for
   it to settle, then returns the stored status and body with
   `Idempotent-Replayed: true`, and logs `"Idempotent replay, returning stored response"`
   with its own correlation ID plus the original request's in
   `original_correlation_id`.
4. **Fingerprint.** Same key with a different body means a different
   fingerprint, and the request gets `422 IDEMPOTENCY_KEY_REUSED`.
5. **Downstream steps are idempotent too.** Booking passes the same key to
   Slot Service and `booking-<id>-confirmation` to Notification Service, so a
   retry after a timeout can never reserve twice or send a second SMS.

Business failures (slot full, patient missing or blocked) are stored and
replayed like successes. Transient failures (timeout, service down) release
the key, so the client can retry the same request.

| Alternative considered | Why not chosen |
|---|---|
| Rely on clients not to retry | Clients and proxies retry on timeouts by design; the server must be safe. |
| Key store in Redis with a TTL | Extra infrastructure, and the key would not be committed together with the booking state. |
| Separate idempotency table | Our booking row is the idempotency record, so the claim and the outcome change atomically in one row. |
| Reject a concurrent duplicate at once with 409 | We wait for the first request instead, so all identical callers receive the same 201. |

## Claims

| Claim | Evidence |
|---|---|
| A repeat returns the same result, marked as a replay | `02-curl-first-vs-repeat.txt`: call 1 → `201`; calls 2 and 3 → `201` with `idempotent-replayed: true` and an identical body (same `booking_id`, `reservation_id` and original `correlation_id`); `01-test-idempotency.log`: `FIRST` vs `REPEAT` |
| The repeat has no duplicate effect | `02-curl-first-vs-repeat.txt` step 5: `reserved_count` 1 on the booked slot, 1 notification; `03-db-state.txt`: 1 booking row, 1 reservation, 1 notification for the key |
| The key and a fingerprint of the request are stored with the response | `03-db-state.txt`: `request_fingerprint` (sha256), `response_status_code` 201 and the stored `response_body`, which is exactly what the repeats returned |
| Same key with a different payload is rejected | `02-curl-first-vs-repeat.txt` step 4 → `422 IDEMPOTENCY_KEY_REUSED`; the other slot keeps `reserved_count` 0 (`03-db-state.txt`: slot 201, 0 reservations); `01-test-idempotency.log`: `REUSED 422` |
| 20 identical requests at the same moment create one booking | `01-test-idempotency.log`: all 20 → `201`, 1 distinct body, 19 replayed, `reserved_count` 1, 1 notification; `03-db-state.txt`: key `race-…` has 1 booking row, its slot 1 reservation, its booking 1 notification |
| The logs show the replays | `04-booking-service.log`: 22 `"Idempotent replay, returning stored response"` lines (1 sequential + 19 simultaneous + 2 curl repeats), each with its own `correlation_id` and the shared `original_correlation_id`; only 4 `"Booking completed"` lines, one per key (bookings 570, 572, 574, 594) |

## How to reproduce

```bash
# PostgreSQL with all four databases (scripts/setup_databases.sql), then in
# four terminals: python main.py in services/slotService, patientService,
# bookingService and notificationService.
pytest -s scripts/test_idempotency.py

# or by hand:
curl -i -X POST localhost:8003/api/v1/bookings -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-1' -d '{"patient_id":1,"slot_id":1}'   # run it twice
```

## Notes

- Booking ids jump from 574 (the simultaneous case) to 594 (the curl run):
  each of the 19 losing inserts used a value from the `bookings` id sequence
  before it hit the unique key. PostgreSQL does not give sequence values back,
  so the gap is expected and shows the 19 claims really were attempted.
- The log starts with one `GET /health` (the readiness check before the run)
  and ends with the shutdown lines from stopping the service, so the file
  holds exactly one run.

## Limitations

- A crash while a booking is `PENDING` leaves that key blocked: retries get
  `409 REQUEST_IN_PROGRESS`, because there is no stale-claim timeout yet.
- Idempotency records are never expired; a production system would delete
  or archive them after a retention period.
- A waiting duplicate holds a request thread for up to 10 s while the first
  request finishes.
