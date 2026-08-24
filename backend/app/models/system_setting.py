"""Global key-value settings row.

One row per setting. Values are opaque text — callers own the encoding
(current use-cases store plain strings and small JSON blobs).

Written to via the admin-only Settings UI; read by any service that
needs a runtime-overridable knob (starting with LLM model selection).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nullable so admin deletion doesn't cascade-nuke settings history.
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
