
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReservationCreate(BaseModel):
    slot_id: int = Field(gt=0)
    patient_id: int = Field(gt=0)


class ReservationResponse(BaseModel):
    id: int
    slot_id: int
    patient_id: int
    status: str
    created_at: datetime | None = None
    cancelled_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
