
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    CheckConstraint,
    func,
)

from database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)

    # One row per Idempotency-Key: the database, not application code,
    # guarantees a notification is recorded (and sent) at most once.
    idempotency_key = Column(String(255), nullable=False, unique=True)
    request_fingerprint = Column(String(64), nullable=False)

    # IDs owned by other services; no foreign keys across databases.
    patient_id = Column(Integer, nullable=False, index=True)
    booking_id = Column(Integer, nullable=True, index=True)

    notification_type = Column(String(10), nullable=False)
    message = Column(String(500), nullable=False)

    # PENDING -> SENT, PENDING -> FAILED, FAILED -> PENDING (retry)
    status = Column(String(20), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    # Why the most recent send attempt failed; cleared when a send succeeds.
    last_error = Column(String(255), nullable=True)

    correlation_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED')",
            name="check_notification_status",
        ),
        CheckConstraint(
            "notification_type IN ('SMS', 'EMAIL')",
            name="check_notification_type",
        ),
    )
