"""Shared helpers for the integration scripts (they need all services running)."""

import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import httpx

PATIENT_URL = os.getenv("PATIENT_SERVICE_URL", "http://127.0.0.1:8002")
SLOT_URL = os.getenv("SLOT_SERVICE_URL", "http://127.0.0.1:8001")
BOOKING_URL = os.getenv("BOOKING_SERVICE_URL", "http://127.0.0.1:8003")
NOTIFICATION_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:8004")


def create_patient(name="Test Patient"):
    phone = "9" + "".join(random.choices("0123456789", k=9))
    response = httpx.post(
        f"{PATIENT_URL}/api/v1/patients",
        json={"full_name": name, "age": 35, "phone": phone},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_slot(capacity, doctor_id=None, start=None):
    # A random doctor per slot keeps test runs clear of BR9 (no overlapping
    # slots for one doctor).
    doctor_id = doctor_id or random.randint(1_000, 1_000_000_000)
    start = start or datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=random.randint(1, 365))
    response = httpx.post(
        f"{SLOT_URL}/api/v1/slots",
        json={
            "doctor_id": doctor_id,
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=30)).isoformat(),
            "capacity": capacity,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_slot(slot_id):
    return httpx.get(f"{SLOT_URL}/api/v1/slots/{slot_id}").json()


def notifications_for(booking_id):
    return httpx.get(f"{NOTIFICATION_URL}/api/v1/notifications", params={"booking_id": booking_id}).json()


def set_notification_simulation(failure_rate=0.0, latency_ms=0, fail_next=0):
    response = httpx.put(
        f"{NOTIFICATION_URL}/api/v1/simulation",
        json={"failure_rate": failure_rate, "latency_ms": latency_ms, "fail_next": fail_next},
    )
    assert response.status_code == 200, response.text


def book(patient_id, slot_id, key=None, timeout=30.0):
    return httpx.post(
        f"{BOOKING_URL}/api/v1/bookings",
        headers={"Idempotency-Key": key or f"key-{uuid.uuid4().hex[:12]}"},
        json={"patient_id": patient_id, "slot_id": slot_id},
        timeout=timeout,
    )


def cancel_booking(booking_id):
    return httpx.post(f"{BOOKING_URL}/api/v1/bookings/{booking_id}/cancel", timeout=30.0)
