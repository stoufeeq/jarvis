"""
Market snapshot — cached aggregate of market data used to ground the AI
advisor in current prices, sectors, headlines, and upcoming macro
events. Refreshed every 4 hours by a Celery task; the advisor reads the
latest row on each chat call. Rows older than 7 days are pruned by the
same task to keep the table compact.
"""

from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
