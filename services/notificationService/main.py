
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Path, Query, Response, status
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
from models import Notification
from schemas import NotificationRequest, NotificationResponse, SimulationSettings
import notification_service
from notification_service import claim_notification, deliver, wait_while_pending

configure_logging("notification-service", os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("notification-service")

IN_PROGRESS_WAIT_SECONDS = float(os.getenv("NOTIFICATION_IN_PROGRESS_WAIT_SECONDS", "1.5"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Hospital Notification Service",
    description=(
        "Mock SMS / e-mail provider. Deduplicates on Idempotency-Key and can be "
        "made slow or failing to exercise the callers' failure handling."
    ),
    version="1.0.0",
    lifespan=lifespan,
    responses=ERROR_RESPONSES,
)
install_common_handlers(app)


@app.get("/health", response_model=HealthResponse, responses=HEALTH_RESPONSES)
def health():
    return health_response("notification-service", engine)


@app.post(
    "/api/v1/notifications",
    response_model=NotificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": NotificationResponse, "description": "Idempotent replay"}},
)
def send_notification(
    request: NotificationRequest,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
    db: Session = Depends(get_db),
):
    notification, outcome = claim_notification(
        db=db,
        request=request,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id_var.get(),
    )

    if outcome == "IN_PROGRESS":
        wait_while_pending(db, notification, IN_PROGRESS_WAIT_SECONDS)
        notification, outcome = claim_notification(
            db=db,
            request=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id_var.get(),
        )

    if outcome == "KEY_REUSED":
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "IDEMPOTENCY_KEY_REUSED",
            "This Idempotency-Key was already used with a different request payload.",
        )

    if outcome == "REPLAY":
        logger.info(
            "Idempotent replay, not sending again",
            extra={"notification_id": notification.id, "idempotency_key": idempotency_key},
        )
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
        return notification

    if outcome == "IN_PROGRESS":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "REQUEST_IN_PROGRESS",
            "A notification with this Idempotency-Key is being delivered; retry shortly.",
        )

    if not deliver(db, notification):
        logger.warning(
            "Simulated provider failure",
            extra={
                "notification_id": notification.id,
                "attempts": notification.attempts,
                "last_error": notification.last_error,
            },
        )
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PROVIDER_UNAVAILABLE",
            "The SMS/e-mail provider failed. Retry with the same Idempotency-Key.",
        )

    # The mock provider's "delivery": this log line is the SMS going out.
    # (The text goes in "sms_text": "message" is reserved by Python logging.)
    logger.info(
        "Mock SMS sent",
        extra={
            "notification_id": notification.id,
            "patient_id": notification.patient_id,
            "booking_id": notification.booking_id,
            "notification_type": notification.notification_type,
            "sms_text": notification.message,
            "attempts": notification.attempts,
        },
    )
    return notification


@app.get("/api/v1/notifications", response_model=list[NotificationResponse])
def list_notifications(
    patient_id: int | None = Query(default=None, gt=0, le=2_147_483_647),
    booking_id: int | None = Query(default=None, gt=0, le=2_147_483_647),
    db: Session = Depends(get_db),
):
    query = db.query(Notification)
    if patient_id is not None:
        query = query.filter(Notification.patient_id == patient_id)
    if booking_id is not None:
        query = query.filter(Notification.booking_id == booking_id)
    return query.order_by(Notification.id).all()


@app.get("/api/v1/notifications/{notification_id}", response_model=NotificationResponse)
def get_notification(notification_id: int = Path(gt=0, le=2_147_483_647), db: Session = Depends(get_db)):
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOTIFICATION_NOT_FOUND", "Notification not found.")
    return notification


# -------------------- FAULT SIMULATION --------------------

@app.get("/api/v1/simulation", response_model=SimulationSettings)
def get_simulation():
    return notification_service.simulation


@app.put("/api/v1/simulation", response_model=SimulationSettings)
def set_simulation(settings: SimulationSettings):
    notification_service.simulation = settings
    logger.warning("Provider simulation changed", extra=settings.model_dump())
    return settings


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8004")), log_config=None)
