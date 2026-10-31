
import pytest
from fastapi.testclient import TestClient

from database import Base, SessionLocal, engine
from main import app
from models import Patient

PAYLOAD = {"full_name": "Asha Rao", "age": 30, "phone": "9876543210"}


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client
    Base.metadata.drop_all(bind=engine)


def register(api, payload=PAYLOAD):
    return api.post("/api/v1/patients", json=payload)


def count():
    with SessionLocal() as db:
        return db.query(Patient).count()


def test_br11_phone_number_belongs_to_one_patient(api):
    first = register(api)

    duplicate = register(api, {**PAYLOAD, "full_name": "Someone Else"})

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PATIENT_PHONE_EXISTS"
    assert count() == 1


def test_br4_unknown_patient_is_404(api):
    # Booking Service relies on this 404 to reject bookings for unknown patients.
    response = api.get("/api/v1/patients/999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"


def test_registered_patient_can_be_fetched(api):
    created = register(api).json()

    fetched = api.get(f"/api/v1/patients/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created


@pytest.mark.parametrize(
    "field, value",
    [
        ("age", -1),
        ("age", 121),
        ("phone", "12345"),
        ("phone", "98765abcde"),
        ("full_name", "A"),
    ],
)
def test_invalid_patient_is_rejected_with_uniform_error(api, field, value):
    response = register(api, {**PAYLOAD, field: value})

    error = response.json()["error"]
    assert response.status_code == 422
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == [f"body.{field}"]
    assert error["correlation_id"] == response.headers["X-Correlation-ID"]
    assert count() == 0


# -------------------- ELIGIBILITY AND NO-SHOW POLICY (BR12) --------------------

import patient_service


def no_show(api, patient_id, booking_id):
    return api.post(f"/api/v1/patients/{patient_id}/no-shows", json={"booking_id": booking_id})


def eligibility(api, patient_id):
    return api.get(f"/api/v1/patients/{patient_id}/eligibility").json()


def test_br12_new_patient_is_active_and_eligible(api):
    patient = register(api).json()

    assert patient["status"] == "ACTIVE"
    assert patient["no_show_count"] == 0
    assert eligibility(api, patient["id"]) == {
        "patient_id": patient["id"], "eligible": True, "reason": None, "message": None,
    }


def test_br12_patient_is_blocked_at_the_no_show_limit(api):
    patient_id = register(api).json()["id"]

    for booking_id in range(1, patient_service.NO_SHOW_LIMIT):
        assert no_show(api, patient_id, booking_id).json()["status"] == "ACTIVE"
    assert eligibility(api, patient_id)["eligible"] is True

    last = no_show(api, patient_id, booking_id=100)

    assert last.status_code == 201
    assert last.json()["status"] == "BLOCKED"
    assert last.json()["no_show_count"] == patient_service.NO_SHOW_LIMIT
    result = eligibility(api, patient_id)
    assert result["eligible"] is False
    assert result["reason"] == "PATIENT_BLOCKED"


def test_br12_same_no_show_reported_twice_counts_once(api):
    patient_id = register(api).json()["id"]
    first = no_show(api, patient_id, booking_id=7)

    repeat = no_show(api, patient_id, booking_id=7)

    assert first.status_code == 201
    assert repeat.status_code == 200
    assert repeat.headers["Idempotent-Replayed"] == "true"
    assert repeat.json()["no_show_count"] == 1


def test_br12_booking_cannot_be_reported_for_two_patients(api):
    first_id = register(api).json()["id"]
    second_id = register(api, {**PAYLOAD, "phone": "9123456780"}).json()["id"]
    no_show(api, first_id, booking_id=7)

    response = no_show(api, second_id, booking_id=7)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_SHOW_OTHER_PATIENT"
    assert api.get(f"/api/v1/patients/{second_id}").json()["no_show_count"] == 0


def test_br12_unblock_makes_patient_eligible_again(api):
    patient_id = register(api).json()["id"]
    for booking_id in range(patient_service.NO_SHOW_LIMIT):
        no_show(api, patient_id, booking_id + 1)

    unblocked = api.post(f"/api/v1/patients/{patient_id}/unblock")
    again = api.post(f"/api/v1/patients/{patient_id}/unblock")

    assert unblocked.status_code == 200
    assert unblocked.json()["status"] == "ACTIVE"
    assert unblocked.json()["no_show_count"] == 0
    assert eligibility(api, patient_id)["eligible"] is True
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


@pytest.mark.parametrize("booking_id", [0, 2_147_483_648])
def test_br12_invalid_booking_id_is_rejected_with_uniform_error(api, booking_id):
    # 2_147_483_648 is one above PostgreSQL's INTEGER maximum.
    patient_id = register(api).json()["id"]

    response = no_show(api, patient_id, booking_id)

    error = response.json()["error"]
    assert response.status_code == 422
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == ["body.booking_id"]
    assert api.get(f"/api/v1/patients/{patient_id}").json()["no_show_count"] == 0


def test_br12_unknown_patient(api):
    assert api.get("/api/v1/patients/999/eligibility").status_code == 404
    assert no_show(api, 999, booking_id=1).status_code == 404
    assert api.post("/api/v1/patients/999/unblock").status_code == 404
