"""
End-to-end test of the main transaction:
Booking -> Patient (validate) -> Slot (reserve) -> Notification (SMS).

Run with all four services up:   pytest -s scripts/test_end_to_end.py
"""

from datetime import datetime, timedelta, timezone

import httpx

from conftest import (
    BOOKING_URL,
    PATIENT_URL,
    SLOT_URL,
    book,
    cancel_booking,
    create_patient,
    create_slot,
    get_slot,
    notifications_for,
    set_notification_simulation,
)


def test_complete_booking_flow():
    patient = create_patient("E2E Patient")
    slot = create_slot(capacity=3)

    response = book(patient["id"], slot["id"])
    print("\nBOOK  ", response.status_code, response.text)

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "CONFIRMED"
    assert data["patient_id"] == patient["id"]
    assert data["slot_id"] == slot["id"]
    assert data["notification_status"] == "SENT"
    assert response.headers["X-Correlation-ID"] == data["correlation_id"]

    booking = httpx.get(f"{BOOKING_URL}/api/v1/bookings/{data['booking_id']}").json()
    print("STORED", booking)
    assert booking["status"] == "CONFIRMED"

    assert get_slot(slot["id"])["reserved_count"] == 1
    notifications = notifications_for(data["booking_id"])
    assert [n["status"] for n in notifications] == ["SENT"]
    assert notifications[0]["correlation_id"] == data["correlation_id"]


def test_unknown_patient_is_rejected_and_slot_untouched():
    slot = create_slot(capacity=3)

    response = book(999999, slot["id"])
    print("\nBOOK  ", response.status_code, response.text)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"
    assert get_slot(slot["id"])["reserved_count"] == 0


def test_full_slot_is_rejected():
    slot = create_slot(capacity=1)
    first = book(create_patient()["id"], slot["id"])
    second = book(create_patient()["id"], slot["id"])
    print("\nFIRST ", first.status_code, first.text)
    print("SECOND", second.status_code, second.text)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SLOT_FULL"


def test_notification_failure_does_not_undo_booking():
    patient = create_patient()
    slot = create_slot(capacity=3)

    set_notification_simulation(failure_rate=1.0)
    try:
        response = book(patient["id"], slot["id"])
    finally:
        set_notification_simulation(failure_rate=0.0)
    print("\nBOOK  ", response.status_code, response.text)

    assert response.status_code == 201
    assert response.json()["status"] == "CONFIRMED"
    assert response.json()["notification_status"] == "FAILED"
    assert get_slot(slot["id"])["reserved_count"] == 1


def test_cancel_releases_place_and_patient_can_rebook():
    patient = create_patient()
    slot = create_slot(capacity=1)
    booking = book(patient["id"], slot["id"]).json()
    assert book(create_patient()["id"], slot["id"]).status_code == 409   # full

    cancelled = cancel_booking(booking["booking_id"])
    again = cancel_booking(booking["booking_id"])
    print("\nCANCEL", cancelled.status_code, cancelled.text)
    print("AGAIN ", again.status_code, again.headers.get("Idempotent-Replayed"), again.text)

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancel_notification_status"] == "SENT"
    assert again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert get_slot(slot["id"])["reserved_count"] == 0                  # freed once

    reservation = httpx.get(f"{SLOT_URL}/api/v1/reservations/{booking['reservation_id']}").json()
    assert reservation["status"] == "CANCELLED"
    sms = notifications_for(booking["booking_id"])
    assert [n["message"].split(".")[0] for n in sms] == ["Appointment confirmed", "Appointment cancelled"]

    rebook = book(patient["id"], slot["id"])                             # same patient, new key
    print("REBOOK", rebook.status_code, rebook.text)
    assert rebook.status_code == 201
    assert get_slot(slot["id"])["reserved_count"] == 1


def test_slot_rules_past_and_overlap():
    past = httpx.post(f"{SLOT_URL}/api/v1/slots", json={
        "doctor_id": 7, "start_time": "2020-01-01T10:00:00Z",
        "end_time": "2020-01-01T10:30:00Z", "capacity": 1,
    })
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=400)
    first = create_slot(capacity=1, start=start)
    overlap = httpx.post(f"{SLOT_URL}/api/v1/slots", json={
        "doctor_id": first["doctor_id"],
        "start_time": (start + timedelta(minutes=10)).isoformat(),
        "end_time": (start + timedelta(minutes=40)).isoformat(),
        "capacity": 1,
    })
    print("\nPAST   ", past.status_code, past.text)
    print("OVERLAP", overlap.status_code, overlap.text)

    assert past.status_code == 422
    assert past.json()["error"]["code"] == "SLOT_IN_PAST"
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "SLOT_OVERLAP"


def test_blocked_patient_cannot_book_until_unblocked():
    patient = create_patient()
    slot = create_slot(capacity=3)

    # BR12: three no-shows (one per missed booking) block the patient.
    for booking_id in range(3):
        recorded = httpx.post(
            f"{PATIENT_URL}/api/v1/patients/{patient['id']}/no-shows",
            json={"booking_id": 900_000_000 + patient["id"] * 10 + booking_id},
        )
        assert recorded.status_code == 201, recorded.text
    print("\nAFTER 3 NO-SHOWS", recorded.json())
    assert recorded.json()["status"] == "BLOCKED"

    blocked = book(patient["id"], slot["id"])
    print("BOOK (blocked) ", blocked.status_code, blocked.text)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PATIENT_BLOCKED"
    assert get_slot(slot["id"])["reserved_count"] == 0                 # no place taken

    unblocked = httpx.post(f"{PATIENT_URL}/api/v1/patients/{patient['id']}/unblock")
    allowed = book(patient["id"], slot["id"])                          # new key
    print("UNBLOCK        ", unblocked.status_code, unblocked.text)
    print("BOOK (unblocked)", allowed.status_code, allowed.text)
    assert unblocked.json()["status"] == "ACTIVE"
    assert allowed.status_code == 201
    assert get_slot(slot["id"])["reserved_count"] == 1
