from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    age: int = Field(ge=0, le=120)
    phone: str = Field(min_length=10, max_length=15, pattern=r"^\+?[0-9]{10,14}$")


class PatientResponse(BaseModel):
    id: int
    full_name: str
    age: int
    phone: str
    status: str
    no_show_count: int

    model_config = ConfigDict(from_attributes=True)


class NoShowCreate(BaseModel):
    booking_id: int = Field(gt=0)


class EligibilityResponse(BaseModel):
    patient_id: int
    eligible: bool
    reason: str | None = None
    message: str | None = None
