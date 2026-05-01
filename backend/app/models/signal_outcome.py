"""
Signal outcome tracking — captures the price at signal creation and at
+1d, +5d, +30d, +90d intervals so we can measure whether each signal
type/direction/strength actually predicts price movement.

Denormalized: stores its own copy of signal_type/direction/strength so
the row survives `SignalEngine.scan_ticker` deleting old signals.
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin
from app.models.signal import SignalDirection, SignalType


class SignalOutcome(TimestampMixin, Base):
    __tablename__ = "signal_outcomes"
    __table_args__ = (
        Index("ix_signal_outcomes_ticker_created", "ticker", "signal_created_at"),
        Index("ix_signal_outcomes_type_direction", "signal_type", "direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Optional link back to the signal — SET NULL on delete so outcomes survive rescans
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )

    # Denormalized signal info (preserved even if signal row is deleted)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    signal_type: Mapped[SignalType] = mapped_column(
        Enum(SignalType, name="signal_type"), nullable=False
    )
    direction: Mapped[SignalDirection] = mapped_column(
        Enum(SignalDirection, name="signal_direction"), nullable=False
    )
    strength: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)

    # Entry snapshot at signal creation
    entry_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    signal_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Outcome snapshots — filled in by daily Celery task once enough time has elapsed
    price_1d: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_5d: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_30d: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_90d: Mapped[float | None] = mapped_column(Numeric(18, 4))

    snapshot_1d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_5d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_30d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_90d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
