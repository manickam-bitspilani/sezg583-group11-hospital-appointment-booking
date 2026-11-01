from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SlotCreate(BaseModel):
    doctor_id: int = Field(gt=0, le=2_147_483_647)
    start_time: datetime
    end_time: datetime
    capacity: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class SlotResponse(BaseModel):
    id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    capacity: int
    reserved_count: int

    model_config = ConfigDict(from_attributes=True)