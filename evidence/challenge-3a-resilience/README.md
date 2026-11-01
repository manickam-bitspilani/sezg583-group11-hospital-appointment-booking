# Evidence: Challenge 3A, resilience (BR5, BR6)

The SMS provider (Notification Service) is the dependency that can be slow or
fail. Booking calls it with a timeout, retries with exponential backoff and
full jitter, and fails gracefully: a booking stays `CONFIRMED` even when the
SMS cannot be sent. Retries never send a second SMS. Recorded on 2026-11-01
on macOS with all four services on PostgreSQL 15.

| File | What it is |
|---|---|
| [01-test-resilience.log](01-test-resilience.log) | `pytest -s scripts/test_resilience.py`: provider failing 0/2/3/4 times, and a provider slower than the timeout |
| [02-test-resilience-provider-down.log](02-test-resilience-provider-down.log) | Notification Service stopped (`curl` refused), then `NOTIFICATION_DOWN=1 pytest ... -k down` |
| [03-booking-service.log](03-booking-service.log) | Booking's JSON log for both runs: every attempt, its outcome and the randomised wait before the next one |
| [04-notification-service.log](04-notification-service.log) | The mock provider's log for run 01: simulated failures, SMS sent, and the replay that was not sent again (ends when the service was stopped for run 02) |
| [05-db-state.txt](05-db-state.txt) | `booking_db` and `notification_db` afterwards: one row per booking, never two |
| [screenshots/test-resilience.png](screenshots/test-resilience.png) | Run 01 in the terminal: the `fail_next` table and `2 passed, 1 skipped` |
| [screenshots/provider-down.png](screenshots/provider-down.png) | Run 02 in the terminal: `curl` cannot connect, booking `201 CONFIRMED` with `notification_status: FAILED`, `1 passed` |

## The problem

A slow or failing SMS provider must not lose or block appointments. Retrying
helps with short glitches, but naive retries cause new problems: retrying at
fixed intervals makes many clients hit a recovering provider at the same
moment, and retrying after a timeout can send a second SMS if the first
attempt actually succeeded after the caller gave up.

## The design

Booking's notification step (`services/bookingService/booking_service.py`, `_notify`):

- **Timeout:** 2 s per attempt (`NOTIFICATION_TIMEOUT_SECONDS`).
- **Retries:** up to 4 attempts (`NOTIFICATION_MAX_ATTEMPTS`), only for errors
  that can change: 503, 502, 504, 409 (attempt still in flight), timeouts and
  connection failures. Other 4xx errors are not retried.
- **Backoff with full jitter:** before attempt *n*+1 the wait is
  `uniform(0, min(2 s, 0.2 s × 2^(n-1)))`. The **cap** doubles (0.2 → 0.4 →
  0.8 s); the actual wait is random below it, so clients that failed together
  do not retry together.
- **No duplicates:** every attempt carries the same
  `Idempotency-Key: booking-<id>-confirmation`. Notification Service stores one
  row per key; a retry for a key that was already delivered returns the stored
  result and logs `"Idempotent replay, not sending again"` (BR6).
- **Graceful failure (BR5):** if all 4 attempts fail, the booking still returns
  `201 CONFIRMED` with `notification_status: FAILED`. The SMS is treated as
  non-critical: losing a message is better than losing an appointment.
- **Repeatable failures:** the mock provider can be told at runtime to fail an
  exact number of times (`fail_next`), at random (`failure_rate`) or slowly
  (`latency_ms`) via `PUT /api/v1/simulation`, so every run gives the same table.

| Alternative considered | Why not chosen |
|---|---|
| Fixed-interval retries | Many clients retry in lockstep and hit a recovering provider together. |
| Exponential backoff without jitter | Still synchronises clients that failed at the same time. |
| Outbox table + background re-sender | Better for long outages, but adds a worker; listed as future work. |
| Circuit breaker (bonus) | Not implemented; during a long outage every booking still makes 4 attempts. |

## Claims

| Claim | Evidence |
|---|---|
| A failing provider is retried until it succeeds | `01-test-resilience.log`: `fail_next` 0 → sent on attempt 1, 2 → attempt 3, 3 → attempt 4; `03-booking-service.log`: `"Notification attempt failed, retrying"` (outcome 503), then `"Notification sent after retry"` with `attempt` 3 and 4 |
| When every attempt fails, the booking stays confirmed (graceful failure) | `01-test-resilience.log`: `fail_next` 4 → `CONFIRMED`, notification `FAILED`, `last_error` recorded; `03-booking-service.log`: `"Notification failed after all retries; booking remains confirmed"`; `05-db-state.txt`: booking 668 |
| Waits use exponential backoff with full jitter | `03-booking-service.log` `retry_in_seconds`, every wait below its cap (table below) |
| A slow provider times out, and the retry does not send again | `01-test-resilience.log`: latency 2.5 s > 2 s timeout → 201, notification `SENT`, 1 SMS row with `attempts` 1; `03-booking-service.log`: booking 669 attempt 1 `ReadTimeout`, sent on attempt 2; `04-notification-service.log`: `"Mock SMS sent"` for booking 669 once, then `"Idempotent replay, not sending again"` for `booking-669-confirmation` 99 ms later |
| With Notification Service stopped, bookings still succeed | `02-test-resilience-provider-down.log`: `curl` refused, booking → `201 CONFIRMED`, `notification_status: FAILED`, `reserved_count` 1; `03-booking-service.log`: booking 670, 4 failed connection attempts, then `"booking remains confirmed"` |
| Retries never cause a duplicate | `05-db-state.txt`: bookings 665–669 each have exactly 1 SMS row although 666–669 needed 2–4 attempts; `04-notification-service.log`: 4 `"Mock SMS sent"` for 4 delivered bookings, 9 `"Simulated provider failure"` (2 + 3 + 4) |

**Backoff waits from `03-booking-service.log` (seconds):**

| Wait before attempt | Cap `min(2, 0.2 × 2^(n-1))` | Observed waits (bookings 666, 667, 668, 669, 670) |
|---|---|---|
| 2 | 0.2 | 0.024, 0.036, 0.127, 0.049, 0.107 |
| 3 | 0.4 | 0.063, 0.156, 0.053, 0.052 |
| 4 | 0.8 | 0.282, 0.76, 0.1 |

Every wait is below its cap, and the waits are random, so a single booking's
waits do not always increase (booking 668: 0.127 → 0.053 → 0.76). That is
the point of full jitter: the *range* grows exponentially, the value is spread.

## How to reproduce

```bash
# PostgreSQL with all four databases, all four services running (python main.py each)
pytest -s scripts/test_resilience.py

# then stop Notification Service (Ctrl+C in its terminal) and:
NOTIFICATION_DOWN=1 pytest -s scripts/test_resilience.py -k down
```

## Limitations

- No circuit breaker: during a long outage every booking still makes 4
  attempts, adding up to about 2 s of backoff plus timeouts to the request.
- A notification that still fails after 4 attempts is not re-sent later; an
  outbox with a background re-sender would cover long outages.
- Retries happen inside the booking request, so the client waits for them.
- With the service stopped, the failure type depends on the OS: on macOS
  (this run) and Linux the closed port is refused immediately (`ConnectError`,
  so all 4 attempts take well under a second); on Windows the connection can
  stall until the 2 s timeout (`ConnectTimeout`). Both are retried and end the
  same way.
