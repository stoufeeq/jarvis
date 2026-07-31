"""Daily market regime classification.

One row per trading day. Populated by the regime refresh Celery task
(and backfilled on demand). Consumed by:
  - auto_trader: gate signals on the current day's regime
  - analysis script: join historical trades to the regime they were in
"""

from datetime import date

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


# Valid regime names. Keep in sync with RegimeService.REGIMES.
# 2026-07-31: crisis tier added — VIX ≥ 30 in either bull or bear.
REGIME_NAMES = (
    "bull_low_vol",
    "bull_high_vol",
    "bull_crisis",
    "bear_low_vol",
    "bear_high_vol",
    "bear_crisis",
)


class MarketRegime(TimestampMixin, Base):
    __tablename__ = "market_regimes"

    # Trading date is the natural key — one row per day.
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    # Store the inputs so future rule tweaks can be validated against
    # historical data without re-fetching from Yahoo.
    spx_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    spx_sma200: Mapped[float | None] = mapped_column(Numeric(12, 4))
    vix_close: Mapped[float | None] = mapped_column(Numeric(8, 4))
