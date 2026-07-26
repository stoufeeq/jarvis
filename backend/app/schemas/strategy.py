from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.signal import SignalDirection, SignalType
from app.models.strategy import (
    AllocationMode,
    StrategyExitReason,
    StrategyTradeStatus,
)

# Accepted keys in signal_type_strength_overrides — must match SignalType enum values.
_VALID_OVERRIDE_KEYS = {t.value for t in SignalType}


def _validate_overrides(v: dict | None) -> dict | None:
    """Reject unknown signal_type keys + out-of-range strength values."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ValueError("signal_type_strength_overrides must be a dict")
    cleaned: dict[str, int] = {}
    for key, val in v.items():
        if key not in _VALID_OVERRIDE_KEYS:
            raise ValueError(f"Unknown signal type in overrides: {key!r}")
        if not isinstance(val, int):
            raise ValueError(f"Strength for {key} must be an int")
        if val < 1 or val > 5:
            raise ValueError(f"Strength for {key} must be 1..5")
        cleaned[key] = val
    return cleaned or None  # drop empty dict


class StrategyCreate(BaseModel):
    name: str
    portfolio_id: int
    description: str | None = None

    signal_type: SignalType | None = None
    direction: SignalDirection | None = None
    min_strength: int = Field(default=4, ge=1, le=5)
    signal_type_strength_overrides: dict[str, int] | None = None
    tickers: str | None = None  # comma-separated whitelist
    excluded_tickers: str | None = None  # comma-separated blacklist
    # Comma-separated list of regime names the strategy is allowed to
    # open new positions in. NULL / empty = no regime gate.
    allowed_regimes: str | None = None

    allocation_mode: AllocationMode = AllocationMode.fixed
    allocation_value: float = Field(default=2000, gt=0)

    max_position_pct: float = Field(default=10, gt=0, le=100)
    min_cash_reserve: float = Field(default=5000, ge=0)

    min_hold_days: int = Field(default=1, ge=0, le=365)
    base_hold_days: int = Field(default=5, ge=1, le=365)
    # Default lowered 30→10 (2026-07-21) based on paper-trade analysis:
    # positions held to the 30-day ceiling had 29% hit rate, PF 0.34.
    max_hold_days: int = Field(default=10, ge=1, le=365)

    exit_on_opposite_signal: bool = True
    extend_on_continuing_signal: bool = True
    # Stop-loss as a signed percent (negative for long positions).
    # e.g. -8.0 = exit when unrealised P&L% <= -8%. None = disabled.
    stop_loss_pct: float | None = Field(default=None, ge=-100, le=0)

    is_active: bool = True

    _v_overrides = field_validator("signal_type_strength_overrides", mode="before")(_validate_overrides)


class StrategyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    signal_type: SignalType | None = None
    direction: SignalDirection | None = None
    min_strength: int | None = None
    signal_type_strength_overrides: dict[str, int] | None = None
    tickers: str | None = None
    excluded_tickers: str | None = None
    allowed_regimes: str | None = None

    allocation_mode: AllocationMode | None = None
    allocation_value: float | None = None

    max_position_pct: float | None = None
    min_cash_reserve: float | None = None

    min_hold_days: int | None = None
    base_hold_days: int | None = None
    max_hold_days: int | None = None

    exit_on_opposite_signal: bool | None = None
    extend_on_continuing_signal: bool | None = None
    stop_loss_pct: float | None = Field(default=None, ge=-100, le=0)

    is_active: bool | None = None

    _v_overrides = field_validator("signal_type_strength_overrides", mode="before")(_validate_overrides)


class StrategyRead(BaseModel):
    id: int
    user_id: int
    portfolio_id: int
    name: str
    description: str | None

    signal_type: SignalType | None
    direction: SignalDirection | None
    min_strength: int
    signal_type_strength_overrides: dict[str, int] | None = None
    tickers: str | None
    excluded_tickers: str | None = None
    allowed_regimes: str | None = None

    allocation_mode: AllocationMode
    allocation_value: float

    max_position_pct: float
    min_cash_reserve: float

    min_hold_days: int
    base_hold_days: int
    max_hold_days: int

    exit_on_opposite_signal: bool
    extend_on_continuing_signal: bool
    stop_loss_pct: float | None = None

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
    # Signal metadata snapshotted at trade creation — survives rescans
    # that wipe the FK. Nullable for pre-migration rows.
    trigger_signal_type: SignalType | None = None
    trigger_signal_strength: int | None = None

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
