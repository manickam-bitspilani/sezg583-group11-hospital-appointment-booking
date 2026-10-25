"""
Patient business rules: booking eligibility and the no-show policy.
main.py only maps HTTP requests to these functions.
"""

import os

from sqlalchemy import case, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import NoShow, Patient

NO_SHOW_LIMIT = int(os.getenv("NO_SHOW_LIMIT", "3"))


def check_eligibility(patient: Patient):
    """
    BR12  Only an ACTIVE patient may book. Booking Service asks this question
          instead of reading the status itself, so the rule lives here only.

    Returns (eligible, reason, message).
    """
    if patient.status == "BLOCKED":
        return (
            False,
            "PATIENT_BLOCKED",
            f"Patient is blocked from booking after {patient.no_show_count} no-shows.",
        )
    return True, None, None


def record_no_show(db: Session, patient_id: int, booking_id: int):
    """
    BR12  Each missed appointment counts once; at NO_SHOW_LIMIT no-shows the
          patient becomes BLOCKED.

    Reporting the same booking again changes nothing (unique booking_id). The
    counter and the status change in one conditional UPDATE, so concurrent
    reports for different bookings can never lose an increment.

    Returns (patient, outcome): RECORDED, ALREADY_RECORDED, PATIENT_NOT_FOUND
    or NO_SHOW_OTHER_PATIENT (booking already reported for another patient).
    """
    patient = db.get(Patient, patient_id)
    if patient is None:
        return None, "PATIENT_NOT_FOUND"

    db.add(NoShow(patient_id=patient_id, booking_id=booking_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.query(NoShow).filter(NoShow.booking_id == booking_id).one()
        if existing.patient_id != patient_id:
            return patient, "NO_SHOW_OTHER_PATIENT"
        return patient, "ALREADY_RECORDED"

    try:
        db.execute(
            update(Patient)
            .where(Patient.id == patient_id)
            .values(
                no_show_count=Patient.no_show_count + 1,
                status=case(
                    (Patient.no_show_count + 1 >= NO_SHOW_LIMIT, "BLOCKED"),
                    else_=Patient.status,
                ),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(patient)
    return patient, "RECORDED"


def unblock(db: Session, patient_id: int):
    """
    BLOCKED -> ACTIVE (a staff decision), resetting the no-show counter. The
    no-show history rows are kept.

    Returns (patient, outcome): UNBLOCKED, PATIENT_NOT_FOUND or
    INVALID_STATE_TRANSITION (patient is not blocked).
    """
    patient = db.get(Patient, patient_id)
    if patient is None:
        return None, "PATIENT_NOT_FOUND"

    changed = db.execute(
        update(Patient)
        .where(Patient.id == patient_id, Patient.status == "BLOCKED")
        .values(status="ACTIVE", no_show_count=0)
    ).rowcount
    db.commit()
    db.refresh(patient)

    if changed != 1:
        return patient, "INVALID_STATE_TRANSITION"
    return patient, "UNBLOCKED"
