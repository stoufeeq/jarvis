"""
Halal compliance — cached Sharia screening verdict per ticker.

Look-aside cache: HalalScreenerService writes a row here when it first
screens a ticker (or refreshes a stale one). Read path on the API is a
single indexed lookup; writes happen on miss + 24h TTL.

Verdict logic lives in app/services/halal_screener.py — see that file
for the AAOIFI ratio thresholds and the whitelist/banned-industries
source data at app/data/halal_whitelist.json.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class HalalStatus(str, enum.Enum):
    compliant = "compliant"
    non_compliant = "non_compliant"
    unknown = "unknown"


class HalalCompliance(TimestampMixin, Base):
    __tablename__ = "halal_compliance"

    # Ticker is the natural key; no separate id needed.
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)

    status: Mapped[HalalStatus] = mapped_column(
        Enum(HalalStatus, name="halal_status"), nullable=False
    )

    # Short human-readable reason — e.g. "ETF whitelist",
    # "Industry: Banks—Diversified", "Debt 41% > 33%", "Missing financials".
    reason: Mapped[str | None] = mapped_column(Text)

    # Snapshot of the data we screened on, for the detail-page view.
    quote_type: Mapped[str | None] = mapped_column(String(20))
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(200))
    debt_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    cash_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # When the verdict was computed; HalalScreenerService treats rows
    # older than 24h as stale and recomputes on next read.
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
