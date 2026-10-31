
import os
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, Path, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from common import (
    ERROR_RESPONSES,
    HEALTH_RESPONSES,
    ApiError,
    HealthResponse,
    configure_logging,
    correlation_id_var,
    health_response,
    install_common_handlers,
)
from database import Base, engine, get_db
from models import Booking
from schemas import BookingRecord, BookingRequest, BookingResponse
import booking_service

configure_logging("booking-service", os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("booking-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Hospital Booking Service",
    description="Coordinates patient validation, appointment reservations, and notifications.",
    version="1.0.0",
    lifespan=lifespan,
    responses=ERROR_RESPONSES,
)
install_common_handlers(app)


def get_http_client():
    with httpx.Client(timeout=booking_service.DOWNSTREAM_TIMEOUT_SECONDS) as client:
        yield client


@app.get("/health", response_model=HealthResponse, responses=HEALTH_RESPONSES)
def health():
    return health_response("booking-service", engine)


@app.post(
    "/api/v1/bookings",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_booking(
    request: BookingRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
    db: Session = Depends(get_db),
    client: httpx.Client = Depends(get_http_client),
):
    status_code, body, replayed = booking_service.place_booking(
        db, client, request, idempotency_key, correlation_id_var.get()
    )
    headers = {"Idempotent-Replayed": "true"} if replayed else None
    return JSONResponse(status_code=status_code, content=body, headers=headers)


@app.get("/api/v1/bookings/{booking_id}", response_model=BookingRecord)
def get_booking(booking_id: int = Path(gt=0, le=2_147_483_647), db: Session = Depends(get_db)):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "BOOKING_NOT_FOUND", "Booking not found.")
    return booking


@app.post(
    "/api/v1/bookings/{booking_id}/cancel",
    response_model=BookingRecord,
    responses={200: {"description": "Cancelled, or already cancelled (Idempotent-Replayed: true)"}},
)
def cancel_booking(
    response: Response,
    booking_id: int = Path(gt=0, le=2_147_483_647),
    db: Session = Depends(get_db),
    client: httpx.Client = Depends(get_http_client),
):
    """
    BR10  Only a CONFIRMED booking can be cancelled, and only before the slot
          starts. Repeating a cancel returns the booking unchanged.
    """
    booking, replayed = booking_service.cancel_booking(db, client, booking_id, correlation_id_var.get())
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return booking


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8003")), log_config=None)
