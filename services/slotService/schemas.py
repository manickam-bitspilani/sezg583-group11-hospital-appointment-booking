from datetime import datetime
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class SlotCreate(BaseModel):
    doctor_id: int = Field(gt=0)
    # A time without a timezone cannot be compared with the current UTC time
    # (BR8), so it is rejected with 422 instead of failing with a 500.
    start_time: AwareDatetime
    end_time: AwareDatetime
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