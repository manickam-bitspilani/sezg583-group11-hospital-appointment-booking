"""
Booking business logic: the booking transaction (validate patient, reserve
slot, notify), cancellation, and the idempotency record that makes a booking
safe to repeat. main.py only maps HTTP requests to these functions.
"""

import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone

import httpx
from fastapi import status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session

from common import CORRELATION_HEADER, ApiError, error_body
from models import Booking
from schemas import BookingRequest, BookingResponse

logger = logging.getLogger("booking-service")

PATIENT_URL = os.getenv(
    "PATIENT_SERVICE_URL", "http://127.0.0.1:8002"
)
SLOT_URL = os.getenv(
    "SLOT_SERVICE_URL", "http://127.0.0.1:8001"
)
NOTIFICATION_URL = os.getenv(
    "NOTIFICATION_SERVICE_URL", "http://127.0.0.1:8004"
)
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "5"))
NOTIFICATION_TIMEOUT_SECONDS = float(os.getenv("NOTIFICATION_TIMEOUT_SECONDS", "2"))
NOTIFICATION_MAX_ATTEMPTS = int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "4"))
NOTIFICATION_BACKOFF_BASE_SECONDS = float(os.getenv("NOTIFICATION_BACKOFF_BASE_SECONDS", "0.2"))
NOTIFICATION_BACKOFF_MAX_SECONDS = float(os.getenv("NOTIFICATION_BACKOFF_MAX_SECONDS", "2"))
IDEMPOTENCY_WAIT_SECONDS = float(os.getenv("IDEMPOTENCY_WAIT_SECONDS", "10"))


class DownstreamRejected(Exception):
    """A downstream service gave a final business answer (e.g. 404, 409)."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


def _downstream_error(response: httpx.Response, service: str):
    try:
        error = response.json()["error"]
        return error["code"], error["message"]
    except (ValueError, KeyError, TypeError):
        return "DOWNSTREAM_ERROR", f"{service} returned HTTP {response.status_code}."


# -------------------- IDEMPOTENCY RECORD --------------------

def fingerprint(request: BookingRequest) -> str:
    canonical = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def claim_booking(
    db: Session,
    request: BookingRequest,
    idempotency_key: str,
    correlation_id: str,
):
    """
    BR3  One Idempotency-Key = one booking attempt.

    Inserting the key is the claim: the unique constraint lets exactly one of
    several simultaneous identical requests win. Returns (booking, outcome):
      NEW         caller owns the key and runs the booking flow
      EXISTING    key already claimed with the same payload
      KEY_REUSED  key already claimed with a different payload
    """
    request_fingerprint = fingerprint(request)

    booking = Booking(
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        patient_id=request.patient_id,
        slot_id=request.slot_id,
        status="PENDING",
        correlation_id=correlation_id,
    )
    db.add(booking)
    try:
        db.commit()
        db.refresh(booking)
        return booking, "NEW"
    except IntegrityError:
        db.rollback()

    existing = (
        db.query(Booking)
        .filter(Booking.idempotency_key == idempotency_key)
        .first()
    )
    if existing is None:
        # The owner released the key (transient failure) between our insert
        # and this read; let the client retry.
        return None, "RELEASED"

    if existing.request_fingerprint != request_fingerprint:
        return existing, "KEY_REUSED"

    return existing, "EXISTING"


def wait_until_settled(db: Session, booking: Booking, timeout_seconds: float):
    """Poll a PENDING booking owned by a concurrent request until it settles."""
    deadline = time.monotonic() + timeout_seconds
    try:
        while booking.status == "PENDING" and time.monotonic() < deadline:
            time.sleep(0.1)
            db.refresh(booking)
    except InvalidRequestError:
        # Row deleted: the owner hit a transient failure and released the key.
        return None
    return booking


def settle(
    db: Session,
    booking: Booking,
    status: str,
    response_status_code: int,
    response_body: dict,
    **fields,
):
    """Store the final state together with the response that will be replayed."""
    booking.status = status
    booking.response_status_code = response_status_code
    booking.response_body = response_body
    for name, value in fields.items():
        setattr(booking, name, value)
    db.commit()


def release(db: Session, booking: Booking):
    """
    Free the key after a transient downstream failure (timeout, service down)
    so the client can retry with the same key. Safe because Slot Service is
    itself idempotent on that key: a retry never reserves twice.
    """
    db.delete(booking)
    db.commit()


# -------------------- PLACE BOOKING --------------------

def place_booking(
    db: Session,
    client: httpx.Client,
    request: BookingRequest,
    idempotency_key: str,
    correlation_id: str,
):
    """
    Run the booking transaction once per Idempotency-Key (BR3).

    Returns (status_code, body, replayed). A repeated key gets the stored
    response back with replayed=True and causes no new side effects.
    """
    booking, outcome = claim_booking(db, request, idempotency_key, correlation_id)

    if outcome == "KEY_REUSED":
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "IDEMPOTENCY_KEY_REUSED",
            "This Idempotency-Key was already used with a different request payload.",
        )

    if outcome in ("EXISTING", "RELEASED"):
        # A concurrent request owns this key: wait for its result.
        if outcome == "EXISTING":
            booking = wait_until_settled(db, booking, IDEMPOTENCY_WAIT_SECONDS)

        if booking is None or booking.status == "PENDING":
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "REQUEST_IN_PROGRESS",
                "A request with this Idempotency-Key is still being processed; retry shortly.",
            )

        logger.info(
            "Idempotent replay, returning stored response",
            extra={
                "booking_id": booking.id,
                "idempotency_key": idempotency_key,
                "original_correlation_id": booking.correlation_id,
            },
        )
        return booking.response_status_code, booking.response_body, True

    try:
        body = _run_booking(db, client, booking, request, idempotency_key, correlation_id)
        return status.HTTP_201_CREATED, body, False

    except DownstreamRejected as rejected:
        # Final answer (patient missing, slot full ...): store it so a retry
        # with the same key gets the same answer.
        body = error_body(rejected.code, rejected.message)
        settle(db, booking, "FAILED", rejected.status_code, body)
        logger.info("Booking failed", extra={"booking_id": booking.id, "reason": rejected.code})
        return rejected.status_code, body, False

    except httpx.TimeoutException:
        release(db, booking)
        logger.warning("Downstream service timed out; booking key released")
        raise ApiError(504, "DOWNSTREAM_TIMEOUT", "A downstream service timed out. Retry with the same Idempotency-Key.")

    except httpx.RequestError:
        release(db, booking)
        logger.exception("Downstream service connection failed; booking key released")
        raise ApiError(503, "DOWNSTREAM_UNAVAILABLE", "A downstream service is unavailable. Retry with the same Idempotency-Key.")

    except ApiError:
        release(db, booking)
        raise


def _run_booking(db, client, booking, request, idempotency_key, correlation_id):
    headers = {CORRELATION_HEADER: correlation_id}

    # 1. Ask Patient Service whether the patient exists (BR4) and may book
    # (BR12). The eligibility rule itself lives in Patient Service only.
    patient = client.get(
        f"{PATIENT_URL}/api/v1/patients/{request.patient_id}/eligibility",
        headers=headers,
    )

    if patient.status_code == 404:
        raise DownstreamRejected(404, "PATIENT_NOT_FOUND", "Patient not found.")

    if patient.status_code != 200:
        raise ApiError(502, "PATIENT_SERVICE_ERROR", "Patient Service request failed.")

    eligibility = patient.json()
    if not eligibility["eligible"]:
        raise DownstreamRejected(409, eligibility["reason"], eligibility["message"])

    # 2. Reserve the slot through Slot Service (same key: safe to retry).
    reservation = client.post(
        f"{SLOT_URL}/api/v1/reservations",
        headers={**headers, "Idempotency-Key": idempotency_key},
        json={
            "slot_id": request.slot_id,
            "patient_id": request.patient_id,
        },
    )

    if reservation.status_code in (404, 409, 422):
        code, message = _downstream_error(reservation, "Slot Service")
        raise DownstreamRejected(reservation.status_code, code, message)

    if reservation.status_code != 201:
        raise ApiError(502, "SLOT_SERVICE_ERROR", "Slot Service request failed.")

    result = reservation.json()

    # 3. Notify the patient. Non-critical (BR5): a failure is recorded but
    # does not undo the confirmed reservation.
    notification_status = _notify(
        client,
        booking,
        "confirmation",
        f"Appointment confirmed. Booking ID: {booking.id}, Slot ID: {request.slot_id}",
        headers,
    )

    body = BookingResponse(
        booking_id=booking.id,
        reservation_id=result["id"],
        patient_id=request.patient_id,
        slot_id=request.slot_id,
        status="CONFIRMED",
        notification_status=notification_status,
        correlation_id=correlation_id,
    ).model_dump()

    settle(
        db,
        booking,
        "CONFIRMED",
        status.HTTP_201_CREATED,
        body,
        reservation_id=result["id"],
        notification_status=notification_status,
    )

    logger.info(
        "Booking completed",
        extra={
            "booking_id": booking.id,
            "reservation_id": result["id"],
            "patient_id": request.patient_id,
            "slot_id": request.slot_id,
            "notification_status": notification_status,
        },
    )
    return body


# -------------------- NOTIFICATION (retry with backoff) --------------------

# Worth retrying: provider down (503), a concurrent attempt still running
# (409 REQUEST_IN_PROGRESS), gateway errors. Other 4xx will not change.
RETRYABLE_NOTIFICATION_STATUSES = {409, 502, 503, 504}


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter: uniform in [0, min(cap, base * 2^(attempt-1))]."""
    ceiling = min(NOTIFICATION_BACKOFF_MAX_SECONDS, NOTIFICATION_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
    return random.uniform(0, ceiling)


def _notify(client, booking, kind, message, headers):
    """
    Send an SMS (kind: "confirmation" or "cancellation") with timeout +
    retries (exponential backoff and jitter). Every attempt carries the same
    Idempotency-Key, so a retry after a timeout where the first attempt
    actually succeeded can never send a second SMS: Notification Service just
    replays the stored result.
    """
    log_fields = {"booking_id": booking.id, "notification_kind": kind}

    for attempt in range(1, NOTIFICATION_MAX_ATTEMPTS + 1):
        try:
            notification = client.post(
                f"{NOTIFICATION_URL}/api/v1/notifications",
                headers={
                    **headers,
                    # Stable per booking and kind: a resend can never notify twice.
                    "Idempotency-Key": f"booking-{booking.id}-{kind}",
                },
                json={
                    "patient_id": booking.patient_id,
                    "booking_id": booking.id,
                    "message": message,
                    "notification_type": "SMS",
                },
                timeout=NOTIFICATION_TIMEOUT_SECONDS,
            )
            outcome = notification.status_code
        except httpx.RequestError as error:
            outcome = type(error).__name__

        if outcome in (200, 202):
            if attempt > 1:
                logger.info("Notification sent after retry", extra={**log_fields, "attempt": attempt})
            return "SENT"

        if isinstance(outcome, int) and outcome not in RETRYABLE_NOTIFICATION_STATUSES:
            logger.warning(
                "Notification rejected, not retrying; booking remains confirmed",
                extra={**log_fields, "attempt": attempt, "outcome": outcome},
            )
            return "FAILED"

        if attempt == NOTIFICATION_MAX_ATTEMPTS:
            break

        delay = backoff_delay(attempt)
        logger.warning(
            "Notification attempt failed, retrying",
            extra={**log_fields, "attempt": attempt, "outcome": outcome, "retry_in_seconds": round(delay, 3)},
        )
        time.sleep(delay)

    logger.warning(
        "Notification failed after all retries; booking remains confirmed",
        extra={**log_fields, "attempts": NOTIFICATION_MAX_ATTEMPTS, "outcome": outcome},
    )
    return "FAILED"


# -------------------- CANCEL BOOKING --------------------

def cancel_booking(db: Session, client: httpx.Client, booking_id: int, correlation_id: str):
    """
    BR10  Only a CONFIRMED booking can be cancelled (CONFIRMED -> CANCELLED),
          and only before the slot starts. Cancelling releases the slot place
          and sends a cancellation SMS.

    Idempotent: cancelling a cancelled booking returns it unchanged. Safe to
    retry after a timeout because Slot Service's cancel is idempotent too.

    Returns (booking, replayed).
    """
    headers = {CORRELATION_HEADER: correlation_id}

    booking = db.get(Booking, booking_id)
    if booking is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "BOOKING_NOT_FOUND", "Booking not found.")

    if booking.status == "CANCELLED":
        return booking, True

    if booking.status != "CONFIRMED":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INVALID_STATE_TRANSITION",
            f"A {booking.status} booking cannot be cancelled.",
        )

    # 1. Release the reservation in Slot Service (the place goes back to the slot).
    try:
        cancelled = client.post(
            f"{SLOT_URL}/api/v1/reservations/{booking.reservation_id}/cancel",
            headers=headers,
        )
    except httpx.TimeoutException:
        raise ApiError(504, "DOWNSTREAM_TIMEOUT", "Slot Service timed out. Retry the cancellation.")
    except httpx.RequestError:
        raise ApiError(503, "DOWNSTREAM_UNAVAILABLE", "Slot Service is unavailable. Retry the cancellation.")

    if cancelled.status_code == 409:
        code, message = _downstream_error(cancelled, "Slot Service")
        raise ApiError(409, code, message)

    if cancelled.status_code != 200:
        raise ApiError(502, "SLOT_SERVICE_ERROR", "Slot Service request failed.")

    # 2. CONFIRMED -> CANCELLED, conditional so only one concurrent cancel wins.
    changed = db.execute(
        update(Booking)
        .where(Booking.id == booking_id, Booking.status == "CONFIRMED")
        .values(status="CANCELLED", cancelled_at=datetime.now(timezone.utc))
    ).rowcount
    db.commit()
    db.refresh(booking)

    if changed != 1:
        return booking, True

    # 3. Tell the patient (non-critical, same retry policy as confirmations).
    booking.cancel_notification_status = _notify(
        client,
        booking,
        "cancellation",
        f"Appointment cancelled. Booking ID: {booking.id}, Slot ID: {booking.slot_id}",
        headers,
    )
    db.commit()
    db.refresh(booking)

    logger.info(
        "Booking cancelled",
        extra={
            "booking_id": booking.id,
            "reservation_id": booking.reservation_id,
            "cancel_notification_status": booking.cancel_notification_status,
        },
    )
    return booking, False
