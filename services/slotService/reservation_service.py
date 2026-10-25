
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AppointmentSlot, Reservation

# Reservation state machine. Only these transitions are allowed; every
# transition is applied with a conditional UPDATE on the current status, so
# two concurrent requests can never both perform it.
#
#   PENDING ──▶ CONFIRMED ──▶ CANCELLED
#      └──────────────────────────▲
#
# Reservations are created directly as CONFIRMED today (no time-bound holds),
# so PENDING is reserved for a future "hold" step.
ALLOWED_TRANSITIONS = {
    "PENDING": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"CANCELLED"},
    "CANCELLED": set(),
}


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    # SQLite (unit tests) returns naive datetimes; PostgreSQL returns aware ones.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def reserve_slot(
    db: Session,
    slot_id: int,
    patient_id: int,
    idempotency_key: str,
):
    """
    Business rules enforced here:
      BR1  A slot is never booked beyond its capacity (atomic conditional
           UPDATE + CHECK constraint, no check-then-update in Python).
      BR2  A patient holds at most one reservation per slot (unique
           constraint on slot_id + patient_id).
      BR3  A repeated Idempotency-Key returns the original reservation; the
           same key with a different payload is rejected.
      BR8  A slot that has already started cannot be reserved.
    """
    try:
        # 1. Check whether this key has already been used (BR3).
        existing = (
            db.query(Reservation)
            .filter(
                Reservation.idempotency_key == idempotency_key
            )
            .first()
        )

        if existing is not None:
            if (
                existing.slot_id != slot_id
                or existing.patient_id != patient_id
            ):
                return None, "IDEMPOTENCY_KEY_REUSED"

            return existing, None

        # 2. Atomically claim capacity in PostgreSQL (BR1, BR8).
        now = utc_now()
        result = db.execute(
            update(AppointmentSlot)
            .where(
                AppointmentSlot.id == slot_id,
                AppointmentSlot.start_time > now,
                AppointmentSlot.reserved_count
                < AppointmentSlot.capacity,
            )
            .values(
                reserved_count=AppointmentSlot.reserved_count + 1
            )
        )

        if result.rowcount != 1:
            db.rollback()

            slot = db.get(AppointmentSlot, slot_id)

            if slot is None:
                return None, "SLOT_NOT_FOUND"

            if as_utc(slot.start_time) <= now:
                return None, "SLOT_ALREADY_STARTED"

            return None, "SLOT_FULL"

        # 3. Create the reservation in the same transaction.
        reservation = Reservation(
            slot_id=slot_id,
            patient_id=patient_id,
            idempotency_key=idempotency_key,
            status="CONFIRMED",
        )

        db.add(reservation)
        db.commit()
        db.refresh(reservation)

        return reservation, None

    except IntegrityError:
        # Roll back the capacity increment if insertion fails.
        db.rollback()

        # Another concurrent request may have used this key first.
        existing = (
            db.query(Reservation)
            .filter(
                Reservation.idempotency_key == idempotency_key
            )
            .first()
        )

        if existing is not None:
            if (
                existing.slot_id != slot_id
                or existing.patient_id != patient_id
            ):
                return None, "IDEMPOTENCY_KEY_REUSED"

            return existing, None

        # Handle a duplicate booking made with another key (BR2).
        duplicate = (
            db.query(Reservation)
            .filter(
                Reservation.slot_id == slot_id,
                Reservation.patient_id == patient_id,
                Reservation.status != "CANCELLED",
            )
            .first()
        )

        if duplicate is not None:
            return None, "DUPLICATE_BOOKING"

        raise

    except Exception:
        db.rollback()
        raise


def cancel_reservation(db: Session, reservation_id: int):
    """
    BR10  CONFIRMED -> CANCELLED frees exactly one place in the slot, and only
          before the slot starts.

    Cancelling is idempotent: cancelling a cancelled reservation returns it
    unchanged and never frees capacity a second time. The status change and
    the capacity release happen in one transaction, and the status change is
    conditional on the current status, so of several concurrent cancels only
    one releases capacity.

    Returns (reservation, outcome): CANCELLED, ALREADY_CANCELLED,
    RESERVATION_NOT_FOUND, SLOT_ALREADY_STARTED or INVALID_STATE_TRANSITION.
    """
    reservation = db.get(Reservation, reservation_id)
    if reservation is None:
        return None, "RESERVATION_NOT_FOUND"

    if reservation.status == "CANCELLED":
        return reservation, "ALREADY_CANCELLED"

    if "CANCELLED" not in ALLOWED_TRANSITIONS[reservation.status]:
        return reservation, "INVALID_STATE_TRANSITION"

    slot = db.get(AppointmentSlot, reservation.slot_id)
    now = utc_now()
    if as_utc(slot.start_time) <= now:
        return reservation, "SLOT_ALREADY_STARTED"

    allowed_from = [state for state, targets in ALLOWED_TRANSITIONS.items() if "CANCELLED" in targets]
    try:
        changed = db.execute(
            update(Reservation)
            .where(
                Reservation.id == reservation_id,
                Reservation.status.in_(allowed_from),
            )
            .values(status="CANCELLED", cancelled_at=now)
        ).rowcount

        if changed != 1:
            # A concurrent cancel got there first.
            db.rollback()
            db.refresh(reservation)
            return reservation, "ALREADY_CANCELLED"

        db.execute(
            update(AppointmentSlot)
            .where(
                AppointmentSlot.id == reservation.slot_id,
                AppointmentSlot.reserved_count > 0,
            )
            .values(reserved_count=AppointmentSlot.reserved_count - 1)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(reservation)
    return reservation, "CANCELLED"
