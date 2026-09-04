"""Dividend event — one row per (ticker, ex-date).

Deliberately NOT per-portfolio. The amount a given portfolio received is
derived on read from shares-held-at-ex-date, walked from the trade
ledger. Storing a denormalised per-portfolio figure would go stale the
moment a back-dated trade is edited or imported, and those edits are
routine here (IBKR CSV imports land months of history at once).
"""
from datetime import date as _date

from sqlalchemy import Date, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class Dividend(TimestampMixin, Base):
    __tablename__ = "dividends"
    __table_args__ = (
        UniqueConstraint("ticker", "ex_date", name="uq_dividend_ticker_ex_date"),
        Index("ix_dividends_ticker_ex_date", "ticker", "ex_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Entitlement date — you must hold on/before this to receive the payment.
    ex_date: Mapped[_date] = mapped_column(Date, nullable=False, index=True)
    # When cash lands. Often unknown: yfinance's historical series carries
    # ex-dates only; pay date is available for the upcoming event alone.
    pay_date: Mapped[_date | None] = mapped_column(Date, nullable=True)
    amount_per_share: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="USD")
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="yfinance")
