from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationRequest(BaseModel):
    patient_id: int = Field(gt=0)
    booking_id: int | None = Field(default=None, gt=0)
    message: str = Field(min_length=1, max_length=500)
    notification_type: str = Field(pattern="^(SMS|EMAIL)$")


class NotificationResponse(BaseModel):
    id: int
    patient_id: int
    booking_id: int | None
    notification_type: str
    message: str
    status: str
    attempts: int
    last_error: str | None = None
    correlation_id: str | None
    created_at: datetime
    sent_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SimulationSettings(BaseModel):
    """Behaviour of the mock SMS/e-mail provider."""

    failure_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Probability that a send attempt fails.")
    latency_ms: int = Field(default=0, ge=0, le=60000, description="Delay added to every send attempt.")
    fail_next: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Fail exactly this many upcoming send attempts, then fall back to failure_rate.",
    )
