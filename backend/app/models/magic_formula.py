"""Cached Magic Formula screen result — one row per ticker.

Excluded tickers are kept with a reason rather than dropped, so the UI
can answer "why isn't JPM in this list" without the user having to know
that Greenblatt excludes financials.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class MagicFormulaRank(TimestampMixin, Base):
    __tablename__ = "magic_formula_ranks"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(100))

    # Raw inputs, stored so a surprising rank can be traced to its numbers.
    ebit: Mapped[float | None] = mapped_column(Numeric(24, 2))
    capital_employed: Mapped[float | None] = mapped_column(Numeric(24, 2))
    enterprise_value: Mapped[float | None] = mapped_column(Numeric(24, 2))

    roce: Mapped[float | None] = mapped_column(Numeric(12, 6))
    earnings_yield: Mapped[float | None] = mapped_column(Numeric(12, 6))

    # NULL on excluded rows — they take no part in the ranking.
    rank_roce: Mapped[int | None] = mapped_column(Integer)
    rank_yield: Mapped[int | None] = mapped_column(Integer)
    combined_rank: Mapped[int | None] = mapped_column(Integer, index=True)

    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(200))
    in_sp500: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
