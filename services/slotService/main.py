
import logging
import os
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    Depends,
    Header,
    Response,
    status,
)
from sqlalchemy.orm import Session

from common import (
    ERROR_RESPONSES,
    HEALTH_RESPONSES,
    ApiError,
    HealthResponse,
    configure_logging,
    health_response,
    install_common_handlers,
)
from database import Base, engine, get_db
from models import AppointmentSlot, Reservation
from schemas import SlotCreate, SlotResponse
from reservation_schemas import ReservationCreate, ReservationResponse
from reservation_service import cancel_reservation, reserve_slot
import slot_service

configure_logging("slot-service", os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("slot-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tables are created on startup so a fresh database works without a
    # separate init step (a missing table was the cause of the earlier
    # "database error" on reservations).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Hospital Slot Booking - Slot Service",
    description="Manages hospital appointment slots and reservations.",
    version="1.0.0",
    lifespan=lifespan,
    responses=ERROR_RESPONSES,
)
install_common_handlers(app)


# -------------------- HEALTH CHECK --------------------

@app.get("/health", response_model=HealthResponse, responses=HEALTH_RESPONSES)
def health():
    return health_response("slot-service", engine)


# -------------------- CREATE APPOINTMENT SLOT --------------------

@app.post(
    "/api/v1/slots",
    response_model=SlotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_slot(
    slot_data: SlotCreate,
    db: Session = Depends(get_db),
):
    new_slot, error = slot_service.create_slot(db, slot_data)

    if error == "SLOT_IN_PAST":
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            error,
            "start_time must be in the future.",
        )
    if error == "SLOT_OVERLAP":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            error,
            "This doctor already has a slot that overlaps this time range.",
        )

    logger.info("Slot created", extra={"slot_id": new_slot.id, "capacity": new_slot.capacity})
    return new_slot


# -------------------- LIST ALL APPOINTMENT SLOTS --------------------

@app.get(
    "/api/v1/slots",
    response_model=list[SlotResponse],
)
def list_slots(db: Session = Depends(get_db)):
    return (
        db.query(AppointmentSlot)
        .order_by(AppointmentSlot.start_time)
        .all()
    )


# -------------------- GET SLOT BY ID --------------------

@app.get(
    "/api/v1/slots/{slot_id}",
    response_model=SlotResponse,
)
def get_slot(
    slot_id: int,
    db: Session = Depends(get_db),
):
    slot = (
        db.query(AppointmentSlot)
        .filter(AppointmentSlot.id == slot_id)
        .first()
    )

    if slot is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "SLOT_NOT_FOUND",
            "Appointment slot not found.",
        )

    return slot


# -------------------- CREATE RESERVATION --------------------

RESERVATION_ERRORS = {
    "SLOT_NOT_FOUND": (
        status.HTTP_404_NOT_FOUND,
        "Appointment slot not found.",
    ),
    "SLOT_FULL": (
        status.HTTP_409_CONFLICT,
        "Appointment slot is full.",
    ),
    "IDEMPOTENCY_KEY_REUSED": (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "This Idempotency-Key was already used with a different request payload.",
    ),
    "DUPLICATE_BOOKING": (
        status.HTTP_409_CONFLICT,
        "This patient already has a reservation for this appointment slot.",
    ),
    "SLOT_ALREADY_STARTED": (
        status.HTTP_409_CONFLICT,
        "This appointment slot has already started.",
    ),
    "RESERVATION_NOT_FOUND": (
        status.HTTP_404_NOT_FOUND,
        "Reservation not found.",
    ),
    "INVALID_STATE_TRANSITION": (
        status.HTTP_409_CONFLICT,
        "This reservation cannot be cancelled in its current state.",
    ),
}


@app.post(
    "/api/v1/reservations",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reservation(
    request: ReservationCreate,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
    db: Session = Depends(get_db),
):
    reservation, error = reserve_slot(
        db=db,
        slot_id=request.slot_id,
        patient_id=request.patient_id,
        idempotency_key=idempotency_key,
    )

    if error is not None:
        status_code, message = RESERVATION_ERRORS[error]
        logger.info(
            "Reservation rejected",
            extra={"reason": error, "slot_id": request.slot_id, "patient_id": request.patient_id},
        )
        raise ApiError(status_code, error, message)

    logger.info(
        "Reservation confirmed",
        extra={"reservation_id": reservation.id, "slot_id": reservation.slot_id, "patient_id": reservation.patient_id},
    )
    return reservation


# -------------------- GET / CANCEL RESERVATION --------------------

@app.get(
    "/api/v1/reservations/{reservation_id}",
    response_model=ReservationResponse,
)
def get_reservation(reservation_id: int, db: Session = Depends(get_db)):
    reservation = db.get(Reservation, reservation_id)
    if reservation is None:
        status_code, message = RESERVATION_ERRORS["RESERVATION_NOT_FOUND"]
        raise ApiError(status_code, "RESERVATION_NOT_FOUND", message)
    return reservation


@app.post(
    "/api/v1/reservations/{reservation_id}/cancel",
    response_model=ReservationResponse,
    responses={200: {"description": "Cancelled, or already cancelled (Idempotent-Replayed: true)"}},
)
def cancel(reservation_id: int, response: Response, db: Session = Depends(get_db)):
    reservation, outcome = cancel_reservation(db, reservation_id)

    if outcome == "ALREADY_CANCELLED":
        response.headers["Idempotent-Replayed"] = "true"
        return reservation

    if outcome != "CANCELLED":
        status_code, message = RESERVATION_ERRORS[outcome]
        logger.info("Cancellation rejected", extra={"reason": outcome, "reservation_id": reservation_id})
        raise ApiError(status_code, outcome, message)

    logger.info(
        "Reservation cancelled, capacity released",
        extra={"reservation_id": reservation.id, "slot_id": reservation.slot_id},
    )
    return reservation


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8001")), log_config=None)
