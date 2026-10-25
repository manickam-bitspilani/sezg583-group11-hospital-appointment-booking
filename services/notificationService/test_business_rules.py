
import pytest
from fastapi.testclient import TestClient

import main
import notification_service
from database import Base, SessionLocal, engine
from main import app
from models import Notification
from schemas import NotificationRequest, SimulationSettings

PAYLOAD = {
    "patient_id": 1,
    "booking_id": 7,
    "message": "Appointment confirmed.",
    "notification_type": "SMS",
}


@pytest.fixture
def api():
    notification_service.simulation = SimulationSettings(failure_rate=0, latency_ms=0, fail_next=0)
    with TestClient(app) as client:
        yield client
    Base.metadata.drop_all(bind=engine)


def send(api, key, payload=PAYLOAD):
    return api.post("/api/v1/notifications", headers={"Idempotency-Key": key}, json=payload)


def count(status=None):
    from database import SessionLocal

    with SessionLocal() as db:
        query = db.query(Notification)
        if status:
            query = query.filter(Notification.status == status)
        return query.count()


def test_br6_same_key_notifies_only_once(api):
    first = send(api, "booking-7-confirmation")
    repeat = send(api, "booking-7-confirmation")

    assert first.status_code == 202
    assert repeat.status_code == 200
    assert repeat.headers["Idempotent-Replayed"] == "true"
    assert repeat.json()["id"] == first.json()["id"]
    assert count() == 1


def test_br6_same_key_with_different_payload_is_rejected(api):
    send(api, "booking-7-confirmation")

    response = send(api, "booking-7-confirmation", {**PAYLOAD, "message": "Something else"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_provider_failure_returns_503_and_retry_with_same_key_succeeds(api):
    api.put("/api/v1/simulation", json={"failure_rate": 1.0, "latency_ms": 0})
    failed = send(api, "booking-7-confirmation")

    api.put("/api/v1/simulation", json={"failure_rate": 0.0, "latency_ms": 0})
    retried = send(api, "booking-7-confirmation")

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert retried.status_code == 202
    assert retried.json()["attempts"] == 2
    assert count() == 1
    assert count("SENT") == 1


# -------------------- DETERMINISTIC FAILURES AND last_error --------------------

def simulate(api, **settings):
    response = api.put("/api/v1/simulation", json=settings)
    assert response.status_code == 200, response.text


def test_fail_next_fails_exactly_n_attempts_then_succeeds(api):
    simulate(api, fail_next=2)

    first = send(api, "booking-7-confirmation")
    second = send(api, "booking-7-confirmation")
    third = send(api, "booking-7-confirmation")

    assert first.status_code == 503
    assert second.status_code == 503
    assert third.status_code == 202
    assert third.json()["attempts"] == 3
    assert third.json()["last_error"] is None
    assert count() == 1
    assert api.get("/api/v1/simulation").json()["fail_next"] == 0


def test_failed_send_records_last_error(api):
    simulate(api, fail_next=1)

    assert send(api, "booking-7-confirmation").status_code == 503

    notification_id = api.get("/api/v1/notifications", params={"booking_id": 7}).json()[0]["id"]
    stored = api.get(f"/api/v1/notifications/{notification_id}").json()
    assert stored["status"] == "FAILED"
    assert "fail_next" in stored["last_error"]


def test_simulation_accepts_old_body_without_fail_next(api):
    # scripts/test_resilience.py sends only these two fields.
    response = api.put("/api/v1/simulation", json={"failure_rate": 0.5, "latency_ms": 0})

    assert response.status_code == 200
    assert response.json() == {"failure_rate": 0.5, "latency_ms": 0, "fail_next": 0}


def test_concurrent_request_while_pending_gets_409(api, monkeypatch):
    monkeypatch.setattr(main, "IN_PROGRESS_WAIT_SECONDS", 0.2)
    with SessionLocal() as db:
        db.add(Notification(
            idempotency_key="booking-7-confirmation",
            request_fingerprint=notification_service.fingerprint(NotificationRequest(**PAYLOAD)),
            patient_id=1,
            booking_id=7,
            notification_type="SMS",
            message=PAYLOAD["message"],
            status="PENDING",
            attempts=0,
        ))
        db.commit()

    response = send(api, "booking-7-confirmation")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
    assert count() == 1
