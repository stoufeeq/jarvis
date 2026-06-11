"""
Strategy + StrategyTrade models for Auto Paper Trader.

A Strategy is a user-defined rule that auto-executes paper trades when
matching signals fire. Paper-portfolio only — never touches real portfolios.

Workflow:
  1. Signal scan fires (every 15 min)
  2. New signals checked against active strategies
  3. If a signal matches filter + risk rules → buy via PortfolioService.execute_paper_trade
  4. StrategyTrade row links the paper trade back to the strategy + signal
  5. Daily exit sweep + on-scan opposite-signal sells handle exits
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Use Postgres JSONB in production for indexed-path access, plain JSON in
# tests (SQLite). variant() returns the right impl per dialect.
_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")

from app.database import Base
from app.models.base import TimestampMixin
from app.models.signal import SignalDirection, SignalType

if TYPE_CHECKING:
    pass


class StrategyTradeStatus(str, enum.Enum):
    open = "open"           # active position, not yet exited
    closed = "closed"       # exited normally (planned exit, opposite signal, max hold)


class StrategyExitReason(str, enum.Enum):
    planned = "planned"               # planned_exit_at reached
    opposite_signal = "opposite_signal"  # opposing direction signal fired
    max_hold = "max_hold"             # max_hold_days ceiling hit
    panic_close = "panic_close"       # user clicked "panic close all"
    manual = "manual"                 # closed by user manually


class AllocationMode(str, enum.Enum):
    fixed = "fixed"           # fixed dollar amount per trade
    percent = "percent"       # percent of available cash


class Strategy(TimestampMixin, Base):
    __tablename__ = "strategies"
    __table_args__ = (
        Index("ix_strategies_user_active", "user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # ── Signal filter ──────────────────────────────────────────────────
    # All filters AND together. Null = match anything.
    signal_type: Mapped[SignalType | None] = mapped_column(
        Enum(SignalType, name="signal_type"), nullable=True
    )
    direction: Mapped[SignalDirection | None] = mapped_column(
        Enum(SignalDirection, name="signal_direction"), nullable=True
    )
    min_strength: Mapped[int] = mapped_column(SmallInteger, default=4, nullable=False)
    # Per-signal-type strength override: {"fundamental": 4, "technical": 5,
    # "options_flow": 3, "earnings_upcoming": 3, "insider": 4}. When a key
    # is present, the auto-trader uses that as the gate for signals of that
    # type instead of the global min_strength. Signal types absent from the
    # map fall back to min_strength. Empty dict / null = use min_strength
    # globally (legacy behaviour).
    signal_type_strength_overrides: Mapped[dict | None] = mapped_column(
        _JSON_TYPE, nullable=True
    )
    # Optional comma-separated ticker whitelist. Empty = any ticker.
    tickers: Mapped[str | None] = mapped_column(String(2000))

    # ── Allocation rules ────────────────────────────────────────────────
    allocation_mode: Mapped[AllocationMode] = mapped_column(
        Enum(AllocationMode, name="allocation_mode"),
        default=AllocationMode.fixed,
        nullable=False,
    )
    allocation_value: Mapped[float] = mapped_column(Numeric(18, 4), default=2000, nullable=False)

    # ── Risk limits ─────────────────────────────────────────────────────
    max_position_pct: Mapped[float] = mapped_column(Numeric(8, 4), default=10.0, nullable=False)
    min_cash_reserve: Mapped[float] = mapped_column(Numeric(18, 4), default=5000, nullable=False)

    # ── Hold period (days) ──────────────────────────────────────────────
    min_hold_days: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    base_hold_days: Mapped[int] = mapped_column(SmallInteger, default=5, nullable=False)
    max_hold_days: Mapped[int] = mapped_column(SmallInteger, default=30, nullable=False)

    # ── Dynamic exit behaviour ──────────────────────────────────────────
    exit_on_opposite_signal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extend_on_continuing_signal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    trades: Mapped[list["StrategyTrade"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class StrategyTrade(TimestampMixin, Base):
    """Links a strategy-triggered paper trade to its originating signal +
    exit metadata. Buy and sell are separate rows in the `trades` table —
    StrategyTrade keeps the open/close pair grouped per strategy event."""

    __tablename__ = "strategy_trades"
    __table_args__ = (
        Index("ix_strategy_trades_strategy_status", "strategy_id", "status"),
        Index("ix_strategy_trades_ticker_status", "ticker", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[SignalDirection] = mapped_column(
        Enum(SignalDirection, name="signal_direction"), nullable=False
    )

    # The Trade rows in the immutable trades ledger
    buy_trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )
    sell_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )

    # The signal that triggered the buy (may be deleted by rescans → SET NULL)
    trigger_signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )

    # Snapshot of entry price + quantity so we don't depend on Trade row's
    # availability (it'll always exist but indexing for analytics is easier).
    entry_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)

    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_exit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[StrategyTradeStatus] = mapped_column(
        Enum(StrategyTradeStatus, name="strategy_trade_status"),
        default=StrategyTradeStatus.open,
        nullable=False,
    )
    exit_reason: Mapped[StrategyExitReason | None] = mapped_column(
        Enum(StrategyExitReason, name="strategy_exit_reason"), nullable=True
    )

    strategy: Mapped["Strategy"] = relationship(back_populates="trades")
