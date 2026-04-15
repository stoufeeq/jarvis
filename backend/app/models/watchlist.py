from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Watchlist(TimestampMixin, Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Main")

    user: Mapped["User"] = relationship(back_populates="watchlists")  # noqa: F821
    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(TimestampMixin, Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "ticker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))

    # DB-cached price data — refreshed by Celery every 5 min
    last_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    last_change: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    last_change_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    previous_close: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    fifty_two_week_high: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    fifty_two_week_low: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    price_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    watchlist: Mapped["Watchlist"] = relationship(back_populates="items")
