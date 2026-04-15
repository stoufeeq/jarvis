from datetime import datetime

from pydantic import BaseModel


class WatchlistItemCreate(BaseModel):
    ticker: str
    notes: str | None = None


class WatchlistItemRead(BaseModel):
    id: int
    ticker: str
    notes: str | None
    created_at: datetime
    last_price: float | None = None
    last_change: float | None = None
    last_change_pct: float | None = None
    previous_close: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    pe_ratio: float | None = None
    rsi14: float | None = None
    price_updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class WatchlistCreate(BaseModel):
    name: str = "Main"


class WatchlistRead(BaseModel):
    id: int
    user_id: int
    name: str
    items: list[WatchlistItemRead] = []
    created_at: datetime

    model_config = {"from_attributes": True}
