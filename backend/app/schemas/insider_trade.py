from datetime import datetime

from pydantic import BaseModel

from app.models.insider_trade import InsiderTransactionType


class InsiderTradeRead(BaseModel):
    id: int
    ticker: str
    company_name: str | None
    insider_name: str
    insider_title: str | None
    is_director: bool
    is_officer: bool
    transaction_type: InsiderTransactionType
    shares: float
    price_per_share: float | None
    total_value: float | None
    shares_owned_after: float | None
    filed_at: datetime
    transaction_date: datetime | None

    model_config = {"from_attributes": True}
