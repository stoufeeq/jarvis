from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class NewsItem(TimestampMixin, Base):
    __tablename__ = "news_items"
    __table_args__ = (
        Index("ix_news_ticker_published", "ticker", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)  # None = general market news
    headline: Mapped[str] = mapped_column(String(1000), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(2000))
    source: Mapped[str | None] = mapped_column(String(100))

    # Sentiment: -1.0 (very bearish) to +1.0 (very bullish)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    # Claude-extracted signal summary
    ai_signal: Mapped[str | None] = mapped_column(Text)

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
