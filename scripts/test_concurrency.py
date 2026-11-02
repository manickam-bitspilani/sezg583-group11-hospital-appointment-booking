"""
Challenge 1: simultaneous bookings for the same slot never over-allocate it
(BR1), and simultaneous cancels of one booking free its place exactly once
(BR10).

Every request goes through Booking Service, so this is the real transaction:
eligibility -> reserve -> notify. Slot Service claims capacity with one atomic
conditional UPDATE (reserved_count < capacity), so concurrent requests queue
on the slot's row lock and exactly `capacity` of them succeed.

Run with services up:   pytest -s scripts/test_concurrency.py
Vary the main case:     CAPACITY=1 REQUESTS=25 pytest -s scripts/test_concurrency.py

The database checks read slot_db directly as slot_svc (SLOT_DB_* variables,
defaults match services/slotService/.env.example).
"""

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from conftest import book, cancel_booking, create_patient, create_slot, get_slot

CASES = (
    [(int(os.environ["CAPACITY"]), int(os.getenv("REQUESTS", "20")))]
    if os.getenv("CAPACITY")
    else [(5, 20), (1, 25)]
)
PARALLEL_CANCELS = 10


def slot_db_state(slot_id):
    """reserved_count and reservation rows for one slot, straight from slot_db."""
    with psycopg.connect(
        host=os.getenv("SLOT_DB_HOST", "localhost"),
        port=int(os.getenv("SLOT_DB_PORT", "5432")),
        dbname=os.getenv("SLOT_DB_NAME", "slot_db"),
        user=os.getenv("SLOT_DB_USER", "slot_svc"),
        password=os.getenv("SLOT_DB_PASSWORD", "slot_pw"),
    ) as conn:
        capacity, reserved = conn.execute(
            "SELECT capacity, reserved_count FROM appointment_slots WHERE id = %s", (slot_id,)
        ).fetchone()
        rows = dict(conn.execute(
            "SELECT status, count(*) FROM reservations WHERE slot_id = %s GROUP BY status", (slot_id,)
        ).fetchall())
    return capacity, reserved, rows


@pytest.mark.parametrize("capacity, requests", CASES)
def test_parallel_bookings_never_overbook(capacity, requests):
    slot = create_slot(capacity=capacity)
    patients = [create_patient(f"Concurrency {i}") for i in range(requests)]

    with ThreadPoolExecutor(max_workers=requests) as executor:
        responses = list(executor.map(lambda p: book(p["id"], slot["id"]), patients))

    statuses = Counter(r.status_code for r in responses)
    errors = Counter(r.json()["error"]["code"] for r in responses if r.status_code != 201)
    db_capacity, db_reserved, db_rows = slot_db_state(slot["id"])

    print(f"\n{requests} parallel bookings for slot {slot['id']} with capacity {capacity}")
    print(f"  status codes : {dict(sorted(statuses.items()))}")
    print(f"  rejections   : {dict(errors)}")
    print(f"  slot_db      : capacity={db_capacity} reserved_count={db_reserved} reservations={db_rows}")

    assert statuses[201] == capacity
    assert statuses[409] == requests - capacity
    assert errors == {"SLOT_FULL": requests - capacity}
    assert db_reserved == db_capacity == capacity
    assert db_rows == {"CONFIRMED": capacity}
    assert get_slot(slot["id"])["reserved_count"] == capacity


def test_concurrent_cancels_free_the_place_once():
    slot = create_slot(capacity=2)
    first, second = (book(create_patient(f"Cancel {i}")["id"], slot["id"]) for i in range(2))
    assert first.status_code == second.status_code == 201
    booking_id = first.json()["booking_id"]
    before = slot_db_state(slot["id"])

    with ThreadPoolExecutor(max_workers=PARALLEL_CANCELS) as executor:
        responses = list(executor.map(lambda _: cancel_booking(booking_id), range(PARALLEL_CANCELS)))

    statuses = Counter(r.status_code for r in responses)
    replayed = sum(r.headers.get("Idempotent-Replayed") == "true" for r in responses)
    after = slot_db_state(slot["id"])

    print(f"\n{PARALLEL_CANCELS} parallel cancels of booking {booking_id} (slot {slot['id']}, capacity 2)")
    print(f"  status codes : {dict(sorted(statuses.items()))}, replayed: {replayed}")
    print(f"  before       : reserved_count={before[1]} reservations={before[2]}")
    print(f"  after        : reserved_count={after[1]} reservations={after[2]}")

    assert statuses == {200: PARALLEL_CANCELS}
    assert {r.json()["status"] for r in responses} == {"CANCELLED"}
    assert before[1] == 2
    assert after[1] == 1
    assert after[2] == {"CONFIRMED": 1, "CANCELLED": 1}
