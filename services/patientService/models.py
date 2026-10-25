from sqlalchemy import Column, Integer, String, DateTime, CheckConstraint, ForeignKey, func
from database import Base


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    phone = Column(String(15), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ACTIVE -> BLOCKED (automatically at the no-show limit), BLOCKED -> ACTIVE (unblock)
    status = Column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    # No-shows since the patient was registered or last unblocked.
    no_show_count = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        CheckConstraint("age >= 0 AND age <= 120", name="check_patient_age"),
        CheckConstraint("status IN ('ACTIVE', 'BLOCKED')", name="check_patient_status"),
        CheckConstraint("no_show_count >= 0", name="check_no_show_count_nonnegative"),
    )


class NoShow(Base):
    """One row per missed appointment; the unique booking_id makes reporting it idempotent."""

    __tablename__ = "patient_no_shows"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    # Owned by Booking Service; no foreign key across databases.
    booking_id = Column(Integer, nullable=False, unique=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
