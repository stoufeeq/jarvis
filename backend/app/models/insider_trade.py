import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class InsiderTransactionType(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    gift = "gift"
    option_exercise = "option_exercise"


class InsiderTrade(TimestampMixin, Base):
    """
    SEC Form 4 filings — insider transactions.
    Sourced from EDGAR API and/or Quiver Quant.
    """
    __tablename__ = "insider_trades"
    __table_args__ = (
        Index("ix_insider_ticker_filed", "ticker", "filed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255))

    insider_name: Mapped[str] = mapped_column(String(255), nullable=False)
    insider_title: Mapped[str | None] = mapped_column(String(255))  # CEO, CFO, Director, etc.
    is_director: Mapped[bool] = mapped_column(default=False)
    is_officer: Mapped[bool] = mapped_column(default=False)
    is_ten_pct_owner: Mapped[bool] = mapped_column(default=False)

    transaction_type: Mapped[InsiderTransactionType] = mapped_column(
        Enum(InsiderTransactionType, name="insider_transaction_type"), nullable=False
    )
    shares: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    price_per_share: Mapped[float | None] = mapped_column(Numeric(18, 4))
    total_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    shares_owned_after: Mapped[float | None] = mapped_column(Numeric(18, 2))

    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transaction_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # EDGAR accession number — one filing produces multiple rows, so not unique
    sec_accession_number: Mapped[str | None] = mapped_column(String(50), index=True)
