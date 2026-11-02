# Evidence: Challenge 1, concurrency control (BR1, BR10)

Simultaneous requests for the same appointment slot must never over-allocate
it, and simultaneous cancels of one booking must free its place exactly once.
Recorded on 2026-11-01 with all four services running on PostgreSQL 17.

| File | What it is |
|---|---|
| [01-test-concurrency.log](01-test-concurrency.log) | `pytest -s scripts/test_concurrency.py`: 3 cases, each request a real `POST /api/v1/bookings` through Booking Service |
| [02-db-state.txt](02-db-state.txt) | Final state of the three slots, read directly from `slot_db` afterwards |
| [03-slot-service.log](03-slot-service.log) | Slot Service's JSON log for that run: every accept, reject and release decision |
| [screenshots/test-concurrency.png](screenshots/test-concurrency.png) | Terminal screenshot of a re-run on Windows (2026-11-01 17:08 IST): `3 passed`, `{201: 5, 409: 15}`, `{201: 1, 409: 24}`, parallel cancels `reserved_count` 2 → 1 |

The screenshot is from a later run than the log files. Each run creates fresh
slots, so its slot ids (38, 39, 40, booking 326) differ from the ones in the
logs (14, 15, 16); the outcome of every case is identical.

## The problem

Many patients try to take the last places of a slot at the same moment. If
the service reads `reserved_count`, compares it with `capacity` in Python and
then writes `reserved_count + 1`, the requests interleave like this
(capacity 5, four places already taken):

```
request A: SELECT reserved_count -> 4      (4 < 5, OK)
request B: SELECT reserved_count -> 4      (4 < 5, OK)
request A: UPDATE reserved_count = 5       place 5 given to A
request B: UPDATE reserved_count = 5       place 5 given to B as well
```

Both requests passed the check before either wrote, so two patients hold the
same last place and one increment is lost (`reserved_count` says 5, but six
reservations exist). With 20 requests arriving together, many of them can
read the same value. Checking in application code cannot prevent this,
because nothing stops another request from changing the row between the
read and the write.

## The design

Slot Service claims a place with **one atomic conditional UPDATE**, in the same
transaction as the reservation insert
(`services/slotService/reservation_service.py`, `reserve_slot`, step 2):

```sql
UPDATE appointment_slots
SET    reserved_count = reserved_count + 1
WHERE  id = :slot_id AND start_time > now() AND reserved_count < capacity
```

- The check and the increment are one statement. PostgreSQL locks the slot's
  row for the update, so concurrent requests for the same slot queue on that
  lock, and each one re-evaluates `reserved_count < capacity` against the
  latest committed value.
- `rowcount = 0` means the slot is full (or started, or missing), and the
  request gets `409 SLOT_FULL`; nothing is written.
- `CHECK (reserved_count <= capacity)` in `models.py` is a second line of
  defence: even a future bug in the code could not store an over-allocated slot.
- Cancelling uses the same idea (`cancel_reservation`): only the request whose
  `UPDATE ... WHERE status = 'CONFIRMED'` matches releases the place, so
  repeated or parallel cancels free it once.

| Alternative considered | Why not chosen |
|---|---|
| `SELECT ... FOR UPDATE`, then check and update | Correct, but two round trips and the lock is held longer; the conditional update does the same in one statement. |
| Optimistic locking (version column) | Under heavy contention most requests fail and must retry; 20 requests for 5 places would mean many wasted retries. |
| One row per seat + unique constraint | Suits numbered seats; our capacity is a count, so this adds rows without benefit. |

## Claims

| Claim | Evidence |
|---|---|
| 20 parallel bookings on capacity 5: exactly 5 succeed, 15 are rejected | `01-test-concurrency.log`: `[5-20]` → `{201: 5, 409: 15}`, all rejections `SLOT_FULL` |
| 25 parallel bookings on capacity 1: exactly 1 succeeds, 24 are rejected | `01-test-concurrency.log`: `[1-25]` → `{201: 1, 409: 24}` |
| The rejections carry a meaningful error | `01-test-concurrency.log`: `rejections : {'SLOT_FULL': ...}`; `03-slot-service.log`: `"Reservation rejected"` with `"reason": "SLOT_FULL"` |
| The database is consistent afterwards | `02-db-state.txt`: slots 14 and 15 have `reserved_count = capacity =` CONFIRMED reservations; 0 over-allocated slots and 0 slots whose count differs from their reservations in the whole database |
| Slot Service made exactly the expected decisions | `03-slot-service.log`: 8 `"Reservation confirmed"` (5 + 1 + 2 for the cancel case) and 39 `"Reservation rejected"` (15 + 24) |
| 10 parallel cancels of one booking free its place once (BR10) | `01-test-concurrency.log`: all 10 → 200 `CANCELLED`, 9 replayed; `reserved_count` 2 → 1; `02-db-state.txt`: slot 16 has 1 CONFIRMED + 1 CANCELLED and `reserved_count = 1`; `03-slot-service.log`: `"Reservation cancelled, capacity released"` exactly once |

## How to reproduce

```bash
# PostgreSQL with all four databases (scripts/setup_databases.sql), then in
# four terminals: python main.py in services/slotService, patientService,
# bookingService and notificationService.
pytest -s scripts/test_concurrency.py                       # both cases + parallel cancels
CAPACITY=1 REQUESTS=20 pytest -s scripts/test_concurrency.py  # any other capacity / load
```

Each run creates fresh patients and slots, so it can be repeated.

## Limitations

- Requests for one very popular slot serialise on its row; other slots are
  unaffected. This is the price of strict correctness on a single counter.
- The guarantee depends on PostgreSQL's row locking. Unit tests use SQLite,
  which serialises writes differently, so the concurrent behaviour is proven
  by this script against PostgreSQL, not by the unit tests.
