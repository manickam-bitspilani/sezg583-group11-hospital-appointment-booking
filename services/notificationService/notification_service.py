
import hashlib
import json
import os
import random
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Notification
from schemas import NotificationRequest, SimulationSettings

# Mock provider behaviour; starts from env vars and can be changed at runtime
# through PUT /api/v1/simulation (handy for demoing failures without restarts).
simulation = SimulationSettings(
    failure_rate=float(os.getenv("NOTIFICATION_FAILURE_RATE", "0")),
    latency_ms=int(os.getenv("NOTIFICATION_LATENCY_MS", "0")),
    fail_next=int(os.getenv("NOTIFICATION_FAIL_NEXT", "0")),
)

# Sync endpoints run in a thread pool, so concurrent sends could otherwise
# both read fail_next=1 and both fail.
_simulation_lock = threading.Lock()


def fingerprint(request: NotificationRequest) -> str:
    canonical = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def claim_notification(
    db: Session,
    request: NotificationRequest,
    idempotency_key: str,
    correlation_id: str,
):
    """
    BR6  A patient is notified at most once per Idempotency-Key.

    Returns (notification, outcome):
      NEW          row created, caller must deliver it
      RETRY        earlier attempt FAILED, caller now owns the retry
      REPLAY       already SENT, return it without sending again
      IN_PROGRESS  another request is delivering it right now
      KEY_REUSED   same key, different payload
    """
    request_fingerprint = fingerprint(request)

    existing = (
        db.query(Notification)
        .filter(Notification.idempotency_key == idempotency_key)
        .first()
    )

    if existing is None:
        notification = Notification(
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            patient_id=request.patient_id,
            booking_id=request.booking_id,
            notification_type=request.notification_type,
            message=request.message,
            status="PENDING",
            attempts=0,
            correlation_id=correlation_id,
        )
        db.add(notification)
        try:
            db.commit()
            db.refresh(notification)
            return notification, "NEW"
        except IntegrityError:
            # A concurrent request inserted the same key first.
            db.rollback()
            existing = (
                db.query(Notification)
                .filter(Notification.idempotency_key == idempotency_key)
                .first()
            )

    if existing.request_fingerprint != request_fingerprint:
        return existing, "KEY_REUSED"

    if existing.status == "SENT":
        return existing, "REPLAY"

    if existing.status == "FAILED":
        # Atomic FAILED -> PENDING so only one concurrent retry wins.
        claimed = db.execute(
            update(Notification)
            .where(
                Notification.id == existing.id,
                Notification.status == "FAILED",
            )
            .values(status="PENDING")
        ).rowcount
        db.commit()
        db.refresh(existing)
        if claimed == 1:
            return existing, "RETRY"

    return existing, "IN_PROGRESS"


def wait_while_pending(db: Session, notification: Notification, timeout_seconds: float):
    """
    A retry often arrives while the attempt it is retrying is still being
    delivered (the caller timed out, the provider did not). Wait briefly for
    that attempt to finish so the retry can return its real outcome.
    """
    deadline = time.monotonic() + timeout_seconds
    while notification.status == "PENDING" and time.monotonic() < deadline:
        time.sleep(0.1)
        db.refresh(notification)


def simulated_failure() -> str | None:
    """
    Decide whether this send attempt fails. fail_next gives an exact,
    repeatable number of failures (for demos and evidence); after that the
    random failure_rate applies. Returns the failure reason, or None.
    """
    with _simulation_lock:
        if simulation.fail_next > 0:
            simulation.fail_next -= 1
            return "Simulated provider failure (fail_next)"

    if random.random() < simulation.failure_rate:
        return "Simulated provider failure (failure_rate)"
    return None


def deliver(db: Session, notification: Notification) -> bool:
    """
    Simulated call to an external SMS / e-mail provider. Only the request
    that claimed the row (BR6) gets here, so each attempt sends at most once.
    """
    notification.attempts += 1

    if simulation.latency_ms:
        time.sleep(simulation.latency_ms / 1000)

    error = simulated_failure()
    if error is not None:
        notification.status = "FAILED"
        notification.last_error = error
        db.commit()
        return False

    notification.status = "SENT"
    notification.sent_at = datetime.now(timezone.utc)
    notification.last_error = None
    db.commit()
    return True
