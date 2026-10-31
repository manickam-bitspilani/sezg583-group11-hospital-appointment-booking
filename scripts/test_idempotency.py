"""
Challenge 2: repeating POST /api/v1/bookings with the same Idempotency-Key
returns the same result and has no duplicate effect -- for sequential
repeats AND for identical requests arriving at the same moment.

Run with services up:   pytest -s scripts/test_idempotency.py
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from conftest import BOOKING_URL, book, create_patient, create_slot, get_slot, notifications_for

PARALLEL = 20


def test_sequential_repeat_returns_stored_response():
    patient = create_patient()
    slot = create_slot(capacity=5)
    key = f"seq-{uuid.uuid4().hex[:8]}"

    first = book(patient["id"], slot["id"], key)
    repeat = book(patient["id"], slot["id"], key)
    print("\nFIRST ", first.status_code, dict(first.headers).get("idempotent-replayed"), first.text)
    print("REPEAT", repeat.status_code, dict(repeat.headers).get("idempotent-replayed"), repeat.text)

    assert first.status_code == repeat.status_code == 201
    assert repeat.json() == first.json()
    assert repeat.headers["Idempotent-Replayed"] == "true"
    assert get_slot(slot["id"])["reserved_count"] == 1
    assert len(notifications_for(first.json()["booking_id"])) == 1


def test_same_key_different_payload_is_rejected():
    patient = create_patient()
    slot_a, slot_b = create_slot(capacity=5), create_slot(capacity=5)
    key = f"reuse-{uuid.uuid4().hex[:8]}"

    book(patient["id"], slot_a["id"], key)
    response = book(patient["id"], slot_b["id"], key)
    print("\nREUSED", response.status_code, response.text)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert get_slot(slot_b["id"])["reserved_count"] == 0


def test_simultaneous_identical_requests_create_one_booking():
    patient = create_patient()
    slot = create_slot(capacity=5)
    key = f"race-{uuid.uuid4().hex[:8]}"

    with ThreadPoolExecutor(max_workers=PARALLEL) as executor:
        responses = list(executor.map(lambda _: book(patient["id"], slot["id"], key), range(PARALLEL)))

    statuses = [r.status_code for r in responses]
    bodies = {r.text for r in responses}
    replayed = sum(r.headers.get("Idempotent-Replayed") == "true" for r in responses)
    booking_id = responses[0].json()["booking_id"]
    final = get_slot(slot["id"])
    notifications = notifications_for(booking_id)

    print(f"\n{PARALLEL} simultaneous requests with Idempotency-Key={key}")
    print(f"  status codes: {sorted(set(statuses))}, distinct bodies: {len(bodies)}, replayed: {replayed}")
    print(f"  body: {responses[0].text}")
    print(f"  slot reserved_count: {final['reserved_count']}, notifications for booking: {len(notifications)}")

    assert statuses == [201] * PARALLEL
    assert len(bodies) == 1
    assert replayed == PARALLEL - 1
    assert final["reserved_count"] == 1
    assert len(notifications) == 1

    stored = httpx.get(f"{BOOKING_URL}/api/v1/bookings/{booking_id}").json()
    assert stored["status"] == "CONFIRMED"
