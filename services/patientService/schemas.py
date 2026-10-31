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
    # patient_no_shows.booking_id is a PostgreSQL INTEGER; larger values would fail with a 500.
    booking_id: int = Field(gt=0, le=2_147_483_647)


class EligibilityResponse(BaseModel):
    patient_id: int
    eligible: bool
    reason: str | None = None
    message: str | None = None
