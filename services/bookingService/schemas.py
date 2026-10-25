from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BookingRequest(BaseModel):
    patient_id: int = Field(gt=0)
    slot_id: int = Field(gt=0)


class BookingResponse(BaseModel):
    booking_id: int
    reservation_id: int
    patient_id: int
    slot_id: int
    status: str
    notification_status: str
    correlation_id: str


class BookingRecord(BaseModel):
    id: int
    patient_id: int
    slot_id: int
    reservation_id: int | None
    status: str
    notification_status: str | None
    cancel_notification_status: str | None = None
    correlation_id: str | None
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
