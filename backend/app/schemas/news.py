from datetime import datetime

from pydantic import BaseModel


class NewsItemRead(BaseModel):
    id: int
    ticker: str | None
    headline: str
    summary: str | None
    url: str | None
    source: str | None
    sentiment_score: float | None
    ai_signal: str | None
    published_at: datetime

    model_config = {"from_attributes": True}
