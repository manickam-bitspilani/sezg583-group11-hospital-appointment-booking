
import httpx
import pytest
from fastapi.testclient import TestClient

import booking_service
from database import Base, engine
from booking_service import backoff_delay
from main import app, get_http_client
from models import Booking


class FakeDownstream:
    """Stands in for Patient, Slot and Notification services."""

    def __init__(self):
        self.reservation_calls = 0
        self.notification_calls = 0
        self.notification_up = True
        # Optional scripted status codes for successive notification calls.
        self.notification_script = []
        self.notification_keys = []
        self.cancel_calls = 0
        self.cancel_status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/api/v1/patients/1/eligibility":
            return httpx.Response(200, json={"patient_id": 1, "eligible": True, "reason": None, "message": None})
        if path == "/api/v1/patients/2/eligibility":
            return httpx.Response(200, json={
                "patient_id": 2, "eligible": False, "reason": "PATIENT_BLOCKED",
                "message": "Patient is blocked from booking after 3 no-shows.",
            })
        if path.startswith("/api/v1/patients/"):
            return httpx.Response(404, json={"error": {"code": "PATIENT_NOT_FOUND", "message": "Patient not found."}})

        if path == "/api/v1/reservations":
            self.reservation_calls += 1
            return httpx.Response(201, json={"id": 10, "slot_id": 5, "patient_id": 1, "status": "CONFIRMED"})

        if path == "/api/v1/reservations/10/cancel":
            self.cancel_calls += 1
            if self.cancel_status == 409:
                return httpx.Response(409, json={"error": {"code": "SLOT_ALREADY_STARTED", "message": "This appointment slot has already started."}})
            return httpx.Response(200, json={"id": 10, "slot_id": 5, "patient_id": 1, "status": "CANCELLED"})

        if path == "/api/v1/notifications":
            self.notification_calls += 1
            self.notification_keys.append(request.headers["Idempotency-Key"])
            if not self.notification_up:
                raise httpx.ConnectError("notification service down", request=request)
            if self.notification_script:
                return httpx.Response(self.notification_script.pop(0), json={})
            return httpx.Response(202, json={})

        return httpx.Response(500)


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(booking_service.time, "sleep", lambda seconds: None)


@pytest.fixture
def downstream():
    fake = FakeDownstream()

    def client_override():
        with httpx.Client(transport=httpx.MockTransport(fake.handle)) as client:
            yield client

    app.dependency_overrides[get_http_client] = client_override
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def api(downstream):
    with TestClient(app) as client:
        yield client
    Base.metadata.drop_all(bind=engine)


def book(api, key, patient_id=1, slot_id=5):
    return api.post(
        "/api/v1/bookings",
        headers={"Idempotency-Key": key},
        json={"patient_id": patient_id, "slot_id": slot_id},
    )


def test_br3_repeated_request_is_replayed_without_new_side_effects(api, downstream):
    first = book(api, "key-1")
    second = book(api, "key-1")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert downstream.reservation_calls == 1
    assert downstream.notification_calls == 1


def test_br3_same_key_with_different_payload_is_rejected(api, downstream):
    book(api, "key-1", slot_id=5)

    response = book(api, "key-1", slot_id=6)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert downstream.reservation_calls == 1


def test_br4_booking_requires_existing_patient(api, downstream):
    response = book(api, "key-1", patient_id=999)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"
    assert downstream.reservation_calls == 0


def test_br12_blocked_patient_cannot_book(api, downstream):
    response = book(api, "key-1", patient_id=2)
    repeat = book(api, "key-1", patient_id=2)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PATIENT_BLOCKED"
    assert downstream.reservation_calls == 0      # no slot place taken
    assert downstream.notification_calls == 0
    assert repeat.json() == response.json()       # stored and replayed (BR3)
    assert repeat.headers["Idempotent-Replayed"] == "true"


def test_br5_booking_stays_confirmed_when_notification_fails(api, downstream):
    downstream.notification_up = False

    response = book(api, "key-1")

    assert response.status_code == 201
    assert response.json()["status"] == "CONFIRMED"
    assert response.json()["notification_status"] == "FAILED"
    assert downstream.notification_calls == booking_service.NOTIFICATION_MAX_ATTEMPTS


def test_notification_is_retried_until_it_succeeds(api, downstream):
    downstream.notification_script = [503, 503, 202]

    response = book(api, "key-1")

    assert response.json()["notification_status"] == "SENT"
    assert downstream.notification_calls == 3


def test_non_retryable_notification_error_is_not_retried(api, downstream):
    downstream.notification_script = [422]

    response = book(api, "key-1")

    assert response.json()["status"] == "CONFIRMED"
    assert response.json()["notification_status"] == "FAILED"
    assert downstream.notification_calls == 1


def test_backoff_grows_exponentially_and_is_capped():
    for attempt in range(1, 10):
        ceiling = min(booking_service.NOTIFICATION_BACKOFF_MAX_SECONDS, booking_service.NOTIFICATION_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
        delays = [backoff_delay(attempt) for _ in range(200)]
        assert all(0 <= d <= ceiling for d in delays)
        assert len(set(delays)) > 1  # jitter: not all clients wait the same


def test_validation_errors_use_uniform_error_format(api):
    response = api.post("/api/v1/bookings", headers={"Idempotency-Key": "k"}, json={"patient_id": 0})

    error = response.json()["error"]
    assert response.status_code == 422
    assert error["code"] == "VALIDATION_ERROR"
    assert {d["field"] for d in error["details"]} == {"body.patient_id", "body.slot_id"}
    assert error["correlation_id"] == response.headers["X-Correlation-ID"]


# -------------------- CANCELLATION (BR10) --------------------

def cancel(api, booking_id):
    return api.post(f"/api/v1/bookings/{booking_id}/cancel")


def test_br10_cancel_releases_reservation_and_notifies(api, downstream):
    booking_id = book(api, "key-1").json()["booking_id"]

    response = cancel(api, booking_id)

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["cancel_notification_status"] == "SENT"
    assert response.json()["cancelled_at"] is not None
    assert downstream.cancel_calls == 1
    assert downstream.notification_keys == [
        f"booking-{booking_id}-confirmation",
        f"booking-{booking_id}-cancellation",
    ]


def test_br10_cancelling_twice_has_no_new_side_effects(api, downstream):
    booking_id = book(api, "key-1").json()["booking_id"]
    first = cancel(api, booking_id)

    second = cancel(api, booking_id)

    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()
    assert downstream.cancel_calls == 1
    assert downstream.notification_calls == 2


def test_br10_only_confirmed_bookings_can_be_cancelled(api, downstream):
    book(api, "key-1", patient_id=999)   # stored as FAILED: patient missing
    booking_id = 1

    response = cancel(api, booking_id)

    assert api.get(f"/api/v1/bookings/{booking_id}").json()["status"] == "FAILED"
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert downstream.cancel_calls == 0


def test_br10_cancel_after_slot_started_is_rejected(api, downstream):
    booking_id = book(api, "key-1").json()["booking_id"]
    downstream.cancel_status = 409

    response = cancel(api, booking_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SLOT_ALREADY_STARTED"
    assert api.get(f"/api/v1/bookings/{booking_id}").json()["status"] == "CONFIRMED"
    assert downstream.notification_calls == 1   # no cancellation SMS


def test_cancel_unknown_booking_is_404(api):
    assert cancel(api, 999).status_code == 404
