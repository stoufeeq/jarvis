import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class AlertType(str, enum.Enum):
    price_above = "price_above"
    price_below = "price_below"
    signal = "signal"          # any signal fires for this ticker
    pnl_threshold = "pnl_threshold"


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    alert_type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, name="alert_type"), nullable=False
    )
    threshold_value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    message: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Notification channels (comma-separated: "email,in_app,telegram")
    channels: Mapped[str] = mapped_column(String(100), default="in_app")

    user: Mapped["User"] = relationship(back_populates="alerts")  # noqa: F821
