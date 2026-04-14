from datetime import datetime

from pydantic import BaseModel

from app.models.signal import SignalDirection, SignalType


class SignalRead(BaseModel):
    id: int
    ticker: str
    signal_type: SignalType
    direction: SignalDirection
    strength: int
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    rationale: str | None
    indicators: str | None
    timeframe: str | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
