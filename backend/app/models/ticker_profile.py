"""Ticker profile — cached reference data (sector, industry, country).

Separate from HalalCompliance and MagicFormulaRank, both of which happen
to store a `sector` too. Those are verdict/score tables whose rows exist
only for tickers that were screened or ranked; reading sector out of them
would make allocation silently incomplete for anything else you hold.

Reference data of this kind essentially never changes — a company's GICS
sector is stable for years — so the TTL is long and refreshes are lazy.

Populated by TickerProfileService on first use of a ticker.
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class TickerProfile(TimestampMixin, Base):
    __tablename__ = "ticker_profiles"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)

    company_name: Mapped[str | None] = mapped_column(String(255))

    # yfinance's own sector vocabulary ("Technology", "Consumer Cyclical").
    # Mapped to GICS names for benchmark comparison in the allocation
    # service — see SECTOR_TO_GICS there.
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(100))

    # EQUITY / ETF / CRYPTOCURRENCY / … — lets the allocation view tell a
    # fund apart from a single name without re-querying the provider.
    quote_type: Mapped[str | None] = mapped_column(String(20))

    # None when the provider had no sector for this symbol. Distinguishes
    # "never looked up" (no row) from "looked up, genuinely unknown"
    # (row with sector NULL), so a failed lookup isn't retried on every
    # page load.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
