
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from schemas import SlotCreate
from reservation_schemas import ReservationCreate


def test_br7_slot_capacity_must_be_positive():
    with pytest.raises(ValidationError):
        SlotCreate(
            doctor_id=1,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            capacity=0,
        )


def test_br7_slot_end_time_must_be_after_start_time():
    start = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        SlotCreate(
            doctor_id=1,
            start_time=start,
            end_time=start,
            capacity=5,
        )


def test_slot_times_must_include_a_timezone():
    with pytest.raises(ValidationError):
        SlotCreate(
            doctor_id=1,
            start_time="2030-01-01T10:00:00",
            end_time="2030-01-01T10:30:00",
            capacity=5,
        )


def test_doctor_id_must_be_positive():
    start = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        SlotCreate(
            doctor_id=0,
            start_time=start,
            end_time=start + timedelta(minutes=30),
            capacity=5,
        )


def test_reservation_ids_must_be_positive():
    with pytest.raises(ValidationError):
        ReservationCreate(slot_id=0, patient_id=10)

    with pytest.raises(ValidationError):
        ReservationCreate(slot_id=1, patient_id=-10)


# -------------------- RESERVATION RULES (service layer) --------------------

from models import AppointmentSlot, Reservation
from reservation_service import cancel_reservation, reserve_slot
from slot_service import create_slot


def _slot(db, capacity, starts_in=timedelta(days=1)):
    start = datetime.now(timezone.utc) + starts_in
    slot = AppointmentSlot(
        doctor_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=30),
        capacity=capacity,
        reserved_count=0,
    )
    db.add(slot)
    db.commit()
    return slot.id


def test_br1_slot_is_never_booked_beyond_capacity(db):
    slot_id = _slot(db, capacity=2)

    assert reserve_slot(db, slot_id, patient_id=1, idempotency_key="k1")[1] is None
    assert reserve_slot(db, slot_id, patient_id=2, idempotency_key="k2")[1] is None

    reservation, error = reserve_slot(db, slot_id, patient_id=3, idempotency_key="k3")

    assert reservation is None
    assert error == "SLOT_FULL"
    assert db.get(AppointmentSlot, slot_id).reserved_count == 2


def test_br2_patient_cannot_reserve_same_slot_twice(db):
    slot_id = _slot(db, capacity=5)
    reserve_slot(db, slot_id, patient_id=1, idempotency_key="first")

    reservation, error = reserve_slot(db, slot_id, patient_id=1, idempotency_key="second")

    assert error == "DUPLICATE_BOOKING"
    # The capacity claimed by the rejected attempt was rolled back.
    assert db.get(AppointmentSlot, slot_id).reserved_count == 1


def test_br3_same_key_returns_original_reservation(db):
    slot_id = _slot(db, capacity=5)
    first, _ = reserve_slot(db, slot_id, patient_id=1, idempotency_key="same")

    repeat, error = reserve_slot(db, slot_id, patient_id=1, idempotency_key="same")

    assert error is None
    assert repeat.id == first.id
    assert db.query(Reservation).count() == 1
    assert db.get(AppointmentSlot, slot_id).reserved_count == 1


def test_br3_same_key_with_different_payload_is_rejected(db):
    slot_id = _slot(db, capacity=5)
    reserve_slot(db, slot_id, patient_id=1, idempotency_key="same")

    reservation, error = reserve_slot(db, slot_id, patient_id=2, idempotency_key="same")

    assert error == "IDEMPOTENCY_KEY_REUSED"
    assert db.query(Reservation).count() == 1


def test_unknown_slot_is_reported(db):
    assert reserve_slot(db, 999, patient_id=1, idempotency_key="k")[1] == "SLOT_NOT_FOUND"


# -------------------- SLOT CREATION RULES --------------------

def _slot_request(start, minutes=30, doctor_id=1):
    return SlotCreate(
        doctor_id=doctor_id,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        capacity=2,
    )


def test_br8_slot_must_start_in_the_future(db):
    slot, error = create_slot(db, _slot_request(datetime.now(timezone.utc) - timedelta(hours=1)))

    assert slot is None
    assert error == "SLOT_IN_PAST"


def test_br8_started_slot_cannot_be_reserved(db):
    slot_id = _slot(db, capacity=5, starts_in=-timedelta(minutes=5))

    reservation, error = reserve_slot(db, slot_id, patient_id=1, idempotency_key="late")

    assert error == "SLOT_ALREADY_STARTED"
    assert db.get(AppointmentSlot, slot_id).reserved_count == 0


def test_br9_doctor_cannot_have_overlapping_slots(db):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    assert create_slot(db, _slot_request(start))[1] is None

    overlapping = create_slot(db, _slot_request(start + timedelta(minutes=15)))
    back_to_back = create_slot(db, _slot_request(start + timedelta(minutes=30)))
    other_doctor = create_slot(db, _slot_request(start, doctor_id=2))

    assert overlapping[1] == "SLOT_OVERLAP"
    assert back_to_back[1] is None
    assert other_doctor[1] is None


# -------------------- CANCELLATION (state machine) --------------------

def test_br10_cancel_frees_capacity_exactly_once(db):
    slot_id = _slot(db, capacity=1)
    reservation, _ = reserve_slot(db, slot_id, patient_id=1, idempotency_key="k1")

    first = cancel_reservation(db, reservation.id)
    second = cancel_reservation(db, reservation.id)

    assert first[1] == "CANCELLED"
    assert first[0].status == "CANCELLED"
    assert first[0].cancelled_at is not None
    assert second[1] == "ALREADY_CANCELLED"
    assert db.get(AppointmentSlot, slot_id).reserved_count == 0


def test_br10_cancelled_place_can_be_booked_again(db):
    slot_id = _slot(db, capacity=1)
    reservation, _ = reserve_slot(db, slot_id, patient_id=1, idempotency_key="k1")
    assert reserve_slot(db, slot_id, patient_id=2, idempotency_key="k2")[1] == "SLOT_FULL"

    cancel_reservation(db, reservation.id)

    # The freed place goes to another patient, and the original patient can
    # also book again later (BR2 only counts active reservations).
    assert reserve_slot(db, slot_id, patient_id=2, idempotency_key="k2")[1] is None
    cancel_reservation(db, db.query(Reservation).filter_by(idempotency_key="k2").one().id)
    assert reserve_slot(db, slot_id, patient_id=1, idempotency_key="k3")[1] is None


def test_br10_reservation_cannot_be_cancelled_after_slot_starts(db):
    slot_id = _slot(db, capacity=1)
    reservation, _ = reserve_slot(db, slot_id, patient_id=1, idempotency_key="k1")
    slot = db.get(AppointmentSlot, slot_id)
    slot.start_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    _, outcome = cancel_reservation(db, reservation.id)

    assert outcome == "SLOT_ALREADY_STARTED"
    assert db.get(Reservation, reservation.id).status == "CONFIRMED"
    assert db.get(AppointmentSlot, slot_id).reserved_count == 1


def test_br10_unknown_reservation_is_reported(db):
    assert cancel_reservation(db, 999)[1] == "RESERVATION_NOT_FOUND"
