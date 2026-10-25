from sqlalchemy import text
from sqlalchemy.orm import Session

from models import AppointmentSlot
from reservation_service import utc_now
from schemas import SlotCreate


def create_slot(db: Session, data: SlotCreate):
    """
    Business rules enforced here:
      BR8  A slot must start in the future.
      BR9  A doctor cannot have two overlapping slots
           (overlap: new.start < existing.end AND new.end > existing.start;
           back-to-back slots are allowed).

    The overlap check is a read followed by an insert, so two concurrent
    requests for the same doctor could both pass it. On PostgreSQL a
    transaction-scoped advisory lock per doctor serialises slot creation for
    that doctor; other doctors are not blocked.

    Returns (slot, error): error is None, SLOT_IN_PAST or SLOT_OVERLAP.
    """
    if data.start_time <= utc_now():
        return None, "SLOT_IN_PAST"

    try:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:doctor_id)"), {"doctor_id": data.doctor_id})

        overlapping = (
            db.query(AppointmentSlot.id)
            .filter(
                AppointmentSlot.doctor_id == data.doctor_id,
                AppointmentSlot.start_time < data.end_time,
                AppointmentSlot.end_time > data.start_time,
            )
            .first()
        )
        if overlapping is not None:
            db.rollback()
            return None, "SLOT_OVERLAP"

        slot = AppointmentSlot(
            doctor_id=data.doctor_id,
            start_time=data.start_time,
            end_time=data.end_time,
            capacity=data.capacity,
            reserved_count=0,
        )
        db.add(slot)
        db.commit()  # releases the advisory lock
    except Exception:
        db.rollback()
        raise

    db.refresh(slot)
    return slot, None
