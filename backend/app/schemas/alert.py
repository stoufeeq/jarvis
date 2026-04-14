from datetime import datetime

from pydantic import BaseModel

from app.models.alert import AlertType


class AlertCreate(BaseModel):
    ticker: str
    alert_type: AlertType
    threshold_value: float | None = None
    message: str | None = None
    channels: str = "in_app"


class AlertUpdate(BaseModel):
    is_active: bool | None = None
    threshold_value: float | None = None
    is_triggered: bool | None = None
    acknowledged_at: datetime | None = None


class AlertRead(BaseModel):
    id: int
    user_id: int
    ticker: str
    alert_type: AlertType
    threshold_value: float | None
    message: str | None
    is_active: bool
    is_triggered: bool
    triggered_at: datetime | None
    acknowledged_at: datetime | None
    channels: str
    created_at: datetime

    model_config = {"from_attributes": True}
