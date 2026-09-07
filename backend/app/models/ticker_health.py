"""Per-ticker data-provider resolution status.

Only tickers the user actually references (open positions, watchlist
items) are tracked — no value in monitoring symbols nobody holds.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class TickerHealth(TimestampMixin, Base):
    __tablename__ = "ticker_health"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    # False only after a sustained run of failures — see
    # TickerHealthService.FAILURE_THRESHOLD for why one 404 is not enough.
    resolves: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL means it has never resolved since tracking began — usually a
    # typo. A populated but stale value means it used to work, pointing
    # at a delisting or ticker change instead.
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
