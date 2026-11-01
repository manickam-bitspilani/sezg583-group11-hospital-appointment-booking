# Evidence: Slot Service tests

Raw output of the Slot Service unit tests and its end-to-end test, recorded
on 2026-11-01. Each claim below links to the file that shows it. The
concurrency proof for BR1 is in
[../../challenge-1-concurrency/](../../challenge-1-concurrency/).

| File | What it is |
|---|---|
| [unit-tests.log](unit-tests.log) | `python -m pytest -v` in `services/slotService` (in-memory SQLite) |
| [e2e-test.log](e2e-test.log) | `python scripts/run_e2e.py` against the running Slot Service on PostgreSQL 17 (`slot_db`) |
| [service.log](service.log) | Slot Service's own JSON log lines during that end-to-end run |

## How to reproduce

```bash
cd services/slotService
pip install -r requirements-dev.txt

python -m pytest -v              # unit tests, no PostgreSQL needed

python main.py                   # terminal 1: needs PostgreSQL with slot_db
python scripts/run_e2e.py        # terminal 2: fresh doctor, patients and keys each run
```

## Claims

| Claim | Evidence |
|---|---|
| All 20 unit tests pass, one or more per business rule | `unit-tests.log`: `20 passed` |
| **BR1** A slot is never booked beyond its capacity | `unit-tests.log`: `test_br1_*`; `e2e-test.log`: "a full slot rejects the next patient" → 409 `SLOT_FULL`, then `reserved_count` 2 = `capacity` 2; parallel proof in `challenge-1-concurrency/` |
| **BR2** A patient holds at most one active reservation per slot | `unit-tests.log`: `test_br2_*`; `e2e-test.log`: same patient with a new key → 409 `DUPLICATE_BOOKING` |
| **BR3** The same Idempotency-Key returns the original reservation; a different payload is rejected | `unit-tests.log`: `test_br3_*`; `e2e-test.log`: same key → same reservation `id`, different payload → 422 `IDEMPOTENCY_KEY_REUSED`, and `reserved_count` stays 1 |
| **BR7** A slot has positive capacity and ends after it starts | `unit-tests.log`: `test_br7_*`; `e2e-test.log`: capacity 0 and end-before-start → 422 `VALIDATION_ERROR` |
| **BR8** A slot must start in the future; a started slot can't be reserved | `unit-tests.log`: `test_br8_*` (both); `e2e-test.log`: past slot → 422 `SLOT_IN_PAST` |
| **BR9** A doctor can't have overlapping slots; back-to-back is fine | `unit-tests.log`: `test_br9_*`; `e2e-test.log`: overlap → 409 `SLOT_OVERLAP`, back-to-back → 201 |
| **BR10** Cancelling frees the place exactly once; repeating changes nothing; the patient can book again | `unit-tests.log`: `test_br10_*` (4 tests); `e2e-test.log`: cancel → `CANCELLED`, cancel again → `Idempotent-Replayed: true`, `reserved_count` 2 → 1 (not 0), rebook → 201; `service.log`: `"Reservation cancelled, capacity released"` once |
| IDs beyond PostgreSQL's `INTEGER` range are a 422, not a 500 | `unit-tests.log`: `test_doctor_id_must_fit_in_an_integer_column`, `test_reservation_ids_must_fit_in_an_integer_column`; `e2e-test.log`: `doctor_id` and `slot_id` 2147483648 → 422 `VALIDATION_ERROR` |
| Unknown slot / reservation is 404 | `unit-tests.log`: `test_unknown_slot_is_reported`, `test_br10_unknown_reservation_is_reported`; `e2e-test.log`: `SLOT_NOT_FOUND`, `RESERVATION_NOT_FOUND` |
| `/health` reports database status, including failure | `unit-tests.log`: `test_health_is_ok_when_database_answers`, `test_health_is_503_when_database_is_down`; `e2e-test.log`: `database: up` |
| Every decision is logged with a reason | `service.log`: `"Reservation rejected"` lines carry `reason` `SLOT_FULL`, `DUPLICATE_BOOKING`, `IDEMPOTENCY_KEY_REUSED` and `SLOT_NOT_FOUND`; each request line has its `correlation_id` |

## Failure cases shown

Not only the happy path: full slot (409), duplicate patient (409), reused
key (422), invalid capacity and times (422), slot in the past (422),
overlapping slot (409), out-of-range ids (422), unknown slot and
reservation (404), and `/health` returning 503 when the database is down.

## Notes

- `test_health_is_503_when_database_is_down` waits for a database connection
  timeout, which is why the unit-test run takes about 2 minutes.
- Startup log lines have `correlation_id: "-"` because they are not part of a
  request.
