from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class DailyBriefing(TimestampMixin, Base):
    """
    AI-generated pre-market briefing. One per user per calendar day.
    content_json stores the full structured Gemini response.
    summary stores 3-4 plain-text bullet points for the dashboard card.
    """
    __tablename__ = "daily_briefings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    briefing_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    overall_sentiment: Mapped[str] = mapped_column(Text, default="neutral")  # bullish/neutral/cautious/bearish
    summary: Mapped[str | None] = mapped_column(Text)          # compact bullets for dashboard card
    content_json: Mapped[str | None] = mapped_column(Text)     # full JSON from Gemini
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
