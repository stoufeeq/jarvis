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


class SignalOutcomeRead(BaseModel):
    id: int
    signal_id: int | None
    ticker: str
    signal_type: SignalType
    direction: SignalDirection
    strength: int
    rationale: str | None
    entry_price: float
    signal_created_at: datetime
    price_1d: float | None
    price_5d: float | None
    price_30d: float | None
    price_90d: float | None
    snapshot_1d_at: datetime | None
    snapshot_5d_at: datetime | None
    snapshot_30d_at: datetime | None
    snapshot_90d_at: datetime | None

    model_config = {"from_attributes": True}
