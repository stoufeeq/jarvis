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
