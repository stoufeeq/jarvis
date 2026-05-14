from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.signal import SignalDirection, SignalType
from app.models.strategy import (
    AllocationMode,
    StrategyExitReason,
    StrategyTradeStatus,
)


class StrategyCreate(BaseModel):
    name: str
    portfolio_id: int
    description: str | None = None

    signal_type: SignalType | None = None
    direction: SignalDirection | None = None
    min_strength: int = Field(default=4, ge=1, le=5)
    tickers: str | None = None  # comma-separated whitelist

    allocation_mode: AllocationMode = AllocationMode.fixed
    allocation_value: float = Field(default=2000, gt=0)

    max_position_pct: float = Field(default=10, gt=0, le=100)
    min_cash_reserve: float = Field(default=5000, ge=0)

    min_hold_days: int = Field(default=1, ge=0, le=365)
    base_hold_days: int = Field(default=5, ge=1, le=365)
    max_hold_days: int = Field(default=30, ge=1, le=365)

    exit_on_opposite_signal: bool = True
    extend_on_continuing_signal: bool = True

    is_active: bool = True


class StrategyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    signal_type: SignalType | None = None
    direction: SignalDirection | None = None
    min_strength: int | None = None
    tickers: str | None = None

    allocation_mode: AllocationMode | None = None
    allocation_value: float | None = None

    max_position_pct: float | None = None
    min_cash_reserve: float | None = None

    min_hold_days: int | None = None
    base_hold_days: int | None = None
    max_hold_days: int | None = None

    exit_on_opposite_signal: bool | None = None
    extend_on_continuing_signal: bool | None = None

    is_active: bool | None = None


class StrategyRead(BaseModel):
    id: int
    user_id: int
    portfolio_id: int
    name: str
    description: str | None

    signal_type: SignalType | None
    direction: SignalDirection | None
    min_strength: int
    tickers: str | None

    allocation_mode: AllocationMode
    allocation_value: float

    max_position_pct: float
    min_cash_reserve: float

    min_hold_days: int
    base_hold_days: int
    max_hold_days: int

    exit_on_opposite_signal: bool
    extend_on_continuing_signal: bool

    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategyTradeRead(BaseModel):
    id: int
    strategy_id: int
    ticker: str
    direction: SignalDirection

    buy_trade_id: int | None
    sell_trade_id: int | None
    trigger_signal_id: int | None

    entry_price: float
    exit_price: float | None
    quantity: float

    entry_at: datetime
    planned_exit_at: datetime
    exited_at: datetime | None

    status: StrategyTradeStatus
    exit_reason: StrategyExitReason | None

    model_config = {"from_attributes": True}


class StrategyStats(BaseModel):
    """Aggregate stats per strategy: open positions, total P&L, win rate."""
    open_count: int
    closed_count: int
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate_pct: float | None
