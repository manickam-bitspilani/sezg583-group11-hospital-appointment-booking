
from sqlalchemy import (
    JSON,
    Column,
    Integer,
    String,
    DateTime,
    CheckConstraint,
    func,
)

from database import Base


class Booking(Base):
    """
    The booking saga record and, at the same time, the idempotency record:
    the key, a fingerprint of the request and the final response are stored
    together so a repeated request is answered from here.
    """

    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)

    idempotency_key = Column(String(255), nullable=False, unique=True)
    request_fingerprint = Column(String(64), nullable=False)

    # IDs owned by Patient / Slot services; no cross-database foreign keys.
    patient_id = Column(Integer, nullable=False, index=True)
    slot_id = Column(Integer, nullable=False, index=True)
    reservation_id = Column(Integer, nullable=True)

    # PENDING -> CONFIRMED | FAILED,  CONFIRMED -> CANCELLED
    status = Column(String(20), nullable=False, default="PENDING")
    notification_status = Column(String(20), nullable=True)
    cancel_notification_status = Column(String(20), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    response_status_code = Column(Integer, nullable=True)
    response_body = Column(JSON, nullable=True)

    correlation_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED')",
            name="check_booking_status",
        ),
        CheckConstraint(
            "notification_status IS NULL OR notification_status IN ('SENT', 'FAILED')",
            name="check_booking_notification_status",
        ),
        CheckConstraint(
            "cancel_notification_status IS NULL OR cancel_notification_status IN ('SENT', 'FAILED')",
            name="check_booking_cancel_notification_status",
        ),
    )
