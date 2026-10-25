
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    CheckConstraint,
    Index,
    func,
    text,
)

from database import Base


class AppointmentSlot(Base):
    __tablename__ = "appointment_slots"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, nullable=False, index=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    capacity = Column(Integer, nullable=False)
    reserved_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "capacity > 0",
            name="check_capacity_positive",
        ),
        CheckConstraint(
            "reserved_count >= 0",
            name="check_reserved_nonnegative",
        ),
        CheckConstraint(
            "reserved_count <= capacity",
            name="check_reserved_within_capacity",
        ),
        CheckConstraint(
            "end_time > start_time",
            name="check_valid_slot_time",
        ),
    )


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)

    slot_id = Column(
        Integer,
        ForeignKey("appointment_slots.id"),
        nullable=False,
        index=True,
    )

    patient_id = Column(Integer, nullable=False, index=True)

    # Supports idempotent reservation requests.
    idempotency_key = Column(
        String(255),
        nullable=True,
        unique=True,
    )

    # State machine: see ALLOWED_TRANSITIONS in reservation_service.py.
    status = Column(
        String(20),
        nullable=False,
        default="PENDING",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'CANCELLED')",
            name="check_reservation_status",
        ),
        # BR2: one *active* reservation per patient and slot. Cancelled
        # reservations are excluded so the patient can book the slot again.
        Index(
            "uq_active_reservation_per_patient_slot",
            "slot_id",
            "patient_id",
            unique=True,
            postgresql_where=text("status <> 'CANCELLED'"),
            sqlite_where=text("status <> 'CANCELLED'"),
        ),
    )
