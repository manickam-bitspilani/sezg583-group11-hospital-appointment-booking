import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Response, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

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
from models import Patient
from schemas import EligibilityResponse, NoShowCreate, PatientCreate, PatientResponse
import patient_service

configure_logging("patient-service", os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("patient-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Hospital Patient Service",
    version="1.0.0",
    lifespan=lifespan,
    responses=ERROR_RESPONSES,
)
install_common_handlers(app)


@app.get("/health", response_model=HealthResponse, responses=HEALTH_RESPONSES)
def health():
    return health_response("patient-service", engine)


@app.post("/api/v1/patients", response_model=PatientResponse, status_code=201)
def create_patient(data: PatientCreate, db: Session = Depends(get_db)):
    """
    BR11  A phone number belongs to one patient. Enforced by the unique
          constraint on patients.phone, so two concurrent registrations with
          the same number cannot both succeed.
    """
    patient = Patient(**data.model_dump())
    db.add(patient)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "PATIENT_PHONE_EXISTS", "A patient with this phone number already exists.")
    db.refresh(patient)
    logger.info("Patient registered", extra={"patient_id": patient.id})
    return patient


@app.get("/api/v1/patients", response_model=list[PatientResponse])
def list_patients(db: Session = Depends(get_db)):
    return db.query(Patient).order_by(Patient.id).all()


@app.get("/api/v1/patients/{patient_id}", response_model=PatientResponse)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, patient_id)


def _get_or_404(db: Session, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "PATIENT_NOT_FOUND", "Patient not found.")
    return patient


# -------------------- ELIGIBILITY AND NO-SHOW POLICY (BR12) --------------------

@app.get("/api/v1/patients/{patient_id}/eligibility", response_model=EligibilityResponse)
def get_eligibility(patient_id: int, db: Session = Depends(get_db)):
    """BR12  May this patient book? Called by Booking Service before reserving a slot."""
    patient = _get_or_404(db, patient_id)
    eligible, reason, message = patient_service.check_eligibility(patient)
    return EligibilityResponse(patient_id=patient.id, eligible=eligible, reason=reason, message=message)


@app.post(
    "/api/v1/patients/{patient_id}/no-shows",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": PatientResponse, "description": "Already recorded (Idempotent-Replayed: true)"}},
)
def record_no_show(patient_id: int, data: NoShowCreate, response: Response, db: Session = Depends(get_db)):
    """
    BR12  Record a missed appointment (by front-desk staff). At NO_SHOW_LIMIT
          no-shows the patient is blocked automatically.
    """
    patient, outcome = patient_service.record_no_show(db, patient_id, data.booking_id)

    if outcome == "PATIENT_NOT_FOUND":
        raise ApiError(status.HTTP_404_NOT_FOUND, outcome, "Patient not found.")
    if outcome == "NO_SHOW_OTHER_PATIENT":
        raise ApiError(status.HTTP_409_CONFLICT, outcome, "This booking's no-show is already recorded for another patient.")
    if outcome == "ALREADY_RECORDED":
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
        return patient

    logger.info(
        "No-show recorded",
        extra={"patient_id": patient.id, "booking_id": data.booking_id,
               "no_show_count": patient.no_show_count, "status": patient.status},
    )
    if patient.status == "BLOCKED":
        logger.warning("Patient blocked: no-show limit reached", extra={"patient_id": patient.id})
    return patient


@app.post("/api/v1/patients/{patient_id}/unblock", response_model=PatientResponse)
def unblock_patient(patient_id: int, db: Session = Depends(get_db)):
    """BLOCKED -> ACTIVE; resets the no-show counter."""
    patient, outcome = patient_service.unblock(db, patient_id)

    if outcome == "PATIENT_NOT_FOUND":
        raise ApiError(status.HTTP_404_NOT_FOUND, outcome, "Patient not found.")
    if outcome == "INVALID_STATE_TRANSITION":
        raise ApiError(status.HTTP_409_CONFLICT, outcome, f"An {patient.status} patient cannot be unblocked.")

    logger.info("Patient unblocked", extra={"patient_id": patient.id})
    return patient


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8002")), log_config=None)
