from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.halal_compliance import HalalStatus


class HalalComplianceRead(BaseModel):
    """Verdict + the inputs we screened on, for the badge tooltip and
    the (future) stock detail breakdown view."""

    ticker: str
    status: HalalStatus
    reason: str | None = None

    quote_type: str | None = None
    sector: str | None = None
    industry: str | None = None
    debt_pct: float | None = None
    cash_pct: float | None = None

    computed_at: datetime

    model_config = ConfigDict(from_attributes=True)
