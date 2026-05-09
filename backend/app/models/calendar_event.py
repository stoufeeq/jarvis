"""
Calendar events: upcoming earnings, ex-dividend dates, and macro events.

Earnings + ex-dividend rows are populated by a daily Celery task that
fetches yfinance data for every ticker in any user's watchlist or
portfolio. Macro events are queried from the Signal table directly
(populated by EconomicCalendarProvider) — not duplicated here.

This table is essentially a cache so the /calendar API doesn't have to
hit yfinance on every page load.
"""

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class CalendarEventType(str, enum.Enum):
    earnings = "earnings"
    ex_dividend = "ex_dividend"


class CalendarEvent(TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        Index("ix_calendar_events_ticker_date", "ticker", "event_date"),
        Index("ix_calendar_events_type_date", "event_type", "event_date"),
        # One row per (ticker, event_type, event_date) — re-runs of the
        # refresh task upsert rather than duplicate.
        UniqueConstraint("ticker", "event_type", "event_date", name="uq_calendar_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[CalendarEventType] = mapped_column(
        Enum(CalendarEventType, name="calendar_event_type"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Display title — e.g. "AAPL Earnings", "MSFT Ex-Dividend $0.83"
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional details — e.g. estimated EPS, dividend amount
    details: Mapped[str | None] = mapped_column(Text)

    # When this row was last refreshed from yfinance
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
